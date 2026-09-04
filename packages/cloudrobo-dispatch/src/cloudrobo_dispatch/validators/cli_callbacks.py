"""Generated validator callbacks for the dispatch CLI.

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

def _validate_constraints(ctx, param, value):
    if value is None:
        return value
    import json as _json

    try:
        data = _json.loads(value)
    except _json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON for 'constraints': {e}")
    errs = _validator.validate_field(
        _rules.CREATEDISPATCHERTASKREQUESTBODY_RULES["fields"]["constraints"], data, 'constraints')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_content_match(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.QUERY_PARAM_RULES["content_match"], value, 'content_match')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_end_time(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.QUERY_PARAM_RULES["end_time"], value, 'end_time')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_infer_service_id(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.QUERY_PARAM_RULES["infer_service_id"], value, 'infer_service_id')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_name(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.CREATEDISPATCHERTASKREQUESTBODY_RULES["fields"]["name"], value, 'name')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_robot_id(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.QUERY_PARAM_RULES["robot_id"], value, 'robot_id')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_session_id(ctx, param, value):
    if value is None:
        return current_workspace().get("workspace_id")
    errs = _validator.validate_field(
        _rules.PATH_PARAM_RULES["session_id"], value, 'session_id')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_sort_dir(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.QUERY_PARAM_RULES["sort_dir"], value, 'sort_dir')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_sort_key(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.QUERY_PARAM_RULES["sort_key"], value, 'sort_key')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_start_time(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.QUERY_PARAM_RULES["start_time"], value, 'start_time')
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
def _validate_task(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.CREATEDISPATCHERTASKREQUESTBODY_RULES["fields"]["task"], value, 'task')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_task_id(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.PATH_PARAM_RULES["task_id"], value, 'task_id')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
