import json
import re
import sys
import traceback
from functools import wraps

import click

from cloudrobo_core.cli.cli_utils import get_client, out
from .client import DatasetClient, DatasetError, is_debug_mode


def handle_dataset_error(func):
    """装饰器：统一处理 DatasetError 异常"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except DatasetError as e:
            if is_debug_mode():
                traceback.print_exc()
            else:
                click.echo(e.get_user_message(), err=True)
            sys.exit(1)
        except Exception as e:
            if is_debug_mode():
                traceback.print_exc()
            else:
                click.echo(f"执行失败: {str(e)}", err=True)
                click.echo("提示: 使用 CLOUDROBO_DEBUG=1 查看详细错误信息", err=True)
            sys.exit(1)

    return wrapper


def _print_task_logs(client, task_id, log_type="system"):
    """打印任务日志：先通过list_log_files获取file_path，再获取日志内容"""
    is_system = (log_type == "system")
    default_file_name = "system-std-output.log" if is_system else "job-std-output.log"
    try:
        log_files = client.list_log_files(task_id, is_system=is_system)
        payload = log_files.get("payload", log_files)
        items = payload.get("list", []) if isinstance(payload, dict) else []
        if items:
            file_name = items[0].get("file_name", default_file_name)
            file_path = items[0].get("file_path", "")
        else:
            file_name = default_file_name
            file_path = ""
        result = client.get_task_log_tail(task_id, file_name, file_path=file_path)
        payload2 = result.get("payload", result)
        item = payload2.get("item", {}) if isinstance(payload2, dict) else {}
        content = item.get("content", "")
        if content:
            click.echo(f"\n--- {log_type} 日志 ---")
            click.echo(content)
        else:
            click.echo(f"\n{log_type} 日志为空")
    except Exception as e:
        click.echo(f"\n获取{log_type}日志失败: {e}", err=True)


@click.group(name="dataset")
def dataset():
    """数据处理命令组，包含 proc（处理任务）和 eval（评测任务）两个子命令组"""
    pass


# ---- proc 子命令组（数据处理任务） ----

@dataset.group("proc")
def proc_group():
    """数据处理任务"""
    pass


@proc_group.command("create-task")
@click.option("--name", required=True, help="任务名称")
@click.option("--algo-type", required=True, help="算法类型")
@click.option("--task-config", required=True, help="任务配置(JSON)，包含algo_id/algo_name/algo_entrance/image/cluster_type/output_type/output_path/output_name/dataset_configs/envs/worker_spec等全部字段")
@click.option("--workspace-id", default=None, help="工作空间ID，不提供则使用默认配置")
@click.option("--wait", "wait_for_result", is_flag=True, help="创建后等待任务完成")
@click.option("--timeout", type=int, default=1800, help="等待超时秒数(配合--wait使用)")
@click.option("--dry-run", is_flag=True)
@click.pass_context
@handle_dataset_error
def create_task(ctx, name, algo_type, task_config, workspace_id, wait_for_result, timeout, dry_run):
    """创建数据处理任务"""
    if dry_run:
        click.echo(f"[DRY-RUN] create_task(name={name},algo_type={algo_type},task_config={task_config})")
        return
    client = get_client(ctx, DatasetClient)
    try:
        req = json.loads(task_config)
    except json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON: {e}")
    req["name"] = name
    req["algo_type"] = algo_type
    if not (3 <= len(req.get("name", "")) <= 64):
        raise click.BadParameter("name 长度必须为 3-64 个字符")
    if not re.fullmatch(r'[\u4e00-\u9fa5a-zA-Z0-9_\-./]+', req.get("name", "")):
        raise click.BadParameter("name 只能包含中文、数字、字母、下划线（_）、连字符（-）、点（.）、斜线（/）")
    if "description" in req and len(req["description"]) > 512:
        raise click.BadParameter("description 长度不能超过 512 个字符")
    if not (2 <= len(req.get("output_name", "")) <= 64):
        raise click.BadParameter("output_name 长度必须为 2-64 个字符")
    output_name = req.get("output_name", "")
    if not re.fullmatch(r'[\u4e00-\u9fa5a-zA-Z0-9_\-./ ]+', output_name) or output_name.startswith(' ') or output_name.endswith(' '):
        raise click.BadParameter("output_name 只能包含中文、数字、字母、下划线（_）、连字符（-）、点（.）、斜线（/）、空格，且不能以空格开头或结尾")
    result = client.create_task(req, workspace_id=workspace_id)
    task_id = result.get("payload", result).get("id")
    click.echo(f"任务已创建: {task_id}", nl=False)
    status = result.get("payload", result).get("status", "UNKNOWN")
    click.echo(f", 状态: {status}")

    if wait_for_result and task_id:
        click.echo("等待任务完成...")
        last_status = status

        def on_status(new_status, detail):
            nonlocal last_status
            click.echo(f"  {last_status} → {new_status}")
            last_status = new_status

        result = client.wait_task(task_id, timeout=timeout, on_status=on_status)
        click.echo(f"最终状态: {last_status}")
        payload = result.get("payload", result)
        if last_status == "SUCCEEDED":
            click.echo(f"输出资产: {payload.get('target_asset_name', '')}")
            click.echo(f"输出路径: {payload.get('target_path', '')}")
            _print_task_logs(client, task_id, "system")
        elif last_status == "FAILED":
            if click.confirm("\n任务失败，是否查看日志？", default=True):
                _print_task_logs(client, task_id, "system")
                _print_task_logs(client, task_id, "job")
    else:
        out(result)


@proc_group.command("list-tasks")
@click.option("--status", default=None, help="状态过滤(RUNNING/SUCCEEDED/FAILED/PENDING)")
@click.option("--name", default=None, help="按名称模糊查询")
@click.option("--order-by", default=None, type=click.Choice(["start_at", "finish_at"]), help="排序指标")
@click.option("--order", default=None, type=click.Choice(["DESC", "ASC"]), help="排序方式")
@click.option("--offset", type=int, default=None, help="分页偏移量")
@click.option("--limit", type=int, default=None, help="每页数量(1-100)")
@click.option("--user-id", default=None, help="创建者ID过滤")
@click.option("--algo-name", default=None, help="算法名称过滤")
@click.option("--output-name", default=None, help="输出数据集名称过滤")
@click.option("--workspace-id", default=None, help="工作空间ID，不提供则使用默认配置")
@click.pass_context
@handle_dataset_error
def list_tasks(ctx, status, name, order_by, order, offset, limit, user_id, algo_name, output_name, workspace_id):
    """列出处理任务"""
    client = get_client(ctx, DatasetClient)
    params = {}
    if status:
        params["statuses"] = status
    if name:
        params["name"] = name
    if order_by:
        params["order_by"] = order_by
    if order:
        params["order"] = order
    if offset is not None:
        params["offset"] = offset
    if limit is not None:
        params["limit"] = limit
    if user_id:
        params["user_id"] = user_id
    if algo_name:
        params["algo_name"] = algo_name
    if output_name:
        params["output_name"] = output_name
    result = client.list_tasks(workspace_id=workspace_id, **params)
    out(result)


@proc_group.command("show-task")
@click.option("--task-id", required=True, help="任务ID")
@click.pass_context
@handle_dataset_error
def show_task(ctx, task_id):
    """查看任务详情"""
    client = get_client(ctx, DatasetClient)
    result = client.get_task_detail(task_id)
    out(result)


@proc_group.command("restart-task")
@click.option("--task-id", required=True, help="任务ID")
@click.pass_context
@handle_dataset_error
def restart_task(ctx, task_id):
    """重启任务"""
    client = get_client(ctx, DatasetClient)
    result = client.restart_task(task_id)
    out(result)


@proc_group.command("delete-task")
@click.option("--task-id", required=True, help="任务ID")
@click.pass_context
@handle_dataset_error
def delete_task(ctx, task_id):
    """删除数据处理任务"""
    client = get_client(ctx, DatasetClient)
    result = client.delete_tasks([task_id])
    click.echo("已删除")


@proc_group.command("update-task")
@click.option("--task-id", required=True, help="任务ID")
@click.option("--task-config", required=True, help="更新内容JSON")
@click.pass_context
@handle_dataset_error
def update_task(ctx, task_id, task_config):
    """修改任务"""
    client = get_client(ctx, DatasetClient)
    try:
        req = json.loads(task_config)
    except json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON: {e}")
    if "name" in req and not (3 <= len(req.get("name", "")) <= 64):
        raise click.BadParameter("name 长度必须为 3-64 个字符")
    if not re.fullmatch(r'[\u4e00-\u9fa5a-zA-Z0-9_\-./]+', req.get("name", "")):
        raise click.BadParameter("name 只能包含中文、数字、字母、下划线（_）、连字符（-）、点（.）、斜线（/）")
    if "description" in req and len(req["description"]) > 512:
        raise click.BadParameter("description 长度不能超过 512 个字符")
    result = client.update_task(task_id, req)
    out(result)


@proc_group.command("get-frames")
@click.option("--task-id", required=True, help="任务ID")
@click.option("--prefix", required=True, help="前缀，show-task接口返回的target_path（输出数据集）或者dataset_configs.obs_path（输入数据集）的值，再去除桶前缀，比如target_path: obs://bucket-0/cloudrobo/test/，则prefix传cloudrobo/test/")
@click.pass_context
@handle_dataset_error
def get_frames(ctx, task_id, prefix):
    """查看任务帧数据"""
    client = get_client(ctx, DatasetClient)
    result = client.get_task_frames(task_id, prefix=prefix)
    out(result)


@proc_group.command("get-log")
@click.option("--task-id", required=True, help="任务ID")
@click.option("--file-name", default=None, help="日志文件名(通过--is-system传true或者false获取)，不传的话默认为查用户日志")
@click.option("--file-path", default=None, help="日志文件路径(通过--is-system传true或者false获取，不传的话会用file-name拼接")
@click.option("--is-system", type=bool, default=None, help="True=系统日志, False=用户日志，仅列出日志文件，不传则返回日志内容")
@click.option("--all", "show_all", is_flag=True, help="返回全部日志，默认仅返回最新64KB")
@click.pass_context
@handle_dataset_error
def get_log(ctx, task_id, file_name, file_path, is_system, show_all):
    """获取任务日志"""
    client = get_client(ctx, DatasetClient)
    if is_system is not None:
        result = client.list_log_files(task_id, is_system=is_system)
        out(result)
        return
    if not file_name:
        file_name = "job-std-output.log"
    if show_all:
        result = client.get_task_log(task_id, file_name, file_path=file_path or "")
    else:
        result = client.get_task_log_tail(task_id, file_name, file_path=file_path or "")
    out(result)


@proc_group.command("get-preview")
@click.option("--task-id", required=True, help="任务ID")
@click.option("--file-name", required=True, help="文件名称，get-frames接口返回的prefix加上files，比如prefix是cloudrobo/，files是test.parquet，则file-name传cloudrobo/test.parquet")
@click.pass_context
@handle_dataset_error
def get_preview(ctx, task_id, file_name):
    """预览任务数据"""
    client = get_client(ctx, DatasetClient)
    result = client.get_task_preview(task_id, file_name)
    out(result)


@proc_group.command("wait-task")
@click.option("--task-id", required=True, help="任务ID")
@click.option("--timeout", type=int, default=1800, help="超时秒数")
@click.option("--interval", type=int, default=10, help="轮询间隔秒数")
@click.pass_context
@handle_dataset_error
def wait_task(ctx, task_id, timeout, interval):
    """等待任务到达终态，实时输出状态变更"""
    client = get_client(ctx, DatasetClient)
    last_status = [None]

    def on_status(new_status, detail):
        click.echo(f"  {last_status[0] or 'START'} → {new_status}")
        last_status[0] = new_status

    result = client.wait_task(task_id, timeout=timeout, interval=interval, on_status=on_status)
    status = result.get("payload", result).get("status", "UNKNOWN")
    click.echo(f"最终状态: {status}")
    payload = result.get("payload", result)
    if status == "SUCCEEDED":
        click.echo(f"输出资产: {payload.get('target_asset_name', '')}")
        click.echo(f"输出路径: {payload.get('target_path', '')}")
        _print_task_logs(client, task_id, "system")
    elif status == "FAILED":
        if click.confirm("\n任务失败，是否查看日志？", default=True):
            _print_task_logs(client, task_id, "system")
            _print_task_logs(client, task_id, "job")


@proc_group.command("download-log")
@click.option("--task-id", required=True, help="任务ID")
@click.option("--file-name", required=True, help="日志文件名(通过get-log --is-system获取)")
@click.option("--file-path", required=True, help="日志文件路径(通过get-log --is-system获取)")
@click.pass_context
@handle_dataset_error
def download_log(ctx, task_id, file_name, file_path):
    """下载任务日志文件"""
    client = get_client(ctx, DatasetClient)
    result = client.download_task_log(task_id, file_name, file_path)
    out(result)


@proc_group.command("get-resource-usage")
@click.option("--task-id", required=True, help="任务ID")
@click.option("--metric", required=True,
              type=click.Choice(["CPU_UTIL", "CPU_USED_CORE", "MEM_UTIL", "MEM_USED_MB",
                                 "NETWORK_TX_RATE", "NETWORK_RX_RATE",
                                 "DISK_READ_KB", "DISK_WRITE_KB"]),
              help="监控指标")
@click.option("--start", required=True, type=int, help="起始时间戳(秒)")
@click.option("--end", required=True, type=int, help="结束时间戳(秒)")
@click.option("--step", required=True, type=int, help="采样间隔秒数(10-3600)")
@click.option("--pod-name", required=True, help="容器名，show-task接口返回的pod_names的值")
@click.pass_context
@handle_dataset_error
def get_resource_usage(ctx, task_id, metric, start, end, step, pod_name):
    """获取任务资源监控数据(CPU/内存/网络/磁盘)"""
    _max_ts = 9999999999
    if not (0 <= start <= _max_ts):
        raise click.BadParameter(f"start 必须是秒单位的时间戳")
    if not (0 <= end <= _max_ts):
        raise click.BadParameter(f"end 必须是秒单位的时间戳")
    if start >= end:
        raise click.BadParameter("start 必须小于 end")
    client = get_client(ctx, DatasetClient)
    result = client.get_task_resource_usage(task_id, metric, start, end, step, pod_name)
    out(result)


# ---- eval 子命令组 ----

@dataset.group("eval")
def eval_group():
    """数据评测任务"""
    pass


def _print_eval_task_logs(client, task_id, log_type="system"):
    is_system = (log_type == "system")
    default_file_name = "system-std-output.log" if is_system else "job-std-output.log"
    try:
        log_files = client.list_eval_log_files(task_id, is_system=is_system)
        payload = log_files.get("payload", log_files)
        items = payload.get("list", []) if isinstance(payload, dict) else []
        if items:
            file_name = items[0].get("file_name", default_file_name)
            file_path = items[0].get("file_path", "")
        else:
            file_name = default_file_name
            file_path = ""
        result = client.get_eval_task_log(task_id, file_name, file_path=file_path)
        payload2 = result.get("payload", result)
        item = payload2.get("item", {}) if isinstance(payload2, dict) else {}
        content = item.get("content", "")
        if content:
            click.echo(f"\n--- {log_type} 日志 ---")
            click.echo(content)
        else:
            click.echo(f"\n{log_type} 日志为空")
    except Exception as e:
        click.echo(f"\n获取{log_type}日志失败: {e}", err=True)


@eval_group.command("create-task")
@click.option("--name", required=True, help="任务名称")
@click.option("--task-config", required=True, help="任务配置(JSON)，包含algo_id/algo_name/algo_entrance/dataset_type/dataset_id/dataset_name/dataset_path/image/robot_config/worker_spec/resource_pool_type等全部字段")
@click.option("--workspace-id", default=None, help="工作空间ID")
@click.option("--wait", "wait_for_result", is_flag=True, help="创建后等待任务完成")
@click.option("--timeout", type=int, default=1800, help="等待超时秒数")
@click.option("--dry-run", is_flag=True)
@click.pass_context
@handle_dataset_error
def eval_create_task(ctx, name, task_config, workspace_id, wait_for_result, timeout, dry_run):
    """创建数据评测任务"""
    if dry_run:
        click.echo(f"[DRY-RUN] create_task(name={name},task_config={task_config})")
        return
    client = get_client(ctx, DatasetClient)
    try:
        req = json.loads(task_config)
    except json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON: {e}")
    req["name"] = name
    if not (3 <= len(req.get("name", "")) <= 64):
        raise click.BadParameter("name 长度必须为 3-64 个字符")
    if not re.fullmatch(r'[\u4e00-\u9fa5a-zA-Z0-9_\-./]+', req.get("name", "")):
        raise click.BadParameter("name 只能包含中文、数字、字母、下划线（_）、连字符（-）、点（.）、斜线（/）")
    if "description" in req and len(req["description"]) > 512:
        raise click.BadParameter("description 长度不能超过 512 个字符")
    dataset_name = req.get("dataset_name", "")
    if not (2 <= len(dataset_name) <= 64):
        raise click.BadParameter("dataset_name 长度必须为 2-64 个字符")
    if not re.fullmatch(r'[\u4e00-\u9fa5a-zA-Z0-9_\-./ ]+', dataset_name) or dataset_name.startswith(' ') or dataset_name.endswith(' '):
        raise click.BadParameter("dataset_name 只能包含中文、数字、字母、下划线（_）、连字符（-）、点（.）、斜线（/）、空格，且不能以空格开头或结尾")
    result = client.create_eval_task(req, workspace_id=workspace_id)
    task_id = result.get("payload", result).get("id")
    click.echo(f"评测任务已创建: {task_id}", nl=False)
    status = result.get("payload", result).get("status", "UNKNOWN")
    click.echo(f", 状态: {status}")

    if wait_for_result and task_id:
        click.echo("等待任务完成...")
        last_status = status

        def on_status(new_status, detail):
            nonlocal last_status
            click.echo(f"  {last_status} → {new_status}")
            last_status = new_status

        result = client.wait_eval_task(task_id, timeout=timeout, on_status=on_status)
        click.echo(f"最终状态: {last_status}")
        payload = result.get("payload", result)
        if last_status == "SUCCEEDED":
            click.echo(f"评测报告路径: {payload.get('target_report_path', '')}")
            _print_eval_task_logs(client, task_id, "system")
        elif last_status == "FAILED":
            if click.confirm("\n任务失败，是否查看日志？", default=True):
                _print_eval_task_logs(client, task_id, "system")
                _print_eval_task_logs(client, task_id, "job")
    else:
        out(result)


@eval_group.command("list-tasks")
@click.option("--status", default=None, help="状态过滤(逗号分隔)")
@click.option("--workspace-id", default=None, help="工作空间ID")
@click.option("--name", default=None, help="按名称模糊查询")
@click.option("--algo-name", default=None, help="算法名称过滤")
@click.option("--order-by", default=None, type=click.Choice(["start_at", "finish_at"]), help="排序指标")
@click.option("--order", default=None, type=click.Choice(["DESC", "ASC"]), help="排序方式")
@click.option("--offset", type=int, default=None, help="分页偏移量")
@click.option("--limit", type=int, default=20, help="每页数量")
@click.option("--user-id", default=None, help="创建者ID过滤")
@click.option("--dataset-name", default=None, help="数据集名称过滤")
@click.pass_context
@handle_dataset_error
def eval_list_tasks(ctx, status, workspace_id, name, algo_name, order_by, order, offset, limit, user_id, dataset_name):
    """列出评测任务"""
    client = get_client(ctx, DatasetClient)
    params = {"limit": limit}
    if status:
        params["statuses"] = status
    if name:
        params["name"] = name
    if algo_name:
        params["algo_names"] = algo_name
    if order_by:
        params["order_by"] = order_by
    if order:
        params["order"] = order
    if offset is not None:
        params["offset"] = offset
    if user_id:
        params["user_id"] = user_id
    if dataset_name:
        params["dataset_name"] = dataset_name
    result = client.list_eval_tasks(workspace_id=workspace_id, **params)
    out(result)


@eval_group.command("show-task")
@click.option("--task-id", required=True, help="任务ID")
@click.pass_context
@handle_dataset_error
def eval_show_task(ctx, task_id):
    """查看评测任务详情"""
    client = get_client(ctx, DatasetClient)
    result = client.get_eval_task_detail(task_id)
    out(result)


@eval_group.command("update-task")
@click.option("--task-id", required=True, help="任务ID")
@click.option("--task-config", required=True, help="更新内容JSON")
@click.pass_context
@handle_dataset_error
def eval_update_task(ctx, task_id, task_config):
    """修改评测任务"""
    client = get_client(ctx, DatasetClient)
    try:
        req = json.loads(task_config)
    except json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON: {e}")
    if "name" in req and not (3 <= len(req.get("name", "")) <= 64):
        raise click.BadParameter("name 长度必须为 3-64 个字符")
    if not re.fullmatch(r'[\u4e00-\u9fa5a-zA-Z0-9_\-./]+', req.get("name", "")):
        raise click.BadParameter("name 只能包含中文、数字、字母、下划线（_）、连字符（-）、点（.）、斜线（/）")
    if "description" in req and len(req["description"]) > 512:
        raise click.BadParameter("description 长度不能超过 512 个字符")
    result = client.update_eval_task(task_id, req)
    out(result)


@eval_group.command("delete-task")
@click.option("--task-id", required=True, help="任务ID")
@click.pass_context
@handle_dataset_error
def eval_delete_task(ctx, task_id):
    """删除评测任务"""
    client = get_client(ctx, DatasetClient)
    result = client.delete_eval_task(task_id)
    click.echo("已删除")


@eval_group.command("get-log")
@click.option("--task-id", required=True, help="任务ID")
@click.option("--file-name", default=None, help="日志文件名(通过--is-system传true或者false获取)，不传的话默认为查用户日志")
@click.option("--file-path", default=None, help="日志文件路径(通过--is-system传true或者false获取)，不传的话会用file-name拼接")
@click.option("--is-system", type=bool, default=None, help="True=系统日志, False=用户日志，仅列出日志文件，不传则返回日志内容")
@click.option("--all", "show_all", is_flag=True, help="返回全部日志，默认仅返回最新64KB")
@click.pass_context
@handle_dataset_error
def eval_get_log(ctx, task_id, file_name, file_path, is_system, show_all):
    """获取评测任务日志"""
    client = get_client(ctx, DatasetClient)
    if is_system is not None:
        result = client.list_eval_log_files(task_id, is_system=is_system)
        out(result)
        return
    if not file_name:
        file_name = "job-std-output.log"
    if show_all:
        result = client.get_eval_task_log(task_id, file_name, file_path=file_path or "")
    else:
        result = client.get_eval_task_log_tail(task_id, file_name, file_path=file_path or "")
    out(result)


@eval_group.command("get-preview")
@click.option("--task-id", required=True, help="任务ID")
@click.option("--file-name", required=True, help="评测报告文件名，show-task接口返回的target_report_path，再去除桶前缀，比如target_report_path是obs://bucket-0/cloudrobo/test.pdf，则file-name传cloudrobo/test.pdf")
@click.option("--is-download", is_flag=True, help="下载链接(默认为预览链接)")
@click.pass_context
@handle_dataset_error
def eval_get_preview(ctx, task_id, file_name, is_download):
    """获取评测报告预览/下载链接"""
    client = get_client(ctx, DatasetClient)
    result = client.get_eval_task_preview(task_id, file_name=file_name, is_download=is_download)
    out(result)


@eval_group.command("wait-task")
@click.option("--task-id", required=True, help="任务ID")
@click.option("--timeout", type=int, default=1800, help="超时秒数")
@click.option("--interval", type=int, default=10, help="轮询间隔秒数")
@click.pass_context
@handle_dataset_error
def eval_wait_task(ctx, task_id, timeout, interval):
    """等待评测任务到达终态，实时输出状态变更"""
    client = get_client(ctx, DatasetClient)
    last_status = [None]

    def on_status(new_status, detail):
        click.echo(f"  {last_status[0] or 'START'} → {new_status}")
        last_status[0] = new_status

    result = client.wait_eval_task(task_id, timeout=timeout, interval=interval, on_status=on_status)
    status = result.get("payload", result).get("status", "UNKNOWN")
    click.echo(f"最终状态: {status}")
    if status == "FAILED":
        if click.confirm("\n任务失败，是否查看日志？", default=True):
            _print_eval_task_logs(client, task_id, "system")
            _print_eval_task_logs(client, task_id, "job")


@eval_group.command("download-log")
@click.option("--task-id", required=True, help="任务ID")
@click.option("--file-name", required=True, help="日志文件名(通过get-log --is-system获取)")
@click.option("--file-path", required=True, help="日志文件路径(通过get-log --is-system获取)")
@click.pass_context
@handle_dataset_error
def eval_download_log(ctx, task_id, file_name, file_path):
    """下载评测任务日志文件"""
    client = get_client(ctx, DatasetClient)
    result = client.download_eval_task_log(task_id, file_name, file_path)
    out(result)
