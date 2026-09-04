import logging
import os
import time
from typing import Any, Dict, List, Optional, Set

from cloudrobo_core.sdk import BaseClient
from cloudrobo_core.sdk.obs_client import OBSClient

from .validators import AssetValidator, ValidationError, validate_params
from .validators.rules import TAG_PATTERN

logger = logging.getLogger(__name__)


class AssetClient(BaseClient):
    SERVICE = "cloudrobo-asset-manager"

    def __init__(self, http_client):
        super().__init__(http_client)
        self._tag_cache: Dict[str, tuple] = {}
        self._tag_cache_ttl: float = 3600.0

    def list_repositories(self, **params) -> Dict:
        return self._client.get(self._url("/v1/repositories"), params=params)

    def list_catalogs(self, repository_id: str, **params) -> Dict:
        params["repository_id"] = repository_id
        return self._client.get(self._url("/v1/catalogs"), params=params)

    def show_catalog(self, catalog_id: str) -> Dict:
        return self._client.get(self._url(f"/v1/catalogs/{catalog_id}"))

    @validate_params("create_asset")
    def create_asset(self, req: Dict) -> Dict:
        if req.get("tags"):
            req["tags"] = self._validate_tags(req["tags"], req.get("type"), req.get("sub_type"))
        return self._client.post(self._url("/v1/assets"), json=req)

    def list_assets(self, **params) -> Dict:
        return self._client.get(self._url("/v1/assets"), params=params)

    @validate_params("update_asset")
    def update_asset(self, asset_id: str, req: Dict) -> Dict:
        return self._client.put(self._url(f"/v1/assets/{asset_id}"), json=req)

    def delete_asset(self, asset_id: str) -> Any:
        return self._client.delete(self._url(f"/v1/assets/{asset_id}"))

    def show_asset(self, asset_id: str) -> Dict:
        return self._client.get(self._url(f"/v1/assets/{asset_id}"))

    def batch_delete_assets(self, req: Dict) -> Dict:
        return self._client.post(self._url("/v1/assets/batch-delete"), json=req)

    @validate_params("create_version")
    def create_asset_version(self, asset_id: str, req: Dict) -> Dict:
        return self._client.post(self._url(f"/v1/assets/{asset_id}/versions"), json=req)

    def list_asset_versions(self, asset_id: str, **params) -> Dict:
        return self._client.get(self._url(f"/v1/assets/{asset_id}/versions"), params=params)

    def show_asset_version(self, asset_id: str, version_id: str) -> Dict:
        return self._client.get(self._url(f"/v1/assets/{asset_id}/versions/{version_id}"))

    @validate_params("update_version")
    def update_asset_version(self, asset_id: str, version_id: str, req: Dict) -> Dict:
        return self._client.put(self._url(f"/v1/assets/{asset_id}/versions/{version_id}"), json=req)

    def delete_asset_version(self, asset_id: str, version_id: str) -> Any:
        return self._client.delete(self._url(f"/v1/assets/{asset_id}/versions/{version_id}"))

    def batch_delete_asset_versions(self, asset_id: str, req: Dict) -> Dict:
        return self._client.post(self._url(f"/v1/assets/{asset_id}/versions/batch-delete"), json=req)

    def add_tags(self, asset_id: str, tags: List[str]) -> Dict:
        tags = self._validate_tags(tags)
        return self._client.post(self._url(f"/v1/assets/{asset_id}/tags"), json={"tags": tags})

    def delete_tag(self, asset_id: str, tag: str) -> Any:
        return self._client.delete(self._url(f"/v1/assets/{asset_id}/tags/{tag}"))

    def show_asset_tree(self, asset_id: str, version_id: str, query_type: str) -> Dict:
        return self._client.get(self._url(f"/v1/assets/{asset_id}/versions/{version_id}/tree"), params={"type": query_type})

    def check_asset_permission(self, asset_id: str, version_id: str, req: Dict) -> Dict:
        return self._client.post(self._url(f"/v1/assets/{asset_id}/versions/{version_id}/check-permission"), json=req)

    def list_asset_actions(self, asset_id: str, version_id: str) -> Dict:
        return self._client.get(self._url(f"/v1/assets/{asset_id}/versions/{version_id}/actions"))

    def create_asset_action(self, asset_id: str, version_id: str, req: Dict) -> Dict:
        body = req if "actions" in req else {"actions": [req]}
        return self._client.post(self._url(f"/v1/assets/{asset_id}/versions/{version_id}/actions"), json=body)

    def show_asset_action(self, asset_id: str, version_id: str, action: str) -> Dict:
        return self._client.get(self._url(f"/v1/assets/{asset_id}/versions/{version_id}/actions/{action}"))

    def update_asset_action(self, asset_id: str, version_id: str, action: str, req: Dict) -> Dict:
        return self._client.put(self._url(f"/v1/assets/{asset_id}/versions/{version_id}/actions/{action}"), json=req)

    def delete_asset_action(self, asset_id: str, version_id: str, action: str) -> Any:
        return self._client.delete(self._url(f"/v1/assets/{asset_id}/versions/{version_id}/actions/{action}"))

    def search_assets(self, req: Dict) -> Dict:
        return self._client.post(self._url("/v1/asset-service/search"), json=req)

    def list_publication_assets(self, **params) -> Dict:
        return self._client.get(self._url("/v1/asset-service/publication-assets"), params=params)

    def list_all_tags(self, **params) -> Dict:
        return self._client.get(self._url("/v1/asset-tags"), params=params)

    def _resolve_version(self, versions: Dict, version_id: str = None) -> Optional[Dict]:
        """Resolve version data from versions list by version_id."""
        data = versions.get("data", [])
        if not version_id:
            return data[0] if data else None
        for v in data:
            if v.get("id") == version_id or v.get("version_id") == version_id:
                return v
        return None

    def _get_all_valid_tags(self, asset_type: str = None, sub_type: str = None) -> Set[str]:
        cache_key = f"{asset_type or ''}:{sub_type or ''}"
        now = time.time()
        cached = self._tag_cache.get(cache_key)
        if cached is not None:
            tags, ts = cached
            if now - ts < self._tag_cache_ttl:
                return tags

        valid_tags = set()
        for lang in ("zh", "en"):
            params = {"language": lang}
            if asset_type:
                params["type"] = asset_type
            if sub_type:
                params["sub_type"] = sub_type
            result = self.list_all_tags(**params)
            if isinstance(result, dict) and "data" in result:
                for item in result["data"]:
                    tags_data = item.get("tags", {})
                    if isinstance(tags_data, dict) and "data" in tags_data:
                        for tag_item in tags_data["data"]:
                            if "tag" in tag_item:
                                valid_tags.add(tag_item["tag"])

        self._tag_cache[cache_key] = (valid_tags, now)
        return valid_tags

    def _validate_tags(self, tags: List[str], asset_type: str = None, sub_type: str = None) -> List[str]:
        if not tags:
            return []
        format_valid = [tag for tag in tags if isinstance(tag, str) and tag and TAG_PATTERN.match(tag)]
        format_invalid = [tag for tag in tags if isinstance(tag, str) and tag and not TAG_PATTERN.match(tag)]
        non_string = [tag for tag in tags if not isinstance(tag, str) or not tag]
        if non_string:
            logger.warning("Non-string or empty tags dropped: %s", non_string)
        if format_invalid:
            logger.warning("Tags with invalid format dropped: %s", format_invalid)
        valid_tags = self._get_all_valid_tags(asset_type, sub_type)
        validated = [tag for tag in format_valid if tag in valid_tags]
        not_in_server = [tag for tag in format_valid if tag not in valid_tags]
        if not_in_server:
            logger.warning("Tags not in server predefined list dropped: %s", not_in_server)
        return list(dict.fromkeys(validated))

    def import_asset(self, catalog_id: str, asset_type: str, local_path: str, name: str = None,
                     sub_type: str = None,  ext_metadata: Dict = None, asset_id: str = None,
                     version_id: str = None,
                     description: str = None, part_size: int = 9437184, enable_checkpoint: bool = True,
                     version: str = None, status: str = None, tags: list = None,
                     parent_asset_version_id: str = None, generation_method: str = None,
                     overwrite: bool = False) -> Dict:
        """Import asset: register asset (auto-creates version) and upload local folder to OBS.

        Args:
            catalog_id: Catalog ID
            name: Asset name
            asset_type: Asset type
            local_path: Local folder path to upload
            sub_type: Asset sub_type, optional
            ext_metadata: Extended metadata dict, optional
            asset_id: Asset ID, if provided will create new version instead of creating new asset
            version_id: Version ID, if provided with asset_id will reuse existing version (no new version created).
                        Use this to retry failed uploads or incrementally add new files without accumulating empty versions.
            description: Asset description, optional
            enable_checkpoint: Enable resumable upload, default True
            part_size: Part size in bytes for OBS upload, default 9MB
            version: Version string, optional (for create version)
            status: Asset status, optional
            tags: Tags list, optional (for create asset)
            parent_asset_version_id: Parent asset version ID, optional
            generation_method: Generation method, optional
            overwrite: Force overwrite existing OBS objects, default False (incremental upload in version-id mode).

        Returns:
            Version data dict from show_asset_version (contains version details from backend)
        """
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local path does not exist: {local_path}")
        if os.path.isfile(local_path):
            raise ValueError(f"Local path must be a directory, not a file: {local_path}")

        version_data = None

        if asset_id and version_id:
            existing = self.show_asset(asset_id)
            if not existing or (isinstance(existing, dict) and not existing.get("id") and not existing.get("asset_id")):
                raise ValueError(f"Asset with id {asset_id} does not exist")
            version_data = self.show_asset_version(asset_id, version_id)
            if not version_data or (isinstance(version_data, dict) and not version_data.get("id") and not version_data.get("version_id")):
                raise ValueError(f"Version with id {version_id} does not exist for asset {asset_id}")
        elif asset_id:
            existing = self.show_asset(asset_id)
            if not existing or (isinstance(existing, dict) and not existing.get("id") and not existing.get("asset_id")):
                raise ValueError(f"Asset with id {asset_id} does not exist")
            if ext_metadata is not None:
                errs = AssetValidator().validate_ext_metadata(
                    existing.get("type"), existing.get("sub_type"), ext_metadata)
                if errs:
                    raise ValidationError(errs)
            if tags is not None:
                logger.warning("Tags are asset-level, not version-level. Use add_tags() after import.")
            version_req = {}
            if description is not None:
                version_req["description"] = description
            if ext_metadata is not None:
                version_req["ext_metadata"] = ext_metadata
            if version is not None:
                version_req["version"] = version
            if status is not None:
                version_req["status"] = status
            if parent_asset_version_id is not None:
                version_req["parent_asset_version_id"] = parent_asset_version_id
            if generation_method is not None:
                version_req["generation_method"] = generation_method
            new_version = self.create_asset_version(asset_id, version_req)
            version_id = new_version.get("id") or new_version.get("version_id")
            if not version_id:
                raise RuntimeError(f"Failed to create new version for asset {asset_id}: API returned no version ID")
        else: 
            req = {"catalog_id": catalog_id, "type": asset_type}
            if name is not None:
                req["name"] = name
            if sub_type is not None:
                req["sub_type"] = sub_type
            if description is not None:
                req["description"] = description
            if ext_metadata is not None:
                req["ext_metadata"] = ext_metadata
            if status is not None:
                req["status"] = status
            if tags is not None:
                req["tags"] = self._validate_tags(tags, asset_type, sub_type)
            if parent_asset_version_id is not None:
                req["parent_asset_version_id"] = parent_asset_version_id
            if generation_method is not None:
                req["generation_method"] = generation_method
            asset = self.create_asset(req)
            asset_id = asset.get("id") or asset.get("asset_id")
            version_id = asset.get("latest_version_id")
            if not asset_id or not version_id:
                raise RuntimeError(f"Failed to create asset: API returned asset_id={asset_id}, version_id={version_id}")

        if not version_data:
            version_data = self.show_asset_version(asset_id, version_id)
        obs_url = self._extract_obs_url_from_version(version_data)

        if not obs_url or not obs_url.startswith("obs://"):
            raise RuntimeError(f"Invalid OBS URL for asset {asset_id} version {version_id}: {obs_url}")

        bucket_name = obs_url.replace("obs://", "").split("/")[0]
        prefix_start = len(f"obs://{bucket_name}/")
        obs_prefix = obs_url[prefix_start:] if len(obs_url) > prefix_start else ""
        if not obs_prefix:
            raise RuntimeError(f"OBS URL has no path prefix (bucket-only URL): {obs_url}")
        obs_client = OBSClient(self._client.config, bucket_name)
        if asset_id and version_id and not overwrite:
            logger.info("Incremental upload mode: existing OBS objects will be skipped (use overwrite=True to force)")
        success = obs_client.upload_folder(local_path, obs_prefix, show_progress=True,
                                            overwrite=overwrite, enable_checkpoint=enable_checkpoint, part_size=part_size)
        if not success:
            raise RuntimeError(f"Failed to upload folder to OBS: {obs_url}")

        version_data = self.show_asset_version(asset_id, version_id)
        if version_data.get("status") == "CREATING":
            try:
                self.update_asset_version(asset_id, version_id, {"status": "DRAFT"})
                version_data = self.show_asset_version(asset_id, version_id)
            except Exception as e:
                logger.warning("Failed to update version status CREATING→DRAFT: %s", e)

        return version_data

    def export_asset(self, asset_id: str, local_path: str, version_id: str = None,
                     enable_checkpoint: bool = True, part_size: int = 5242880) -> Dict:
        """Export asset: get OBS URL from latest version, download to local path.

        Args:
            asset_id: Asset ID
            local_path: Local target path
            version_id: Specific version to export, uses latest if not specified
            enable_checkpoint: Enable resumable download, default True
            part_size: Part size in bytes for OBS download, default 5MB

        Returns:
            Export result dict with asset_id, version_id, obs_url, local_path, status
        """
        versions = self.list_asset_versions(asset_id)
        if not versions or (isinstance(versions, dict) and not versions.get("data")):
            raise RuntimeError(f"No versions found for asset {asset_id}")

        if not version_id:
            version_data = self._resolve_version(versions)
        else:
            version_data = self._resolve_version(versions, version_id)
        if not version_data:
            raise RuntimeError(f"Version {version_id} not found for asset {asset_id}")

        resolved_version_id = version_data.get("id") or version_data.get("version_id")
        obs_url = self._extract_obs_url_from_version(version_data)

        if not obs_url or not obs_url.startswith("obs://"):
            raise RuntimeError(f"Invalid OBS URL for asset {asset_id} version {resolved_version_id}: {obs_url}")

        if os.path.isfile(local_path):
            raise ValueError(f"Local path must be a directory, not a file: {local_path}")

        asset_data = self.show_asset(asset_id)
        version_detail = self.show_asset_version(asset_id, resolved_version_id)

        export_path = os.path.join(local_path, asset_id)
        os.makedirs(export_path, exist_ok=True)

        bucket_name = obs_url.replace("obs://", "").split("/")[0]
        prefix_start = len(f"obs://{bucket_name}/")
        obs_prefix = obs_url[prefix_start:] if len(obs_url) > prefix_start else ""
        if not obs_prefix:
            raise RuntimeError(f"OBS URL has no path prefix (bucket-only URL): {obs_url}")
        obs_client = OBSClient(self._client.config, bucket_name)
        success = obs_client.download_folder(obs_prefix, export_path, show_progress=True,
                                              enable_checkpoint=enable_checkpoint, part_size=part_size)
        if not success:
            raise RuntimeError(f"Failed to download folder from OBS: {obs_url}")

        metadata = self._build_export_metadata(asset_data, version_detail)
        readme_path = self._write_readme(export_path, metadata)

        return {
            "asset_id": asset_id,
            "version_id": resolved_version_id,
            "obs_url": obs_url,
            "local_path": export_path,
            "readme_path": readme_path,
            "metadata": metadata,
            "status": "exported",
        }

    @staticmethod
    def _extract_obs_url_from_version(version_data: Dict) -> Optional[str]:
        """Extract OBS URL from a single version data dict, supporting nested keys."""
        for key in ("url", "obs_url", "file_url", "path", "obs_path"):
            value = version_data.get(key) or ""
            if value:
                return value
        for nested_key in ("storage", "file_info", "location"):
            nested = version_data.get(nested_key)
            if isinstance(nested, dict):
                for key in ("url", "obs_url", "path", "obs_path"):
                    value = nested.get(key) or ""
                    if value:
                        return value
        return None

    @staticmethod
    def _build_export_metadata(asset_data: Dict, version_data: Dict) -> Dict:
        """从资产和版本详情构建 frontmatter 字典（不包含 asset_id 和 catalog_id）。"""
        fm = {
            "name": asset_data.get("name"),
            "type": asset_data.get("type"),
        }
        if asset_data.get("tags"):
            fm["tags"] = asset_data["tags"]
        if asset_data.get("sub_type"):
            fm["sub_type"] = asset_data["sub_type"]
        fm["description"] = version_data.get("description") or asset_data.get("description")
        fm["status"] = version_data.get("status") or asset_data.get("status")
        ext_metadata = version_data.get("ext_metadata") or asset_data.get("ext_metadata")
        if ext_metadata:
            fm["ext_metadata"] = ext_metadata
        fm["version"] = version_data.get("version")
        if version_data.get("parent_asset_version_id"):
            fm["parent_asset_version_id"] = version_data["parent_asset_version_id"]
        if version_data.get("generation_method"):
            fm["generation_method"] = version_data["generation_method"]
        return {k: v for k, v in fm.items() if v not in (None, [], {}, "")}

    @staticmethod
    def _write_readme(local_path: str, metadata: Dict) -> str:
        """写入 README.md (frontmatter + body)，返回文件路径。

        如果 README.md 已存在，保留原有 body 部分，只替换 frontmatter。
        如果不存在，使用默认模板生成。
        """
        import yaml as _yaml

        readme_path = os.path.join(local_path, "README.md")
        fm_str = _yaml.dump(metadata, default_flow_style=False, allow_unicode=True, sort_keys=False)
        new_frontmatter = f"---\n{fm_str}---\n"

        if os.path.isfile(readme_path):
            with open(readme_path, "r", encoding="utf-8") as f:
                existing_content = f.read()
            body = AssetClient._extract_body_from_readme(existing_content)
            content = new_frontmatter + "\n" + body
        else:
            name = metadata.get("name", "Asset")
            desc = metadata.get("description", "")
            folder_name = os.path.basename(os.path.abspath(local_path))
            body_lines = [f"# {name}", ""]
            if desc:
                body_lines.extend([desc, ""])
            body_lines.extend([
                "## Import",
                "",
                "```bash",
                f"cloudrobo asset import-asset --catalog-id <your-catalog-id> --local-path ./{folder_name}",
                "```",
                "",
            ])
            content = new_frontmatter + "\n" + "\n".join(body_lines)

        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(content)
        return readme_path

    @staticmethod
    def _extract_body_from_readme(content: str) -> str:
        """从 README.md 内容中提取 body 部分（frontmatter 之后的内容）。

        如果没有 frontmatter，返回全部内容。
        """
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                return parts[2].lstrip("\n")
        return content
