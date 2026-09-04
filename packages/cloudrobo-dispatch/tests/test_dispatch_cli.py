from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from cloudrobo_dispatch.cli import dispatch


def _runner():
    return CliRunner()


def _invoke(runner, args, client):
    with patch("cloudrobo_dispatch.cli.get_client", return_value=client):
        return runner.invoke(dispatch, args)


class TestDispatchCli:
    def test_list_tasks_passes_optional_filters(self):
        client = MagicMock()
        client.list_dispatcher_tasks.return_value = {"tasks": []}
        result = _invoke(
            _runner(),
            [
                "list-tasks",
                "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "--limit", "50",
                "--offset", "10",
                "--sort-key", "update_at",
                "--sort-dir", "asc",
                "--status", "RUNNING",
                "--robot-id", "r1",
                "--start-time", "1000",
                "--end-time", "2000",
                "--infer-service-id", "svc1",
                "--content-match", "grasp",
            ],
            client,
        )
        assert result.exit_code == 0, result.output
        client.list_dispatcher_tasks.assert_called_once_with(
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            limit=50,
            offset=10,
            sort_key="update_at",
            sort_dir="ASC",
            status="RUNNING",
            robot_id="r1",
            start_time=1000,
            end_time=2000,
            infer_service_id="svc1",
            content_match="grasp",
        )

    def test_list_tasks_accepts_free_text_sort_dir(self):
        client = MagicMock()
        client.list_dispatcher_tasks.return_value = {"tasks": []}
        result = _invoke(
            _runner(),
            ["list-tasks", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--sort-dir", "DESC"],
            client,
        )
        assert result.exit_code == 0, result.output
        client.list_dispatcher_tasks.assert_called_once_with(
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", sort_key="updated_at", sort_dir="DESC"
        )

    def test_list_tasks_rejects_invalid_status(self):
        client = MagicMock()
        client.list_dispatcher_tasks.return_value = {"tasks": []}
        result = _invoke(
            _runner(),
            ["list-tasks", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--status", "BOGUS"],
            client,
        )
        assert result.exit_code != 0
        assert "BOGUS" in result.output
        client.list_dispatcher_tasks.assert_not_called()

    def test_list_tasks_accepts_lowercase_status(self):
        client = MagicMock()
        client.list_dispatcher_tasks.return_value = {"tasks": []}
        result = _invoke(
            _runner(),
            ["list-tasks", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--status", "running"],
            client,
        )
        assert result.exit_code == 0, result.output
        client.list_dispatcher_tasks.assert_called_once_with(
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", sort_key="updated_at", status="RUNNING", sort_dir="DESC"
        )

    def test_list_tasks_accepts_only_four_status_values(self):
        client = MagicMock()
        client.list_dispatcher_tasks.return_value = {"tasks": []}
        for status in ("RUNNING", "COMPLETED", "FAILED", "CANCELLED"):
            result = _invoke(
                _runner(),
                ["list-tasks", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--status", status],
                client,
            )
            assert result.exit_code == 0, result.output
            client.list_dispatcher_tasks.assert_called_once_with(
                "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", sort_key="updated_at", status=status, sort_dir="DESC"
            )
            client.list_dispatcher_tasks.reset_mock()

    def test_list_tasks_rejects_legacy_status_values(self):
        client = MagicMock()
        client.list_dispatcher_tasks.return_value = {"tasks": []}
        for status in ("PENDING", "PLANNING", "TIMEOUT", "RETRYING"):
            result = _invoke(
                _runner(),
                ["list-tasks", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--status", status],
                client,
            )
            assert result.exit_code != 0, status
            client.list_dispatcher_tasks.assert_not_called()

    def test_list_tasks_rejects_invalid_limit(self):
        client = MagicMock()
        client.list_dispatcher_tasks.return_value = {"tasks": []}
        result = _invoke(
            _runner(),
            ["list-tasks", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--limit", "101"],
            client,
        )
        assert result.exit_code != 0
        client.list_dispatcher_tasks.assert_not_called()

    def test_list_tasks_rejects_invalid_offset(self):
        client = MagicMock()
        client.list_dispatcher_tasks.return_value = {"tasks": []}
        result = _invoke(
            _runner(),
            ["list-tasks", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--offset", "-1"],
            client,
        )
        assert result.exit_code != 0
        client.list_dispatcher_tasks.assert_not_called()

    def test_list_tasks_rejects_overlong_robot_id(self):
        # QUERY_PARAM_RULES.robot_id.max_length = 64 -> `--robot-id` validated
        client = MagicMock()
        client.list_dispatcher_tasks.return_value = {"tasks": []}
        result = _invoke(
            _runner(),
            ["list-tasks", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--robot-id", "r" * 65],
            client,
        )
        assert result.exit_code != 0
        client.list_dispatcher_tasks.assert_not_called()

    def test_list_tasks_rejects_overlong_content_match(self):
        # QUERY_PARAM_RULES.content_match.max_length = 1024 -> `--content_match` validated
        client = MagicMock()
        client.list_dispatcher_tasks.return_value = {"tasks": []}
        result = _invoke(
            _runner(),
            ["list-tasks", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--content-match", "c" * 1025],
            client,
        )
        assert result.exit_code != 0
        client.list_dispatcher_tasks.assert_not_called()

    def test_list_tasks_rejects_invalid_sort_key(self):
        # yaml enum sort_key -> click.Choice enforces only create_at/update_at
        client = MagicMock()
        client.list_dispatcher_tasks.return_value = {"tasks": []}
        result = _invoke(
            _runner(),
            ["list-tasks", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--sort-key", "nah"],
            client,
        )
        assert result.exit_code != 0
        client.list_dispatcher_tasks.assert_not_called()

    def test_show_task_result_rejects_invalid_limit(self):
        client = MagicMock()
        client.show_dispatcher_task_result.return_value = {"logs": []}
        result = _invoke(
            _runner(),
            ["show-task-result", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--task-id", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "--limit", "0"],
            client,
        )
        assert result.exit_code != 0
        client.show_dispatcher_task_result.assert_not_called()

    def test_show_task_result_rejects_invalid_offset(self):
        client = MagicMock()
        client.show_dispatcher_task_result.return_value = {"logs": []}
        result = _invoke(
            _runner(),
            ["show-task-result", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--task-id", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "--offset", "-1"],
            client,
        )
        assert result.exit_code != 0
        client.show_dispatcher_task_result.assert_not_called()

    def test_list_tasks_only_session_id_when_no_filters(self):
        client = MagicMock()
        client.list_dispatcher_tasks.return_value = {"tasks": []}
        result = _invoke(
            _runner(), ["list-tasks", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"], client
        )
        assert result.exit_code == 0, result.output
        client.list_dispatcher_tasks.assert_called_once_with(
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", sort_key="updated_at", sort_dir="DESC"
        )

    def test_create_task_builds_strict_body(self):
        client = MagicMock()
        client.create_dispatcher_task.return_value = {"task_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}
        result = _invoke(
            _runner(),
            [
                "create-task",
                "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "--name", "task-1",
                "--task", "grasp red cube",
                "--constraints-json",
                ('{"model":{"exec_model_id":"ext_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"robot_id":"1234567890abcdef1234567890abcdef",'
                 '"exec_constraints":{"max_iter_num":100,"max_run_time":10}}'),
            ],
            client,
        )
        assert result.exit_code == 0, result.output
        sid, req = client.create_dispatcher_task.call_args.args
        assert sid == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        assert req == {
            "name": "task-1",
            "task": "grasp red cube",
            "constraints": {
                "model": {"exec_model_id": "ext_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
                "robot_id": "1234567890abcdef1234567890abcdef",
                "exec_constraints": {"max_iter_num": 100, "max_run_time": 10},
            },
        }

    def test_create_task_fills_session_id_from_callback_when_not_provided(self):
        """session_id now comes from the _validate_session_id callback (which
        fills in the current workspace) instead of a click default."""
        client = MagicMock()
        client.create_dispatcher_task.return_value = {"task_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}
        with patch(
            "cloudrobo_dispatch.validators.cli_callbacks.current_workspace",
            return_value={"workspace_id": "default-session-1"},
        ):
            result = _invoke(
                _runner(),
                [
                    "create-task",
                    "--name", "task-1",
                    "--task", "grasp red cube",
                    "--constraints-json",
                    '{"model":{"exec_model_id":"ext_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"robot_id":"1234567890abcdef1234567890abcdef"}',
                ],
                client,
            )
        assert result.exit_code == 0, result.output
        assert client.create_dispatcher_task.call_args.args[0] == "default-session-1"

    def test_create_task_dry_run(self):
        client = MagicMock()
        result = _invoke(
            _runner(),
            [
                "create-task",
                "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "--name", "task-1",
                "--task", "task desc",
                "--constraints-json",
                '{"model":{"exec_model_id":"ext_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"robot_id":"1234567890abcdef1234567890abcdef"}',
                "--dry-run",
            ],
            client,
        )
        assert result.exit_code == 0, result.output
        assert "[DRY-RUN]" in result.output
        client.create_dispatcher_task.assert_not_called()

    def test_create_task_missing_required_constraints_json(self):
        client = MagicMock()
        result = _invoke(
            _runner(),
            [
                "create-task",
                "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "--name", "task-1",
                "--task", "grasp red cube",
            ],
            client,
        )
        assert result.exit_code != 0
        assert "Missing option '--constraints-json'" in result.output
        client.create_dispatcher_task.assert_not_called()

    def test_create_task_invalid_constraints_json(self):
        client = MagicMock()
        result = _invoke(
            _runner(),
            [
                "create-task",
                "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "--name", "task-1",
                "--task", "grasp red cube",
                "--constraints-json", "{not-json",
            ],
            client,
        )
        assert result.exit_code != 0
        assert "Invalid JSON" in result.output
        client.create_dispatcher_task.assert_not_called()

    def test_cancel_task_dry_run(self):
        client = MagicMock()
        result = _invoke(
            _runner(),
            ["cancel-task", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--task-id", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "--dry-run"],
            client,
        )
        assert result.exit_code == 0, result.output
        assert "[DRY-RUN]" in result.output
        client.cancel_dispatcher_task.assert_not_called()

    def test_session_id_rejects_path_traversal(self):
        client = MagicMock()
        client.list_dispatcher_tasks.return_value = {"tasks": []}
        result = _invoke(
            _runner(),
            ["list-tasks", "--session-id", "../etc/passwd"],
            client,
        )
        assert result.exit_code != 0
        client.list_dispatcher_tasks.assert_not_called()

    def test_task_id_rejects_path_traversal(self):
        client = MagicMock()
        client.show_dispatcher_task.return_value = {"id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}
        result = _invoke(
            _runner(),
            ["show-task", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--task-id", "../evil"],
            client,
        )
        assert result.exit_code != 0
        client.show_dispatcher_task.assert_not_called()

    def test_show_task(self):
        client = MagicMock()
        client.show_dispatcher_task.return_value = {"id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}
        result = _invoke(
            _runner(),
            ["show-task", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--task-id", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"],
            client,
        )
        assert result.exit_code == 0, result.output
        client.show_dispatcher_task.assert_called_once_with("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    def test_cancel_task(self):
        client = MagicMock()
        client.cancel_dispatcher_task.return_value = None
        result = _invoke(
            _runner(),
            ["cancel-task", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--task-id", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"],
            client,
        )
        assert result.exit_code == 0, result.output
        client.cancel_dispatcher_task.assert_called_once_with("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    def test_show_task_result(self):
        client = MagicMock()
        client.show_dispatcher_task_result.return_value = {"log_items": []}
        result = _invoke(
            _runner(),
            ["show-task-result", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--task-id", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "--inverse", "--limit", "200", "--offset", "10"],
            client,
        )
        assert result.exit_code == 0, result.output
        client.show_dispatcher_task_result.assert_called_once_with(
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", inverse=True, limit=200, offset=10
        )

    def test_wait_task_returns_final_status(self):
        client = MagicMock()
        client.wait_dispatcher_task.return_value = {"task": {"status": "COMPLETED"}}
        result = _invoke(
            _runner(),
            ["wait-task", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--task-id", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "--timeout", "120"],
            client,
        )
        assert result.exit_code == 0, result.output
        client.wait_dispatcher_task.assert_called_once_with("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", 120)

    def test_wait_task_default_timeout(self):
        client = MagicMock()
        client.wait_dispatcher_task.return_value = {"task": {"status": "FAILED"}}
        result = _invoke(
            _runner(),
            ["wait-task", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--task-id", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"],
            client,
        )
        assert result.exit_code == 0, result.output
        client.wait_dispatcher_task.assert_called_once_with("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", 600)

    def test_wait_task_rejects_path_traversal(self):
        client = MagicMock()
        result = _invoke(
            _runner(),
            ["wait-task", "--session-id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "--task-id", "../evil"],
            client,
        )
        assert result.exit_code != 0
        client.wait_dispatcher_task.assert_not_called()
