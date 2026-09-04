import functools
import json

import click

from cloudrobo_core.cli.cli_utils import get_client, out
from .client import TrainClient


@click.group()
def train():
    """模型训练命令组"""
    pass


_SIMRL_FLAG = click.option("--sim-rl", is_flag=True, help="操作仿真强化学习任务而非普通训练任务")
_VERBOSE_FLAG = click.option("--verbose", "-v", is_flag=True, help="展示提交内容详情")
_WORKSPACE_ID_FLAG = click.option("--workspace-id", default=None, help="工作空间ID，不提供则使用默认配置")


def _show_submit_details(req, task_type="训练任务"):
    """展示待提交内容详情"""
    click.echo(f"\n==================== 待提交{task_type} ====================")
    click.echo(json.dumps(req, indent=2, ensure_ascii=False))
    click.echo("=" * (28 + len(task_type)))


def _load_config(task_config, config_file):
    """从 --config 或 --config-file 加载 JSON 配置"""
    if task_config and config_file:
        raise click.UsageError("--config 和 --config-file 不能同时使用")
    if not task_config and not config_file:
        raise click.UsageError("必须提供 --config 或 --config-file 之一")
    if config_file:
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            raise click.BadParameter(f"文件不存在: {config_file}")
        except json.JSONDecodeError as e:
            raise click.BadParameter(f"文件 JSON 解析失败: {e}")
    try:
        return json.loads(task_config)
    except json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON: {e}")


_CONFIG_FLAG = click.option("--config", "task_config", default=None, help="任务配置(JSON字符串)")
_CONFIG_FILE_FLAG = click.option("--config-file", default=None, type=click.Path(),
                                 help="任务配置文件路径（与 --config 二选一，PowerShell 推荐）")


def _build_params(**kwargs):
    """过滤 None 和空值，构建 API 请求参数"""
    result = {}
    for k, v in kwargs.items():
        if v is None:
            continue
        if isinstance(v, str) and not v:
            continue
        if isinstance(v, (tuple, list)) and not v:
            continue
        result[k] = v
    return result


def _increment_version(version_name):
    """递增版本号，如 v0.0.33 → v0.0.34"""
    import re
    m = re.match(r"^(v?)(\d+)\.(\d+)\.(\d+)$", version_name)
    if not m:
        return version_name + ".1"
    prefix, major, minor, patch = m.groups()
    return f"{prefix}{major}.{minor}.{int(patch) + 1}"


def _handle_output_models_for_restart(task_detail, user_config):
    """重训时处理 output_models：换基模型时自动将 NEW_MODEL 转为 NEW_VERSION"""
    original_output = task_detail.get("output_models", [])
    if not original_output:
        return
    if "output_models" in user_config:
        user_output = user_config["output_models"]
        if isinstance(user_output, list) and user_output:
            if any("model_asset_id" not in om for om in user_output):
                return
        effective = user_output
    else:
        effective = original_output
    if not isinstance(effective, list):
        return
    converted = False
    for om in effective:
        if not isinstance(om, dict):
            continue
        save_mode = om.get("save_mode")
        if save_mode == "NEW_MODEL":
            om["save_mode"] = "NEW_VERSION"
            om.pop("version_id", None)
            if om.get("version_name"):
                om["version_name"] = _increment_version(om["version_name"])
            converted = True
        elif save_mode == "NEW_VERSION":
            om.pop("version_id", None)
            if om.get("version_name"):
                om["version_name"] = _increment_version(om["version_name"])
            converted = True
    if converted and "output_models" not in user_config:
        user_config["output_models"] = effective


def _catch_sdk_errors(fn):
    """捕获 SDK 层 ValueError 并转为 click.UsageError"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as e:
            raise click.UsageError(str(e))
    return wrapper


_STATUS_ZH = {
    "DRAFT": ("草稿", "cyan"),
    "WAITING": ("等待中", "yellow"),
    "RUNNING": ("运行中", "green"),
    "FINISHED": ("已完成", "blue"),
    "FAILED": ("失败", "red"),
    "STOPPING": ("停止中", "yellow"),
    "STOPPED": ("已停止", "magenta"),
    "DELETING": ("删除中", "yellow"),
    "ABNORMAL": ("异常", "red"),
    "CREATING": ("创建中", "yellow"),
    "CREATE_FAILED": ("创建失败", "red"),
    "RUN_FAILED": ("运行失败", "red"),
    "STOP_FAILED": ("停止失败", "red"),
    "DELETE_FAILED": ("删除失败", "red"),
    "NOT_EXIST": ("不存在", "dim"),
    "UNKNOWN": ("未知", "dim"),
}


def _fmt_status(status):
    from rich.text import Text
    label, color = _STATUS_ZH.get(status, (status, "white"))
    return Text(label, style=color)


def _fmt_spec(spec):
    import re
    if not spec:
        return "-"
    return re.sub(r"^(?:Ascend|ASCEND):\s*", "", spec)


def _fmt_time_ms(ts_ms):
    from datetime import datetime, timezone, timedelta
    if not ts_ms:
        return "-"
    tz = timezone(timedelta(hours=8))
    try:
        return datetime.fromtimestamp(ts_ms / 1000, tz).strftime("%Y/%m/%d %H:%M:%S GMT+08:00")
    except (ValueError, OSError):
        return str(ts_ms)


def _render_task_table(payload, sim_rl=False):
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    tasks = (payload or {}).get("list", []) if isinstance(payload, dict) else []
    page_info = (payload or {}).get("page_info", {}) if isinstance(payload, dict) else {}
    console = Console()
    if not tasks:
        console.print("[dim]无任务记录[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("作业名称/ID")
    table.add_column("状态")
    if sim_rl:
        table.add_column("来源模型")
        table.add_column("实例规格")
        table.add_column("创建者")
        table.add_column("创建时间")
    else:
        table.add_column("训练方式")
        table.add_column("实例规格")
        table.add_column("运行记录")
        table.add_column("创建者")
        table.add_column("创建时间")

    for t in tasks:
        name = t.get("name", "-")
        tid = t.get("id", "-")
        name_id = Text.assemble((f"{name}\n", "bold blue"), (tid, "dim"))
        status = _fmt_status(t.get("status"))
        spec = _fmt_spec(t.get("spec"))
        user = t.get("user_name", "-")
        ctime = _fmt_time_ms(t.get("create_at"))

        if sim_rl:
            input_models = t.get("input_models", [])
            if input_models and isinstance(input_models, list):
                model = input_models[0]
                model_name = model.get("model_name", "-")
                model_version = model.get("model_version_name", "")
                source_model = f"{model_name} | {model_version}" if model_version else model_name
            else:
                source_model = "-"
            table.add_row(name_id, status, source_model, spec, user, ctime)
        else:
            mode = t.get("train_mode")
            if mode == "TRAIN_FROM_SCRATCH":
                mode_zh = "无基模型训练"
            elif mode == "MODEL_TUNING":
                mode_zh = "模型调优"
            else:
                mode_zh = mode or "-"
            rec = t.get("record_num")
            rec_str = str(rec) if rec is not None else "0"
            table.add_row(name_id, status, mode_zh, spec, rec_str, user, ctime)

    console.print(table)
    total = page_info.get("total", len(tasks))
    if total:
        console.print(f"\n共 {total} 条记录")


@train.command("list-tasks")
@click.option("--workspace-id", default=None, help="工作空间ID")
@click.option("--train-mode", "train_mode", multiple=True, help="训练模式（可重复）：TRAIN_FROM_SCRATCH/MODEL_TUNING")
@click.option("--status", multiple=True, help="任务状态过滤（可重复）")
@click.option("--offset", type=int, default=None, help="分页偏移量")
@click.option("--limit", type=int, default=None, help="每页数量")
@click.option("--order", default=None, help="排序方向：DESC/ASC")
@click.option("--name", default=None, help="任务名称模糊查询")
@click.option("--group-id", default=None, help="任务组ID")
@click.option("--user-name", default=None, help="创建者名称")
@click.option("--run-id", default=None, help="运行流水号")
@click.option("--execution-id", default=None, help="执行ID")
@click.option("--include-archived", is_flag=True, default=None, help="包含已归档任务（仅普通训练）")
@click.option("--include-history", is_flag=True, default=None, help="包含历史任务")
@click.option("--only-total", is_flag=True, default=None, help="仅返回总数")
@click.option("--exact-name", is_flag=True, default=None, help="精确搜索任务名称")
@click.option("--order-time", default=None, help="排序时间字段（仅普通训练）：create_at 等")
@click.option("--order-by", default=None, help="排序字段（仅仿真强化学习）：create_at/start_at 等")
@click.option("--display-type", default=None, help="展示类型（仅普通训练）")
@click.option("--type", "type_", default=None, help="类型（仅普通训练）")
@click.option("--json", "as_json", is_flag=True, help="输出原始JSON而非表格")
@_SIMRL_FLAG
@_catch_sdk_errors
@click.pass_context
def list_tasks(ctx, workspace_id, train_mode, status, offset, limit, order, name, group_id,
               user_name, run_id, execution_id, include_archived, include_history,
               only_total, exact_name, order_time, order_by, display_type, type_, as_json, sim_rl):
    """列出训练任务"""
    client = get_client(ctx, TrainClient)
    params = _build_params(
        workspace_id=workspace_id,
        train_mode=list(train_mode) if train_mode else None,
        status=list(status) if status else None,
        offset=offset,
        limit=limit,
        order=order,
        name=name,
        group_id=group_id,
        user_name=user_name,
        run_id=run_id,
        execution_id=execution_id,
        include_archived=include_archived,
        include_history=include_history,
        only_total=only_total,
        exact_name=exact_name,
        order_time=order_time if not sim_rl else None,
        order_by=order_by if sim_rl else None,
        display_type=display_type if not sim_rl else None,
        type=type_ if not sim_rl else None,
    )
    result = client.list_sim_rl_tasks(**params) if sim_rl else client.list_train_tasks(**params)
    if as_json:
        out(result)
    else:
        _render_task_table(result, sim_rl=sim_rl)

@train.command("create-task")
@_CONFIG_FLAG
@_CONFIG_FILE_FLAG
@_SIMRL_FLAG
@_VERBOSE_FLAG
@_WORKSPACE_ID_FLAG
@_catch_sdk_errors
@click.pass_context
def create_task(ctx, task_config, config_file, sim_rl, verbose, workspace_id):
    """创建训练任务（通用，提交完整配置JSON）"""
    _config = _load_config(task_config, config_file)
    if workspace_id:
        _config["workspace_id"] = workspace_id
    client = get_client(ctx, TrainClient)
    if sim_rl:
        if verbose:
            _show_submit_details(_config, "仿真强化学习任务")
        result = client.create_sim_rl_task(_config, workspace_id=workspace_id)
    else:
        if verbose:
            _show_submit_details(_config, "训练任务")
        result = client.create_train_task(_config, workspace_id=workspace_id)
    out(result)


@train.command("show-task")
@click.option("--task-id", required=True, help="任务ID")
@_SIMRL_FLAG
@click.pass_context
def show_task(ctx, task_id, sim_rl):
    """查看训练任务详情"""
    client = get_client(ctx, TrainClient)
    if sim_rl:
        result = client.show_sim_rl_task(task_id)
    else:
        result = client.show_train_task(task_id)
    out(result)


@train.command("update-task")
@click.option("--task-id", required=True, help="任务ID")
@_CONFIG_FLAG
@_CONFIG_FILE_FLAG
@_SIMRL_FLAG
@click.pass_context
def update_task(ctx, task_id, task_config, config_file, sim_rl):
    """更新训练任务名称和描述"""
    _config = _load_config(task_config, config_file)
    client = get_client(ctx, TrainClient)
    if sim_rl:
        result = client.update_sim_rl_task(task_id, _config)
    else:
        result = client.update_train_task(task_id, _config)
    out(result)


@train.command("delete-tasks")
@click.option("--task-id", "task_ids", required=True, multiple=True, help="任务ID（可重复）")
@_SIMRL_FLAG
@click.pass_context
def delete_tasks(ctx, task_ids, sim_rl):
    """删除训练任务（普通任务批量删除，仿真强化学习任务逐个删除）"""
    client = get_client(ctx, TrainClient)
    if sim_rl:
        for tid in task_ids:
            client.delete_sim_rl_task(tid)
        out({"deleted": list(task_ids)})
    else:
        execution_ids = []
        for tid in task_ids:
            try:
                task = client.show_train_task(tid)
                exec_id = task.get("execution_id", tid)
                execution_ids.append(exec_id)
            except Exception:
                execution_ids.append(tid)
        result = client.batch_delete_train_tasks(execution_ids)
        out(result if result is not None else {"deleted": list(task_ids)})


@train.command("stop-task")
@click.option("--task-id", required=True, help="任务ID")
@_SIMRL_FLAG
@click.pass_context
def stop_task(ctx, task_id, sim_rl):
    """停止训练任务"""
    client = get_client(ctx, TrainClient)
    if sim_rl:
        result = client.stop_sim_rl_task(task_id)
    else:
        result = client.stop_train_task(task_id)
    out(result)


@train.command("restart-task")
@click.option("--task-id", required=True, help="任务ID")
@_CONFIG_FLAG
@_CONFIG_FILE_FLAG
@_SIMRL_FLAG
@_VERBOSE_FLAG
@_WORKSPACE_ID_FLAG
@_catch_sdk_errors
@click.pass_context
def restart_task(ctx, task_id, task_config, config_file, sim_rl, verbose, workspace_id):
    """重新提交训练任务（支持修改配置）"""
    client = get_client(ctx, TrainClient)

    user_config = dict()
    if task_config or config_file:
        user_config = _load_config(task_config, config_file)

    if sim_rl:
        task_detail = client.show_sim_rl_task(task_id)
        status = task_detail.get("status")
        if status != "DRAFT":
            raise click.UsageError(
                f"仿真强化学习任务只能在草稿状态时重启，当前状态: {status}"
            )
        result = client.restart_sim_rl_task(
            task_id, req=user_config if user_config else None, workspace_id=workspace_id, task_detail=task_detail
        )
        if verbose and user_config:
            _show_submit_details(user_config, "重训配置修改")
    else:
        task_detail = client.show_train_task(task_id)
        status = task_detail.get("status")
        allowed = {"DRAFT", "FAILED", "STOPPED", "FINISHED", "CREATE_FAILED"}
        if status not in allowed:
            raise click.UsageError(
                f"训练任务只能在失败、已停止、已完成或草稿状态时重启，当前状态: {status}"
            )

        if user_config:
            if status != "DRAFT":
                forbidden = {"name", "train_mode", "train_method"}
                violations = forbidden & set(user_config.keys())
                if violations:
                    raise click.UsageError(
                        f"非草稿状态重训时不能修改以下字段: {', '.join(sorted(violations))}"
                    )
            else:
                if "name" in user_config:
                    raise click.UsageError("草稿状态重训时不能修改作业名称")

        _handle_output_models_for_restart(task_detail, user_config)
        result = client.restart_train_task(task_id, req=user_config if user_config else None, workspace_id=workspace_id, task_detail=task_detail)

        if verbose and user_config:
            _show_submit_details(user_config, "重训配置修改")
    out(result)


@train.command("clone-task")
@click.option("--task-id", required=True, help="任务ID")
@_CONFIG_FLAG
@_CONFIG_FILE_FLAG
@_VERBOSE_FLAG
@_WORKSPACE_ID_FLAG
@_catch_sdk_errors
@click.pass_context
def clone_task(ctx, task_id, task_config, config_file, verbose, workspace_id):
    """克隆仿真强化学习任务（自动查询原任务配置）"""
    client = get_client(ctx, TrainClient)

    user_config = dict()
    if task_config or config_file:
        user_config = _load_config(task_config, config_file)

    if workspace_id:
        user_config["workspace_id"] = workspace_id

    task_detail = client.show_sim_rl_task(task_id)
    result = client.copy_sim_rl_task(task_id, req=user_config if user_config else None, task_detail=task_detail)
    if verbose and user_config:
        _show_submit_details(user_config, "克隆配置修改")
    out(result)


@train.command("save-draft")
@_CONFIG_FLAG
@_CONFIG_FILE_FLAG
@_SIMRL_FLAG
@_VERBOSE_FLAG
@_WORKSPACE_ID_FLAG
@_catch_sdk_errors
@click.pass_context
def save_draft(ctx, task_config, config_file, sim_rl, verbose, workspace_id):
    """保存训练配置草稿"""
    _config = _load_config(task_config, config_file)
    if workspace_id:
        _config["workspace_id"] = workspace_id
    client = get_client(ctx, TrainClient)
    if sim_rl:
        if verbose:
            _show_submit_details(_config, "仿真强化学习任务草稿")
        result = client.create_sim_rl_task_draft(_config, workspace_id=workspace_id)
    else:
        if verbose:
            _show_submit_details(_config, "训练任务草稿")
        result = client.save_draft(_config, workspace_id=workspace_id)
    out(result)


@train.command("resume-task")
@click.option("--task-id", required=True, help="任务ID")
@click.pass_context
def resume_task(ctx, task_id):
    """续训训练任务（仅普通训练任务）"""
    client = get_client(ctx, TrainClient)
    result = client.resume_train_task(task_id)
    out(result)


@train.command("get-stages")
@click.option("--task-id", required=True, help="任务ID")
@_SIMRL_FLAG
@click.pass_context
def get_stages(ctx, task_id, sim_rl):
    """获取训练阶段信息"""
    client = get_client(ctx, TrainClient)
    result = client.list_train_stages(task_id)
    out(result)


@train.command("get-resource-usage")
@click.option("--task-id", required=True, help="任务ID")
@click.option("--metric", required=True, help="指标类型，如 cpu_util/mem_util/gpu_util 等")
@click.option("--start", required=True, type=int, help="起始时间戳（秒）")
@click.option("--end", required=True, type=int, help="结束时间戳（秒）")
@click.option("--worker-index", type=int, default=None, help="工作节点索引")
@click.option("--step", type=int, default=None, help="步长")
@_SIMRL_FLAG
@click.pass_context
def get_resource_usage(ctx, task_id, metric, start, end, worker_index, step, sim_rl):
    """查看资源使用情况"""
    client = get_client(ctx, TrainClient)
    params = {}
    if worker_index is not None:
        params["worker_index"] = worker_index
    if step is not None:
        params["step"] = step
    if sim_rl:
        result = client.show_sim_rl_task_resource_usage(task_id, metric, start, end, **params)
    else:
        result = client.show_resource_usage(task_id, metric, start, end, **params)
    out(result)


@train.command("get-logs")
@click.option("--task-id", required=True, help="任务ID")
@click.option("--file-name", default=None, help="日志文件名称，和 --log-name-pre 必须提供一个")
@click.option("--log-name-pre", default=None, help="日志文件名前缀，和 --file-name 必须提供一个（file_name 优先）")
@click.option("--work-num", type=int, default=None, help="多机训练节点序号")
@click.option("--catalog", required=True, help="文件目录类型：logs/metrics")
@click.option("--start-byte", type=int, default=0, help="起始字节")
@click.option("--end-byte", type=int, default=100000, help="结束字节")
@click.option("--offset", type=int, default=None, help="分页偏移量")
@click.option("--limit", type=int, default=None, help="每页数量")
@_SIMRL_FLAG
@click.pass_context
def get_logs(ctx, task_id, file_name, log_name_pre, work_num, catalog, start_byte, end_byte, offset, limit, sim_rl):
    """获取训练日志内容"""
    if not file_name and not log_name_pre:
        raise click.UsageError("必须提供 --file-name 或 --log-name-pre 之一")
    client = get_client(ctx, TrainClient)
    params = {}
    if file_name:
        params["file_name"] = file_name
    if log_name_pre:
        params["log_name_pre"] = log_name_pre
    if work_num is not None:
        params["work_num"] = work_num
    if catalog:
        params["catalog"] = catalog
    if start_byte is not None:
        params["start_byte"] = start_byte
    if end_byte is not None:
        params["end_byte"] = end_byte
    if offset is not None:
        params["offset"] = offset
    if limit is not None:
        params["limit"] = limit
    if sim_rl:
        result = client.show_sim_rl_task_observations_content(task_id, **params)
    else:
        result = client.get_log_content(task_id, **params)
    out(result)


@train.command("get-signed-url")
@click.option("--task-id", required=True, help="任务ID")
@click.option("--file-source", required=True, help="日志类型：EVALUATE/TRAIN/TRAINING_METRICS 等")
@click.option("--file-name", required=True, help="日志文件名称")
@click.option("--catalog", required=True, help="文件目录类型：logs/metrics")
@_SIMRL_FLAG
@click.pass_context
def get_signed_url(ctx, task_id, file_source, file_name, catalog, sim_rl):
    """获取日志文件下载签名URL"""
    client = get_client(ctx, TrainClient)
    params = {}
    if catalog:
        params["catalog"] = catalog
    if sim_rl:
        result = client.show_sim_rl_task_observations_signed_url(task_id, file_source, file_name, **params)
    else:
        result = client.get_log_signed_url(task_id, file_source, file_name, **params)
    out(result)


@train.command("get-events")
@click.option("--task-id", required=True, help="任务ID")
@click.option("--start-time", required=True, type=int, help="开始时间戳（毫秒）")
@click.option("--end-time", required=True, type=int, help="结束时间戳（毫秒）")
@click.option("--level", default=None, help="事件级别：Info/Warning/Error")
@click.option("--source", "event_source", default=None, help="事件来源：K8S/Job/Task")
@click.option("--pattern", "event_pattern", default=None, help="事件内容匹配模式")
@click.option("--offset", type=int, default=None, help="分页偏移量")
@click.option("--limit", type=int, default=None, help="每页数量")
@click.option("--order", default=None, help="排序方式：DESC/ASC")
@_SIMRL_FLAG
@click.pass_context
def get_events(ctx, task_id, start_time, end_time, level, event_source, event_pattern, offset, limit, order, sim_rl):
    """获取训练事件"""
    client = get_client(ctx, TrainClient)
    params = {}
    if level:
        params["level"] = level
    if event_source:
        params["source"] = event_source
    if event_pattern:
        params["pattern"] = event_pattern
    if offset is not None:
        params["offset"] = offset
    if limit is not None:
        params["limit"] = limit
    if order:
        params["order"] = order
    if sim_rl:
        result = client.list_sim_rl_task_events(task_id, start_time, end_time, **params)
    else:
        result = client.list_events(task_id, start_time, end_time, **params)
    out(result)


@train.command("stats")
@_WORKSPACE_ID_FLAG
@click.option("--user-id", default=None, help="用户ID")
@_SIMRL_FLAG
@_catch_sdk_errors
@click.pass_context
def stats(ctx, workspace_id, user_id, sim_rl):
    """统计各状态训练任务数量"""
    client = get_client(ctx, TrainClient)
    if sim_rl:
        result = client.count_sim_rl_tasks_by_status(workspace_id, user_id)
    else:
        result = client.count_train_tasks_by_status(workspace_id, user_id)
    out(result)


@train.command("list-checkpoints")
@click.option("--task-id", required=True, help="任务ID")
@click.option("--offset", type=int, default=None, help="分页偏移量")
@click.option("--limit", type=int, default=None, help="每页数量")
@click.option("--order", default=None, help="排序方式：DESC/ASC")
@click.option("--status", default=None, help="注册状态：UNREGISTERED/PENDING/PROCESSING/SUCCESS/FAILED/EXPIRED")
@click.option("--name", default=None, help="checkpoint 名称模糊搜索")
@click.pass_context
def list_checkpoints(ctx, task_id, offset, limit, order, status, name):
    """获取训练任务 checkpoint 列表（仅普通训练任务）"""
    client = get_client(ctx, TrainClient)
    params = {}
    if offset is not None:
        params["offset"] = offset
    if limit is not None:
        params["limit"] = limit
    if order:
        params["order"] = order
    if status:
        params["status"] = status
    if name:
        params["name"] = name
    result = client.list_train_checkpoints(task_id, **params)
    out(result)


@train.command("register-checkpoint")
@click.option("--task-id", required=True, help="任务ID")
@click.option("--checkpoint-name", required=True, help="checkpoint 名称")
@click.option("--save-mode", default="NEW_VERSION", help="保存方式：NEW_VERSION/NEW_MODEL")
@click.option("--version-name", default=None, help="版本标签（NEW_VERSION 模式可选）")
@click.option("--model-name", default=None, help="模型名称（NEW_MODEL 模式必填）")
@_VERBOSE_FLAG
@_catch_sdk_errors
@click.pass_context
def register_checkpoint(ctx, task_id, checkpoint_name, save_mode, version_name, model_name, verbose):
    """注册 checkpoint 为模型资产版本（仅普通训练任务）"""
    client = get_client(ctx, TrainClient)
    req = {"save_mode": save_mode, "checkpoint_name": checkpoint_name}
    if version_name:
        req["version_name"] = version_name
    if model_name:
        req["model_name"] = model_name
    if verbose:
        _show_submit_details(req, "checkpoint 注册")
    result = client.register_train_checkpoint(task_id, req)
    out(result)
