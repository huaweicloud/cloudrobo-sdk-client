"""Generated validator callbacks for the robot CLI.

本文件由 cloudrobo-client/scripts/gen_schemas.py 从根 pilot-manager.yaml
自动生成（存在根时以根为准，各包 robo-operations.yaml 是其分发视图）。
请勿手动修改；修改请改根 yaml 后重新生成。
"""

# isort: off
import click
from cloudrobo_workspace.config import load_workspace as current_workspace

from . import rules as _rules
from .validator import _validator
# isort: on

def _validate_description(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.CREATEROBOTREQUESTBODY_RULES["fields"]["description"], value, 'description')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_limit(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.QUERY_PARAM_RULES["limit"], value, 'limit')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_manufacturer(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.CREATEROBOTREQUESTBODY_RULES["fields"]["manufacturer"], value, 'manufacturer')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_name(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.CREATEROBOTREQUESTBODY_RULES["fields"]["name"], value, 'name')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_offset(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.QUERY_PARAM_RULES["offset"], value, 'offset')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_password(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.EXPORTROBOTCERTIFICATEREQUESTBODY_RULES["fields"]["password"], value, 'password')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_robot_id(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.PATH_PARAM_RULES["robot_id"], value, 'robot_id')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_robot_model(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.CREATEROBOTREQUESTBODY_RULES["fields"]["robot_model"], value, 'robot_model')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_sort(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.QUERY_PARAM_RULES["sort"], value, 'sort')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_status(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.QUERY_PARAM_RULES["status"], value, 'status')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_type(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.CREATEROBOTREQUESTBODY_RULES["fields"]["type"], value, 'type')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_user_id(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.QUERY_PARAM_RULES["user_id"], value, 'user_id')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_user_name(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.QUERY_PARAM_RULES["user_name"], value, 'user_name')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_workspace_id(ctx, param, value):
    if value is None:
        return current_workspace().get("workspace_id")
    errs = _validator.validate_field(
        _rules.CREATEROBOTREQUESTBODY_RULES["fields"]["workspace_id"], value, 'workspace_id')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
