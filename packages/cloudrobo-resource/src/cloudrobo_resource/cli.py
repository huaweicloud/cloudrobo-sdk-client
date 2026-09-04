# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

import sys
import traceback
from functools import wraps

import click

from cloudrobo_core.cli.cli_utils import get_client, out
from cloudrobo_core.sdk.exceptions import CloudRoboError, ResourceNotFoundError
from .client import ResourceClient, ResourceError, is_debug_mode


def handle_error(func):
    """装饰器：统一处理资源管理命令的异常"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ResourceError as e:
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


@click.group()
def resource():
    """资源管理命令组"""
    pass


@resource.command("list-quotas")
@click.option("--workspace-id", default=None, help="工作空间ID")
@click.option("--resource-id", default=None, help="资源ID")
@click.option("--resource-type", type=click.Choice(["CCE", "MODELARTS"]), default=None, help="资源类型")
@click.option("--resource-sub-type", type=click.Choice(["CPU", "GPU", "STANDARD", "LITE"]), default=None, help="资源子类型")
@click.option("--pool-type", type=click.Choice(["DEDICATED", "SHARED"]), default=None, help="资源池类型")
@click.option("--limit", type=click.IntRange(1, 50), default=None, help="每页数量(1-50)")
@click.option("--offset", type=click.IntRange(0), default=None, help="偏移量(>=0)")
@click.option("--order", type=click.Choice(["ASC", "DESC"]), default=None, help="排序方式")
@click.pass_context
@handle_error
def list_quotas_cmd(ctx, workspace_id, resource_id, resource_type, resource_sub_type, pool_type, limit, offset, order):
    """查询配额列表"""
    client = get_client(ctx, ResourceClient)
    params = {}
    if workspace_id is not None:
        params["workspace_id"] = workspace_id
    if resource_id is not None:
        params["resource_id"] = resource_id
    if resource_type is not None:
        params["resource_type"] = resource_type
    if resource_sub_type is not None:
        params["resource_sub_type"] = resource_sub_type
    if pool_type is not None:
        params["pool_type"] = pool_type
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    if order is not None:
        params["order"] = order
    result = client.list_quotas(**params)
    out(result)


@resource.command("list-pools")
@click.option("--resource-type", type=click.Choice(["CCE", "MODELARTS"]), default=None, help="资源类型")
@click.option("--resource-sub-type", type=click.Choice(["CPU", "GPU", "STANDARD", "LITE"]), default=None, help="资源子类型")
@click.option("--pool-type", type=click.Choice(["DEDICATED", "SHARED"]), default=None, help="资源池类型")
@click.option("--usages", default=None, help="用途列表(逗号分隔)")
@click.option("--limit", type=click.IntRange(1, 50), default=None, help="每页数量(1-50)")
@click.option("--offset", type=click.IntRange(0), default=None, help="偏移量(>=0)")
@click.option("--order", type=click.Choice(["ASC", "DESC"]), default=None, help="排序方式")
@click.pass_context
@handle_error
def list_pools_cmd(ctx, resource_type, resource_sub_type, pool_type, usages, limit, offset, order):
    """查询资源池列表"""
    client = get_client(ctx, ResourceClient)
    params = {}
    if resource_type is not None:
        params["resource_type"] = resource_type
    if resource_sub_type is not None:
        params["resource_sub_type"] = resource_sub_type
    if pool_type is not None:
        params["pool_type"] = pool_type
    if usages is not None:
        params["usages"] = [u.strip() for u in usages.split(",") if u.strip()]
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    if order is not None:
        params["order"] = order
    result = client.list_pools(**params)
    out(result)


@resource.command("show-pool")
@click.option("--pool-id", required=True, help="资源池ID")
@click.pass_context
@handle_error
def show_pool_cmd(ctx, pool_id):
    """查询资源池详情"""
    client = get_client(ctx, ResourceClient)
    try:
        result = client.show_pool(pool_id)
    except ResourceNotFoundError:
        click.echo(f"资源池 {pool_id} 不存在", err=True)
        sys.exit(1)
    out(result)
