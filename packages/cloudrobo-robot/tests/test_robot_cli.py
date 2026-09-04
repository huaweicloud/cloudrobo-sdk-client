import glob
import io
import os
import zipfile
from datetime import datetime
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from cloudrobo_robot.cli import robot

_ROBOT_ID = "0123456789abcdef0123456789abcdef"


def _invoke(runner, args, client):
    with patch("cloudrobo_robot.cli.get_client", return_value=client):
        return runner.invoke(robot, args)


class TestRobotCli:
    def test_create_passes_all_required_and_optional(self):
        client = MagicMock()
        client.create_robot.return_value = {"id": "r1"}
        result = _invoke(
            CliRunner(),
            [
                "create",
                "--name", "robot1",
                "--type", "HUMANOID",
                "--manufacturer", "JAKA",
                "--robot-model", "S101",
                "--workspace-id", "ws-1",
                "--description", "a new robot",
            ],
            client,
        )
        assert result.exit_code == 0, result.output
        req = client.create_robot.call_args.args[0]
        assert req == {
            "name": "robot1",
            "type": "HUMANOID",
            "manufacturer": "JAKA",
            "robot_model": "S101",
            "workspace_id": "ws-1",
            "description": "a new robot",
        }

    def test_create_fills_workspace_id_from_callback_when_not_provided(self):
        """workspace_id now comes from the _validate_workspace_id callback
        (which fills in the current workspace) instead of a click default."""
        client = MagicMock()
        client.create_robot.return_value = {"id": "r1"}
        with patch(
            "cloudrobo_robot.validators.cli_callbacks.current_workspace",
            return_value={"workspace_id": "default-ws-1"},
        ):
            result = _invoke(
                CliRunner(),
                [
                    "create",
                    "--name", "robot1",
                    "--type", "HUMANOID",
                    "--manufacturer", "JAKA",
                    "--robot-model", "S101",
                ],
                client,
            )
        assert result.exit_code == 0, result.output
        req = client.create_robot.call_args.args[0]
        assert req["workspace_id"] == "default-ws-1"

    def test_create_dry_run_skips_client(self):
        client = MagicMock()
        result = _invoke(
            CliRunner(),
            [
                "create",
                "--name", "robot1",
                "--type", "HUMANOID",
                "--manufacturer", "JAKA",
                "--robot-model", "S101",
                "--workspace-id", "ws-1",
                "--dry-run",
            ],
            client,
        )
        assert result.exit_code == 0, result.output
        client.create_robot.assert_not_called()
        assert "DRY-RUN" in result.output

    def test_create_rejects_invalid_type_enum(self):
        client = MagicMock()
        result = _invoke(
            CliRunner(),
            [
                "create",
                "--name", "robot1",
                "--type", "BOGUS",
                "--manufacturer", "JAKA",
                "--robot-model", "S101",
                "--workspace-id", "ws-1",
            ],
            client,
        )
        assert result.exit_code != 0
        assert "BOGUS" in result.output
        client.create_robot.assert_not_called()

    def test_create_accepts_type_enum_case_insensitive(self):
        client = MagicMock()
        client.create_robot.return_value = {"id": "r1"}
        result = _invoke(
            CliRunner(),
            [
                "create",
                "--name", "robot1",
                "--type", "humanoid",
                "--manufacturer", "JAKA",
                "--robot-model", "S101",
                "--workspace-id", "ws-1",
            ],
            client,
        )
        assert result.exit_code == 0, result.output
        req = client.create_robot.call_args.args[0]
        assert req["type"] == "HUMANOID"

    def test_create_type_help_shows_enum_choices(self):
        result = CliRunner().invoke(robot, ["create", "--help"])
        assert result.exit_code == 0, result.output
        assert "--type" in result.output
        assert "operation" in result.output

    def test_list_works_without_workspace_id(self):
        """workspace_id is optional (required=False) and defaults from the
        current_workspace() global; list must succeed without --workspace-id."""
        client = MagicMock()
        client.list_robots.return_value = {"robots": []}
        result = _invoke(CliRunner(), ["list", "--name", "robot1"], client)
        assert result.exit_code == 0, result.output
        client.list_robots.assert_called_once()

    def test_list_passes_all_filters(self):
        client = MagicMock()
        client.list_robots.return_value = {"robots": []}
        result = _invoke(
            CliRunner(),
            [
                "list",
                "--limit", "20",
                "--offset", "5",
                "--sort", "created_at:desc",
                "--name", "robot1",
                "--status", "online",
                "--manufacturer", "JAKA",
                "--robot-model", "S101",
                "--workspace-id", "ws-1",
                "--type", "HUMANOID",
                "--user-id", "u1",
                "--user-name", "zhangsan",
            ],
            client,
        )
        assert result.exit_code == 0, result.output
        client.list_robots.assert_called_once_with(
            limit=20,
            offset=5,
            sort="created_at:desc",
            name="robot1",
            status="online",
            manufacturer="JAKA",
            robot_model="S101",
            workspace_id="ws-1",
            type="HUMANOID",
            user_id="u1",
            user_name="zhangsan",
        )

    def test_list_rejects_invalid_limit(self):
        client = MagicMock()
        result = _invoke(CliRunner(), ["list", "--limit", "101"], client)
        assert result.exit_code != 0
        assert "101" in result.output
        client.list_robots.assert_not_called()

    def test_list_rejects_invalid_offset(self):
        client = MagicMock()
        result = _invoke(CliRunner(), ["list", "--offset", "-1"], client)
        assert result.exit_code != 0
        client.list_robots.assert_not_called()

    def test_list_rejects_invalid_type_enum(self):
        client = MagicMock()
        result = _invoke(CliRunner(), ["list", "--type", "BOGUS"], client)
        assert result.exit_code != 0
        assert "BOGUS" in result.output
        client.list_robots.assert_not_called()

    def test_list_rejects_overlong_sort_filter(self):
        # QUERY_PARAM_RULES.sort.max_length = 64 -> `--sort` must be validated
        client = MagicMock()
        bad = "z" * 65
        result = _invoke(CliRunner(), ["list", "--sort", bad], client)
        assert result.exit_code != 0
        assert "sort" in result.output.lower()
        client.list_robots.assert_not_called()

    def test_list_rejects_overlong_user_name_filter(self):
        # QUERY_PARAM_RULES.user_name.max_length = 32 -> callbacks present
        client = MagicMock()
        bad = "u" * 33
        result = _invoke(CliRunner(), ["list", "--user-name", bad], client)
        assert result.exit_code != 0
        client.list_robots.assert_not_called()

    def test_show_passes_robot_id(self):
        client = MagicMock()
        client.show_robot.return_value = {"id": "r1"}
        result = _invoke(CliRunner(), ["show", "--robot-id", _ROBOT_ID], client)
        assert result.exit_code == 0, result.output
        client.show_robot.assert_called_once_with(_ROBOT_ID)

    def test_show_requires_robot_id(self):
        result = CliRunner().invoke(robot, ["show"])
        assert result.exit_code != 0

    def test_update_passes_optional_fields(self):
        client = MagicMock()
        client.update_robot.return_value = {"id": "r1"}
        result = _invoke(
            CliRunner(),
            ["update", "--robot-id", _ROBOT_ID, "--name", "robot1", "--description", "d1", "--workspace-id", "ws-2"],
            client,
        )
        assert result.exit_code == 0, result.output
        req = client.update_robot.call_args.args[1]
        assert req == {"name": "robot1", "description": "d1", "workspace_id": "ws-2"}

    def test_update_dry_run_skips_client(self):
        client = MagicMock()
        result = _invoke(CliRunner(), ["update", "--robot-id", _ROBOT_ID, "--dry-run"], client)
        assert result.exit_code == 0, result.output
        client.update_robot.assert_not_called()
        assert "DRY-RUN" in result.output

    def test_delete_calls_client(self):
        client = MagicMock()
        client.delete_robot.return_value = None
        result = _invoke(CliRunner(), ["delete", "--robot-id", _ROBOT_ID], client)
        assert result.exit_code == 0, result.output
        client.delete_robot.assert_called_once_with(_ROBOT_ID)

    def test_delete_rejects_overlong_robot_id(self):
        client = MagicMock()
        bad = "x" * 65  # PATH_PARAM_RULES.robot_id.max_length = 64
        result = _invoke(CliRunner(), ["delete", "--robot-id", bad], client)
        assert result.exit_code != 0
        assert "Invalid value" in result.output
        client.delete_robot.assert_not_called()

    def test_delete_dry_run_skips_client(self):
        client = MagicMock()
        result = _invoke(CliRunner(), ["delete", "--robot-id", _ROBOT_ID, "--dry-run"], client)
        assert result.exit_code == 0, result.output
        client.delete_robot.assert_not_called()
        assert "DRY-RUN" in result.output

    def test_export_certificate_passes_password(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("cert.pem", "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----")
        client = MagicMock()
        client.show_robot.return_value = {"name": "test-robot"}
        client.export_robot_certificate.return_value = buf.getvalue()
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.makedirs("certs")
            result = _invoke(
                runner,
                ["export-certificate", "--robot-id", _ROBOT_ID, "--password", "secret", "--output", "certs"],
                client,
            )
        assert result.exit_code == 0, result.output
        client.show_robot.assert_called_once_with(_ROBOT_ID)
        client.export_robot_certificate.assert_called_once_with(_ROBOT_ID, {"password": "secret"})

    def test_export_certificate_dry_run_skips_client(self):
        client = MagicMock()
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.makedirs("certs")
            result = _invoke(
                runner,
                ["export-certificate", "--robot-id", _ROBOT_ID, "--output", "certs", "--dry-run"],
                client,
            )
        assert result.exit_code == 0, result.output
        client.show_robot.assert_not_called()
        client.export_robot_certificate.assert_not_called()
        assert "DRY-RUN" in result.output

    def test_export_certificate_writes_valid_zip_to_output(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("cert.pem", "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----")
            zf.writestr("config.yaml", "robot: s101\n")
        zip_bytes = buf.getvalue()

        client = MagicMock()
        client.show_robot.return_value = {"name": "test-robot"}
        client.export_robot_certificate.return_value = zip_bytes

        runner = CliRunner()
        with runner.isolated_filesystem():
            os.makedirs("certs")
            result = _invoke(
                runner,
                ["export-certificate", "--robot-id", _ROBOT_ID, "--output", "certs"],
                client,
            )
            assert result.exit_code == 0, result.output
            files = glob.glob("certs/cert_config_test-robot_*.zip")
            assert len(files) == 1
            out_path = files[0]
            assert zipfile.is_zipfile(out_path)
            with zipfile.ZipFile(out_path) as zf:
                assert zf.read("cert.pem") == b"-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
                assert zf.read("config.yaml") == b"robot: s101\n"
            assert "certificate written to" in result.output
        client.show_robot.assert_called_once_with(_ROBOT_ID)
        client.export_robot_certificate.assert_called_once_with(_ROBOT_ID, {})

    def test_export_certificate_fails_when_output_not_directory(self):
        client = MagicMock()
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = _invoke(
                runner,
                ["export-certificate", "--robot-id", _ROBOT_ID, "--output", "nonexistent"],
                client,
            )
        assert result.exit_code != 0
        assert "不存在" in result.output or "不是目录" in result.output
        client.show_robot.assert_not_called()

    def test_export_certificate_fails_when_file_already_exists(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("cert.pem", "data")
        client = MagicMock()
        client.show_robot.return_value = {"name": "test-robot"}
        client.export_robot_certificate.return_value = buf.getvalue()

        runner = CliRunner()
        with runner.isolated_filesystem():
            os.makedirs("certs")
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            existing = os.path.join("certs", f"cert_config_test-robot_{ts}.zip")
            with open(existing, "wb") as f:
                f.write(b"old")
            result = _invoke(
                runner,
                ["export-certificate", "--robot-id", _ROBOT_ID, "--output", "certs"],
                client,
            )
        assert result.exit_code != 0
        assert "已存在" in result.output
        assert "导出已取消" in result.output

    def test_show_sdk_returns_info(self):
        client = MagicMock()
        client.show_sdk.return_value = {"file_name": "sdk.zip", "version": "1.0.0"}
        result = _invoke(CliRunner(), ["show-sdk"], client)
        assert result.exit_code == 0, result.output
        client.show_sdk.assert_called_once_with()
