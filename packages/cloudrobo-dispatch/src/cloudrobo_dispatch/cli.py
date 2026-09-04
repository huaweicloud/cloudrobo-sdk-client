import json

import click
from cloudrobo_core.cli.cli_utils import get_client, out
from cloudrobo_core.sdk.exceptions import PathTraversalError, validate_safe_id

from .client import DispatchClient

# isort: off
# ===== VALIDATOR IMPORTS =====
from .validators.cli_callbacks import (
    _validate_constraints,
    _validate_content_match,
    _validate_end_time,
    _validate_infer_service_id,
    _validate_name,
    _validate_robot_id,
    _validate_session_id,
    _validate_sort_dir,
    _validate_sort_key,
    _validate_start_time,
    _validate_status,
    _validate_task,
    _validate_task_id,
)
# ===== END VALIDATOR IMPORTS =====
# isort: on




TASK_STATUSES = ["RUNNING", "COMPLETED", "FAILED", "CANCELLED"]


@click.group()
def dispatch():
    """智能体调度命令组"""





def _parse_required_json(ctx, raw, key):
    """解析必填 JSON 对象选项；缺失或非法抛出 click.BadParameter。"""
    if not raw:
        raise click.BadParameter(f"'{key}' is required and must be a JSON object")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON for '{key}': {e}")


# ===== 调度任务管理 (RoboDispatcherTaskManagement) =====


@dispatch.command("create-task")
@click.option("--session-id", required=False, default=None, callback=_validate_session_id, help='会话ID')
@click.option("--constraints-json", required=True, callback=_validate_constraints, help='任务约束')
@click.option("--name", required=True, callback=_validate_name, help='任务名称')
@click.option("--task", required=True, callback=_validate_task, help='任务描述')
@click.option("--dry-run", is_flag=True)
@click.pass_context
def create_task(ctx, session_id, constraints_json, name, task, dry_run):
    """创建任务(CreateDispatcherTask)"""
    validate_safe_id(session_id, "session_id")
    req = {
        "name": name,
        "task": task,
        "constraints": _parse_required_json(ctx, constraints_json, "constraints"),
    }
    if dry_run:
        click.echo(f"[DRY-RUN] create_dispatcher_task(session_id={session_id}, req={req})")
        return
    client = get_client(ctx, DispatchClient)
    result = client.create_dispatcher_task(session_id, req)
    out(result)


@dispatch.command("list-tasks")
@click.option("--session-id", required=False, default=None, callback=_validate_session_id, help='会话ID')
@click.option("--limit", type=click.IntRange(1, 100), help='每页数据条数')
@click.option("--offset", type=click.IntRange(0, 10000), help='分页页码偏移量')
@click.option("--sort-key", type=click.Choice(['create_at', 'created_at', 'update_at', 'updated_at'], case_sensitive=False), callback=_validate_sort_key, default='updated_at', help='排序字段，支持created_at, updated_at, create_at, update_at，默认值updated_at。')
@click.option("--sort-dir", type=click.Choice(['ASC', 'DESC'], case_sensitive=False), callback=_validate_sort_dir, default='DESC', help='结果排序方式。支持DESC(desc)，ASC(asc)，默认值DESC。')
@click.option("--status", type=click.Choice(['CANCELLED', 'COMPLETED', 'FAILED', 'RUNNING'], case_sensitive=False), callback=_validate_status, help='根据执行状态查询相关日志。')
@click.option("--robot-id", default=None, callback=_validate_robot_id, help='机器人id')
@click.option("--start-time", type=click.IntRange(0, 32503680000000), callback=_validate_start_time, help='按起止时间筛选，执行日志开始时间，UTC时间戳，单位毫秒')
@click.option("--end-time", type=click.IntRange(0, 32503680000000), callback=_validate_end_time, help='按起止时间筛选，执行日志结束时间，UTC时间戳，单位毫秒')
@click.option("--infer-service-id", default=None, callback=_validate_infer_service_id, help='推理服务id')
@click.option("--content-match", default=None, callback=_validate_content_match, help='技能prompt或服务名称模糊搜索内容')
@click.pass_context
def list_tasks(ctx, session_id, limit, offset, sort_key, sort_dir, status,
               robot_id, start_time, end_time, infer_service_id, content_match):
    """列出会话任务(ListDispatcherTasks)"""
    validate_safe_id(session_id, "session_id")
    client = get_client(ctx, DispatchClient)
    params = {}
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    if sort_key:
        params["sort_key"] = sort_key
    if sort_dir:
        params["sort_dir"] = sort_dir
    if status:
        params["status"] = status
    if robot_id:
        params["robot_id"] = robot_id
    if start_time is not None:
        params["start_time"] = start_time
    if end_time is not None:
        params["end_time"] = end_time
    if infer_service_id:
        params["infer_service_id"] = infer_service_id
    if content_match:
        params["content_match"] = content_match
    result = client.list_dispatcher_tasks(session_id, **params)
    out(result)


@dispatch.command("show-task")
@click.option("--session-id", required=False, default=None, callback=_validate_session_id, help='会话ID')
@click.option("--task-id", required=True, callback=_validate_task_id, help='任务唯一标识ID')
@click.pass_context
def show_task(ctx, session_id, task_id):
    """查询任务详情(ShowDispatcherTask)"""
    validate_safe_id(session_id, "session_id")
    validate_safe_id(task_id, "task_id")
    client = get_client(ctx, DispatchClient)
    result = client.show_dispatcher_task(session_id, task_id)
    out(result)


@dispatch.command("cancel-task")
@click.option("--session-id", required=False, default=None, callback=_validate_session_id, help='会话ID')
@click.option("--task-id", required=True, callback=_validate_task_id, help='任务唯一标识ID')
@click.option("--dry-run", is_flag=True)
@click.pass_context
def cancel_task(ctx, session_id, task_id, dry_run):
    """取消任务(CancelDispatcherTask)"""
    validate_safe_id(session_id, "session_id")
    validate_safe_id(task_id, "task_id")
    if dry_run:
        click.echo(
            f"[DRY-RUN] cancel_dispatcher_task(session={session_id}, task={task_id})"
        )
        return
    client = get_client(ctx, DispatchClient)
    client.cancel_dispatcher_task(session_id, task_id)
    out(f"canceled: (session={session_id}, task={task_id})")


@dispatch.command("show-task-result")
@click.option("--session-id", required=False, default=None, callback=_validate_session_id, help='会话ID')
@click.option("--task-id", required=True, callback=_validate_task_id, help='任务唯一标识ID')
@click.option("--inverse", is_flag=True, default=False, help='倒置，如果为true，则倒序查询，此时offset为0代表最后一个字节')
@click.option("--limit", type=click.IntRange(100, 10000), default=200, help='单次请求日志字节数限制，默认200')
@click.option("--offset", type=click.IntRange(0, 2147483647), default=0, help='单次请求日志字节偏移量，默认0')
@click.pass_context
def show_task_result(ctx, session_id, task_id, inverse, limit, offset):
    """获取任务结果(ShowDispatcherTaskResult)"""
    validate_safe_id(session_id, "session_id")
    validate_safe_id(task_id, "task_id")
    client = get_client(ctx, DispatchClient)
    params = {}
    if inverse:
        params["inverse"] = True
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    result = client.show_dispatcher_task_result(session_id, task_id, **params)
    out(result)


@dispatch.command("wait-task")
@click.option("--session-id", required=False, default=None, callback=_validate_session_id, help='会话ID')
@click.option("--task-id", required=True, callback=_validate_task_id, help='任务唯一标识ID')
@click.option("--timeout", type=click.IntRange(1, 3600), default=600, help="等待任务超时时间(秒)，默认600")
@click.pass_context
def wait_task(ctx, session_id, task_id, timeout):
    """等待任务完成(每5秒查询直到状态非RUNNING或超时)"""
    validate_safe_id(session_id, "session_id")
    validate_safe_id(task_id, "task_id")
    client = get_client(ctx, DispatchClient)
    try:
        result = client.wait_dispatcher_task(session_id, task_id, timeout)
    except (TimeoutError, PathTraversalError) as e:
        click.echo(f"[ERROR] {e}", err=True)
        ctx.exit(1)
    out(result)
