from unittest.mock import MagicMock, patch

import pytest
from cloudrobo_core.sdk import Config, HttpClient
from cloudrobo_dispatch.client import TERMINAL_STATES, DispatchClient


def _make_mock_client():
    mock = MagicMock(spec=HttpClient)
    mock.config = MagicMock(spec=Config)
    mock.config.get_endpoint.side_effect = lambda svc: f"https://api.example.com/{svc}"
    mock.config.project_id = "proj1"
    return mock


class TestDispatchClient:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = DispatchClient(self.mock_http)

    def test_terminals_only_four_dispatch_states(self):
        assert TERMINAL_STATES == {"COMPLETED", "FAILED", "CANCELLED"}

    def test_create_dispatcher_task(self):
        self.mock_http.post.return_value = {"task_id": "task1"}
        req = {
            "name": "task-1",
            "task": "grasp red cube",
            "constraints": {"model": {"exec_model_id": "ext_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}, "robot_id": "1234567890abcdef1234567890abcdef"},
        }
        result = self.client.create_dispatcher_task("sess1", req)
        assert result["task_id"] == "task1"
        url = self.mock_http.post.call_args.args[0]
        assert url.endswith("/v1/robo-dispatcher/sessions/sess1/tasks")

    def test_list_dispatcher_tasks(self):
        self.mock_http.get.return_value = {"tasks": []}
        result = self.client.list_dispatcher_tasks("sess1", limit=10, status="RUNNING")
        assert result["tasks"] == []
        url = self.mock_http.get.call_args.args[0]
        params = self.mock_http.get.call_args.kwargs["params"]
        assert url.endswith("/v1/robo-dispatcher/sessions/sess1/tasks")
        assert params["limit"] == 10
        assert params["status"] == "RUNNING"

    def test_list_dispatcher_tasks_passes_all_query_params(self):
        self.mock_http.get.return_value = {"tasks": []}
        self.client.list_dispatcher_tasks(
            "sess1",
            limit=10,
            offset=5,
            sort_key="started_at",
            sort_dir="ASC",
            status="FAILED",
            robot_id="r1",
            start_time=1000,
            end_time=2000,
            infer_service_id="svc1",
            content_match="grasp",
        )
        params = self.mock_http.get.call_args.kwargs["params"]
        assert params == {
            "limit": 10,
            "offset": 5,
            "sort_key": "started_at",
            "sort_dir": "ASC",
            "status": "FAILED",
            "robot_id": "r1",
            "start_time": 1000,
            "end_time": 2000,
            "infer_service_id": "svc1",
            "content_match": "grasp",
        }

    def test_show_dispatcher_task(self):
        self.mock_http.get.return_value = {"id": "task1"}
        result = self.client.show_dispatcher_task("sess1", "task1")
        assert result["id"] == "task1"
        url = self.mock_http.get.call_args.args[0]
        assert url.endswith("/v1/robo-dispatcher/sessions/sess1/tasks/task1")

    def test_cancel_dispatcher_task(self):
        self.mock_http.delete.return_value = None
        result = self.client.cancel_dispatcher_task("sess1", "task1")
        assert result is None
        self.mock_http.delete.assert_called_once()
        url = self.mock_http.delete.call_args.args[0]
        assert url.endswith("/v1/robo-dispatcher/sessions/sess1/tasks/task1")

    def test_show_dispatcher_task_result(self):
        self.mock_http.get.return_value = {"task": {}, "log_items": [], "page_info": {}}
        result = self.client.show_dispatcher_task_result("sess1", "task1", inverse=True, limit=200)
        assert result["log_items"] == []
        url = self.mock_http.get.call_args.args[0]
        params = self.mock_http.get.call_args.kwargs["params"]
        assert url.endswith("/v1/robo-dispatcher/sessions/sess1/tasks/task1/result")
        assert params["inverse"] is True
        assert params["limit"] == 200

    def test_show_dispatcher_task_result_passes_all_query_params(self):
        self.mock_http.get.return_value = {"task": {}, "log_items": [], "page_info": {}}
        self.client.show_dispatcher_task_result(
            "sess1", "task1", inverse=True, limit=200, offset=10
        )
        params = self.mock_http.get.call_args.kwargs["params"]
        assert params == {"inverse": True, "limit": 200, "offset": 10}

    def test_wait_dispatcher_task_returns_on_terminal_state(self):
        self.mock_http.get.side_effect = [
            {"task": {"status": "RUNNING"}},
            {"task": {"status": "COMPLETED"}},
        ]
        with patch("cloudrobo_dispatch.client.time.sleep") as mock_sleep:
            result = self.client.wait_dispatcher_task("sess1", "task1", timeout=600)
        assert result["task"]["status"] == "COMPLETED"
        mock_sleep.assert_called_once_with(5)

    def test_wait_dispatcher_task_times_out(self):
        self.mock_http.get.return_value = {"task": {"status": "RUNNING"}}

        def fake_monotonic():
            fake_monotonic.calls += 1
            return fake_monotonic.calls * 10

        fake_monotonic.calls = 0
        with (
            patch("cloudrobo_dispatch.client.time.monotonic", side_effect=fake_monotonic),
            patch("cloudrobo_dispatch.client.time.sleep"),
            pytest.raises(TimeoutError) as excinfo,
        ):
            self.client.wait_dispatcher_task("sess1", "task1", timeout=5)
        assert "超时" in str(excinfo.value)
        assert self.mock_http.get.call_count >= 1
