import pytest
from unittest.mock import MagicMock

from cloudrobo_core.sdk import Config, HttpClient
from cloudrobo_eval.client import EvalClient


def _make_mock_client():
    mock = MagicMock(spec=HttpClient)
    mock.config = MagicMock(spec=Config)
    mock.config.get_endpoint.side_effect = lambda svc: f"https://api.example.com/{svc}"
    mock.config.project_id = "proj1"
    return mock


class TestEvalClient:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = EvalClient(self.mock_http)

    def test_create_eval_job(self):
        self.mock_http.post.return_value = {"id": "eval1"}
        result = self.client.create_eval_job({"name": "sim-eval"})
        assert result["id"] == "eval1"

    def test_batch_delete_eval_jobs(self):
        self.mock_http.post.return_value = ""
        self.client.batch_delete_eval_jobs(["j1", "j2"])
        self.mock_http.post.assert_called_once()

    def test_get_vnc_address(self):
        self.mock_http.get.return_value = {"vnc_address": "vnc://xxx"}
        result = self.client.get_vnc_address("j1", "e1")
        assert "vnc_address" in result
