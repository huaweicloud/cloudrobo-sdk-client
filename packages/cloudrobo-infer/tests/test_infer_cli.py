from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from cloudrobo_infer.cli import infer


def _invoke(runner, args, client):
    with patch("cloudrobo_infer.cli.get_client", return_value=client):
        return runner.invoke(infer, args)


class TestInferCli:
    def test_create_passes_all_optional_fields(self):
        client = MagicMock()
        client.create_infer_service.return_value = {"id": "cccccccc-cccc-cccc-cccc-cccccccccccc"}
        result = _invoke(
            CliRunner(),
            [
                "create",
                "--name", "chat-api",
                "--flavor", "cpu.2",
                "--model-json", '{"model_id":"m1","model_version_id":"v1","mount_path":"/app/data"}',
                "--workspace-id", "ws-1",
                "--pool-id", "pool-public",
                "--pool-type", "SHARED",
                "--description", "desc",
                "--image-swr-url", "swr.cn-southwest-2.myhuaweicloud.com/demo/x:latest",
                "--cmd", "python server.py",
                "--envs-json", '{"LOG_LEVEL":"INFO"}',
                "--stop-schedule-json", '{"duration":60,"time_unit":"MINUTES"}',
                "--deploy-timeout-minutes", "60",
                "--service-invoke-json", '{"port":8080,"protocol":"HTTP","auth_type":"API_KEY"}',
                "--skill-config-json", '{"strict":true,"skills":[{"name":"x","prompt":"y"}]}',
                "--files-json", '[{"source":"OBS","mount_path":"/data"}]',
                "--model-ext-metadata", '{"model": "x"}',
                "--startup-health-json", '{"check_method":"HTTP"}',
                "--readiness-health-json", '{"check_method":"HTTP"}',
                "--liveness-health-json", '{"check_method":"HTTP"}',
                "--internet-access-enable",
            ],
            client,
        )
        assert result.exit_code == 0, result.output
        req = client.create_infer_service.call_args.args[0]
        assert req["name"] == "chat-api"
        assert req["flavor"] == "cpu.2"
        assert req["model"]["model_id"] == "m1"
        assert req["model"]["model_version_id"] == "v1"
        assert req["model"]["mount_path"] == "/app/data"
        assert req["workspace_id"] == "ws-1"
        assert req["pool_id"] == "pool-public"
        assert req["pool_type"] == "SHARED"
        assert req["description"] == "desc"
        assert req["image_swr_url"] == "swr.cn-southwest-2.myhuaweicloud.com/demo/x:latest"
        assert req["cmd"] == "python server.py"
        assert req["envs"] == {"LOG_LEVEL": "INFO"}
        assert req["stop_schedule"] == {"duration": 60, "time_unit": "MINUTES"}
        assert req["deploy_timeout_minutes"] == 60
        assert req["service_invoke"] == {"port": 8080, "protocol": "HTTP", "auth_type": "API_KEY"}
        assert req["skill_config"] == {"strict": True, "skills": [{"name": "x", "prompt": "y"}]}
        assert req["files"] == [{"source": "OBS", "mount_path": "/data"}]
        assert req["model_ext_metadata"] == '{"model": "x"}'
        assert req["startup_health"] == {"check_method": "HTTP"}
        assert req["readiness_health"] == {"check_method": "HTTP"}
        assert req["liveness_health"] == {"check_method": "HTTP"}
        assert req["internet_access_enable"] is True

    def test_create_pool_type_is_enum(self):
        client = MagicMock()
        client.create_infer_service.return_value = {"id": "cccccccc-cccc-cccc-cccc-cccccccccccc"}
        result = _invoke(
            CliRunner(),
            [
                "create",
                "--name", "chat-api",
                "--flavor", "cpu.2",
                "--model-json", '{"model_id":"m1","model_version_id":"v1"}',
                "--workspace-id", "ws-1",
                "--pool-id", "pool-public",
                "--pool-type", "BOGUS",
            ],
            client,
        )
        assert result.exit_code != 0
        client.create_infer_service.assert_not_called()

    def test_create_pool_type_accepts_dedicated(self):
        client = MagicMock()
        client.create_infer_service.return_value = {"id": "cccccccc-cccc-cccc-cccc-cccccccccccc"}
        result = _invoke(
            CliRunner(),
            [
                "create",
                "--name", "chat-api",
                "--flavor", "cpu.2",
                "--model-json", '{"model_id":"m1","model_version_id":"v1"}',
                "--workspace-id", "ws-1",
                "--pool-id", "pool-public",
                "--pool-type", "dedicated",
            ],
            client,
        )
        assert result.exit_code == 0, result.output
        req = client.create_infer_service.call_args.args[0]
        assert req["pool_type"] == "DEDICATED"

    def test_create_rejects_required_missing(self):
        client = MagicMock()
        result = _invoke(
            CliRunner(),
            [
                "create",
                "--name", "chat-api",
                "--flavor", "cpu.2",
                "--model-json", '{"model_id":"m1","model_version_id":"v1"}',
                "--workspace-id", "ws-1",
            ],
            client,
        )
        assert result.exit_code != 0
        client.create_infer_service.assert_not_called()

    def test_create_rejects_missing_model_json(self):
        client = MagicMock()
        result = _invoke(
            CliRunner(),
            [
                "create",
                "--name", "chat-api",
                "--flavor", "cpu.2",
                "--workspace-id", "ws-1",
                "--pool-id", "pool-public",
                "--pool-type", "SHARED",
            ],
            client,
        )
        assert result.exit_code != 0
        client.create_infer_service.assert_not_called()

    def test_create_rejects_invalid_model_json(self):
        client = MagicMock()
        result = _invoke(
            CliRunner(),
            [
                "create",
                "--name", "chat-api",
                "--flavor", "cpu.2",
                "--model-json", "{bad model json}",
                "--workspace-id", "ws-1",
                "--pool-id", "pool-public",
                "--pool-type", "SHARED",
            ],
            client,
        )
        assert result.exit_code != 0
        client.create_infer_service.assert_not_called()

    def test_create_rejects_invalid_deploy_timeout(self):
        client = MagicMock()
        result = _invoke(
            CliRunner(),
            [
                "create",
                "--name", "chat-api",
                "--flavor", "cpu.2",
                "--model-json", '{"model_id":"m1","model_version_id":"v1"}',
                "--workspace-id", "ws-1",
                "--pool-id", "pool-public",
                "--pool-type", "SHARED",
                "--deploy-timeout-minutes", "0",
            ],
            client,
        )
        assert result.exit_code != 0
        client.create_infer_service.assert_not_called()

    def test_create_dry_run_skips_client(self):
        client = MagicMock()
        result = _invoke(
            CliRunner(),
            [
                "create",
                "--name", "chat-api",
                "--flavor", "cpu.2",
                "--model-json", '{"model_id":"m1","model_version_id":"v1"}',
                "--workspace-id", "ws-1",
                "--pool-id", "pool-public",
                "--pool-type", "SHARED",
                "--description", "desc",
                "--dry-run",
            ],
            client,
        )
        assert result.exit_code == 0, result.output
        client.create_infer_service.assert_not_called()
        assert "DRY-RUN" in result.output

    def test_create_invalid_json_raises_bad_parameter(self):
        client = MagicMock()
        result = _invoke(
            CliRunner(),
            [
                "create",
                "--name", "chat-api",
                "--flavor", "cpu.2",
                "--model-json", '{"model_id":"m1","model_version_id":"v1"}',
                "--workspace-id", "ws-1",
                "--pool-id", "pool-public",
                "--pool-type", "SHARED",
                "--envs-json", "{bad json}",
            ],
            client,
        )
        assert result.exit_code != 0
        client.create_infer_service.assert_not_called()

    def test_list_passes_optional_filters(self):
        client = MagicMock()
        client.list_infer_services.return_value = {"services": []}
        result = _invoke(
            CliRunner(),
            [
                "list",
                "--limit", "20",
                "--offset", "5",
                "--sort-key", "update_at",
                "--sort-dir", "asc",
                "--workspace-id", "ws-1",
                "--status", "RUNNING",
                "--name", "svc",
                "--model-id", "m1",
                "--model-name", "gpt",
                "--model-version-id", "v1",
                "--model-version-name", "v1-name",
                "--user-name", "zhangsan",
                "--user-id", "u1",
            ],
            client,
        )
        assert result.exit_code == 0, result.output
        client.list_infer_services.assert_called_once_with(
            limit=20,
            offset=5,
            sort_key="update_at",
            sort_dir="ASC",
            workspace_id="ws-1",
            status="RUNNING",
            name="svc",
            model_id="m1",
            model_name="gpt",
            model_version_id="v1",
            model_version_name="v1-name",
            user_name="zhangsan",
            user_id="u1",
        )

    def test_list_works_without_workspace_id(self):
        """workspace_id is optional (required=False) and defaults from the
        current_workspace() global; list must succeed without --workspace-id."""
        client = MagicMock()
        client.list_infer_services.return_value = {"services": []}
        result = _invoke(CliRunner(), ["list"], client)
        assert result.exit_code == 0, result.output
        client.list_infer_services.assert_called_once()

    def test_list_rejects_invalid_sort_dir(self):
        client = MagicMock()
        result = _invoke(
            CliRunner(),
            ["list", "--workspace-id", "ws-1", "--sort-dir", "bogus"],
            client,
        )
        assert result.exit_code != 0
        client.list_infer_services.assert_not_called()

    def test_list_rejects_invalid_limit(self):
        client = MagicMock()
        result = _invoke(
            CliRunner(),
            ["list", "--workspace-id", "ws-1", "--limit", "51"],
            client,
        )
        assert result.exit_code != 0
        client.list_infer_services.assert_not_called()

    def test_list_rejects_invalid_offset(self):
        client = MagicMock()
        result = _invoke(
            CliRunner(),
            ["list", "--workspace-id", "ws-1", "--offset", "-1"],
            client,
        )
        assert result.exit_code != 0
        client.list_infer_services.assert_not_called()

    def test_update_passes_optional_fields(self):
        client = MagicMock()
        client.update_infer_service.return_value = {"id": "cccccccc-cccc-cccc-cccc-cccccccccccc"}
        result = _invoke(
            CliRunner(),
            [
                "update",
                "--service-id", "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "--description", "new desc",
                "--model-ext-metadata", '{"r2c": "config"}',
            ],
            client,
        )
        assert result.exit_code == 0, result.output
        req = client.update_infer_service.call_args.args[1]
        assert req == {"description": "new desc", "model_ext_metadata": '{"r2c": "config"}'}

    def test_update_only_accepts_description_and_metadata(self):
        client = MagicMock()
        result = _invoke(
            CliRunner(),
            ["update", "--service-id", "cccccccc-cccc-cccc-cccc-cccccccccccc", "--name", "new-name"],
            client,
        )
        assert result.exit_code != 0
        client.update_infer_service.assert_not_called()

    def test_list_logs_passes_optional_fields(self):
        client = MagicMock()
        client.list_infer_service_logs.return_value = {"logs": []}
        result = _invoke(
            CliRunner(),
            [
                "list-logs",
                "--service-id", "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "--start-time", "1000",
                "--end-time", "2000",
                "--limit", "100",
                "--is-desc",
                "--is-count",
                "--keywords", "error",
                "--highlight",
                "--line-num", "99",
            ],
            client,
        )
        assert result.exit_code == 0, result.output
        req = client.list_infer_service_logs.call_args.args[1]
        assert req["limit"] == 100
        assert req["is_desc"] is True
        assert req["is_count"] is True
        assert req["keywords"] == "error"
        assert req["highlight"] is True
        assert req["line_num"] == "99"

    def test_list_logs_rejects_invalid_limit(self):
        client = MagicMock()
        result = _invoke(
            CliRunner(),
            [
                "list-logs",
                "--service-id", "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "--start-time", "1000",
                "--end-time", "2000",
                "--limit", "5001",
            ],
            client,
        )
        assert result.exit_code != 0
        client.list_infer_service_logs.assert_not_called()

    def test_list_logs_rejects_invalid_time_range(self):
        client = MagicMock()
        result = _invoke(
            CliRunner(),
            [
                "list-logs",
                "--service-id", "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "--start-time", "-1",
                "--end-time", "2000",
            ],
            client,
        )
        assert result.exit_code != 0
        client.list_infer_service_logs.assert_not_called()

    def test_show_calls_client(self):
        client = MagicMock()
        client.show_infer_service.return_value = {"id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "status": "RUNNING"}
        result = _invoke(CliRunner(), ["show", "--service-id", "cccccccc-cccc-cccc-cccc-cccccccccccc"], client)
        assert result.exit_code == 0, result.output
        client.show_infer_service.assert_called_once_with("cccccccc-cccc-cccc-cccc-cccccccccccc")

    def test_show_requires_service_id(self):
        result = CliRunner().invoke(infer, ["show"])
        assert result.exit_code != 0

    def test_delete_calls_client(self):
        client = MagicMock()
        client.delete_infer_service.return_value = None
        result = _invoke(CliRunner(), ["delete", "--service-id", "cccccccc-cccc-cccc-cccc-cccccccccccc"], client)
        assert result.exit_code == 0, result.output
        client.delete_infer_service.assert_called_once_with("cccccccc-cccc-cccc-cccc-cccccccccccc")

    def test_delete_rejects_overlong_service_id(self):
        client = MagicMock()
        bad = "x" * 37  # PATH_PARAM_RULES.service_id.max_length = 36
        result = _invoke(CliRunner(), ["delete", "--service-id", bad], client)
        assert result.exit_code != 0
        assert "Invalid value" in result.output
        client.delete_infer_service.assert_not_called()

    def test_delete_rejects_empty_service_id(self):
        client = MagicMock()
        result = _invoke(CliRunner(), ["delete", "--service-id", ""], client)
        assert result.exit_code != 0
        client.delete_infer_service.assert_not_called()

    def test_delete_dry_run_skips_client(self):
        client = MagicMock()
        result = _invoke(CliRunner(), ["delete", "--service-id", "cccccccc-cccc-cccc-cccc-cccccccccccc", "--dry-run"], client)
        assert result.exit_code == 0, result.output
        client.delete_infer_service.assert_not_called()
        assert "DRY-RUN" in result.output

    def test_start_calls_client(self):
        client = MagicMock()
        client.start_infer_service.return_value = {"status": "RUNNING"}
        result = _invoke(CliRunner(), ["start", "--service-id", "cccccccc-cccc-cccc-cccc-cccccccccccc"], client)
        assert result.exit_code == 0, result.output
        client.start_infer_service.assert_called_once_with("cccccccc-cccc-cccc-cccc-cccccccccccc")

    def test_stop_calls_client(self):
        client = MagicMock()
        client.stop_infer_service.return_value = {"status": "STOPPED"}
        result = _invoke(CliRunner(), ["stop", "--service-id", "cccccccc-cccc-cccc-cccc-cccccccccccc"], client)
        assert result.exit_code == 0, result.output
        client.stop_infer_service.assert_called_once_with("cccccccc-cccc-cccc-cccc-cccccccccccc")

    def test_wait_deploy_returns_success_when_terminal(self):
        client = MagicMock()
        client.wait_deploy.return_value = {"id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "status": "RUNNING"}
        result = _invoke(CliRunner(), ["wait-deploy", "--service-id", "cccccccc-cccc-cccc-cccc-cccccccccccc"], client)
        assert result.exit_code == 0, result.output
        client.wait_deploy.assert_called_once()
        assert "RUNNING" in result.output

    def test_wait_deploy_requires_service_id(self):
        result = CliRunner().invoke(infer, ["wait-deploy"])
        assert result.exit_code != 0

    def test_wait_deploy_passes_timeout(self):
        client = MagicMock()
        client.wait_deploy.return_value = {"id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "status": "STOPPED"}
        result = _invoke(CliRunner(), ["wait-deploy", "--service-id", "cccccccc-cccc-cccc-cccc-cccccccccccc", "--timeout", "60"], client)
        assert result.exit_code == 0, result.output
        client.wait_deploy.assert_called_once_with("cccccccc-cccc-cccc-cccc-cccccccccccc", timeout=60)

    def test_no_deploy_and_wait_command(self):
        result = CliRunner().invoke(infer, ["deploy-and-wait", "--help"])
        assert result.exit_code != 0
