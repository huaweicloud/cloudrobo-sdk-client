import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from cloudrobo_core.sdk.obs_client import OBSClient


def test_upload_file_preserves_default_call_and_forwards_progress(tmp_path):
    path = tmp_path / "data"
    path.write_bytes(b"abc")
    obs = OBSClient.__new__(OBSClient)
    obs.bucket_name = "bucket"
    obs.client = MagicMock()
    obs.client.uploadFile.return_value = SimpleNamespace(status=200)
    assert obs.upload_file(str(path), "key")
    assert "progressCallback" not in obs.client.uploadFile.call_args.kwargs
    callback = MagicMock()
    assert obs.upload_file(
        str(path), "key", progress_callback=callback, checkpoint_file="checkpoint"
    )
    assert obs.client.uploadFile.call_args.kwargs["progressCallback"] is callback
    assert obs.client.uploadFile.call_args.kwargs["checkpointFile"] == "checkpoint"


@pytest.mark.parametrize("style", ["legacy_keyword", "legacy_positional", "snake_case", "both"])
def test_integration_upload_progress_callback_compatibility(tmp_path, style, caplog):
    path = tmp_path / "data"
    path.write_bytes(b"abc")
    obs = OBSClient.__new__(OBSClient)
    obs.bucket_name = "bucket"
    obs.client = MagicMock()
    obs.client.uploadFile.return_value = SimpleNamespace(status=200)
    callback = MagicMock()
    caplog.set_level("INFO", logger="cloudrobo_core.sdk.obs_client")
    if style == "legacy_positional":
        assert obs.upload_file(str(path), "key", None, True, 9437184, callback)
    else:
        options = {"checkpoint_file": "checkpoint"}
        if style in ("legacy_keyword", "both"):
            options["progressCallback"] = callback if style == "legacy_keyword" else MagicMock()
        if style in ("snake_case", "both"):
            options["progress_callback"] = callback
        assert obs.upload_file(str(path), "key", **options)
        assert obs.client.uploadFile.call_args.kwargs["checkpointFile"] == "checkpoint"
    assert obs.client.uploadFile.call_args.kwargs["progressCallback"] is callback
    assert "File uploaded successfully" not in caplog.text


def test_failed_multipart_abort_keeps_checkpoint(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps(dict(bucketName="bucket", objectKey="key", uploadId="upload")))
    obs = OBSClient.__new__(OBSClient)
    obs.client = MagicMock()
    obs.client.abortMultipartUpload.return_value = SimpleNamespace(status=500)
    assert not obs.abort_checkpoint_upload(str(path))
    assert path.exists()
    obs.client.abortMultipartUpload.return_value = SimpleNamespace(status=404)
    assert obs.abort_checkpoint_upload(str(path))
    assert not path.exists()


def test_upload_failure_details_and_success_reset(tmp_path, caplog):
    path = tmp_path / "data"
    path.write_bytes(b"abc")
    obs = OBSClient.__new__(OBSClient)
    obs.bucket_name = "bucket"
    obs.access_key, obs.secret_key = "sensitive-ak", "sensitive-sk"
    obs.client = MagicMock()
    obs.client.uploadFile.return_value = SimpleNamespace(
        status=403,
        errorCode="AccessDenied",
        requestId="request-123",
        errorMessage="Permission denied sensitive-ak sensitive-sk token=private-token",
    )
    assert not obs.upload_file(str(path), "key")
    assert obs.last_upload_error["http_status"] == "403"
    assert obs.last_upload_error["error_code"] == "AccessDenied"
    assert obs.last_upload_error["request_id"] == "request-123"
    diagnostics = json.dumps(obs.last_upload_error) + caplog.text
    for value in ("sensitive-ak", "sensitive-sk", "private-token"):
        assert value not in diagnostics
    obs.client.uploadFile.return_value = SimpleNamespace(status=200)
    assert obs.upload_file(str(path), "key")
    assert obs.last_upload_error is None


@pytest.mark.parametrize("error", [PermissionError(13, "checkpoint cannot be written"), None])
def test_upload_exception_or_missing_response(tmp_path, error):
    path = tmp_path / "data"
    path.write_bytes(b"abc")
    obs = OBSClient.__new__(OBSClient)
    obs.bucket_name = "bucket"
    obs.client = MagicMock()
    if error:
        error.winerror = 5
        obs.client.uploadFile.side_effect = error
    else:
        obs.client.uploadFile.return_value = None
    assert not obs.upload_file(str(path), "key")
    if error:
        assert obs.last_upload_error["exception_type"] == "PermissionError"
        assert obs.last_upload_error["errno"] == 13
        assert obs.last_upload_error["winerror"] == 5
        assert any(f["function"] == "upload_file" for f in obs.last_upload_error["stack"])
    else:
        assert "no response" in obs.last_upload_error["message"]


def test_diagnostic_redaction_preserves_network_reason(monkeypatch):
    from cloudrobo_core.sdk.obs_diagnostics import credential_values, exception_details

    monkeypatch.setenv("HUAWEI_CLOUD_SK", "raw-private-sk")
    try:
        raise OSError(
            "TLS failed raw-private-sk https://user:pwd@obs.example/key?Signature=secret-query "
            '<SecurityToken>xml-private</SecurityToken> "secret_key": "json-private"'
        )
    except OSError as exc:
        details = exception_details(exc, credential_values())
    output = json.dumps(details)
    assert "TLS failed" in output
    assert "obs.example/key" in output
    for value in ("raw-private-sk", "user:pwd", "secret-query", "xml-private", "json-private"):
        assert value not in output
    assert "stack" in details
    assert all(set(frame) == {"file", "line", "function"} for frame in details["stack"])


def test_sdk_multipart_error_capture_is_bounded_sanitized_and_restored(tmp_path):
    import logging

    path = tmp_path / "data"
    path.write_bytes(b"data")
    obs = OBSClient.__new__(OBSClient)
    obs.bucket_name = "bucket"
    obs.secret_key = "private-sdk-key"
    obs.client = MagicMock()
    original_log = obs.client.log_client

    def upload(**kwargs):
        for i in range(20):
            obs.client.log_client.log(logging.ERROR, "part %d failed private-sdk-key", i)
        obs.client.log_client.log(logging.DEBUG, "do not collect headers")
        raise Exception("some parts are failed when upload")

    obs.client.uploadFile.side_effect = upload
    assert not obs.upload_file(str(path), "key")
    assert obs.client.log_client is original_log
    assert len(obs.last_upload_error["sdk_errors"]) == 5
    assert "part 19 failed" in obs.last_upload_error["sdk_errors"][-1]
    assert "private-sdk-key" not in str(obs.last_upload_error)
    assert "do not collect headers" not in str(obs.last_upload_error)
