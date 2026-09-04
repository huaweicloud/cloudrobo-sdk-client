from unittest.mock import MagicMock, patch

import pytest
from cloudrobo_core.sdk import Config, HttpClient
from cloudrobo_core.sdk.exceptions import PathTraversalError
from cloudrobo_infer.client import TERMINAL_STATES, InferClient
from cloudrobo_core.sdk.exceptions import BadParameterError


def _make_mock_client():
    mock = MagicMock(spec=HttpClient)
    mock.config = MagicMock(spec=Config)
    mock.config.get_endpoint.side_effect = lambda svc: f"https://api.example.com/{svc}"
    mock.config.project_id = "proj1"
    return mock


class TestInferClient:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = InferClient(self.mock_http)

    def test_create_infer_service(self):
        self.mock_http.post.return_value = {"id": "svc1"}
        req = {
            "name": "chat-api",
            "flavor": "cpu.2",
            "model": {"model_id": "m1", "model_version_id": "v1"},
            "workspace_id": "ws-1",
            "pool_id": "pool-public",
            "pool_type": "SHARED",
        }
        result = self.client.create_infer_service(req)
        assert result["id"] == "svc1"
        self.mock_http.post.assert_called_once_with(
            "https://api.example.com/cloudrobo-service/v1/infer-services",
            json=req,
        )

    def test_create_infer_service_rejects_invalid_req(self):
        req = {
            "name": "chat-api",
            "flavor": "cpu.2",
            "model": {"model_id": "m1", "model_version_id": "v1"},
            "workspace_id": "ws-1",
        }
        with pytest.raises(BadParameterError):
            self.client.create_infer_service(req)
        self.mock_http.post.assert_not_called()

    def test_list_infer_services(self):
        self.mock_http.get.return_value = {"services": []}
        result = self.client.list_infer_services(status="RUNNING", limit=10)
        assert "services" in result
        self.mock_http.get.assert_called_once_with(
            "https://api.example.com/cloudrobo-service/v1/infer-services",
            params={"status": "RUNNING", "limit": 10},
        )

    def test_show_infer_service(self):
        self.mock_http.get.return_value = {"id": "svc1", "status": "RUNNING"}
        result = self.client.show_infer_service("svc1")
        assert result["id"] == "svc1"
        self.mock_http.get.assert_called_once_with(
            "https://api.example.com/cloudrobo-service/v1/infer-services/svc1"
        )

    def test_update_infer_service(self):
        self.mock_http.put.return_value = {"id": "svc1", "description": "updated"}
        result = self.client.update_infer_service("svc1", {"description": "updated"})
        assert result["description"] == "updated"
        self.mock_http.put.assert_called_once_with(
            "https://api.example.com/cloudrobo-service/v1/infer-services/svc1",
            json={"description": "updated"},
        )

    def test_delete_infer_service_returns_dict(self):
        self.mock_http.delete.return_value = {"id": "svc1", "status": "DELETING"}
        result = self.client.delete_infer_service("svc1")
        assert result["status"] == "DELETING"
        self.mock_http.delete.assert_called_once_with(
            "https://api.example.com/cloudrobo-service/v1/infer-services/svc1"
        )

    def test_start_infer_service(self):
        self.mock_http.post.return_value = {"status": "RUNNING"}
        result = self.client.start_infer_service("svc1")
        assert result["status"] == "RUNNING"
        self.mock_http.post.assert_called_once_with(
            "https://api.example.com/cloudrobo-service/v1/infer-services/svc1/start"
        )

    def test_stop_infer_service(self):
        self.mock_http.post.return_value = {"status": "STOPPED"}
        result = self.client.stop_infer_service("svc1")
        assert result["status"] == "STOPPED"
        self.mock_http.post.assert_called_once_with(
            "https://api.example.com/cloudrobo-service/v1/infer-services/svc1/stop"
        )

    def test_list_infer_service_logs(self):
        self.mock_http.post.return_value = {
            "logs": [{"line_num": "10001", "content": "service started"}],
            "count": 1,
        }
        req = {"start_time": 1779782400000, "end_time": 1779868800000, "keywords": "error"}
        result = self.client.list_infer_service_logs("svc1", req)
        assert result["count"] == 1
        self.mock_http.post.assert_called_once_with(
            "https://api.example.com/cloudrobo-service/v1/infer-services/svc1/logs",
            json=req,
        )

    def test_path_params_validated(self):
        with pytest.raises(PathTraversalError):
            self.client.show_infer_service("../etc/passwd")
        with pytest.raises(PathTraversalError):
            self.client.delete_infer_service("a/b")

    def test_wait_deploy_returns_when_not_deploying(self):
        self.mock_http.get.side_effect = [
            {"id": "svc1", "status": "DEPLOYING"},
            {"id": "svc1", "status": "RUNNING"},
        ]
        with patch("cloudrobo_infer.client.time.sleep") as mock_sleep:
            result = self.client.wait_deploy("svc1", timeout=600)
        assert result["status"] == "RUNNING"
        mock_sleep.assert_called_once_with(5)

    def test_wait_deploy_raises_on_timeout(self):
        self.mock_http.get.return_value = {"id": "svc1", "status": "DEPLOYING"}
        with patch("cloudrobo_infer.client.time.sleep") as mock_sleep, pytest.raises(RuntimeError):
            self.client.wait_deploy("svc1", timeout=5)
        assert mock_sleep.called

    def test_terminal_states_excludes_deploying(self):
        assert "DEPLOYING" not in TERMINAL_STATES
        assert TERMINAL_STATES == {"FAILED", "RUNNING", "STOPPING", "STOPPED", "DELETING", "ERROR"}
