import json
import os
import re

import click
import yaml

from cloudrobo_core.cli.cli_utils import get_client, out
from cloudrobo_core.sdk.exceptions import ResourceNotFoundError
from .client import AssetClient
from .validators import AssetValidator
from .validators.rules import EXT_METADATA_RULES, VALID_STATUSES


def _parse_json(value, param_name):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as e:
            raise click.BadParameter(f"Invalid JSON for {param_name}: {e}")
    return value


def parse_md_frontmatter(md_path: str) -> dict:
    with open(md_path, 'r', encoding='utf-8-sig') as f:
        content = f.read()

    match = re.match(r'^---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)', content, re.DOTALL)
    if not match:
        raise ValueError(f"Invalid markdown format: no frontmatter found in {md_path}")

    fm = yaml.safe_load(match.group(1))
    return fm or {}


def _safe_parse_frontmatter(local_path: str) -> dict:
    md_path = os.path.join(local_path, "README.md")
    if not os.path.isfile(md_path):
        return {}
    try:
        return parse_md_frontmatter(md_path)
    except (ValueError, yaml.YAMLError):
        return {}


@click.group()
def asset():
    """资产管理命令组"""
    pass


@asset.command("list-repositories")
@click.option("--name", default=None, help="根据资产库名称模糊查询")
@click.option("--sort-dir", default=None, type=click.Choice(["asc", "desc"], case_sensitive=False), help="排序方向(asc/desc)")
@click.option("--offset", type=int, default=None, help="起始数据偏移量")
@click.option("--limit", type=int, default=None, help="返回的对象数量")
@click.pass_context
def list_repositories(ctx, name, sort_dir, offset, limit):
    """列出资产库列表"""
    client = get_client(ctx, AssetClient)
    params = {}
    if name:
        params["name"] = name
    if sort_dir:
        params["sort_dir"] = sort_dir
    if offset is not None:
        params["offset"] = offset
    if limit is not None:
        params["limit"] = limit
    result = client.list_repositories(**params)
    out(result)


@asset.command("list-catalogs")
@click.option("--repository-id", required=True, help="仓库ID")
@click.option("--name", default=None, help="根据资产目录名称模糊查询")
@click.option("--sort-dir", default=None, type=click.Choice(["asc", "desc"], case_sensitive=False), help="排序方向(asc/desc)")
@click.option("--offset", type=int, default=None, help="起始数据偏移量")
@click.option("--limit", type=int, default=None, help="返回的对象数量")
@click.pass_context
def list_catalogs(ctx, repository_id, name, sort_dir, offset, limit):
    """列出目录"""
    client = get_client(ctx, AssetClient)
    params = {}
    if name:
        params["name"] = name
    if sort_dir:
        params["sort_dir"] = sort_dir
    if offset is not None:
        params["offset"] = offset
    if limit is not None:
        params["limit"] = limit
    result = client.list_catalogs(repository_id, **params)
    out(result)


@asset.command("show-catalog")
@click.option("--catalog-id", required=True, help="目录ID")
@click.pass_context
def show_catalog(ctx, catalog_id):
    """查看目录详情"""
    client = get_client(ctx, AssetClient)
    result = client.show_catalog(catalog_id)
    out(result)


@asset.command("create-asset")
@click.option("--catalog-id", required=True, help="目录ID")
@click.option("--name", default=None, help="资产名称（image类型可不传）")
@click.option("--type", "asset_type", required=True, help="资产类型")
@click.option("--sub-type", default=None, help="子类型")
@click.option("--description", default=None, help="描述")
@click.option("--status", default=None, type=click.Choice(list(VALID_STATUSES), case_sensitive=True), help="状态(CREATING/DRAFT/ALPHA/BETA/RELEASE/STABLE/DEPRECATED/ARCHIVE)")
@click.option("--tags", default=None, help="标签列表(逗号分隔)")
@click.option("--url", default=None, help="OBS或SWR路径")
@click.option("--ext-metadata", default=None, help="扩展元数据(JSON字符串)")
@click.option("--parent-asset-version-id", default=None, help="父资产版本ID")
@click.option("--generation-method", default=None, help="资产生成方法")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def create_asset(ctx, catalog_id, name, asset_type, sub_type, description, status, tags, url, ext_metadata, parent_asset_version_id, generation_method, dry_run):
    """创建资产"""
    if dry_run:
        click.echo(f"[DRY-RUN] create_asset(catalog_id={catalog_id}, name={name}, type={asset_type}, "
                   f"sub_type={sub_type}, description={description}, status={status}, tags={tags}, "
                   f"url={url}, ext_metadata={ext_metadata}, "
                   f"parent_asset_version_id={parent_asset_version_id}, "
                   f"generation_method={generation_method})")
        return
    client = get_client(ctx, AssetClient)
    req = {"catalog_id": catalog_id, "type": asset_type}
    if name is not None:
        req["name"] = name
    if sub_type is not None:
        req["sub_type"] = sub_type
    if description is not None:
        req["description"] = description
    if status is not None:
        req["status"] = status
    if tags is not None:
        req["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    if url is not None:
        req["url"] = url
    if ext_metadata is not None:
        req["ext_metadata"] = _parse_json(ext_metadata, "--ext-metadata")
    if parent_asset_version_id is not None:
        req["parent_asset_version_id"] = parent_asset_version_id
    if generation_method is not None:
        req["generation_method"] = generation_method
    result = client.create_asset(req)
    out(result)


@asset.command("list-assets")
@click.option("--repository-id", default=None, help="仓库ID（与catalog-id至少提供一个，同时提供时AND叠加）")
@click.option("--catalog-id", default=None, help="目录ID（与repository-id至少提供一个，同时提供时AND叠加）")
@click.option("--type", "asset_type", default=None, help="资产类型")
@click.option("--sub-type", default=None, help="子类型")
@click.option("--ids", default=None, help="资产ID列表(逗号分隔)")
@click.option("--name", default=None, help="按资产名称模糊查询")
@click.option("--exact-name", default=None, help="按资产名称精确查询")
@click.option("--mine", is_flag=True, help="查询我创建的资产")
@click.option("--author", default=None, help="创建者用户ID列表(逗号分隔)")
@click.option("--tags", default=None, help="按标签查询(逗号分隔)")
@click.option("--tags-operator", default=None, help="多tags筛选规则(and/or)")
@click.option("--status", default=None, help="状态列表(逗号分隔)")
@click.option("--sort-key", default=None, help="排序字段(asset_id/repository_id/catalog_id/name/created_at/updated_at)")
@click.option("--sort-dir", default=None, type=click.Choice(["asc", "desc"], case_sensitive=False), help="排序方向(asc/desc)")
@click.option("--offset", type=int, default=None, help="起始数据偏移量")
@click.option("--limit", type=int, default=None, help="每页返回的资产数量")
@click.option("--ext-metadata", default=None, help="根据ext_metadata的key=value对检索")
@click.option("--permissions", default=None, help="要校验的权限列表(逗号分隔)")
@click.option("--actions", default=None, help="根据action列表检索(逗号分隔)")
@click.option("--actions-operator", default=None, help="多actions筛选规则(and/or)")
@click.option("--recommend-score", is_flag=True, help="是否按运营推荐分排序")
@click.option("--action-status", default=None, help="action状态过滤(逗号分隔,ENABLE/DISABLE)")
@click.pass_context
def list_assets(ctx, repository_id, catalog_id, asset_type, sub_type, ids, name, exact_name, mine, author, tags, tags_operator, status, sort_key, sort_dir, offset, limit, ext_metadata, permissions, actions, actions_operator, recommend_score, action_status):
    """列出资产"""
    if not repository_id and not catalog_id:
        raise click.UsageError("repository-id 和 catalog-id 至少提供一个")
    client = get_client(ctx, AssetClient)
    params = {}
    if repository_id:
        params["repository_id"] = repository_id
    if catalog_id:
        params["catalog_id"] = catalog_id
    if asset_type:
        params["type"] = asset_type
    if sub_type:
        params["sub_type"] = sub_type
    if ids:
        params["ids"] = [i.strip() for i in ids.split(",") if i.strip()]
    if name:
        params["name"] = name
    if exact_name:
        params["exact_name"] = exact_name
    if mine:
        params["mine"] = True
    if author:
        params["author"] = [a.strip() for a in author.split(",") if a.strip()]
    if tags:
        params["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    if tags_operator:
        params["tags_operator"] = tags_operator
    if status:
        params["status"] = [s.strip() for s in status.split(",") if s.strip()]
    if sort_key:
        params["sort_key"] = sort_key
    if sort_dir:
        params["sort_dir"] = sort_dir
    if offset is not None:
        params["offset"] = offset
    if limit is not None:
        params["limit"] = limit
    if ext_metadata:
        params["ext_metadata"] = ext_metadata
    if permissions:
        params["permissions"] = [p.strip() for p in permissions.split(",") if p.strip()]
    if actions:
        params["actions"] = [a.strip() for a in actions.split(",") if a.strip()]
    if actions_operator:
        params["actions_operator"] = actions_operator
    if recommend_score:
        params["recommend_score"] = True
    if action_status:
        params["action_status"] = [s.strip() for s in action_status.split(",") if s.strip()]
    result = client.list_assets(**params)
    out(result)


@asset.command("show-asset")
@click.option("--asset-id", required=True, help="资产ID")
@click.pass_context
def show_asset(ctx, asset_id):
    """查看资产详情"""
    client = get_client(ctx, AssetClient)
    result = client.show_asset(asset_id)
    out(result)


@asset.command("update-asset")
@click.option("--asset-id", required=True, help="资产ID")
@click.option("--name", default=None, help="新名称")
@click.option("--description", default=None, help="新描述")
@click.option("--status", default=None, type=click.Choice(list(VALID_STATUSES), case_sensitive=True), help="状态(CREATING/DRAFT/ALPHA/BETA/RELEASE/STABLE/DEPRECATED/ARCHIVE)")
@click.option("--tags", default=None, help="标签列表(逗号分隔，全量替换)")
@click.option("--ext-metadata", default=None, help="扩展元数据(JSON字符串)")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def update_asset(ctx, asset_id, name, description, status, tags, ext_metadata, dry_run):
    """更新资产"""
    if dry_run:
        click.echo(f"[DRY-RUN] update_asset(asset_id={asset_id}, name={name}, "
                   f"description={description}, status={status}, tags={tags}, "
                   f"ext_metadata={ext_metadata})")
        return
    client = get_client(ctx, AssetClient)
    req = {}
    if name is not None:
        req["name"] = name
    if description is not None:
        req["description"] = description
    if status is not None:
        req["status"] = status
    if tags is not None:
        req["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    if ext_metadata is not None:
        req["ext_metadata"] = _parse_json(ext_metadata, "--ext-metadata")
    result = client.update_asset(asset_id, req)
    out(result)


@asset.command("delete-asset")
@click.option("--asset-id", required=True, help="资产ID")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def delete_asset(ctx, asset_id, dry_run):
    """删除资产"""
    if dry_run:
        click.echo(f"[DRY-RUN] delete_asset(asset_id={asset_id})")
        return
    client = get_client(ctx, AssetClient)
    result = client.delete_asset(asset_id)
    out(result)


@asset.command("batch-delete-assets")
@click.option("--asset-ids", required=True, help="资产ID列表(逗号分隔)")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def batch_delete_assets(ctx, asset_ids, dry_run):
    """批量删除资产"""
    ids = [i.strip() for i in asset_ids.split(",") if i.strip()]
    if dry_run:
        click.echo(f"[DRY-RUN] batch_delete_assets(asset_ids={ids})")
        return
    client = get_client(ctx, AssetClient)
    result = client.batch_delete_assets({"asset_ids": ids})
    out(result)


@asset.command("create-version")
@click.option("--asset-id", required=True, help="资产ID")
@click.option("--version", default=None, help="版本号")
@click.option("--description", default=None, help="描述")
@click.option("--status", default=None, type=click.Choice(list(VALID_STATUSES), case_sensitive=True), help="状态(CREATING/DRAFT/ALPHA/BETA/RELEASE/STABLE/DEPRECATED/ARCHIVE)")
@click.option("--url", default=None, help="OBS或SWR路径")
@click.option("--ext-metadata", default=None, help="扩展元数据(JSON字符串)")
@click.option("--parent-asset-version-id", default=None, help="父资产版本ID")
@click.option("--generation-method", default=None, help="资产生成方法")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def create_version(ctx, asset_id, version, description, status, url, ext_metadata, parent_asset_version_id, generation_method, dry_run):
    """创建资产版本"""
    if dry_run:
        click.echo(f"[DRY-RUN] create_version(asset_id={asset_id}, version={version}, "
                   f"description={description}, status={status}, url={url}, "
                   f"ext_metadata={ext_metadata}, parent_asset_version_id={parent_asset_version_id}, "
                   f"generation_method={generation_method})")
        return
    client = get_client(ctx, AssetClient)
    req = {}
    if version is not None:
        req["version"] = version
    if description is not None:
        req["description"] = description
    if status is not None:
        req["status"] = status
    if url is not None:
        req["url"] = url
    if ext_metadata is not None:
        req["ext_metadata"] = _parse_json(ext_metadata, "--ext-metadata")
    if parent_asset_version_id is not None:
        req["parent_asset_version_id"] = parent_asset_version_id
    if generation_method is not None:
        req["generation_method"] = generation_method
    result = client.create_asset_version(asset_id, req)
    out(result)


@asset.command("list-versions")
@click.option("--asset-id", required=True, help="资产ID")
@click.option("--version", default=None, help="根据版本号模糊查询")
@click.option("--exact-version", default=None, help="根据版本号精确查询")
@click.option("--limit", type=int, default=None, help="每页返回数量")
@click.option("--offset", type=int, default=None, help="偏移量")
@click.option("--sort-key", default=None, help="排序字段(created_at/updated_at/version/image_size)")
@click.option("--sort-dir", default=None, type=click.Choice(["asc", "desc"], case_sensitive=False), help="排序方向(asc/desc)")
@click.option("--actions", default=None, help="根据action列表检索(逗号分隔)")
@click.option("--actions-operator", default=None, help="多actions筛选规则(and/or)")
@click.option("--ext-metadata", default=None, help="根据ext_metadata的key=value对检索")
@click.option("--action-status", default=None, help="action状态过滤(逗号分隔,ENABLE/DISABLE)")
@click.pass_context
def list_versions(ctx, asset_id, version, exact_version, limit, offset, sort_key, sort_dir, actions, actions_operator, ext_metadata, action_status):
    """查询资产版本列表"""
    client = get_client(ctx, AssetClient)
    params = {}
    if version:
        params["version"] = version
    if exact_version:
        params["exact_version"] = exact_version
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    if sort_key:
        params["sort_key"] = sort_key
    if sort_dir:
        params["sort_dir"] = sort_dir
    if actions:
        params["actions"] = [a.strip() for a in actions.split(",") if a.strip()]
    if actions_operator:
        params["actions_operator"] = actions_operator
    if ext_metadata:
        params["ext_metadata"] = ext_metadata
    if action_status:
        params["action_status"] = [s.strip() for s in action_status.split(",") if s.strip()]
    result = client.list_asset_versions(asset_id, **params)
    out(result)


@asset.command("show-version")
@click.option("--asset-id", required=True, help="资产ID")
@click.option("--version-id", required=True, help="版本ID")
@click.pass_context
def show_version(ctx, asset_id, version_id):
    """查看资产版本详情"""
    client = get_client(ctx, AssetClient)
    result = client.show_asset_version(asset_id, version_id)
    out(result)


@asset.command("update-version")
@click.option("--asset-id", required=True, help="资产ID")
@click.option("--version-id", required=True, help="版本ID")
@click.option("--version", default=None, help="版本号")
@click.option("--description", default=None, help="描述")
@click.option("--status", default=None, type=click.Choice(list(VALID_STATUSES), case_sensitive=True), help="状态(CREATING/DRAFT/ALPHA/BETA/RELEASE/STABLE/DEPRECATED/ARCHIVE)")
@click.option("--ext-metadata", default=None, help="扩展元数据(JSON字符串)")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def update_version(ctx, asset_id, version_id, version, description, status, ext_metadata, dry_run):
    """更新资产版本"""
    if dry_run:
        click.echo(f"[DRY-RUN] update_version(asset_id={asset_id}, version_id={version_id}, "
                   f"version={version}, description={description}, status={status}, "
                   f"ext_metadata={ext_metadata})")
        return
    client = get_client(ctx, AssetClient)
    req = {}
    if version is not None:
        req["version"] = version
    if description is not None:
        req["description"] = description
    if status is not None:
        req["status"] = status
    if ext_metadata is not None:
        req["ext_metadata"] = _parse_json(ext_metadata, "--ext-metadata")
    result = client.update_asset_version(asset_id, version_id, req)
    out(result)


@asset.command("delete-version")
@click.option("--asset-id", required=True, help="资产ID")
@click.option("--version-id", required=True, help="版本ID")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def delete_version(ctx, asset_id, version_id, dry_run):
    """删除资产版本"""
    if dry_run:
        click.echo(f"[DRY-RUN] delete_version(asset_id={asset_id}, version_id={version_id})")
        return
    client = get_client(ctx, AssetClient)
    result = client.delete_asset_version(asset_id, version_id)
    out(result)


@asset.command("batch-delete-versions")
@click.option("--asset-id", required=True, help="资产ID")
@click.option("--version-ids", required=True, help="版本ID列表(逗号分隔)")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def batch_delete_versions(ctx, asset_id, version_ids, dry_run):
    """批量删除资产版本"""
    ids = [i.strip() for i in version_ids.split(",") if i.strip()]
    if dry_run:
        click.echo(f"[DRY-RUN] batch_delete_versions(asset_id={asset_id}, version_ids={ids})")
        return
    client = get_client(ctx, AssetClient)
    result = client.batch_delete_asset_versions(asset_id, {"version_ids": ids})
    out(result)


@asset.command("check-permission")
@click.option("--asset-id", required=True, help="资产ID")
@click.option("--version-id", required=True, help="版本ID")
@click.option("--permissions", required=True, help="权限列表(逗号分隔)")
@click.pass_context
def check_permission(ctx, asset_id, version_id, permissions):
    """校验资产权限"""
    client = get_client(ctx, AssetClient)
    perms = [p.strip() for p in permissions.split(",") if p.strip()]
    result = client.check_asset_permission(asset_id, version_id, {"permissions": perms})
    out(result)


@asset.command("add-tags")
@click.option("--asset-id", required=True, help="资产ID")
@click.option("--tags", required=True, help="标签列表(逗号分隔)")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def add_tags(ctx, asset_id, tags, dry_run):
    """添加标签"""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    if dry_run:
        click.echo(f"[DRY-RUN] add_tags(asset_id={asset_id}, tags={tag_list})")
        return
    client = get_client(ctx, AssetClient)
    result = client.add_tags(asset_id, tag_list)
    out(result)


@asset.command("delete-tag")
@click.option("--asset-id", required=True, help="资产ID")
@click.option("--tag", required=True, help="标签名")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def delete_tag(ctx, asset_id, tag, dry_run):
    """删除资产标签"""
    if dry_run:
        click.echo(f"[DRY-RUN] delete_tag(asset_id={asset_id}, tag={tag})")
        return
    client = get_client(ctx, AssetClient)
    result = client.delete_tag(asset_id, tag)
    out(result)


@asset.command("list-tags")
@click.option("--language", required=True, help="语言(zh/en)")
@click.option("--type", "asset_type", default=None, help="资产类型")
@click.option("--sub-type", default=None, help="子类型")
@click.pass_context
def list_tags(ctx, language, asset_type, sub_type):
    """查询预定义标签列表"""
    client = get_client(ctx, AssetClient)
    params = {"language": language}
    if asset_type:
        params["type"] = asset_type
    if sub_type:
        params["sub_type"] = sub_type
    result = client.list_all_tags(**params)
    out(result)


@asset.command("show-lineage")
@click.option("--asset-id", required=True, help="资产ID")
@click.option("--version-id", required=True, help="版本ID")
@click.option("--type", "query_type", required=True, type=click.Choice(["children", "parent"]), help="查询方式(children=父查子, parent=子查父)")
@click.pass_context
def show_lineage(ctx, asset_id, version_id, query_type):
    """查看血缘关系"""
    client = get_client(ctx, AssetClient)
    try:
        result = client.show_asset_tree(asset_id, version_id, query_type)
    except ResourceNotFoundError:
        click.echo("该资产版本没有血缘关系")
        return
    out(result)


@asset.command("list-actions")
@click.option("--asset-id", required=True, help="资产ID")
@click.option("--version-id", required=True, help="版本ID")
@click.pass_context
def list_actions(ctx, asset_id, version_id):
    """查询资产支持的action列表"""
    client = get_client(ctx, AssetClient)
    result = client.list_asset_actions(asset_id, version_id)
    out(result)


@asset.command("create-action")
@click.option("--asset-id", required=True, help="资产ID")
@click.option("--version-id", required=True, help="版本ID")
@click.option("--action-info", required=True, help="Action信息(JSON)")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def create_action(ctx, asset_id, version_id, action_info, dry_run):
    """添加资产action"""
    if dry_run:
        click.echo(f"[DRY-RUN] create_action(asset_id={asset_id}, version_id={version_id}, "
                   f"action_info={action_info})")
        return
    client = get_client(ctx, AssetClient)
    req = _parse_json(action_info, "--action-info")
    result = client.create_asset_action(asset_id, version_id, req)
    out(result)


@asset.command("show-action")
@click.option("--asset-id", required=True, help="资产ID")
@click.option("--version-id", required=True, help="版本ID")
@click.option("--action", required=True, help="Action名称")
@click.pass_context
def show_action(ctx, asset_id, version_id, action):
    """查询资产action详情"""
    client = get_client(ctx, AssetClient)
    result = client.show_asset_action(asset_id, version_id, action)
    out(result)


@asset.command("update-action")
@click.option("--asset-id", required=True, help="资产ID")
@click.option("--version-id", required=True, help="版本ID")
@click.option("--action", required=True, help="Action名称")
@click.option("--action-info", required=True, help="Action更新信息(JSON)")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def update_action(ctx, asset_id, version_id, action, action_info, dry_run):
    """修改资产action"""
    if dry_run:
        click.echo(f"[DRY-RUN] update_action(asset_id={asset_id}, version_id={version_id}, "
                   f"action={action}, action_info={action_info})")
        return
    client = get_client(ctx, AssetClient)
    req = _parse_json(action_info, "--action-info")
    result = client.update_asset_action(asset_id, version_id, action, req)
    out(result)


@asset.command("delete-action")
@click.option("--asset-id", required=True, help="资产ID")
@click.option("--version-id", required=True, help="版本ID")
@click.option("--action", required=True, help="Action名称")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def delete_action(ctx, asset_id, version_id, action, dry_run):
    """删除资产action"""
    if dry_run:
        click.echo(f"[DRY-RUN] delete_action(asset_id={asset_id}, version_id={version_id}, action={action})")
        return
    client = get_client(ctx, AssetClient)
    result = client.delete_asset_action(asset_id, version_id, action)
    out(result)


@asset.command("search-assets")
@click.option("--keyword", required=True, help="搜索关键词")
@click.option("--type", "asset_type", default=None, help="资产类型(simulation/model/dataset)")
@click.option("--limit", type=int, default=None, help="返回数量")
@click.option("--offset", type=int, default=None, help="偏移量")
@click.pass_context
def search_assets(ctx, keyword, asset_type, limit, offset):
    """搜索广场资产"""
    client = get_client(ctx, AssetClient)
    req = {"keyword": keyword}
    if asset_type:
        req["type"] = asset_type
    if limit is not None:
        req["limit"] = limit
    if offset is not None:
        req["offset"] = offset
    result = client.search_assets(req)
    out(result)


@asset.command("list-publication-assets")
@click.option("--type", "asset_type", default=None, help="资产类型")
@click.option("--sub-type", default=None, help="子类型")
@click.option("--ids", default=None, help="资产ID列表(逗号分隔)")
@click.option("--name", default=None, help="按资产名称模糊查询")
@click.option("--exact-name", default=None, help="按资产名称精确查询")
@click.option("--tags", default=None, help="按标签查询(逗号分隔)")
@click.option("--tags-operator", default=None, help="多tags筛选规则(and/or)")
@click.option("--status", default=None, help="状态列表(逗号分隔)")
@click.option("--sort-key", default=None, help="排序字段(asset_id/repository_id/catalog_id/name/created_at/updated_at)")
@click.option("--sort-dir", default=None, type=click.Choice(["asc", "desc"], case_sensitive=False), help="排序方向(asc/desc)")
@click.option("--offset", type=int, default=None, help="起始数据偏移量")
@click.option("--limit", type=int, default=None, help="每页返回的资产数量")
@click.option("--ext-metadata", default=None, help="根据ext_metadata的key=value对检索")
@click.option("--permissions", default=None, help="要校验的权限列表(逗号分隔)")
@click.option("--actions", default=None, help="根据action列表检索(逗号分隔)")
@click.option("--actions-operator", default=None, help="多actions筛选规则(and/or)")
@click.option("--recommend-score", is_flag=True, help="是否按运营推荐分排序")
@click.option("--capabilities", default=None, help="按资产能力过滤(逗号分隔,training/inference/reinforcement_learning)")
@click.option("--action-status", default=None, help="action状态过滤(逗号分隔,ENABLE/DISABLE)")
@click.pass_context
def list_publication_assets(ctx, asset_type, sub_type, ids, name, exact_name, tags, tags_operator, status, sort_key, sort_dir, offset, limit, ext_metadata, permissions, actions, actions_operator, recommend_score, capabilities, action_status):
    """查询官方和社区资产列表"""
    client = get_client(ctx, AssetClient)
    params = {}
    if asset_type:
        params["type"] = asset_type
    if sub_type:
        params["sub_type"] = sub_type
    if ids:
        params["ids"] = [i.strip() for i in ids.split(",") if i.strip()]
    if name:
        params["name"] = name
    if exact_name:
        params["exact_name"] = exact_name
    if tags:
        params["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    if tags_operator:
        params["tags_operator"] = tags_operator
    if status:
        params["status"] = [s.strip() for s in status.split(",") if s.strip()]
    if sort_key:
        params["sort_key"] = sort_key
    if sort_dir:
        params["sort_dir"] = sort_dir
    if offset is not None:
        params["offset"] = offset
    if limit is not None:
        params["limit"] = limit
    if ext_metadata:
        params["ext_metadata"] = ext_metadata
    if permissions:
        params["permissions"] = [p.strip() for p in permissions.split(",") if p.strip()]
    if actions:
        params["actions"] = [a.strip() for a in actions.split(",") if a.strip()]
    if actions_operator:
        params["actions_operator"] = actions_operator
    if recommend_score:
        params["recommend_score"] = True
    if capabilities:
        params["capabilities"] = [c.strip() for c in capabilities.split(",") if c.strip()]
    if action_status:
        params["action_status"] = [s.strip() for s in action_status.split(",") if s.strip()]
    result = client.list_publication_assets(**params)
    out(result)


@asset.command("import-asset")
@click.option("--catalog-id", default=None, help="目录ID（创建新资产时必填）")
@click.option("--name", default=None, help="资产名称")
@click.option("--type", "asset_type", default=None, help="资产类型")
@click.option("--sub-type", default=None, help="子类型")
@click.option("--local-path", required=True, help="本地文件夹路径")
@click.option("--asset-id", default=None, help="资产ID（创建新版本时必填）")
@click.option("--version-id", default=None, help="版本ID，与--asset-id同时使用时复用已有版本（不创建新版本），用于增量上传/重试失败的上传")
@click.option("--overwrite", is_flag=True, default=False, help="强制覆盖已存在的OBS文件（默认增量上传，仅--version-id模式生效）")
@click.option("--ext-metadata", default=None, help="扩展元数据(JSON字符串)")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def import_asset(ctx, catalog_id, name, asset_type, sub_type, local_path, asset_id, version_id, overwrite, ext_metadata, dry_run):
    """导入资产：注册资产+版本并上传到OBS"""
    fm = _safe_parse_frontmatter(local_path)

    resolved_name = fm.get("name") or name
    resolved_type = fm.get("type") or asset_type
    resolved_sub_type = fm.get("sub_type") or sub_type
    resolved_catalog_id = catalog_id

    resolved_version = fm.get("version")
    resolved_description = fm.get("description")
    resolved_status = fm.get("status")
    resolved_tags = fm.get("tags")
    resolved_ext_metadata = fm.get("ext_metadata") or ext_metadata
    resolved_parent = fm.get("parent_asset_version_id")
    resolved_gen_method = fm.get("generation_method")

    if isinstance(resolved_tags, str):
        resolved_tags = [t.strip() for t in resolved_tags.split(",") if t.strip()]

    if isinstance(resolved_ext_metadata, str):
        resolved_ext_metadata = _parse_json(resolved_ext_metadata, "ext_metadata")

    if asset_id:
        if version_id and any(v is not None for v in (resolved_description, resolved_status,
                           resolved_tags, resolved_version, resolved_parent, resolved_gen_method,
                           resolved_ext_metadata)):
            click.echo("Warning: --version-id mode reuses existing version; "
                       "description/status/tags/version/ext_metadata/parent_asset_version_id/generation_method "
                       "from frontmatter or CLI will be ignored.", err=True)
        elif not version_id and resolved_tags is not None:
            click.echo("Warning: tags are asset-level, not version-level. "
                       "Use 'cloudrobo asset add-tags' after import to add tags.", err=True)
    else:
        if not resolved_catalog_id:
            raise click.UsageError(
                "--catalog-id is required when creating a new asset (not found in CLI args)")
        if not resolved_name:
            raise click.UsageError(
                "--name is required (not found in README.md or CLI args)")
        if not resolved_type:
            raise click.UsageError(
                "--type is required (not found in README.md or CLI args)")
        if resolved_type == "simulation" and not resolved_sub_type:
            raise click.UsageError(
                "--sub-type is required for simulation type (not found in README.md or CLI args)")
        if resolved_type in EXT_METADATA_RULES:
            required_fields = list(EXT_METADATA_RULES[resolved_type].get("required_fields", []))
            if resolved_type == "simulation" and resolved_sub_type:
                sub_rules = EXT_METADATA_RULES[resolved_type].get("sub_type_rules", {}).get(resolved_sub_type, {})
                required_fields.extend(sub_rules.get("required_fields", []))
            if required_fields and not resolved_ext_metadata:
                type_desc = f"'{resolved_type}'" + (f"/{resolved_sub_type}" if resolved_sub_type else "")
                raise click.UsageError(
                    f"ext_metadata is required for type {type_desc} "
                    f"(missing required fields: {', '.join(required_fields)}). "
                    f"Provide via README.md frontmatter or --ext-metadata.")
            if resolved_ext_metadata:
                errs = AssetValidator().validate_ext_metadata(resolved_type, resolved_sub_type, resolved_ext_metadata)
                if errs:
                    raise click.UsageError("; ".join(errs))

    if dry_run:
        if not os.path.exists(local_path):
            click.echo(f"Warning: local-path '{local_path}' does not exist", err=True)
        click.echo(f"[DRY-RUN] import_asset(catalog_id={resolved_catalog_id}, name={resolved_name}, "
                   f"type={resolved_type}, sub_type={resolved_sub_type}, local_path={local_path}, "
                   f"asset_id={asset_id}, version_id={version_id}, "
                   f"description={resolved_description}, version={resolved_version}, "
                   f"status={resolved_status}, tags={resolved_tags}, "
                   f"ext_metadata={resolved_ext_metadata}, "
                   f"parent_asset_version_id={resolved_parent}, "
                   f"generation_method={resolved_gen_method}, "
                   f"overwrite={overwrite})")
        return
    client = get_client(ctx, AssetClient)
    result = client.import_asset(
        catalog_id=resolved_catalog_id, asset_type=resolved_type, name=resolved_name,
        local_path=local_path, sub_type=resolved_sub_type,
        asset_id=asset_id, version_id=version_id,
        description=resolved_description, ext_metadata=resolved_ext_metadata,
        version=resolved_version, status=resolved_status, tags=resolved_tags,
        parent_asset_version_id=resolved_parent, generation_method=resolved_gen_method,
        overwrite=overwrite
    )
    out(result)


@asset.command("export-asset")
@click.option("--asset-id", required=True, help="资产ID")
@click.option("--version-id", default=None, help="版本ID，不指定则导出最新版本")
@click.option("--local-path", required=True, help="本地目标路径")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def export_asset(ctx, asset_id, version_id, local_path, dry_run):
    """导出资产：从OBS下载资产版本到本地"""
    if dry_run:
        click.echo(f"[DRY-RUN] export_asset(asset_id={asset_id}, "
                   f"version_id={version_id or 'latest'}, local_path={local_path})")
        return
    client = get_client(ctx, AssetClient)
    result = client.export_asset(
        asset_id=asset_id, local_path=local_path, version_id=version_id,
    )
    out(result)