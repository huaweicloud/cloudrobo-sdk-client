import pytest
from unittest.mock import MagicMock

from cloudrobo_core.sdk import Config, HttpClient


def _make_mock_config():
    mock = MagicMock(spec=Config)
    mock.get_endpoint.side_effect = lambda svc: f"https://api.example.com/{svc}"
    mock.project_id = "proj1"
    return mock


class TestConfig:
    def test_get_endpoint(self):
        config = _make_mock_config()
        endpoint = config.get_endpoint("asset-manager")
        assert endpoint == "https://api.example.com/asset-manager"

    def test_project_id(self):
        config = _make_mock_config()
        assert config.project_id == "proj1"


class TestHttpClient:
    def setup_method(self):
        self.config = _make_mock_config()
        self.client = HttpClient(self.config)

    @staticmethod
    def _make_response(status_code=200, content=b"", json_data=None, text=""):
        resp = MagicMock()
        resp.status_code = status_code
        resp.content = content
        if json_data is not None:
            resp.json.return_value = json_data
        else:
            resp.json.side_effect = ValueError("no json")
        resp.text = text
        return resp

    def test_handle_response_raw_true_returns_raw_bytes(self):
        client = HttpClient.__new__(HttpClient)
        resp = self._make_response(200, content=b"\x50\x4b\x03\x04zip", text="decoded")
        assert client._handle_response(resp, raw=True) == b"\x50\x4b\x03\x04zip"

    def test_handle_response_default_returns_parsed_json(self):
        client = HttpClient.__new__(HttpClient)
        resp = self._make_response(200, content=b"ignored", json_data={"a": 1})
        assert client._handle_response(resp) == {"a": 1}

    def test_handle_response_default_falls_back_to_text(self):
        client = HttpClient.__new__(HttpClient)
        resp = self._make_response(200, content=b"ignored", text="not json")
        assert client._handle_response(resp) == "not json"

    def test_post_forwards_raw_to_request(self):
        client = HttpClient.__new__(HttpClient)
        client.request = MagicMock(return_value=b"x")
        result = client.post("http://example.com/x", json={"a": 1}, raw=True)
        client.request.assert_called_once_with(
            "POST", "http://example.com/x", json={"a": 1}, raw=True
        )
        assert result == b"x"
