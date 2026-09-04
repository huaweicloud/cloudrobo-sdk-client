import json
from datetime import datetime
from pathlib import Path

import click
from cloudrobo_core.cli.cli_utils import get_client, out

from .client import RobotClient

# isort: off
# ===== VALIDATOR IMPORTS =====
from .validators.cli_callbacks import (
    _validate_description,
    _validate_limit,
    _validate_manufacturer,
    _validate_name,
    _validate_offset,
    _validate_password,
    _validate_robot_id,
    _validate_robot_model,
    _validate_sort,
    _validate_status,
    _validate_type,
    _validate_user_id,
    _validate_user_name,
    _validate_workspace_id,
)
# ===== END VALIDATOR IMPORTS =====
# isort: on




ROBOT_TYPE_CHOICES = ["HUMANOID", "QUADRUPED", "ARM", "OPERATION", "WHEELED", "OTHER"]
ROBOT_TYPE_HELP = "HUMANOID-人形/QUADRUPED-四足/ARM-机械臂/OPERATION-复合/WHEELED-轮式/OTHER-其他"


def _robot_type_choice():
    return click.Choice(ROBOT_TYPE_CHOICES, case_sensitive=False)


@click.group()
def robot():
    """机器人管理命令组"""





@robot.command("create")
@click.option("--description", default=None, callback=_validate_description, help='描述')
@click.option("--manufacturer", required=True, callback=_validate_manufacturer, help='厂家')
@click.option("--name", required=True, callback=_validate_name, help='名称')
@click.option("--robot-model", required=True, callback=_validate_robot_model, help='型号')
@click.option("--type", type=click.Choice(['ARM', 'HUMANOID', 'OPERATION', 'OTHER', 'QUADRUPED', 'WHEELED'], case_sensitive=False), callback=_validate_type, required=True, help='机器人类型：HUMANOID-人形，QUADRUPED-四足，ARM-机械臂，OPERATION-复合，WHEELED-轮式，OTHER-其他')
@click.option("--workspace-id", required=False, default=None, callback=_validate_workspace_id, help='工作空间ID')
@click.option("--dry-run", is_flag=True)
@click.pass_context
def create_robot(ctx, description, manufacturer, name, robot_model, type, workspace_id, dry_run):
    """注册机器人"""
    client = get_client(ctx, RobotClient)
    req = {
        "name": name,
        "type": type,
        "manufacturer": manufacturer,
        "robot_model": robot_model,
        "workspace_id": workspace_id,
    }
    if description is not None:
        req["description"] = description
    if dry_run:
        click.echo(f"[DRY-RUN] create_robot({json.dumps(req, ensure_ascii=False)})")
        return
    result = client.create_robot(req)
    out(result)


@robot.command("list")
@click.option("--limit", type=click.IntRange(1, 100), callback=_validate_limit, help='分页查询单页数据条数')
@click.option("--offset", type=click.IntRange(0, 1000), callback=_validate_offset, help='分页查询偏移量')
@click.option("--sort", default=None, callback=_validate_sort, help='排序规则，格式：字段:排序方式，例：created_at:desc')
@click.option("--name", default=None, callback=_validate_name, help='机器人名称模糊筛选')
@click.option("--status", default=None, callback=_validate_status, help='机器人状态精准筛选,支持多选')
@click.option("--manufacturer", default=None, callback=_validate_manufacturer, help='机器人厂家筛选')
@click.option("--robot-model", default=None, callback=_validate_robot_model, help='机器人型号筛选')
@click.option("--workspace-id", required=False, default=None, callback=_validate_workspace_id, help='工作空间唯一标识ID')
@click.option("--type", "robot_type", default=None, callback=_validate_type, help='机器人类型筛选')
@click.option("--user-id", default=None, callback=_validate_user_id, help='用户id筛选')
@click.option("--user-name", default=None, callback=_validate_user_name, help='用户名筛选')
@click.pass_context
def list_robots(ctx, limit, offset, sort, name, status, manufacturer, robot_model,
                workspace_id, robot_type, user_id, user_name):
    """查询机器人列表"""
    client = get_client(ctx, RobotClient)
    params = {}
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    if sort is not None:
        params["sort"] = sort
    if name is not None:
        params["name"] = name
    if status is not None:
        params["status"] = status
    if manufacturer is not None:
        params["manufacturer"] = manufacturer
    if robot_model is not None:
        params["robot_model"] = robot_model
    if workspace_id is not None:
        params["workspace_id"] = workspace_id
    if robot_type is not None:
        params["type"] = robot_type
    if user_id is not None:
        params["user_id"] = user_id
    if user_name is not None:
        params["user_name"] = user_name
    result = client.list_robots(**params)
    out(result)


@robot.command("show")
@click.option("--robot-id", required=True, callback=_validate_robot_id, help='机器人唯一标识ID')
@click.pass_context
def show_robot(ctx, robot_id):
    """查询机器人详情"""
    client = get_client(ctx, RobotClient)
    result = client.show_robot(robot_id)
    out(result)


@robot.command("update")
@click.option("--robot-id", required=True, callback=_validate_robot_id, help='机器人唯一标识ID')
@click.option("--description", default=None, callback=_validate_description, help='描述')
@click.option("--name", default=None, callback=_validate_name, help='机器人名称')
@click.option("--workspace-id", required=False, default=None, callback=_validate_workspace_id, help='当前工作空间ID，只允许上传该机器人对应的工作空间ID')
@click.option("--dry-run", is_flag=True)
@click.pass_context
def update_robot(ctx, robot_id, name, description, workspace_id, dry_run):
    """更新机器人信息"""
    client = get_client(ctx, RobotClient)
    req = {}
    if name is not None:
        req["name"] = name
    if description is not None:
        req["description"] = description
    if workspace_id is not None:
        req["workspace_id"] = workspace_id
    if dry_run:
        click.echo(f"[DRY-RUN] update_robot(robot_id={robot_id}, req={json.dumps(req, ensure_ascii=False)})")
        return
    result = client.update_robot(robot_id, req)
    out(result)


@robot.command("delete")
@click.option("--robot-id", required=True, callback=_validate_robot_id, help='机器人唯一标识ID')
@click.option("--dry-run", is_flag=True)
@click.pass_context
def delete_robot(ctx, robot_id, dry_run):
    """删除机器人"""
    client = get_client(ctx, RobotClient)
    if dry_run:
        click.echo(f"[DRY-RUN] delete_robot(robot_id={robot_id})")
        return
    client.delete_robot(robot_id)
    out(f"deleted: robot_id={robot_id}")


@robot.command("export-certificate")
@click.option("--robot-id", required=True, callback=_validate_robot_id, help='机器人唯一标识ID')
@click.option("--password", default=None, callback=_validate_password, help='机器人证书加密密码')
@click.option("--output", required=True, help="机器人证书导出目录")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def export_certificate(ctx, robot_id, password, output, dry_run):
    """导出机器人证书"""
    client = get_client(ctx, RobotClient)
    req = {}
    if password is not None:
        req["password"] = password
    if dry_run:
        if "password" in req:
            req["password"] = "******"
        click.echo(f"[DRY-RUN] export_robot_certificate(robot_id={robot_id}, req={json.dumps(req, ensure_ascii=False)})")
        return
    output_dir = Path(output)
    if not output_dir.is_dir():
        raise click.ClickException(f"导出目录不存在或导出参数不是目录: {output}")
    robot_info = client.show_robot(robot_id)
    robot_name = robot_info.get("name", "unknown")
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"cert_config_{robot_name}_{timestamp}.zip"
    filepath = output_dir / filename
    if filepath.exists():
        raise click.ClickException(f"文件已存在: {filepath}，导出已取消")
    result = client.export_robot_certificate(robot_id, req)
    if result is not None:
        with open(filepath, "wb") as f:
            f.write(result)
        click.echo(f"certificate written to {filepath}")
    else:
        out(result)


@robot.command("show-sdk")
@click.pass_context
def show_sdk(ctx):
    """查询机器人最新 sdk 包信息"""
    client = get_client(ctx, RobotClient)
    result = client.show_sdk()
    out(result)
