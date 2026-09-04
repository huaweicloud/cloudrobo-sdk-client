"""Generated validator callbacks for the infer CLI.

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

def _validate_cmd(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.CREATEINFERENCESERVICEREQUESTBODY_RULES["fields"]["cmd"], value, 'cmd')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_deploy_timeout_minutes(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.CREATEINFERENCESERVICEREQUESTBODY_RULES["fields"]["deploy_timeout_minutes"], value, 'deploy_timeout_minutes')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_description(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.CREATEINFERENCESERVICEREQUESTBODY_RULES["fields"]["description"], value, 'description')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_end_time(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.LISTINFERENCESERVICELOGSREQUESTBODY_RULES["fields"]["end_time"], value, 'end_time')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_envs(ctx, param, value):
    if value is None:
        return value
    import json as _json

    try:
        data = _json.loads(value)
    except _json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON for 'envs': {e}")
    errs = _validator.validate_field(
        _rules.CREATEINFERENCESERVICEREQUESTBODY_RULES["fields"]["envs"], data, 'envs')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_files(ctx, param, value):
    if value is None:
        return value
    import json as _json

    try:
        data = _json.loads(value)
    except _json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON for 'files': {e}")
    errs = _validator.validate_field(
        _rules.CREATEINFERENCESERVICEREQUESTBODY_RULES["fields"]["files"], data, 'files')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_flavor(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.CREATEINFERENCESERVICEREQUESTBODY_RULES["fields"]["flavor"], value, 'flavor')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_image_swr_url(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.CREATEINFERENCESERVICEREQUESTBODY_RULES["fields"]["image_swr_url"], value, 'image_swr_url')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_keywords(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.LISTINFERENCESERVICELOGSREQUESTBODY_RULES["fields"]["keywords"], value, 'keywords')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_limit(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.LISTINFERENCESERVICELOGSREQUESTBODY_RULES["fields"]["limit"], value, 'limit')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_line_num(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.LISTINFERENCESERVICELOGSREQUESTBODY_RULES["fields"]["line_num"], value, 'line_num')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_liveness_health(ctx, param, value):
    if value is None:
        return value
    import json as _json

    try:
        data = _json.loads(value)
    except _json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON for 'liveness_health': {e}")
    errs = _validator.validate_field(
        _rules.CREATEINFERENCESERVICEREQUESTBODY_RULES["fields"]["liveness_health"], data, 'liveness_health')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_model(ctx, param, value):
    if value is None:
        return value
    import json as _json

    try:
        data = _json.loads(value)
    except _json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON for 'model': {e}")
    errs = _validator.validate_field(
        _rules.CREATEINFERENCESERVICEREQUESTBODY_RULES["fields"]["model"], data, 'model')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_model_ext_metadata(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.CREATEINFERENCESERVICEREQUESTBODY_RULES["fields"]["model_ext_metadata"], value, 'model_ext_metadata')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_model_id(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.QUERY_PARAM_RULES["model_id"], value, 'model_id')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_model_name(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.QUERY_PARAM_RULES["model_name"], value, 'model_name')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_model_version_id(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.QUERY_PARAM_RULES["model_version_id"], value, 'model_version_id')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_model_version_name(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.QUERY_PARAM_RULES["model_version_name"], value, 'model_version_name')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_name(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.CREATEINFERENCESERVICEREQUESTBODY_RULES["fields"]["name"], value, 'name')
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
def _validate_pool_id(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.CREATEINFERENCESERVICEREQUESTBODY_RULES["fields"]["pool_id"], value, 'pool_id')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_pool_type(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.CREATEINFERENCESERVICEREQUESTBODY_RULES["fields"]["pool_type"], value, 'pool_type')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_readiness_health(ctx, param, value):
    if value is None:
        return value
    import json as _json

    try:
        data = _json.loads(value)
    except _json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON for 'readiness_health': {e}")
    errs = _validator.validate_field(
        _rules.CREATEINFERENCESERVICEREQUESTBODY_RULES["fields"]["readiness_health"], data, 'readiness_health')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_service_id(ctx, param, value):
    if value is None:
        return value
    errs = _validator.validate_field(
        _rules.PATH_PARAM_RULES["service_id"], value, 'service_id')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_service_invoke(ctx, param, value):
    if value is None:
        return value
    import json as _json

    try:
        data = _json.loads(value)
    except _json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON for 'service_invoke': {e}")
    errs = _validator.validate_field(
        _rules.CREATEINFERENCESERVICEREQUESTBODY_RULES["fields"]["service_invoke"], data, 'service_invoke')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_skill_config(ctx, param, value):
    if value is None:
        return value
    import json as _json

    try:
        data = _json.loads(value)
    except _json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON for 'skill_config': {e}")
    errs = _validator.validate_field(
        _rules.CREATEINFERENCESERVICEREQUESTBODY_RULES["fields"]["skill_config"], data, 'skill_config')
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
        _rules.LISTINFERENCESERVICELOGSREQUESTBODY_RULES["fields"]["start_time"], value, 'start_time')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
def _validate_startup_health(ctx, param, value):
    if value is None:
        return value
    import json as _json

    try:
        data = _json.loads(value)
    except _json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON for 'startup_health': {e}")
    errs = _validator.validate_field(
        _rules.CREATEINFERENCESERVICEREQUESTBODY_RULES["fields"]["startup_health"], data, 'startup_health')
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
def _validate_stop_schedule(ctx, param, value):
    if value is None:
        return value
    import json as _json

    try:
        data = _json.loads(value)
    except _json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON for 'stop_schedule': {e}")
    errs = _validator.validate_field(
        _rules.CREATEINFERENCESERVICEREQUESTBODY_RULES["fields"]["stop_schedule"], data, 'stop_schedule')
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
        _rules.CREATEINFERENCESERVICEREQUESTBODY_RULES["fields"]["workspace_id"], value, 'workspace_id')
    if errs:
        raise click.BadParameter("; ".join(errs))
    return value
