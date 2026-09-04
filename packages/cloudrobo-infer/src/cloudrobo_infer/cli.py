import json

import click
from cloudrobo_core.cli.cli_utils import get_client, out

from .client import InferClient

# isort: off
# ===== VALIDATOR IMPORTS =====
from .validators.cli_callbacks import (
    _validate_cmd,
    _validate_deploy_timeout_minutes,
    _validate_description,
    _validate_end_time,
    _validate_envs,
    _validate_files,
    _validate_flavor,
    _validate_image_swr_url,
    _validate_keywords,
    _validate_limit,
    _validate_line_num,
    _validate_liveness_health,
    _validate_model,
    _validate_model_ext_metadata,
    _validate_model_id,
    _validate_model_name,
    _validate_model_version_id,
    _validate_model_version_name,
    _validate_name,
    _validate_offset,
    _validate_pool_id,
    _validate_pool_type,
    _validate_readiness_health,
    _validate_service_id,
    _validate_service_invoke,
    _validate_skill_config,
    _validate_sort_dir,
    _validate_sort_key,
    _validate_start_time,
    _validate_startup_health,
    _validate_status,
    _validate_stop_schedule,
    _validate_user_id,
    _validate_user_name,
    _validate_workspace_id,
)
# ===== END VALIDATOR IMPORTS =====
# isort: on





@click.group()
def infer():
    """推理服务命令组"""





def _parse_json_options(ctx, target, options):
    for key, raw in options.items():
        if raw:
            try:
                target[key] = json.loads(raw)
            except json.JSONDecodeError as e:
                raise click.BadParameter(f"Invalid JSON for '{key}': {e}")


def _parse_required_json(ctx, raw, key):
    if not raw:
        raise click.BadParameter(f"'{key}' is required and must be a JSON object")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON for '{key}': {e}")


@infer.command("create")
@click.option("--cmd", default=None, callback=_validate_cmd, help='启动命令')
@click.option("--deploy-timeout-minutes", type=click.IntRange(1, 300), callback=_validate_deploy_timeout_minutes, help='部署超时时间')
@click.option("--description", default=None, callback=_validate_description, help='描述')
@click.option("--envs-json", default=None, callback=_validate_envs, help='环境变量')
@click.option("--files-json", default=None, callback=_validate_files, help='文件挂载')
@click.option("--flavor", required=True, callback=_validate_flavor, help='资源规格')
@click.option("--image-swr-url", default=None, callback=_validate_image_swr_url, help='镜像地址')
@click.option("--internet-access-enable", is_flag=True, default=False, help='是否开启公网访问')
@click.option("--liveness-health-json", default=None, callback=_validate_liveness_health, help='模型服务健康检查')
@click.option("--model-json", required=True, callback=_validate_model, help='模型信息')
@click.option("--model-ext-metadata", default=None, callback=_validate_model_ext_metadata, help='模型扩展元数据，JSON/YAML 格式的 r2c 配置信息。')
@click.option("--name", required=True, callback=_validate_name, help='服务名称')
@click.option("--pool-id", required=True, callback=_validate_pool_id, help='资源池ID')
@click.option("--pool-type", type=click.Choice(['DEDICATED', 'SHARED'], case_sensitive=False), callback=_validate_pool_type, required=True, help='资源池类型')
@click.option("--readiness-health-json", default=None, callback=_validate_readiness_health, help='模型服务健康检查')
@click.option("--service-invoke-json", default=None, callback=_validate_service_invoke, help='服务调用配置')
@click.option("--skill-config-json", default=None, callback=_validate_skill_config, help='技能配置')
@click.option("--startup-health-json", default=None, callback=_validate_startup_health, help='模型服务健康检查')
@click.option("--stop-schedule-json", default=None, callback=_validate_stop_schedule, help='定时停止配置')
@click.option("--workspace-id", required=False, default=None, callback=_validate_workspace_id, help='工作空间ID')
@click.option("--dry-run", is_flag=True)
@click.pass_context
def create_infer_service(ctx, cmd, deploy_timeout_minutes, description, envs_json,
                         files_json, flavor, image_swr_url, internet_access_enable,
                         liveness_health_json, model_json, model_ext_metadata, name,
                         pool_id, pool_type, readiness_health_json,
                         service_invoke_json, skill_config_json, startup_health_json,
                         stop_schedule_json, workspace_id, dry_run):
    """创建推理服务(部署模型)"""
    req = {
        "name": name,
        "flavor": flavor,
        "model": _parse_required_json(ctx, model_json, "model"),
        "workspace_id": workspace_id,
        "pool_id": pool_id,
        "pool_type": pool_type.upper(),
    }
    if description is not None:
        req["description"] = description
    if image_swr_url is not None:
        req["image_swr_url"] = image_swr_url
    if cmd is not None:
        req["cmd"] = cmd
    if deploy_timeout_minutes is not None:
        req["deploy_timeout_minutes"] = deploy_timeout_minutes
    if model_ext_metadata is not None:
        req["model_ext_metadata"] = model_ext_metadata
    if internet_access_enable is not None:
        req["internet_access_enable"] = str(internet_access_enable).lower() in ("1", "true", "yes")
    _parse_json_options(ctx, req, {
        "envs": envs_json,
        "stop_schedule": stop_schedule_json,
        "service_invoke": service_invoke_json,
        "skill_config": skill_config_json,
        "files": files_json,
        "startup_health": startup_health_json,
        "readiness_health": readiness_health_json,
        "liveness_health": liveness_health_json,
    })
    if dry_run:
        click.echo(f"[DRY-RUN] create_infer_service({json.dumps(req, ensure_ascii=False)})")
        return
    client = get_client(ctx, InferClient)
    result = client.create_infer_service(req)
    out(result)


@infer.command("list")
@click.option("--limit", type=click.IntRange(1, 50), default=10, callback=_validate_limit, help='每页数据条数')
@click.option("--offset", type=click.IntRange(0, 1000), default=0, callback=_validate_offset, help='分页页码偏移量')
@click.option("--sort-key", type=click.Choice(['create_at', 'created_at', 'update_at', 'updated_at'], case_sensitive=False), callback=_validate_sort_key, help='排序字段，支持 create_at / update_at / created_at / updated_at')
@click.option("--sort-dir", type=click.Choice(['ASC', 'DESC'], case_sensitive=False), callback=_validate_sort_dir, help='排序方向，ASC正序 / DESC倒序')
@click.option("--name", default=None, callback=_validate_name, help='推理服务名称模糊查询')
@click.option("--workspace-id", required=False, default=None, callback=_validate_workspace_id, help='工作空间ID')
@click.option("--status", default=None, callback=_validate_status, help='根据服务状态查询相关推理服务，支持多选')
@click.option("--model-id", default=None, callback=_validate_model_id, help='模型资产ID筛选')
@click.option("--model-name", default=None, callback=_validate_model_name, help='模型资产名称筛选')
@click.option("--model-version-id", default=None, callback=_validate_model_version_id, help='模型版本ID筛选')
@click.option("--model-version-name", default=None, callback=_validate_model_version_name, help='模型版本名称筛选')
@click.option("--user-name", default=None, callback=_validate_user_name, help='创建人名称筛选')
@click.option("--user-id", default=None, callback=_validate_user_id, help='创建人ID筛选')
@click.option("--contain-ext-metadata", is_flag=True, default=None, help='是否只返回包含 model_ext_metadata 的记录。省略=返回全部；true=只返回有 model_ext_metadata 的记录；false=只返回没有 model_ext_metadata 的记录')
@click.pass_context
def list_infer_services(ctx, limit, offset, sort_key, sort_dir, name, workspace_id,
                        status, model_id, model_name, model_version_id,
                        model_version_name, user_name, user_id, contain_ext_metadata):
    """查询推理服务列表"""
    client = get_client(ctx, InferClient)
    params = {}
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    if sort_key:
        params["sort_key"] = sort_key
    if sort_dir:
        params["sort_dir"] = sort_dir
    if name:
        params["name"] = name
    if workspace_id:
        params["workspace_id"] = workspace_id
    if status:
        params["status"] = status
    if model_id:
        params["model_id"] = model_id
    if model_name:
        params["model_name"] = model_name
    if model_version_id:
        params["model_version_id"] = model_version_id
    if model_version_name:
        params["model_version_name"] = model_version_name
    if user_name:
        params["user_name"] = user_name
    if user_id:
        params["user_id"] = user_id
    if contain_ext_metadata is not None:
        params["contain_ext_metadata"] = contain_ext_metadata
    result = client.list_infer_services(**params)
    out(result)


@infer.command("show")
@click.option("--service-id", required=True, callback=_validate_service_id, help='推理服务唯一标识ID')
@click.pass_context
def show_infer_service(ctx, service_id):
    """查询推理服务详情"""
    client = get_client(ctx, InferClient)
    result = client.show_infer_service(service_id)
    out(result)


@infer.command("update")
@click.option("--service-id", required=True, callback=_validate_service_id, help='推理服务唯一标识ID')
@click.option("--description", default=None, callback=_validate_description, help='描述')
@click.option("--model-ext-metadata", default=None, callback=_validate_model_ext_metadata, help='模型扩展元数据，JSON/YAML 格式的 r2c 配置信息。')
@click.option("--dry-run", is_flag=True)
@click.pass_context
def update_infer_service(ctx, service_id, description, model_ext_metadata, dry_run):
    """更新推理服务配置"""
    req = {}
    if description is not None:
        req["description"] = description
    if model_ext_metadata is not None:
        req["model_ext_metadata"] = model_ext_metadata
    if dry_run:
        click.echo(
            f"[DRY-RUN] update_infer_service(service_id={service_id}, "
            f"{json.dumps(req, ensure_ascii=False)})"
        )
        return
    client = get_client(ctx, InferClient)
    result = client.update_infer_service(service_id, req)
    out(result)


@infer.command("delete")
@click.option("--service-id", required=True, callback=_validate_service_id, help='推理服务唯一标识ID')
@click.option("--dry-run", is_flag=True)
@click.pass_context
def delete_infer_service(ctx, service_id, dry_run):
    """删除推理服务"""
    if dry_run:
        click.echo(f"[DRY-RUN] delete_infer_service(service_id={service_id})")
        return
    client = get_client(ctx, InferClient)
    client.delete_infer_service(service_id)
    out(f"deleted: service_id={service_id}")


@infer.command("start")
@click.option("--service-id", required=True, callback=_validate_service_id, help='推理服务唯一标识ID')
@click.option("--dry-run", is_flag=True)
@click.pass_context
def start_infer_service(ctx, service_id, dry_run):
    """启动推理服务"""
    if dry_run:
        click.echo(f"[DRY-RUN] start_infer_service(service_id={service_id})")
        return
    client = get_client(ctx, InferClient)
    result = client.start_infer_service(service_id)
    out(result)


@infer.command("stop")
@click.option("--service-id", required=True, callback=_validate_service_id, help='推理服务唯一标识ID')
@click.option("--dry-run", is_flag=True)
@click.pass_context
def stop_infer_service(ctx, service_id, dry_run):
    """停止推理服务"""
    if dry_run:
        click.echo(f"[DRY-RUN] stop_infer_service(service_id={service_id})")
        return
    client = get_client(ctx, InferClient)
    result = client.stop_infer_service(service_id)
    out(result)


@infer.command("list-logs")
@click.option("--service-id", required=True, callback=_validate_service_id, help='推理服务唯一标识ID')
@click.option("--end-time", type=click.IntRange(0, 32503680000000), required=True, callback=_validate_end_time, help='搜索日志的结束时间。')
@click.option("--highlight", is_flag=True, default=None, help='在查询结果中日志关键词是否高亮显示。')
@click.option("--is-count", is_flag=True, default=None, help='在查询结果中是否统计日志条数。')
@click.option("--is-desc", is_flag=True, default=None, help='表示日志查询的顺序，当前支持顺序（false）或倒序查询（true）。')
@click.option("--keywords", default=None, callback=_validate_keywords, help='支持关键词精确搜索。关键词指相邻两个分词之间的单词。')
@click.option("--limit", type=click.IntRange(1, 5000), callback=_validate_limit, help='每次查询的日志条数。最小值：1，最大值：5000。')
@click.option("--line-num", default=None, callback=_validate_line_num, help='日志单行序列号，标识日志上报顺序，通常用于分页查询和日志数据的有序处理。分页查询需要使用该参数，用于从上次查询结束的问题继续查询。该参数从上次查询的返回结果中获取。')
@click.option("--start-time", type=click.IntRange(0, 32503680000000), required=True, callback=_validate_start_time, help='搜索日志的起始时间。')
@click.option("--dry-run", is_flag=True)
@click.pass_context
def list_infer_service_logs(ctx, service_id, start_time, end_time, limit, is_desc,
                            line_num, is_count, keywords, highlight, dry_run):
    """查询推理服务日志"""
    req = {"start_time": start_time, "end_time": end_time}
    if limit is not None:
        req["limit"] = limit
    if is_desc is not None:
        req["is_desc"] = True
    if line_num:
        req["line_num"] = line_num
    if is_count is not None:
        req["is_count"] = True
    if keywords:
        req["keywords"] = keywords
    if highlight is not None:
        req["highlight"] = True
    if dry_run:
        click.echo(
            f"[DRY-RUN] list_infer_service_logs(service_id={service_id}, "
            f"{json.dumps(req, ensure_ascii=False)})"
        )
        return
    client = get_client(ctx, InferClient)
    result = client.list_infer_service_logs(service_id, req)
    out(result)


@infer.command("wait-deploy")
@click.option("--service-id", required=True, callback=_validate_service_id, help='推理服务唯一标识ID')
@click.option("--timeout", type=click.IntRange(1, 3600), default=600, help="部署等待超时时间(秒)，默认600")
@click.pass_context
def wait_deploy(ctx, service_id, timeout):
    """等待推理服务部署完成"""
    client = get_client(ctx, InferClient)
    try:
        result = client.wait_deploy(service_id, timeout=timeout)
    except RuntimeError as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False))
        raise click.ClickException(str(e))
    out(result)
