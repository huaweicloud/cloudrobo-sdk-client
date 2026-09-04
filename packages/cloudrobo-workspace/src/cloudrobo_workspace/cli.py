# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

import json
import sys
import traceback
from functools import wraps

import click

from cloudrobo_core.cli.cli_utils import get_client, out
from cloudrobo_core.sdk.exceptions import (
    CloudRoboError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from .client import WorkspaceClient, WorkspaceError, is_debug_mode
from .config import load_workspace, save_workspace


def handle_error(func):
    """装饰器：统一处理工作空间命令的异常"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except WorkspaceError as e:
            if is_debug_mode():
                traceback.print_exc()
            else:
                click.echo(e.get_user_message(), err=True)
            sys.exit(1)
        except CloudRoboError as e:
            if is_debug_mode():
                traceback.print_exc()
            else:
                click.echo(f"错误: {e}", err=True)
            sys.exit(1)
        except click.exceptions.ClickException:
            raise
        except Exception as e:
            if is_debug_mode():
                traceback.print_exc()
            else:
                click.echo(f"执行失败: {e}", err=True)
                click.echo("提示: 设置 CLOUDROBO_DEBUG=1 查看详细错误信息", err=True)
            sys.exit(1)

    return wrapper


def _parse_json(value, param_name):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as e:
            raise click.BadParameter(f"{param_name} 不是合法的 JSON: {e}")
    return value


@click.group()
def workspace():
    """工作空间命令组"""
    pass


@workspace.command("create")
@click.option("--name", required=True, help="工作空间名称")
@click.option("--description", default=None, help="工作空间描述")
@click.option("--default-obs-path", required=True, help="默认OBS路径")
@click.option("--tags", default=None, help="标签列表(逗号分隔)")
@click.option("--member-list", default=None, help="成员列表(JSON字符串)")
@click.option("--dry-run", is_flag=True)
@click.pass_context
@handle_error
def create(ctx, name, description, default_obs_path, tags, member_list, dry_run):
    """创建工作空间"""
    if dry_run:
        click.echo(f"[DRY-RUN] create_workspace(name={name}, default_obs_path={default_obs_path})")
        return
    client = get_client(ctx, WorkspaceClient)
    req = {"name": name, "default_obs_path": default_obs_path}
    if description is not None:
        req["description"] = description
    if tags is not None:
        req["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    if member_list is not None:
        req["member_list"] = _parse_json(member_list, "--member-list")
    try:
        result = client.create_workspace(req)
    except ResourceConflictError as e:
        click.echo(f"创建工作空间失败: 名称 '{name}' 已存在", err=True)
        if str(e):
            click.echo(f"  服务端返回: {e}", err=True)
        sys.exit(1)
    ws_id = result.get("workspace_id", "") if isinstance(result, dict) else ""
    if ws_id:
        click.echo(f"已创建工作空间: {ws_id}")
    else:
        click.echo(f"已创建工作空间: {name}")


@workspace.command("list")
@click.option("--limit", type=click.IntRange(1), default=None, help="每页返回数量(>=1)")
@click.option("--offset", type=click.IntRange(0), default=None, help="偏移量(>=0)")
@click.pass_context
@handle_error
def list_ws(ctx, limit, offset):
    """列出工作空间"""
    client = get_client(ctx, WorkspaceClient)
    params = {}
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    result = client.list_workspaces(**params)
    out(result)


@workspace.command("show")
@click.option("--workspace-id", required=True, help="工作空间ID")
@click.pass_context
@handle_error
def show(ctx, workspace_id):
    """查看工作空间详情"""
    client = get_client(ctx, WorkspaceClient)
    try:
        result = client.show_workspace(workspace_id)
    except ResourceNotFoundError:
        click.echo(f"工作空间 {workspace_id} 不存在", err=True)
        sys.exit(1)
    out(result)


@workspace.command("update")
@click.option("--workspace-id", required=True, help="工作空间ID")
@click.option("--name", default=None, help="工作空间名称")
@click.option("--description", default=None, help="工作空间描述")
@click.option("--tags", default=None, help="标签列表(逗号分隔)")
@click.option("--owner-id", default=None, help="责任人用户ID")
@click.option("--default-obs-path", default=None, help="默认OBS路径")
@click.option("--bind-obs-policy", is_flag=True, help="仅绑定OBS桶策略，不更新其他字段")
@click.option("--dry-run", is_flag=True)
@click.pass_context
@handle_error
def update(ctx, workspace_id, name, description, tags, owner_id, default_obs_path, bind_obs_policy, dry_run):
    """更新工作空间"""
    if dry_run:
        click.echo(f"[DRY-RUN] update_workspace(id={workspace_id})")
        return
    client = get_client(ctx, WorkspaceClient)
    if bind_obs_policy:
        req = {"bind_obs_policy": True}
    else:
        req = {}
        if name is not None:
            req["name"] = name
        if description is not None:
            req["description"] = description
        if tags is not None:
            req["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
        if owner_id is not None:
            req["owner_id"] = owner_id
        if default_obs_path is not None:
            req["default_obs_path"] = default_obs_path
        if not req:
            raise click.UsageError("未提供任何更新字段，请至少指定一个要更新的参数或使用 --bind-obs-policy")
    try:
        result = client.update_workspace(workspace_id, req)
    except ResourceNotFoundError:
        click.echo(f"工作空间 {workspace_id} 不存在，无法更新", err=True)
        sys.exit(1)
    except ResourceConflictError as e:
        click.echo(f"更新工作空间失败: 名称 '{name}' 已被其他工作空间使用", err=True)
        if str(e):
            click.echo(f"  服务端返回: {e}", err=True)
        sys.exit(1)
    click.echo(f"已更新工作空间: {workspace_id}")


@workspace.command("delete")
@click.option("--workspace-id", required=True, help="工作空间ID")
@click.option("--dry-run", is_flag=True)
@click.pass_context
@handle_error
def delete(ctx, workspace_id, dry_run):
    """删除工作空间"""
    if dry_run:
        click.echo(f"[DRY-RUN] delete_workspace(id={workspace_id})")
        return
    client = get_client(ctx, WorkspaceClient)
    try:
        result = client.delete_workspace(workspace_id)
    except ResourceNotFoundError:
        click.echo(f"工作空间 {workspace_id} 不存在，无法删除", err=True)
        sys.exit(1)
    click.echo(f"已删除工作空间: {workspace_id}")


@workspace.command("list-members")
@click.option("--workspace-id", required=True, help="工作空间ID")
@click.pass_context
@handle_error
def list_members(ctx, workspace_id):
    """列出工作空间成员"""
    client = get_client(ctx, WorkspaceClient)
    try:
        result = client.list_workspace_members(workspace_id)
    except ResourceNotFoundError:
        click.echo(f"工作空间 {workspace_id} 不存在", err=True)
        sys.exit(1)
    out(result)


@workspace.command("add-members")
@click.option("--workspace-id", required=True, help="工作空间ID")
@click.option("--member-list", required=True, help="成员列表(JSON字符串)")
@click.pass_context
@handle_error
def add_members(ctx, workspace_id, member_list):
    """添加工作空间成员"""
    client = get_client(ctx, WorkspaceClient)
    members = _parse_json(member_list, "--member-list")
    try:
        result = client.add_workspace_members(workspace_id, {"member_list": members})
    except ResourceNotFoundError:
        click.echo(f"工作空间 {workspace_id} 不存在，无法添加成员", err=True)
        sys.exit(1)
    except ResourceConflictError as e:
        click.echo(f"添加成员失败: 部分成员可能已存在", err=True)
        if str(e):
            click.echo(f"  服务端返回: {e}", err=True)
        sys.exit(1)
    click.echo(f"已添加 {len(members)} 个成员")


@workspace.command("update-member")
@click.option("--workspace-id", required=True, help="工作空间ID")
@click.option("--user-id", required=True, help="用户ID")
@click.option("--role-ids", required=True, help="角色ID列表(逗号分隔)")
@click.pass_context
@handle_error
def update_member(ctx, workspace_id, user_id, role_ids):
    """更新工作空间成员角色"""
    client = get_client(ctx, WorkspaceClient)
    req = {"user_id": user_id, "role_ids": [r.strip() for r in role_ids.split(",") if r.strip()]}
    try:
        result = client.update_workspace_member(workspace_id, req)
    except ResourceNotFoundError:
        click.echo(f"工作空间 {workspace_id} 或成员 {user_id} 不存在", err=True)
        sys.exit(1)
    click.echo(f"已更新成员 {user_id} 的角色")


@workspace.command("delete-members")
@click.option("--workspace-id", required=True, help="工作空间ID")
@click.option("--user-ids", required=True, help="用户ID列表(逗号分隔)")
@click.pass_context
@handle_error
def delete_members(ctx, workspace_id, user_ids):
    """删除工作空间成员"""
    client = get_client(ctx, WorkspaceClient)
    ids = [x.strip() for x in user_ids.split(",") if x.strip()]
    try:
        result = client.delete_workspace_members(workspace_id, ids)
    except ResourceNotFoundError:
        click.echo(f"工作空间 {workspace_id} 不存在，无法删除成员", err=True)
        sys.exit(1)
    click.echo(f"已删除 {len(ids)} 个成员")


@workspace.command("overview")
@click.pass_context
@handle_error
def overview(ctx):
    """查看工作空间概览统计"""
    client = get_client(ctx, WorkspaceClient)
    result = client.get_workspace_overview()
    out(result)


@workspace.command("use")
@click.option("--workspace-id", required=True, help="工作空间ID")
@click.pass_context
@handle_error
def use(ctx, workspace_id):
    """使用指定工作空间，验证有效性并保存工作空间信息"""
    client = get_client(ctx, WorkspaceClient)
    try:
        result = client.show_workspace(workspace_id)
    except ResourceNotFoundError:
        click.echo(f"工作空间 {workspace_id} 不存在", err=True)
        sys.exit(1)
    ws = result.get("workspace", result)
    name = ws.get("name", "")
    asset_catalog_id = ws.get("asset_catalog_id", "")
    default_obs_path = ws.get("default_obs_path", "")

    save_workspace({
        "workspace_id": workspace_id,
        "name": name,
        "asset_catalog_id": asset_catalog_id,
        "default_obs_path": default_obs_path,
    })

    click.echo(f"已切换到工作空间: {name} ({workspace_id})")
    click.echo(f"  asset_catalog_id: {asset_catalog_id}")
    click.echo(f"  default_obs_path: {default_obs_path}")


@workspace.command("current")
@click.pass_context
def current(ctx):
    """显示当前工作空间配置"""
    ws = load_workspace()
    if ws:
        click.echo(json.dumps(ws, ensure_ascii=False, indent=2))
    else:
        click.echo("未配置工作空间")
