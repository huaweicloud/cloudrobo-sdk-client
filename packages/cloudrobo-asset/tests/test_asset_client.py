import os
import time
import pytest
import tempfile
from unittest.mock import MagicMock, patch

from cloudrobo_core.sdk import Config, HttpClient
from cloudrobo_asset.client import AssetClient
from cloudrobo_asset.cli import _safe_parse_frontmatter


def _make_mock_client():
    mock = MagicMock(spec=HttpClient)
    mock.config = MagicMock(spec=Config)
    mock.config.get_endpoint.side_effect = lambda svc: f"https://api.example.com/{svc}"
    mock.config.project_id = "proj1"
    return mock


class TestRepository:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = AssetClient(self.mock_http)

    def test_list_repositories(self):
        self.mock_http.get.return_value = {"items": [{"id": "repo1", "name": "test"}]}
        result = self.client.list_repositories()
        self.mock_http.get.assert_called_once()
        assert "items" in result

    def test_list_repositories_with_name(self):
        self.mock_http.get.return_value = {"items": []}
        self.client.list_repositories(name="my-repo")
        call_args = self.mock_http.get.call_args
        assert call_args[1]["params"]["name"] == "my-repo"


class TestCatalog:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = AssetClient(self.mock_http)

    def test_list_catalogs(self):
        self.mock_http.get.return_value = {"items": []}
        result = self.client.list_catalogs(repository_id="repo1")
        self.mock_http.get.assert_called_once()
        assert "items" in result

    def test_show_catalog(self):
        self.mock_http.get.return_value = {"id": "cat1", "name": "catalog1"}
        result = self.client.show_catalog("cat1")
        assert result["id"] == "cat1"


class TestAsset:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = AssetClient(self.mock_http)

    def test_create_asset(self):
        self.mock_http.post.return_value = {"id": "asset1"}
        result = self.client.create_asset({
            "catalog_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "name": "model-a",
            "type": "model",
            "ext_metadata": {"model_type": "planning"},
        })
        assert result["id"] == "asset1"

    def test_list_assets_with_filters(self):
        self.mock_http.get.return_value = {"items": [], "total": 0}
        result = self.client.list_assets(type="model", status="RELEASE")
        self.mock_http.get.assert_called_once()

    def test_list_assets_no_filters(self):
        self.mock_http.get.return_value = {"items": [], "total": 0}
        result = self.client.list_assets()
        self.mock_http.get.assert_called_once()

    def test_show_asset(self):
        self.mock_http.get.return_value = {"id": "asset1", "name": "model-a"}
        result = self.client.show_asset("asset1")
        assert result["id"] == "asset1"

    def test_update_asset(self):
        self.mock_http.put.return_value = {"id": "asset1", "name": "new-name"}
        result = self.client.update_asset("asset1", {"name": "new-name"})
        self.mock_http.put.assert_called_once()
        assert result["name"] == "new-name"

    def test_delete_asset(self):
        self.mock_http.delete.return_value = ""
        self.client.delete_asset("asset1")
        self.mock_http.delete.assert_called_once()

    def test_batch_delete_assets(self):
        self.mock_http.post.return_value = {"count": 2}
        result = self.client.batch_delete_assets({"asset_ids": ["id1", "id2"]})
        assert result["count"] == 2


class TestAssetVersion:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = AssetClient(self.mock_http)

    def test_create_asset_version(self):
        self.mock_http.post.return_value = {"id": "v1", "version": "1.0.0"}
        result = self.client.create_asset_version("asset1", {"version": "1.0.0"})
        assert result["id"] == "v1"

    def test_list_asset_versions(self):
        self.mock_http.get.return_value = {"data": [{"id": "v1"}]}
        result = self.client.list_asset_versions("asset1")
        assert "data" in result

    def test_show_asset_version(self):
        self.mock_http.get.return_value = {"id": "v1", "version": "1.0.0"}
        result = self.client.show_asset_version("asset1", "v1")
        assert result["id"] == "v1"

    def test_update_asset_version(self):
        self.mock_http.put.return_value = {"id": "v1", "description": "updated"}
        result = self.client.update_asset_version("asset1", "v1", {"description": "updated"})
        self.mock_http.put.assert_called_once()
        assert result["description"] == "updated"

    def test_delete_asset_version(self):
        self.mock_http.delete.return_value = ""
        self.client.delete_asset_version("asset1", "v1")
        self.mock_http.delete.assert_called_once()

    def test_batch_delete_asset_versions(self):
        self.mock_http.post.return_value = {"count": 2}
        result = self.client.batch_delete_asset_versions("asset1", {"version_ids": ["v1", "v2"]})
        assert result["count"] == 2


class TestTag:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = AssetClient(self.mock_http)
        self.client._tag_cache[":"] = ({"tag1", "tag2"}, time.time())

    def test_add_tags(self):
        self.mock_http.post.return_value = {"tags": ["tag1", "tag2"]}
        result = self.client.add_tags("asset1", ["tag1", "tag2"])
        self.mock_http.post.assert_called_once()
        assert result["tags"] == ["tag1", "tag2"]

    def test_delete_tag(self):
        self.mock_http.delete.return_value = ""
        self.client.delete_tag("asset1", "tag1")
        self.mock_http.delete.assert_called_once()


class TestAssetTree:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = AssetClient(self.mock_http)

    def test_show_asset_tree(self):
        self.mock_http.get.return_value = {"nodes": []}
        result = self.client.show_asset_tree("asset1", "v1", "children")
        assert "nodes" in result


class TestPermission:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = AssetClient(self.mock_http)

    def test_check_asset_permission(self):
        self.mock_http.post.return_value = {"allowed": True}
        result = self.client.check_asset_permission("asset1", "v1", {"permissions": ["meta_read"]})
        self.mock_http.post.assert_called_once()
        assert result["allowed"] is True


class TestAction:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = AssetClient(self.mock_http)

    def test_list_asset_actions(self):
        self.mock_http.get.return_value = {"data": [{"action": "FFT"}]}
        result = self.client.list_asset_actions("asset1", "v1")
        assert "data" in result

    def test_create_asset_action(self):
        self.mock_http.post.return_value = {"action": "FFT", "status": "ENABLE"}
        result = self.client.create_asset_action("asset1", "v1", {"action": "FFT"})
        self.mock_http.post.assert_called_once()
        assert result["action"] == "FFT"

    def test_show_asset_action(self):
        self.mock_http.get.return_value = {"action": "FFT", "status": "ENABLE"}
        result = self.client.show_asset_action("asset1", "v1", "FFT")
        assert result["action"] == "FFT"

    def test_update_asset_action(self):
        self.mock_http.put.return_value = {"action": "FFT", "status": "DISABLE"}
        result = self.client.update_asset_action("asset1", "v1", "FFT", {"status": "DISABLE"})
        self.mock_http.put.assert_called_once()
        assert result["status"] == "DISABLE"

    def test_delete_asset_action(self):
        self.mock_http.delete.return_value = ""
        self.client.delete_asset_action("asset1", "v1", "FFT")
        self.mock_http.delete.assert_called_once()


class TestSearch:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = AssetClient(self.mock_http)

    def test_search_assets(self):
        self.mock_http.post.return_value = {"data": [{"id": "a1"}], "total": 1}
        result = self.client.search_assets({"keyword": "robot", "limit": 10})
        self.mock_http.post.assert_called_once()
        assert result["total"] == 1

    def test_list_publication_assets(self):
        self.mock_http.get.return_value = {"data": []}
        result = self.client.list_publication_assets(type="model", status="RELEASE")
        self.mock_http.get.assert_called_once()


class TestImportExport:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = AssetClient(self.mock_http)

    def test_import_asset_local_path_not_exist(self):
        with pytest.raises(FileNotFoundError, match="Local path does not exist"):
            self.client.import_asset(
                catalog_id="cat1", name="m1", asset_type="model",
                local_path="/nonexistent/path"
            )

    @patch("cloudrobo_asset.client.os.path.exists", return_value=True)
    @patch("cloudrobo_asset.client.OBSClient")
    def test_import_asset_new_asset(self, mock_obs_cls, mock_exists):
        self.mock_http.post.return_value = {"id": "asset1", "latest_version_id": "v1"}
        self.mock_http.get.return_value = {"id": "v1", "url": "obs://bucket/prefix"}
        mock_obs = MagicMock()
        mock_obs.upload_folder.return_value = True
        mock_obs_cls.return_value = mock_obs

        result = self.client.import_asset(
            catalog_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890", name="my-model", asset_type="model",
            local_path="./model-dir", ext_metadata={"model_type": "planning"}
        )
        assert result["id"] == "v1"

    @patch("cloudrobo_asset.client.os.path.exists", return_value=True)
    @patch("cloudrobo_asset.client.OBSClient")
    def test_import_asset_existing_asset(self, mock_obs_cls, mock_exists):
        self.mock_http.get.return_value = {"id": "v2", "url": "obs://bucket/prefix"}
        self.mock_http.post.return_value = {"id": "v2"}
        mock_obs = MagicMock()
        mock_obs.upload_folder.return_value = True
        mock_obs_cls.return_value = mock_obs

        result = self.client.import_asset(
            catalog_id="cat1", name="m1", asset_type="model",
            local_path="./model-dir", asset_id="asset1"
        )
        assert result["id"] == "v2"

    @patch("cloudrobo_asset.client.os.path.exists", return_value=True)
    @patch("cloudrobo_asset.client.OBSClient")
    def test_import_asset_obs_upload_fail(self, mock_obs_cls, mock_exists):
        self.mock_http.post.return_value = {"id": "asset1", "latest_version_id": "v1"}
        self.mock_http.get.return_value = {"id": "v1", "url": "obs://bucket/prefix"}
        mock_obs = MagicMock()
        mock_obs.upload_folder.return_value = False
        mock_obs_cls.return_value = mock_obs

        with pytest.raises(RuntimeError, match="Failed to upload folder to OBS"):
            self.client.import_asset(
                catalog_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890", name="my-model", asset_type="model",
                local_path="./model-dir", ext_metadata={"model_type": "planning"}
            )

    @patch("cloudrobo_asset.client.os.path.exists", return_value=True)
    @patch("cloudrobo_asset.client.OBSClient")
    def test_import_asset_mode3_default_incremental(self, mock_obs_cls, mock_exists):
        """Mode 3 (asset_id + version_id) 默认增量上传，overwrite=False"""
        self.mock_http.get.return_value = {"id": "v1", "url": "obs://bucket/prefix"}
        mock_obs = MagicMock()
        mock_obs.upload_folder.return_value = True
        mock_obs_cls.return_value = mock_obs

        result = self.client.import_asset(
            catalog_id="cat1", name="m1", asset_type="model",
            local_path="./model-dir", asset_id="asset1", version_id="v1",
            ext_metadata={"model_type": "planning"}
        )
        assert result["id"] == "v1"
        mock_obs.upload_folder.assert_called_once()
        call_kwargs = mock_obs.upload_folder.call_args[1]
        assert call_kwargs["overwrite"] is False

    @patch("cloudrobo_asset.client.os.path.exists", return_value=True)
    @patch("cloudrobo_asset.client.OBSClient")
    def test_import_asset_mode3_overwrite_true(self, mock_obs_cls, mock_exists):
        """Mode 3 传 overwrite=True 时强制覆盖"""
        self.mock_http.get.return_value = {"id": "v1", "url": "obs://bucket/prefix"}
        mock_obs = MagicMock()
        mock_obs.upload_folder.return_value = True
        mock_obs_cls.return_value = mock_obs

        result = self.client.import_asset(
            catalog_id="cat1", name="m1", asset_type="model",
            local_path="./model-dir", asset_id="asset1", version_id="v1",
            ext_metadata={"model_type": "planning"}, overwrite=True
        )
        assert result["id"] == "v1"
        mock_obs.upload_folder.assert_called_once()
        call_kwargs = mock_obs.upload_folder.call_args[1]
        assert call_kwargs["overwrite"] is True

    @patch("cloudrobo_asset.client.os.path.exists", return_value=True)
    @patch("cloudrobo_asset.client.OBSClient")
    def test_import_asset_creating_to_draft(self, mock_obs_cls, mock_exists):
        """上传成功后版本状态为 CREATING 时自动更新为 DRAFT"""
        self.mock_http.get.side_effect = [
            {"id": "asset1", "type": "model", "sub_type": None},  # show_asset
            {"id": "v1", "url": "obs://bucket/prefix", "status": "CREATING"},  # show_asset_version (before upload)
            {"id": "v1", "url": "obs://bucket/prefix", "status": "CREATING"},  # show_asset_version (after upload)
            {"id": "v1", "url": "obs://bucket/prefix", "status": "DRAFT"},  # show_asset_version (after update)
        ]
        self.mock_http.post.return_value = {"id": "asset1", "latest_version_id": "v1"}
        self.mock_http.put.return_value = {"id": "v1", "status": "DRAFT"}
        mock_obs = MagicMock()
        mock_obs.upload_folder.return_value = True
        mock_obs_cls.return_value = mock_obs

        result = self.client.import_asset(
            catalog_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890", name="my-model", asset_type="model",
            local_path="./model-dir", ext_metadata={"model_type": "planning"}
        )
        assert result["status"] == "DRAFT"
        self.mock_http.put.assert_called_once()
        put_kwargs = self.mock_http.put.call_args[1]
        assert put_kwargs["json"] == {"status": "DRAFT"}

    @patch("cloudrobo_asset.client.os.path.exists", return_value=True)
    @patch("cloudrobo_asset.client.OBSClient")
    def test_import_asset_preserves_user_status(self, mock_obs_cls, mock_exists):
        """用户指定了非 CREATING 状态时，上传后不自动更新"""
        self.mock_http.get.side_effect = [
            {"id": "asset1", "type": "model", "sub_type": None},  # show_asset
            {"id": "v1", "url": "obs://bucket/prefix", "status": "RELEASE"},  # show_asset_version (before upload)
            {"id": "v1", "url": "obs://bucket/prefix", "status": "RELEASE"},  # show_asset_version (after upload)
        ]
        self.mock_http.post.return_value = {"id": "asset1", "latest_version_id": "v1"}
        mock_obs = MagicMock()
        mock_obs.upload_folder.return_value = True
        mock_obs_cls.return_value = mock_obs

        result = self.client.import_asset(
            catalog_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890", name="my-model", asset_type="model",
            local_path="./model-dir", ext_metadata={"model_type": "planning"}, status="RELEASE"
        )
        assert result["status"] == "RELEASE"
        self.mock_http.put.assert_not_called()

    def test_export_asset_no_versions(self):
        self.mock_http.get.return_value = {"data": []}
        with pytest.raises(RuntimeError, match="No versions found"):
            self.client.export_asset(asset_id="asset1", local_path="./out")

    @patch("cloudrobo_asset.client.os.makedirs")
    @patch("cloudrobo_asset.client.OBSClient")
    def test_export_asset_success(self, mock_obs_cls, mock_makedirs):
        self.mock_http.get.side_effect = [
            {"data": [{"id": "v1", "url": "obs://bucket/prefix"}]},  # list_asset_versions
            {"id": "asset1", "name": "test-asset", "type": "model", "tags": ["tag1"]},  # show_asset
            {"id": "v1", "version": "1.0.0", "description": "test"},  # show_asset_version
        ]
        mock_obs = MagicMock()
        mock_obs.download_folder.return_value = True
        mock_obs_cls.return_value = mock_obs

        with patch("builtins.open", MagicMock()):
            result = self.client.export_asset(asset_id="asset1", local_path="./out")
        assert result["asset_id"] == "asset1"
        assert result["status"] == "exported"
        assert "readme_path" in result
        assert "metadata" in result
        assert result["local_path"] == os.path.join("./out", "asset1")
        mock_makedirs.assert_called_once_with(os.path.join("./out", "asset1"), exist_ok=True)

    @patch("cloudrobo_asset.client.os.makedirs")
    @patch("cloudrobo_asset.client.OBSClient")
    def test_export_asset_with_version_id(self, mock_obs_cls, mock_makedirs):
        self.mock_http.get.side_effect = [
            {"data": [{"id": "v2", "url": "obs://bucket/v2"}]},  # list_asset_versions
            {"id": "asset1", "name": "test-asset", "type": "model"},  # show_asset
            {"id": "v2", "version": "2.0.0"},  # show_asset_version
        ]
        mock_obs = MagicMock()
        mock_obs.download_folder.return_value = True
        mock_obs_cls.return_value = mock_obs

        with patch("builtins.open", MagicMock()):
            result = self.client.export_asset(asset_id="asset1", local_path="./out", version_id="v2")
        assert result["version_id"] == "v2"
        assert result["local_path"] == os.path.join("./out", "asset1")

    @patch("cloudrobo_asset.client.os.makedirs")
    @patch("cloudrobo_asset.client.OBSClient")
    def test_export_asset_obs_download_fail(self, mock_obs_cls, mock_makedirs):
        self.mock_http.get.side_effect = [
            {"data": [{"id": "v1", "url": "obs://bucket/prefix"}]},
            {"id": "asset1", "latest_version_id": "v1"},
            {"id": "v1", "version": "1.0.0"},
        ]
        mock_obs = MagicMock()
        mock_obs.download_folder.return_value = False
        mock_obs_cls.return_value = mock_obs

        with pytest.raises(RuntimeError, match="Failed to download folder from OBS"):
            self.client.export_asset(asset_id="asset1", local_path="./out")


class TestResolveVersion:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = AssetClient(self.mock_http)

    def test_resolve_version_by_id(self):
        versions = {"data": [{"id": "v1"}, {"id": "v2"}]}
        result = self.client._resolve_version(versions, "v2")
        assert result["id"] == "v2"

    def test_resolve_version_first_when_no_id(self):
        versions = {"data": [{"id": "v1"}, {"id": "v2"}]}
        result = self.client._resolve_version(versions, None)
        assert result["id"] == "v1"

    def test_resolve_version_empty_data(self):
        versions = {"data": []}
        result = self.client._resolve_version(versions, "v1")
        assert result is None

    def test_resolve_version_not_found(self):
        versions = {"data": [{"id": "v1"}]}
        result = self.client._resolve_version(versions, "v99")
        assert result is None


class TestExtractObsUrl:
    def test_extract_obs_url_from_version_top_level(self):
        version = {"url": "obs://bucket/path"}
        result = AssetClient._extract_obs_url_from_version(version)
        assert result == "obs://bucket/path"

    def test_extract_obs_url_from_version_nested(self):
        version = {"storage": {"url": "obs://bucket/path"}}
        result = AssetClient._extract_obs_url_from_version(version)
        assert result == "obs://bucket/path"

    def test_extract_obs_url_from_version_not_found(self):
        version = {"id": "v1"}
        result = AssetClient._extract_obs_url_from_version(version)
        assert result is None


class TestBuildExportMetadata:
    def test_build_export_metadata_basic(self):
        asset_data = {
            "name": "test-model",
            "type": "model",
            "tags": ["tag1", "tag2"],
            "description": "asset description",
            "status": "RELEASE",
        }
        version_data = {
            "version": "1.0.0",
            "description": "version description",
            "status": "DRAFT",
        }
        result = AssetClient._build_export_metadata(asset_data, version_data)
        assert result["name"] == "test-model"
        assert result["type"] == "model"
        assert result["tags"] == ["tag1", "tag2"]
        assert result["version"] == "1.0.0"
        assert result["description"] == "version description"  # version overrides asset
        assert result["status"] == "DRAFT"  # version overrides asset
        assert "asset_id" not in result
        assert "catalog_id" not in result

    def test_build_export_metadata_with_optional_fields(self):
        asset_data = {
            "name": "test-simulation",
            "type": "simulation",
            "sub_type": "robot",
            "tags": [],
        }
        version_data = {
            "version": "2.0.0",
            "parent_asset_version_id": "parent-uuid",
            "generation_method": "training",
        }
        result = AssetClient._build_export_metadata(asset_data, version_data)
        assert result["sub_type"] == "robot"
        assert result["parent_asset_version_id"] == "parent-uuid"
        assert result["generation_method"] == "training"
        assert "tags" not in result  # empty tags should not be included
        assert result["version"] == "2.0.0"  # version from version_data

    def test_build_export_metadata_fallback_to_asset(self):
        asset_data = {
            "name": "test",
            "type": "dataset",
            "description": "asset desc",
            "status": "RELEASE",
            "ext_metadata": {"key": "value"},
        }
        version_data = {
            "version": "1.0.0",
        }
        result = AssetClient._build_export_metadata(asset_data, version_data)
        assert result["description"] == "asset desc"
        assert result["status"] == "RELEASE"
        assert result["ext_metadata"] == {"key": "value"}

    def test_build_export_metadata_filters_empty_values(self):
        asset_data = {
            "name": "test",
            "type": "model",
            "tags": [],
            "description": "",
            "status": None,
            "ext_metadata": {},
        }
        version_data = {
            "version": "",
        }
        result = AssetClient._build_export_metadata(asset_data, version_data)
        assert result["name"] == "test"
        assert result["type"] == "model"
        assert "tags" not in result
        assert "description" not in result
        assert "status" not in result
        assert "ext_metadata" not in result
        assert "version" not in result


class TestImportAssetWithNewParams:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = AssetClient(self.mock_http)
        self.client._tag_cache[":"] = ({"tag1", "tag2"}, time.time())
        self.client._tag_cache["model:"] = ({"tag1", "tag2"}, time.time())

    @patch("cloudrobo_asset.client.os.path.exists", return_value=True)
    @patch("cloudrobo_asset.client.OBSClient")
    def test_import_asset_with_status_and_tags(self, mock_obs_cls, mock_exists):
        self.mock_http.post.return_value = {"id": "asset1", "latest_version_id": "v1"}
        self.mock_http.get.return_value = {"id": "v1", "url": "obs://bucket/prefix"}
        mock_obs = MagicMock()
        mock_obs.upload_folder.return_value = True
        mock_obs_cls.return_value = mock_obs

        result = self.client.import_asset(
            catalog_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            name="my-model",
            asset_type="model",
            local_path="./model-dir",
            status="RELEASE",
            tags=["tag1", "tag2"],
            ext_metadata={"model_type": "planning"},
        )
        assert result["id"] == "v1"
        call_args = self.mock_http.post.call_args
        req = call_args[1]["json"]
        assert req["status"] == "RELEASE"
        assert req["tags"] == ["tag1", "tag2"]

    @patch("cloudrobo_asset.client.os.path.exists", return_value=True)
    @patch("cloudrobo_asset.client.OBSClient")
    def test_import_asset_new_version_with_params(self, mock_obs_cls, mock_exists):
        self.mock_http.get.return_value = {"id": "v2", "url": "obs://bucket/prefix"}
        self.mock_http.post.return_value = {"id": "v2"}
        mock_obs = MagicMock()
        mock_obs.upload_folder.return_value = True
        mock_obs_cls.return_value = mock_obs

        result = self.client.import_asset(
            catalog_id="cat1",
            name="m1",
            asset_type="model",
            local_path="./model-dir",
            asset_id="asset1",
            version="2.0.0",
            status="DRAFT",
            parent_asset_version_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            generation_method="training",
        )
        assert result["id"] == "v2"
        call_args = self.mock_http.post.call_args
        req = call_args[1]["json"]
        assert req["version"] == "2.0.0"
        assert req["status"] == "DRAFT"
        assert req["parent_asset_version_id"] == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        assert req["generation_method"] == "training"


class TestSafeParseFrontmatter:
    """测试 _safe_parse_frontmatter 函数的各种场景"""

    def test_parse_frontmatter_with_valid_file(self):
        """测试解析包含有效 frontmatter 的 README.md"""
        with tempfile.TemporaryDirectory() as tmpdir:
            readme_path = os.path.join(tmpdir, "README.md")
            content = """---
name: test-asset
type: model
description: Test model
tags:
  - tag1
  - tag2
---
# Test Asset
This is a test asset.
"""
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(content)

            result = _safe_parse_frontmatter(tmpdir)
            assert result["name"] == "test-asset"
            assert result["type"] == "model"
            assert result["description"] == "Test model"
            assert result["tags"] == ["tag1", "tag2"]

    def test_parse_frontmatter_without_readme(self):
        """测试目录中没有 README.md 的情况"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _safe_parse_frontmatter(tmpdir)
            assert result == {}

    def test_parse_frontmatter_with_invalid_yaml(self):
        """测试 README.md 包含无效 YAML 的情况"""
        with tempfile.TemporaryDirectory() as tmpdir:
            readme_path = os.path.join(tmpdir, "README.md")
            content = """---
name: test-asset
invalid yaml: [unclosed
---
# Test
"""
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(content)

            result = _safe_parse_frontmatter(tmpdir)
            assert result == {}

    def test_parse_frontmatter_without_frontmatter(self):
        """测试 README.md 没有 frontmatter 的情况"""
        with tempfile.TemporaryDirectory() as tmpdir:
            readme_path = os.path.join(tmpdir, "README.md")
            content = """# Test Asset
This is a test asset without frontmatter.
"""
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(content)

            result = _safe_parse_frontmatter(tmpdir)
            assert result == {}

    def test_parse_frontmatter_with_complex_metadata(self):
        """测试包含复杂 ext_metadata 的 frontmatter"""
        with tempfile.TemporaryDirectory() as tmpdir:
            readme_path = os.path.join(tmpdir, "README.md")
            content = """---
name: complex-asset
type: algorithm
ext_metadata:
  engine:
    image_url: swr.cn-southwest-2.myhuaweicloud.com/xxx/yyy:latest
  command: python main.py
  environment_variables:
    - key: ENV_VAR
      value: test_value
---
# Complex Asset
"""
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(content)

            result = _safe_parse_frontmatter(tmpdir)
            assert result["name"] == "complex-asset"
            assert result["type"] == "algorithm"
            assert "ext_metadata" in result
            assert result["ext_metadata"]["engine"]["image_url"] == "swr.cn-southwest-2.myhuaweicloud.com/xxx/yyy:latest"


class TestWriteReadme:
    """测试 _write_readme 方法的各种场景"""

    def test_write_readme_create_new(self):
        """测试创建新的 README.md"""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata = {
                "name": "test-asset",
                "type": "model",
                "description": "Test description",
                "tags": ["tag1", "tag2"],
            }
            result = AssetClient._write_readme(tmpdir, metadata)
            assert os.path.isfile(result)
            with open(result, "r", encoding="utf-8") as f:
                content = f.read()
            assert content.startswith("---")
            assert "name: test-asset" in content
            assert "type: model" in content
            assert "# test-asset" in content
            assert "Test description" in content

    def test_write_readme_preserve_existing_body_with_frontmatter(self):
        """测试已有 README.md 包含 frontmatter 时，保留 body 并替换 frontmatter"""
        with tempfile.TemporaryDirectory() as tmpdir:
            readme_path = os.path.join(tmpdir, "README.md")
            existing_content = """---
name: old-name
type: old-type
---
# Custom Title

This is custom body content that should be preserved.

## Another Section

More content here.
"""
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(existing_content)

            metadata = {
                "name": "new-name",
                "type": "model",
                "description": "New description",
            }
            result = AssetClient._write_readme(tmpdir, metadata)
            with open(result, "r", encoding="utf-8") as f:
                content = f.read()

            assert "name: new-name" in content
            assert "type: model" in content
            assert "name: old-name" not in content
            assert "# Custom Title" in content
            assert "This is custom body content that should be preserved." in content
            assert "## Another Section" in content
            assert "More content here." in content

    def test_write_readme_preserve_existing_without_frontmatter(self):
        """测试已有 README.md 不包含 frontmatter 时，保留全部内容并添加 frontmatter"""
        with tempfile.TemporaryDirectory() as tmpdir:
            readme_path = os.path.join(tmpdir, "README.md")
            existing_content = """# My Custom README

This is my original content without any frontmatter.

## Features

- Feature 1
- Feature 2
"""
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(existing_content)

            metadata = {
                "name": "exported-asset",
                "type": "model",
                "tags": ["tag1"],
            }
            result = AssetClient._write_readme(tmpdir, metadata)
            with open(result, "r", encoding="utf-8") as f:
                content = f.read()

            assert content.startswith("---")
            assert "name: exported-asset" in content
            assert "# My Custom README" in content
            assert "This is my original content without any frontmatter." in content
            assert "## Features" in content
            assert "- Feature 1" in content


class TestExtMetadataValidation:
    """测试 validate_create_asset 中 ext_metadata 缺失校验"""

    def setup_method(self):
        from cloudrobo_asset.validators import AssetValidator
        self.validator = AssetValidator()

    def test_model_missing_ext_metadata(self):
        """model 类型缺少 ext_metadata 时应报错"""
        errors = self.validator.validate_create_asset({
            "catalog_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "name": "my-model",
            "type": "model",
        })
        assert any("ext_metadata is required" in e for e in errors)

    def test_dataset_missing_ext_metadata(self):
        """dataset 类型缺少 ext_metadata 时应报错"""
        errors = self.validator.validate_create_asset({
            "catalog_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "name": "my-dataset",
            "type": "dataset",
        })
        assert any("ext_metadata is required" in e for e in errors)

    def test_simulation_robot_missing_ext_metadata(self):
        """simulation/robot 类型缺少 ext_metadata 时应报错"""
        errors = self.validator.validate_create_asset({
            "catalog_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "name": "my-sim",
            "type": "simulation",
            "sub_type": "robot",
        })
        assert any("ext_metadata is required" in e for e in errors)
        assert any("robot_type" in e for e in errors)
        assert any("robot_manufacturer" in e for e in errors)

    def test_simulation_no_subtype_no_ext_metadata_ok(self):
        """simulation 无 sub_type 时 ext_metadata 缺失不报错"""
        errors = self.validator.validate_create_asset({
            "catalog_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "name": "my-sim",
            "type": "simulation",
            "sub_type": "environment",
        })
        assert not any("ext_metadata is required" in e for e in errors)

    def test_model_with_valid_ext_metadata(self):
        """model 类型提供有效 ext_metadata 时不报错"""
        errors = self.validator.validate_create_asset({
            "catalog_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "name": "my-model",
            "type": "model",
            "ext_metadata": {"model_type": "planning"},
        })
        assert not any("ext_metadata is required" in e for e in errors)

    def test_model_with_invalid_ext_metadata(self):
        """model 类型提供无效 ext_metadata 时应报错"""
        errors = self.validator.validate_create_asset({
            "catalog_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "name": "my-model",
            "type": "model",
            "ext_metadata": {"model_type": "invalid_type"},
        })
        assert any("model_type" in e and "must be one of" in e for e in errors)


class TestImportAssetExtMetadataValidation:
    """测试 import_asset Case 2 (新建版本) 中 ext_metadata 校验"""

    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = AssetClient(self.mock_http)

    @patch("cloudrobo_asset.client.os.path.exists", return_value=True)
    @patch("cloudrobo_asset.client.OBSClient")
    def test_import_asset_new_version_invalid_ext_metadata(self, mock_obs_cls, mock_exists):
        """新建版本时 ext_metadata 无效应报 ValidationError"""
        from cloudrobo_asset.validators import ValidationError

        self.mock_http.get.return_value = {
            "id": "asset1", "type": "model", "sub_type": None
        }
        with pytest.raises(ValidationError, match="model_type"):
            self.client.import_asset(
                catalog_id="cat1", name="m1", asset_type="model",
                local_path="./model-dir", asset_id="asset1",
                ext_metadata={"model_type": "invalid_type"},
            )

    @patch("cloudrobo_asset.client.os.path.exists", return_value=True)
    @patch("cloudrobo_asset.client.OBSClient")
    def test_import_asset_new_version_valid_ext_metadata(self, mock_obs_cls, mock_exists):
        """新建版本时 ext_metadata 有效应正常通过"""
        from cloudrobo_asset.validators import ValidationError

        self.mock_http.get.return_value = {
            "id": "v2", "url": "obs://bucket/prefix"
        }
        self.mock_http.post.return_value = {"id": "v2"}
        mock_obs = MagicMock()
        mock_obs.upload_folder.return_value = True
        mock_obs_cls.return_value = mock_obs

        result = self.client.import_asset(
            catalog_id="cat1", name="m1", asset_type="model",
            local_path="./model-dir", asset_id="asset1",
            ext_metadata={"model_type": "planning"},
        )
        assert result["id"] == "v2"

    @patch("cloudrobo_asset.client.os.path.exists", return_value=True)
    @patch("cloudrobo_asset.client.OBSClient")
    def test_import_asset_new_version_no_ext_metadata_ok(self, mock_obs_cls, mock_exists):
        """新建版本时不传 ext_metadata 应正常通过（不校验缺失）"""
        self.mock_http.get.return_value = {
            "id": "v2", "url": "obs://bucket/prefix"
        }
        self.mock_http.post.return_value = {"id": "v2"}
        mock_obs = MagicMock()
        mock_obs.upload_folder.return_value = True
        mock_obs_cls.return_value = mock_obs

        result = self.client.import_asset(
            catalog_id="cat1", name="m1", asset_type="model",
            local_path="./model-dir", asset_id="asset1",
        )
        assert result["id"] == "v2"


class TestCliImportAssetExtMetadata:
    """测试 CLI import-asset 命令的 ext_metadata 前置校验"""

    def test_cli_import_model_missing_ext_metadata(self):
        """model 类型缺少 ext_metadata 时 CLI 应报错"""
        from click.testing import CliRunner
        from cloudrobo_asset.cli import asset

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(asset, [
                "import-asset",
                "--catalog-id", "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "--name", "my-model",
                "--type", "model",
                "--local-path", tmpdir,
                "--dry-run",
            ])
            assert result.exit_code != 0
            assert "ext_metadata is required" in result.output
            assert "model_type" in result.output

    def test_cli_import_model_with_ext_metadata_cli(self):
        """model 类型通过 --ext-metadata 提供 ext_metadata 时应通过"""
        from click.testing import CliRunner
        from cloudrobo_asset.cli import asset

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(asset, [
                "import-asset",
                "--catalog-id", "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "--name", "my-model",
                "--type", "model",
                "--local-path", tmpdir,
                "--ext-metadata", '{"model_type": "planning"}',
                "--dry-run",
            ])
            assert result.exit_code == 0
            assert "DRY-RUN" in result.output

    def test_cli_import_model_with_ext_metadata_frontmatter(self):
        """model 类型通过 README.md frontmatter 提供 ext_metadata 时应通过"""
        from click.testing import CliRunner
        from cloudrobo_asset.cli import asset

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            readme_path = os.path.join(tmpdir, "README.md")
            content = """---
name: my-model
type: model
ext_metadata:
  model_type: planning
---
# My Model
"""
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(content)

            result = runner.invoke(asset, [
                "import-asset",
                "--catalog-id", "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "--local-path", tmpdir,
                "--dry-run",
            ])
            assert result.exit_code == 0
            assert "DRY-RUN" in result.output

    def test_cli_import_dataset_missing_ext_metadata(self):
        """dataset 类型缺少 ext_metadata 时 CLI 应报错"""
        from click.testing import CliRunner
        from cloudrobo_asset.cli import asset

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(asset, [
                "import-asset",
                "--catalog-id", "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "--name", "my-dataset",
                "--type", "dataset",
                "--local-path", tmpdir,
                "--dry-run",
            ])
            assert result.exit_code != 0
            assert "ext_metadata is required" in result.output
            assert "annotation_status" in result.output

    def test_cli_import_simulation_robot_missing_ext_metadata(self):
        """simulation/robot 类型缺少 ext_metadata 时 CLI 应报错"""
        from click.testing import CliRunner
        from cloudrobo_asset.cli import asset

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(asset, [
                "import-asset",
                "--catalog-id", "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "--name", "my-sim",
                "--type", "simulation",
                "--sub-type", "robot",
                "--local-path", tmpdir,
                "--dry-run",
            ])
            assert result.exit_code != 0
            assert "ext_metadata is required" in result.output
            assert "robot_type" in result.output

    def test_cli_import_with_asset_id_skips_ext_metadata_check(self):
        """使用 --asset-id 创建新版本时跳过 ext_metadata 前置校验"""
        from click.testing import CliRunner
        from cloudrobo_asset.cli import asset

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(asset, [
                "import-asset",
                "--asset-id", "asset1",
                "--local-path", tmpdir,
                "--dry-run",
            ])
            assert result.exit_code == 0
            assert "DRY-RUN" in result.output
