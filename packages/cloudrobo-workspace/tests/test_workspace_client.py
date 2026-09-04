import json
import pytest
from unittest.mock import MagicMock, patch, mock_open

from cloudrobo_core.sdk import Config, HttpClient
from cloudrobo_core.sdk.exceptions import PathTraversalError
from cloudrobo_workspace.client import WorkspaceClient, WorkspaceError, is_debug_mode


def _make_mock_client():
    mock = MagicMock(spec=HttpClient)
    mock.config = MagicMock(spec=Config)
    mock.config.get_endpoint.side_effect = lambda svc: f"https://api.example.com/{svc}"
    mock.config.project_id = "proj1"
    return mock


class TestWorkspaceClient:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = WorkspaceClient(self.mock_http)

    def test_create_workspace(self):
        self.mock_http.post.return_value = {"workspace": {"workspace_id": "ws1"}}
        result = self.client.create_workspace({"name": "my-workspace", "default_obs_path": "obs://bucket/path"})
        assert result["workspace"]["workspace_id"] == "ws1"
        self.mock_http.post.assert_called_once()
        args, kwargs = self.mock_http.post.call_args
        assert args[0].endswith("/v1/workspaces")

    def test_list_workspaces(self):
        self.mock_http.get.return_value = {"workspaces": [], "page_info": {"total": 0}}
        result = self.client.list_workspaces()
        assert "workspaces" in result

    def test_list_workspaces_with_pagination(self):
        self.mock_http.get.return_value = {"workspaces": [], "page_info": {"total": 0}}
        result = self.client.list_workspaces(limit=10, offset=0)
        assert "workspaces" in result
        args, kwargs = self.mock_http.get.call_args
        assert kwargs.get("params", {}).get("limit") == 10
        assert kwargs.get("params", {}).get("offset") == 0

    def test_show_workspace(self):
        self.mock_http.get.return_value = {"workspace": {"workspace_id": "ws1", "name": "my-workspace"}}
        result = self.client.show_workspace("ws1")
        assert result["workspace"]["workspace_id"] == "ws1"

    def test_update_workspace(self):
        self.mock_http.put.return_value = {"workspace": {"workspace_id": "ws1", "name": "updated"}}
        result = self.client.update_workspace("ws1", {"name": "updated"})
        assert result["workspace"]["name"] == "updated"
        args, kwargs = self.mock_http.put.call_args
        assert args[0].endswith("/v1/workspaces/ws1")

    def test_delete_workspace(self):
        self.mock_http.delete.return_value = ""
        self.client.delete_workspace("ws1")
        self.mock_http.delete.assert_called_once()
        args, _ = self.mock_http.delete.call_args
        assert args[0].endswith("/v1/workspaces/ws1")

    def test_add_workspace_members(self):
        self.mock_http.post.return_value = {"members": []}
        result = self.client.add_workspace_members("ws1", {"member_list": [{"user_id": "u1", "role_ids": ["r1"]}]})
        assert "members" in result
        args, kwargs = self.mock_http.post.call_args
        assert args[0].endswith("/v1/workspaces/ws1/members")

    def test_list_workspace_members(self):
        self.mock_http.get.return_value = {"members": []}
        result = self.client.list_workspace_members("ws1")
        assert "members" in result

    def test_update_workspace_member(self):
        self.mock_http.put.return_value = {"member": {"user_id": "u1"}}
        result = self.client.update_workspace_member("ws1", {"user_id": "u1", "role_ids": ["r1"]})
        assert result["member"]["user_id"] == "u1"
        args, kwargs = self.mock_http.put.call_args
        assert args[0].endswith("/v1/workspaces/ws1/members")

    def test_delete_workspace_members(self):
        self.mock_http.delete.return_value = ""
        self.client.delete_workspace_members("ws1", ["u1", "u2"])
        self.mock_http.delete.assert_called_once()
        args, kwargs = self.mock_http.delete.call_args
        assert args[0].endswith("/v1/workspaces/ws1/members")
        assert kwargs.get("params", {}).get("user_ids") == ["u1", "u2"]

    def test_get_workspace_overview(self):
        self.mock_http.get.return_value = {
            "workspace_capacity": 10,
            "workspace_used": 3,
            "workspace_available": 7,
            "member_capacity": 100,
            "member_count": 15,
        }
        result = self.client.get_workspace_overview()
        assert result["workspace_capacity"] == 10
        assert result["member_count"] == 15
        args, _ = self.mock_http.get.call_args
        assert args[0].endswith("/v1/workspaces/statistic/overview")

    def test_update_workspace_with_bind_obs_policy(self):
        self.mock_http.put.return_value = {"workspace": {"workspace_id": "ws1"}}
        result = self.client.update_workspace("ws1", {"bind_obs_policy": True})
        assert result["workspace"]["workspace_id"] == "ws1"
        args, kwargs = self.mock_http.put.call_args
        assert kwargs["json"] == {"bind_obs_policy": True}

    def test_show_workspace_with_obs_status(self):
        self.mock_http.get.return_value = {
            "workspace": {"workspace_id": "ws1", "obs_status": "AVAILABLE"}
        }
        result = self.client.show_workspace("ws1")
        assert result["workspace"]["obs_status"] == "AVAILABLE"

    def test_list_workspaces_with_obs_status(self):
        self.mock_http.get.return_value = {
            "workspaces": [
                {"workspace_id": "ws1", "obs_status": "AVAILABLE"},
                {"workspace_id": "ws2", "obs_status": "NOT_EXIST"},
            ],
            "page_info": {"total": 2},
        }
        result = self.client.list_workspaces()
        assert result["workspaces"][0]["obs_status"] == "AVAILABLE"
        assert result["workspaces"][1]["obs_status"] == "NOT_EXIST"

    def test_show_workspace_without_obs_status(self):
        self.mock_http.get.return_value = {
            "workspace": {"workspace_id": "ws1", "name": "my-workspace"}
        }
        result = self.client.show_workspace("ws1")
        assert "obs_status" not in result["workspace"]
        assert result["workspace"]["name"] == "my-workspace"


class TestWorkspaceConfig:
    @patch("cloudrobo_workspace.config.WORKSPACE_PATH")
    def test_load_workspace(self, mock_path):
        from cloudrobo_workspace.config import load_workspace
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps({
            "workspace_id": "ws1",
            "name": "test",
            "asset_catalog_id": "cat1",
            "default_obs_path": "obs://bucket/path",
        })
        result = load_workspace()
        assert result["workspace_id"] == "ws1"
        assert result["name"] == "test"

    @patch("cloudrobo_workspace.config.WORKSPACE_PATH")
    def test_load_workspace_file_not_exists(self, mock_path):
        from cloudrobo_workspace.config import load_workspace
        mock_path.exists.return_value = False
        result = load_workspace()
        assert result == {}

    @patch("cloudrobo_workspace.config.WORKSPACE_PATH")
    def test_load_workspace_invalid_json(self, mock_path):
        from cloudrobo_workspace.config import load_workspace
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "invalid json"
        result = load_workspace()
        assert result == {}

    @patch("cloudrobo_workspace.config.WORKSPACE_PATH")
    @patch("cloudrobo_workspace.config.WORKSPACE_CONFIG_DIR")
    def test_save_workspace(self, mock_dir, mock_path):
        import os
        from cloudrobo_workspace.config import save_workspace
        mock_dir.mkdir = MagicMock()
        mock_path.write_text = MagicMock()
        with patch("os.chmod"):
            save_workspace({
                "workspace_id": "ws1",
                "name": "test",
                "asset_catalog_id": "cat1",
                "default_obs_path": "obs://bucket/path",
            })
        mock_dir.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_path.write_text.assert_called_once()
        written = mock_path.write_text.call_args[0][0]
        parsed = json.loads(written)
        assert parsed["workspace_id"] == "ws1"
        assert parsed["name"] == "test"


class TestWorkspaceClientValidation:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = WorkspaceClient(self.mock_http)

    def test_show_workspace_rejects_empty_id(self):
        with pytest.raises(PathTraversalError, match="workspace_id"):
            self.client.show_workspace("")

    def test_show_workspace_rejects_none_id(self):
        with pytest.raises(PathTraversalError, match="workspace_id"):
            self.client.show_workspace(None)

    def test_show_workspace_rejects_path_traversal(self):
        for bad in ["../ws1", "ws/1", "ws\\1"]:
            with pytest.raises(PathTraversalError, match="path traversal"):
                self.client.show_workspace(bad)

    def test_update_workspace_rejects_empty_id(self):
        with pytest.raises(PathTraversalError):
            self.client.update_workspace("", {"name": "x"})

    def test_delete_workspace_rejects_path_traversal(self):
        with pytest.raises(PathTraversalError):
            self.client.delete_workspace("../etc")

    def test_list_workspace_members_rejects_empty_id(self):
        with pytest.raises(PathTraversalError, match="workspace_id"):
            self.client.list_workspace_members("")

    def test_add_workspace_members_rejects_empty_id(self):
        with pytest.raises(PathTraversalError):
            self.client.add_workspace_members("", {"member_list": []})

    def test_add_workspace_members_rejects_empty_member_list(self):
        with pytest.raises(WorkspaceError, match="member_list"):
            self.client.add_workspace_members("ws1", {"foo": "bar"})

    def test_add_workspace_members_rejects_none_req(self):
        with pytest.raises(WorkspaceError, match="请求体不能为空"):
            self.client.add_workspace_members("ws1", None)

    def test_add_workspace_members_rejects_non_list_member_list(self):
        with pytest.raises(WorkspaceError, match="member_list 必须为列表"):
            self.client.add_workspace_members("ws1", {"member_list": "not-a-list"})

    def test_update_workspace_member_rejects_empty_id(self):
        with pytest.raises(PathTraversalError):
            self.client.update_workspace_member("", {"user_id": "u1", "role_ids": ["r1"]})

    def test_update_workspace_member_requires_user_id(self):
        with pytest.raises(WorkspaceError, match="user_id"):
            self.client.update_workspace_member("ws1", {"role_ids": ["r1"]})

    def test_update_workspace_member_rejects_whitespace_user_id(self):
        with pytest.raises(WorkspaceError, match="user_id 不能为空或纯空格"):
            self.client.update_workspace_member("ws1", {"user_id": "   ", "role_ids": ["r1"]})

    def test_update_workspace_member_requires_role_ids(self):
        with pytest.raises(WorkspaceError, match="role_ids"):
            self.client.update_workspace_member("ws1", {"user_id": "u1"})

    def test_delete_workspace_members_rejects_empty_id(self):
        with pytest.raises(PathTraversalError):
            self.client.delete_workspace_members("", ["u1"])

    def test_delete_workspace_members_rejects_empty_list(self):
        with pytest.raises(WorkspaceError, match="user_ids"):
            self.client.delete_workspace_members("ws1", [])

    def test_create_workspace_rejects_empty_req(self):
        with pytest.raises(WorkspaceError, match="请求体不能为空"):
            self.client.create_workspace({})

    def test_create_workspace_rejects_none_req(self):
        with pytest.raises(WorkspaceError, match="请求体不能为空"):
            self.client.create_workspace(None)

    def test_create_workspace_requires_name(self):
        with pytest.raises(WorkspaceError, match="name"):
            self.client.create_workspace({"default_obs_path": "obs://b/p"})

    def test_create_workspace_requires_default_obs_path(self):
        with pytest.raises(WorkspaceError, match="default_obs_path"):
            self.client.create_workspace({"name": "n"})

    def test_update_workspace_rejects_empty_req(self):
        with pytest.raises(WorkspaceError, match="请求体不能为空"):
            self.client.update_workspace("ws1", {})

    def test_update_workspace_rejects_none_req(self):
        with pytest.raises(WorkspaceError, match="请求体不能为空"):
            self.client.update_workspace("ws1", None)

    def test_valid_id_not_blocked(self):
        self.mock_http.get.return_value = {"workspace": {"workspace_id": "ws-001"}}
        result = self.client.show_workspace("ws-001")
        assert result["workspace"]["workspace_id"] == "ws-001"
        self.mock_http.get.assert_called_once()

    def test_workspace_error_with_suggestion(self):
        try:
            self.client.create_workspace({"name": "n"})
        except WorkspaceError as e:
            assert e.suggestion
            assert "建议" in e.get_user_message()
        else:
            pytest.fail("Should have raised WorkspaceError")

    def test_is_debug_mode_importable(self):
        assert callable(is_debug_mode)

    def test_create_workspace_rejects_whitespace_name(self):
        with pytest.raises(WorkspaceError, match="name 不能为空或纯空格"):
            self.client.create_workspace({"name": "   ", "default_obs_path": "obs://b/p"})

    def test_create_workspace_rejects_whitespace_obs_path(self):
        with pytest.raises(WorkspaceError, match="default_obs_path 不能为空或纯空格"):
            self.client.create_workspace({"name": "n", "default_obs_path": "   "})

    def test_update_workspace_rejects_whitespace_name(self):
        with pytest.raises(WorkspaceError, match="name 不能为空或纯空格"):
            self.client.update_workspace("ws1", {"name": "   "})


class TestWorkspaceCLI:
    def _patch_get_client(self, monkeypatch, http_mock=None):
        if http_mock is None:
            http_mock = _make_mock_client()
        client = WorkspaceClient(http_mock)

        def _fake_get_client(ctx, cls):
            return client

        monkeypatch.setattr("cloudrobo_workspace.cli.get_client", _fake_get_client)
        return client

    def test_show_command_friendly_error_on_path_traversal(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        self._patch_get_client(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(workspace, ["show", "--workspace-id", "../etc"])
        assert result.exit_code == 1
        assert "path traversal" in result.output

    def test_show_command_friendly_error_on_not_found(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        from cloudrobo_core.sdk.exceptions import ResourceNotFoundError
        client = self._patch_get_client(monkeypatch)
        client._client.get.side_effect = ResourceNotFoundError("workspace not found")
        runner = CliRunner()
        result = runner.invoke(workspace, ["show", "--workspace-id", "ws-nope"])
        assert result.exit_code == 1
        assert "不存在" in result.output

    def test_list_command_rejects_negative_limit(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        self._patch_get_client(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(workspace, ["list", "--limit", "-1"])
        assert result.exit_code == 2

    def test_list_command_rejects_negative_offset(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        self._patch_get_client(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(workspace, ["list", "--offset", "-5"])
        assert result.exit_code == 2

    def test_create_command_rejects_invalid_json(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        self._patch_get_client(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            workspace,
            ["create", "--name", "n", "--default-obs-path", "obs://b/p", "--member-list", "{bad json"],
        )
        assert result.exit_code == 2
        assert "JSON" in result.output

    def test_update_command_rejects_no_fields(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        self._patch_get_client(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(workspace, ["update", "--workspace-id", "ws1"])
        assert result.exit_code == 2
        assert "更新字段" in result.output

    def test_use_command_friendly_error_on_not_found(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        from cloudrobo_core.sdk.exceptions import ResourceNotFoundError
        client = self._patch_get_client(monkeypatch)
        client._client.get.side_effect = ResourceNotFoundError("not found")
        runner = CliRunner()
        result = runner.invoke(workspace, ["use", "--workspace-id", "ws-nope"])
        assert result.exit_code == 1
        assert "不存在" in result.output

    def test_service_error_friendly_output(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        from cloudrobo_core.sdk.exceptions import ServiceError
        client = self._patch_get_client(monkeypatch)
        client._client.get.side_effect = ServiceError("Server error 500: boom", status_code=500)
        runner = CliRunner()
        result = runner.invoke(workspace, ["show", "--workspace-id", "ws1"])
        assert result.exit_code == 1
        assert "错误" in result.output

    def test_workspace_error_shows_suggestion(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        self._patch_get_client(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(workspace, ["add-members", "--workspace-id", "ws1", "--member-list", "[]"])
        assert result.exit_code == 1
        assert "建议" in result.output
        assert "--member-list" in result.output

    def test_create_command_rejects_whitespace_name(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        self._patch_get_client(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(workspace, ["create", "--name", "   ", "--default-obs-path", "obs://test"])
        assert result.exit_code == 1
        assert "name 不能为空或纯空格" in result.output

    def test_create_command_friendly_error_on_conflict(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        from cloudrobo_core.sdk.exceptions import ResourceConflictError
        client = self._patch_get_client(monkeypatch)
        client._client.post.side_effect = ResourceConflictError("workspace name already exists")
        runner = CliRunner()
        result = runner.invoke(workspace, ["create", "--name", "dup", "--default-obs-path", "obs://b/p"])
        assert result.exit_code == 1
        assert "已存在" in result.output

    def test_update_command_friendly_error_on_not_found(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        from cloudrobo_core.sdk.exceptions import ResourceNotFoundError
        client = self._patch_get_client(monkeypatch)
        client._client.put.side_effect = ResourceNotFoundError("workspace not found")
        runner = CliRunner()
        result = runner.invoke(workspace, ["update", "--workspace-id", "ws-nope", "--name", "new"])
        assert result.exit_code == 1
        assert "不存在" in result.output

    def test_update_command_friendly_error_on_conflict(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        from cloudrobo_core.sdk.exceptions import ResourceConflictError
        client = self._patch_get_client(monkeypatch)
        client._client.put.side_effect = ResourceConflictError("name conflict")
        runner = CliRunner()
        result = runner.invoke(workspace, ["update", "--workspace-id", "ws1", "--name", "dup-name"])
        assert result.exit_code == 1
        assert "已被" in result.output

    def test_delete_command_friendly_error_on_not_found(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        from cloudrobo_core.sdk.exceptions import ResourceNotFoundError
        client = self._patch_get_client(monkeypatch)
        client._client.delete.side_effect = ResourceNotFoundError("not found")
        runner = CliRunner()
        result = runner.invoke(workspace, ["delete", "--workspace-id", "ws-nope"])
        assert result.exit_code == 1
        assert "不存在" in result.output

    def test_list_members_command_friendly_error_on_not_found(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        from cloudrobo_core.sdk.exceptions import ResourceNotFoundError
        client = self._patch_get_client(monkeypatch)
        client._client.get.side_effect = ResourceNotFoundError("not found")
        runner = CliRunner()
        result = runner.invoke(workspace, ["list-members", "--workspace-id", "ws-nope"])
        assert result.exit_code == 1
        assert "不存在" in result.output

    def test_add_members_command_friendly_error_on_not_found(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        from cloudrobo_core.sdk.exceptions import ResourceNotFoundError
        client = self._patch_get_client(monkeypatch)
        client._client.post.side_effect = ResourceNotFoundError("not found")
        runner = CliRunner()
        result = runner.invoke(workspace, ["add-members", "--workspace-id", "ws-nope", "--member-list", '[{"user_id":"u1","role_ids":["r1"]}]'])
        assert result.exit_code == 1
        assert "不存在" in result.output

    def test_add_members_command_friendly_error_on_conflict(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        from cloudrobo_core.sdk.exceptions import ResourceConflictError
        client = self._patch_get_client(monkeypatch)
        client._client.post.side_effect = ResourceConflictError("member already exists")
        runner = CliRunner()
        result = runner.invoke(workspace, ["add-members", "--workspace-id", "ws1", "--member-list", '[{"user_id":"u1","role_ids":["r1"]}]'])
        assert result.exit_code == 1
        assert "已存在" in result.output or "部分成员" in result.output

    def test_update_member_command_friendly_error_on_not_found(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        from cloudrobo_core.sdk.exceptions import ResourceNotFoundError
        client = self._patch_get_client(monkeypatch)
        client._client.put.side_effect = ResourceNotFoundError("not found")
        runner = CliRunner()
        result = runner.invoke(workspace, ["update-member", "--workspace-id", "ws-nope", "--user-id", "u1", "--role-ids", "r1"])
        assert result.exit_code == 1
        assert "不存在" in result.output

    def test_delete_members_command_friendly_error_on_not_found(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        from cloudrobo_core.sdk.exceptions import ResourceNotFoundError
        client = self._patch_get_client(monkeypatch)
        client._client.delete.side_effect = ResourceNotFoundError("not found")
        runner = CliRunner()
        result = runner.invoke(workspace, ["delete-members", "--workspace-id", "ws-nope", "--user-ids", "u1"])
        assert result.exit_code == 1
        assert "不存在" in result.output

    def test_create_command_success_output(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        client = self._patch_get_client(monkeypatch)
        client._client.post.return_value = {"workspace_id": "ws-new-123"}
        runner = CliRunner()
        result = runner.invoke(workspace, ["create", "--name", "test-ws", "--default-obs-path", "obs://bucket/path"])
        assert result.exit_code == 0
        assert "已创建工作空间: ws-new-123" in result.output

    def test_create_command_success_output_without_id(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        client = self._patch_get_client(monkeypatch)
        client._client.post.return_value = {}
        runner = CliRunner()
        result = runner.invoke(workspace, ["create", "--name", "test-ws", "--default-obs-path", "obs://bucket/path"])
        assert result.exit_code == 0
        assert "已创建工作空间: test-ws" in result.output

    def test_update_command_success_output(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        client = self._patch_get_client(monkeypatch)
        client._client.put.return_value = {}
        runner = CliRunner()
        result = runner.invoke(workspace, ["update", "--workspace-id", "ws1", "--name", "new-name"])
        assert result.exit_code == 0
        assert "已更新工作空间: ws1" in result.output

    def test_delete_command_success_output(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        client = self._patch_get_client(monkeypatch)
        client._client.delete.return_value = ""
        runner = CliRunner()
        result = runner.invoke(workspace, ["delete", "--workspace-id", "ws1"])
        assert result.exit_code == 0
        assert "已删除工作空间: ws1" in result.output

    def test_add_members_command_success_output(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        client = self._patch_get_client(monkeypatch)
        client._client.post.return_value = {}
        runner = CliRunner()
        result = runner.invoke(workspace, ["add-members", "--workspace-id", "ws1", "--member-list", '[{"user_id":"u1","role_ids":["r1"]},{"user_id":"u2","role_ids":["r2"]}]'])
        assert result.exit_code == 0
        assert "已添加 2 个成员" in result.output

    def test_update_member_command_success_output(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        client = self._patch_get_client(monkeypatch)
        client._client.put.return_value = {}
        runner = CliRunner()
        result = runner.invoke(workspace, ["update-member", "--workspace-id", "ws1", "--user-id", "u1", "--role-ids", "r1,r2"])
        assert result.exit_code == 0
        assert "已更新成员 u1 的角色" in result.output

    def test_delete_members_command_success_output(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_workspace.cli import workspace
        client = self._patch_get_client(monkeypatch)
        client._client.delete.return_value = ""
        runner = CliRunner()
        result = runner.invoke(workspace, ["delete-members", "--workspace-id", "ws1", "--user-ids", "u1,u2,u3"])
        assert result.exit_code == 0
        assert "已删除 3 个成员" in result.output
