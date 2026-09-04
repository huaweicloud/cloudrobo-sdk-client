import pytest
from unittest.mock import MagicMock

from cloudrobo_core.sdk import Config, HttpClient
from cloudrobo_core.sdk.exceptions import PathTraversalError
from cloudrobo_resource.client import ResourceClient, ResourceError, is_debug_mode


def _make_mock_client():
    mock = MagicMock(spec=HttpClient)
    mock.config = MagicMock(spec=Config)
    mock.config.get_endpoint.side_effect = lambda svc: f"https://api.example.com/{svc}"
    mock.config.project_id = "proj1"
    return mock


class TestResourceClient:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = ResourceClient(self.mock_http)

    def test_list_quotas(self):
        self.mock_http.get.return_value = {"domain_quotas": [], "quotas": [], "page_info": {"total": 0}}
        result = self.client.list_quotas()
        assert "quotas" in result
        assert "domain_quotas" in result
        args, kwargs = self.mock_http.get.call_args
        assert args[0].endswith("/v1/resources/quotas")

    def test_list_quotas_with_params(self):
        self.mock_http.get.return_value = {"domain_quotas": [], "quotas": [], "page_info": {"total": 0}}
        result = self.client.list_quotas(workspace_id="ws1", resource_type="CCE", pool_type="DEDICATED")
        assert "quotas" in result
        args, kwargs = self.mock_http.get.call_args
        params = kwargs.get("params", {})
        assert params.get("workspace_id") == "ws1"
        assert params.get("resource_type") == "CCE"
        assert params.get("pool_type") == "DEDICATED"

    def test_list_quotas_with_pagination(self):
        self.mock_http.get.return_value = {"domain_quotas": [], "quotas": [], "page_info": {"total": 0}}
        result = self.client.list_quotas(limit=20, offset=40)
        assert "quotas" in result
        args, kwargs = self.mock_http.get.call_args
        params = kwargs.get("params", {})
        assert params.get("limit") == 20
        assert params.get("offset") == 40

    def test_list_quotas_with_order(self):
        self.mock_http.get.return_value = {"domain_quotas": [], "quotas": [], "page_info": {"total": 0}}
        result = self.client.list_quotas(order="ASC")
        assert "quotas" in result
        args, kwargs = self.mock_http.get.call_args
        params = kwargs.get("params", {})
        assert params.get("order") == "ASC"

    def test_list_pools(self):
        self.mock_http.get.return_value = {"resources": [], "page_info": {"total": 0}}
        result = self.client.list_pools()
        assert "resources" in result
        args, kwargs = self.mock_http.get.call_args
        assert args[0].endswith("/v1/resources/pools")

    def test_list_pools_with_params(self):
        self.mock_http.get.return_value = {"resources": [], "page_info": {"total": 0}}
        result = self.client.list_pools(resource_type="MODELARTS", pool_type="SHARED", usages=["TRAINING"])
        assert "resources" in result
        args, kwargs = self.mock_http.get.call_args
        params = kwargs.get("params", {})
        assert params.get("resource_type") == "MODELARTS"
        assert params.get("pool_type") == "SHARED"
        assert params.get("usages") == ["TRAINING"]

    def test_show_pool(self):
        self.mock_http.get.return_value = {"resource_id": "pool-001", "resource_name": "test-pool"}
        result = self.client.show_pool("pool-001")
        assert result["resource_id"] == "pool-001"
        args, kwargs = self.mock_http.get.call_args
        assert args[0].endswith("/v1/resources/pools/pool-001")

    def test_show_pool_url_construction(self):
        self.mock_http.get.return_value = {"resource_id": "abc-123"}
        self.client.show_pool("abc-123")
        args, kwargs = self.mock_http.get.call_args
        assert args[0].endswith("/v1/resources/pools/abc-123")
        assert "params" not in kwargs or not kwargs.get("params")


class TestResourceClientValidation:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = ResourceClient(self.mock_http)

    def test_show_pool_rejects_empty_id(self):
        with pytest.raises(PathTraversalError, match="pool_id"):
            self.client.show_pool("")

    def test_show_pool_rejects_none_id(self):
        with pytest.raises(PathTraversalError, match="pool_id"):
            self.client.show_pool(None)

    def test_show_pool_rejects_path_traversal(self):
        for bad in ["../etc", "p/1", "p\\1"]:
            with pytest.raises(PathTraversalError, match="path traversal"):
                self.client.show_pool(bad)

    def test_valid_pool_id_not_blocked(self):
        self.mock_http.get.return_value = {"resource_id": "pool-001"}
        result = self.client.show_pool("pool-001")
        assert result["resource_id"] == "pool-001"
        self.mock_http.get.assert_called_once()

    def test_resource_error_with_suggestion(self):
        err = ResourceError("测试错误", "测试建议")
        assert err.suggestion == "测试建议"
        assert "建议" in err.get_user_message()

    def test_is_debug_mode_importable(self):
        assert callable(is_debug_mode)


class TestResourceCLI:
    def _patch_get_client(self, monkeypatch, http_mock=None):
        if http_mock is None:
            http_mock = _make_mock_client()
        client = ResourceClient(http_mock)

        def _fake_get_client(ctx, cls):
            return client

        monkeypatch.setattr("cloudrobo_resource.cli.get_client", _fake_get_client)
        return client

    def test_show_pool_friendly_error_on_path_traversal(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_resource.cli import resource
        self._patch_get_client(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(resource, ["show-pool", "--pool-id", "../etc"])
        assert result.exit_code == 1
        assert "path traversal" in result.output

    def test_show_pool_friendly_error_on_not_found(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_resource.cli import resource
        from cloudrobo_core.sdk.exceptions import ResourceNotFoundError
        client = self._patch_get_client(monkeypatch)
        client._client.get.side_effect = ResourceNotFoundError("pool not found")
        runner = CliRunner()
        result = runner.invoke(resource, ["show-pool", "--pool-id", "pool-nope"])
        assert result.exit_code == 1
        assert "不存在" in result.output

    def test_list_quotas_rejects_negative_offset(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_resource.cli import resource
        self._patch_get_client(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(resource, ["list-quotas", "--offset", "-1"])
        assert result.exit_code == 2

    def test_list_pools_rejects_invalid_choice(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_resource.cli import resource
        self._patch_get_client(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(resource, ["list-pools", "--resource-type", "INVALID"])
        assert result.exit_code == 2

    def test_service_error_friendly_output(self, monkeypatch):
        from click.testing import CliRunner
        from cloudrobo_resource.cli import resource
        from cloudrobo_core.sdk.exceptions import ServiceError
        client = self._patch_get_client(monkeypatch)
        client._client.get.side_effect = ServiceError("Server error 500: boom", status_code=500)
        runner = CliRunner()
        result = runner.invoke(resource, ["show-pool", "--pool-id", "p1"])
        assert result.exit_code == 1
        assert "错误" in result.output
