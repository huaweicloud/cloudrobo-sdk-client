import base64
import io
import json
import zipfile
from unittest.mock import MagicMock

import pytest
from cloudrobo_core.sdk import Config, HttpClient
from cloudrobo_core.sdk.exceptions import PathTraversalError
from cloudrobo_robot.client import RobotClient


def _make_mock_client():
    mock = MagicMock(spec=HttpClient)
    mock.config = MagicMock(spec=Config)
    mock.config.get_endpoint.side_effect = lambda svc: f"https://api.example.com/{svc}"
    mock.config.project_id = "proj1"
    return mock


def _inject_path_traversal():
    return pytest.raises(PathTraversalError)


class TestRobotClient:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = RobotClient(self.mock_http)

    def test_service_name(self):
        assert RobotClient.SERVICE == "cloudrobo-service"

    def test_create_robot_posts_to_robots(self):
        self.mock_http.post.return_value = {"id": "robot1"}
        req = {
            "name": "arm-1",
            "type": "ARM",
            "manufacturer": "hms",
            "robot_model": "model-x",
            "workspace_id": "ws-1",
        }
        result = self.client.create_robot(req)
        assert result["id"] == "robot1"
        args, kwargs = self.mock_http.post.call_args
        assert "/v1/robots" in args[0]
        assert kwargs["json"] == req

    def test_list_robots_passes_params(self):
        self.mock_http.get.return_value = {"robots": []}
        result = self.client.list_robots(limit=10, offset=0, workspace_id="ws-1")
        assert "robots" in result
        _, kwargs = self.mock_http.get.call_args
        assert kwargs["params"] == {"limit": 10, "offset": 0, "workspace_id": "ws-1"}

    def test_show_robot_uses_robot_id_path(self):
        self.mock_http.get.return_value = {"id": "r1", "name": "arm-1"}
        result = self.client.show_robot("r1")
        assert result["id"] == "r1"
        args, _ = self.mock_http.get.call_args
        assert "/v1/robots/r1" in args[0]

    def test_update_robot_uses_robot_id_path_and_req(self):
        self.mock_http.put.return_value = {"id": "r1", "name": "updated"}
        result = self.client.update_robot("r1", {"workspace_id": "ws-1", "name": "updated"})
        assert result["name"] == "updated"
        args, kwargs = self.mock_http.put.call_args
        assert "/v1/robots/r1" in args[0]
        assert kwargs["json"] == {"workspace_id": "ws-1", "name": "updated"}

    def test_delete_robot_uses_robot_id_path(self):
        self.mock_http.delete.return_value = None
        result = self.client.delete_robot("r1")
        assert result is None
        args, _ = self.mock_http.delete.call_args
        assert "/v1/robots/r1" in args[0]

    def test_export_robot_certificate_uses_path_and_req(self):
        self.mock_http.post.return_value = b"certificate-bytes"
        result = self.client.export_robot_certificate("r1", {"password": "secret"})
        assert result == b"certificate-bytes"
        args, kwargs = self.mock_http.post.call_args
        assert "/v1/robots/r1/certificate/export" in args[0]
        assert kwargs["json"] == {"password": "secret"}

    def test_export_robot_certificate_passes_raw_true(self):
        self.mock_http.post.return_value = b"raw"
        self.client.export_robot_certificate("r1", {})
        _, kwargs = self.mock_http.post.call_args
        assert kwargs["raw"] is True

    def test_export_robot_certificate_returns_raw_zip_bytes(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("cert.pem", "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----")
        zip_bytes = buf.getvalue()
        self.mock_http.post.return_value = zip_bytes
        result = self.client.export_robot_certificate("r1", {})
        assert result == zip_bytes
        assert zipfile.is_zipfile(io.BytesIO(result))

    def test_export_robot_certificate_decodes_base64_json_wrapper(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("config.yaml", "robot: s101\n")
        zip_bytes = buf.getvalue()
        encoded = base64.b64encode(zip_bytes).decode("ascii")
        self.mock_http.post.return_value = json.dumps({"content": encoded}).encode("utf-8")
        result = self.client.export_robot_certificate("r1", {})
        assert result == zip_bytes
        assert zipfile.is_zipfile(io.BytesIO(result))

    def test_show_sdk_gets_robots_sdk(self):
        self.mock_http.get.return_value = {"file_name": "sdk.zip", "version": "1.0.0"}
        result = self.client.show_sdk()
        assert result["file_name"] == "sdk.zip"
        args, _ = self.mock_http.get.call_args
        assert "/v1/robots/sdk" in args[0]

    def test_show_robot_rejects_path_traversal(self):
        with _inject_path_traversal():
            self.client.show_robot("../etc/passwd")

    def test_show_robot_rejects_empty_id(self):
        with _inject_path_traversal():
            self.client.show_robot("")

    def test_update_robot_rejects_path_traversal(self):
        with _inject_path_traversal():
            self.client.update_robot("a/../b", {"workspace_id": "ws-1", "name": "new-name-1"})

    def test_delete_robot_rejects_path_traversal(self):
        with _inject_path_traversal():
            self.client.delete_robot("a\\b")

    def test_export_robot_certificate_rejects_path_traversal(self):
        with _inject_path_traversal():
            self.client.export_robot_certificate("a/../b", {"password": ""})
