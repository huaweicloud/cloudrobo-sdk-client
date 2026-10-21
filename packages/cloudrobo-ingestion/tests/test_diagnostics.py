import json
import logging
import socket
import sqlite3
import ssl
import time
from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner
from cloudrobo_ingestion.cli import ingestion
from cloudrobo_ingestion.diagnostics import DiagnosticError, ErrorTracker, make_error
from cloudrobo_ingestion.progress import DisabledReporter, Reporter


@pytest.mark.parametrize(
    "exc,suffix",
    [
        (socket.gaierror(-2, "Name or service not known"), "DNS_FAILED"),
        (ConnectionRefusedError("connection refused"), "CONNECT_FAILED"),
        (TimeoutError("timed out"), "TIMEOUT"),
        (ssl.SSLError("certificate verify failed"), "TLS_FAILED"),
    ],
)
@pytest.mark.parametrize("component", ["obs", "report"])
def test_network_diagnostic_classification(exc, suffix, component):
    info = make_error(component, "uploading" if component == "obs" else "connect", exc)
    assert info["code"] == f"{component.upper()}_{suffix}"
    assert info["hint"] and info["details"]["exception_type"]
    assert "失败" in info["message"]


@pytest.mark.parametrize(
    "details,code",
    [
        (dict(http_status=403, error_code="AccessDenied"), "OBS_ACCESS_DENIED"),
        (dict(error_code="NoSuchBucket"), "OBS_BUCKET_NOT_FOUND"),
        (dict(error_code="SignatureDoesNotMatch"), "OBS_AUTH_FAILED"),
        (dict(message="SDK returned failure"), "OBS_UPLOAD_FAILED"),
    ],
)
def test_obs_response_and_unknown_reason(details, code):
    info = make_error("obs", "uploading", diagnostic=details)
    assert info["code"] == code
    assert info["message"].startswith("OBS 上传失败")
    if details.get("message"):
        assert "无法解析" not in info["message"]


def test_nested_cause_and_redaction(monkeypatch):
    monkeypatch.setenv("HUAWEI_CLOUD_SK", "private-value")
    try:
        try:
            raise socket.gaierror(-2, "Name or service not known private-value")
        except Exception as cause:
            raise RuntimeError("upload failed") from cause
    except RuntimeError as exc:
        info = make_error(
            "obs",
            "uploading",
            exc,
            context=dict(
                endpoint="https://user:pass@obs.invalid?signature=private-query",
                token="private-token",
            ),
        )
    assert info["code"] == "OBS_DNS_FAILED"
    text = json.dumps(info)
    for value in ("private-value", "private-query", "private-token", "user:pass"):
        assert value not in text
    assert len(info["details"]["causes"]) == 2


def test_tracker_log_throttle_and_recovery(caplog):
    clock = [0.0]
    tracker = ErrorTracker(logging.getLogger("diagnostics-test"), clock=lambda: clock[0])
    caplog.set_level(logging.INFO)
    first = tracker.failed(
        make_error("report", "connect", ConnectionRefusedError("connection refused"))
    )
    for i in range(100):
        tracker.failed(
            make_error("report", "connect", ConnectionRefusedError("connection refused"))
        )
    assert tracker.info["error_id"] == first["error_id"]
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1
    clock[0] = 60
    tracker.failed(make_error("report", "connect", ConnectionRefusedError("connection refused")))
    assert "重复次数=102" in caplog.text
    tracker.recovered()
    assert tracker.info is None and "异常已恢复" in caplog.text


def wait_for(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("diagnostic not observed")


@pytest.mark.parametrize("failure_stage", ["connect", "publish"])
def test_reporter_failure_close_does_not_mask_and_recovers(failure_stage, caplog):
    offline = [True]

    class Transport:
        def publish(self, *args):
            if offline[0]:
                raise ConnectionRefusedError("connection refused")

        def close(self):
            raise RuntimeError("secondary close error")

    def factory(path):
        if offline[0] and failure_stage == "connect":
            raise ConnectionRefusedError("connection refused")
        return Transport()

    caplog.set_level(logging.INFO)
    reporter = Reporter(
        "unused", "topic", factory=factory, interval=0.02, endpoints=["tcp/localhost:7447"]
    )
    reporter.set("batch", b"data", terminal=True)
    reporter.start()
    try:
        state = wait_for(
            lambda: reporter.diagnostics() if reporter.diagnostics()["error_info"] else None
        )
        assert state["error_info"]["component"] == "report"
        assert state["error_info"]["stage"] == failure_stage
        assert state["error_info"]["context"]["endpoints"] == ["tcp/localhost:7447"]
        offline[0] = False
        wait_for(lambda: reporter.diagnostics()["connected"])
        assert reporter.diagnostics()["error_info"] is None
        assert reporter.acks.get(timeout=1) == ("batch", b"data")
        assert "异常已恢复" in caplog.text
    finally:
        reporter.close()


def test_serialization_is_local_and_does_not_block_other_batches():
    calls = []

    class Broken:
        def serialize(self):
            raise TypeError("bad protobuf field")

    class Transport:
        def publish(self, topic, payload):
            calls.append(payload)

        def close(self):
            pass

    reporter = Reporter("unused", "topic", factory=lambda _: Transport(), interval=0.02)
    reporter.set("bad", Broken(), terminal=True)
    reporter.set("good", b"good", terminal=True)
    reporter.start()
    try:
        wait_for(lambda: calls)
        info = reporter.diagnostics()["error_info"]
        assert info["component"] == "local" and info["stage"] == "serialize"
        reporter.set("bad", b"fixed", terminal=True)
        wait_for(lambda: b"fixed" in calls)
        assert reporter.diagnostics()["error_info"] is None
    finally:
        reporter.close()


def test_cli_displays_simultaneous_errors_and_json(monkeypatch):
    from cloudrobo_ingestion import lifecycle

    obs = make_error(
        "obs",
        "uploading",
        socket.gaierror(-2, "Name or service not known"),
        context=dict(endpoint="https://obs.invalid", file="data.parquet"),
    )
    report = make_error(
        "report",
        "connect",
        ConnectionRefusedError("connection refused"),
        context=dict(endpoints=["tcp/127.0.0.1:7447"]),
    )
    state = dict(
        running=True,
        error_info=obs,
        batch_errors=[dict(path="batch", error_info=obs)],
        reporter=dict(enabled=True, connected=False, errors=[report]),
    )
    monkeypatch.setattr(lifecycle, "status", lambda: state)
    text = CliRunner().invoke(ingestion, ["status"])
    assert text.exit_code == 0, text.output
    assert "OBS 上传异常" in text.output and "进度上报异常" in text.output
    assert text.output.count("错误 ID：" + obs["error_id"]) == 1
    assert "https://obs.invalid" in text.output and "建议：" in text.output
    output = CliRunner().invoke(ingestion, ["status", "--json"])
    assert json.loads(output.output)["error_info"] == obs


@pytest.mark.parametrize(
    "exc,stage",
    [
        (RuntimeError("program fault"), "runtime"),
        (sqlite3.OperationalError("database is locked"), "database"),
    ],
)
def test_fatal_error_retained_for_offline_status(config, monkeypatch, exc, stage):
    from cloudrobo_ingestion import lifecycle
    from cloudrobo_ingestion.daemon import Supervisor

    config = replace(config, run_mode="upload_only")
    path = config.work_dir / "status.json"
    supervisor = Supervisor(config, DisabledReporter(), path)
    monkeypatch.setattr(supervisor, "scan", lambda: (_ for _ in ()).throw(exc))
    with pytest.raises(DiagnosticError):
        supervisor.run()
    monkeypatch.setattr(lifecycle, "runtime_dir", lambda: config.work_dir)
    state = lifecycle.status()
    assert state["state"] == "failed" and not state["running"]
    assert state["error_info"]["stage"] == stage


def test_wrong_obs_endpoint_survives_sdk_wrapper_and_worker(config, batch_dir, monkeypatch):
    from cloudrobo_core.sdk import obs_client
    from cloudrobo_ingestion.filesystem import manifest
    from cloudrobo_ingestion.store import BatchStore
    from cloudrobo_ingestion.worker import run_worker
    from test_worker_supervisor import Connection

    monkeypatch.setenv("CLOUDROBO_OBS_ENDPOINT", "https://obs.example.invalid")
    monkeypatch.setenv("HUAWEI_CLOUD_AK", "private-ak")
    monkeypatch.setenv("HUAWEI_CLOUD_SK", "private-sk")
    sdk = MagicMock()
    sdk.uploadFile.side_effect = socket.gaierror(-2, "Name or service not known private-sk")
    monkeypatch.setattr(obs_client, "ObsClient", lambda **kwargs: sdk)
    store = BatchStore(config)
    batch = store.add("批次001", config)
    connection = Connection()
    run_worker(config, batch, manifest(batch_dir), connection)
    result = next(e for e in connection.messages if e["event"] == "result")
    info = result["error_info"]
    assert info["component"] == "obs" and info["code"] == "OBS_DNS_FAILED"
    assert info["context"]["endpoint"] == "https://obs.example.invalid"
    assert info["context"]["file"] == "data/file-000.parquet"
    assert "无法解析服务器地址" in result["error"]
    store.update(batch["id"], state="failed", error=result["error"], error_info=info)
    store.close()
    store = BatchStore(config)
    assert store.get(batch["id"])["error_info"] == info
    assert any(info["error_id"] in e.get("message", "") for e in connection.messages)
    assert "private-ak" not in str(connection.messages) and "private-sk" not in str(
        connection.messages
    )
    store.close()


def test_cleanup_error_keeps_original_obs_error(config, monkeypatch):
    from cloudrobo_ingestion import daemon

    supervisor = daemon.Supervisor(config, DisabledReporter(), config.work_dir / "status.json")
    batch = supervisor.store.add("batch", config)
    original = make_error(
        "obs", "uploading", diagnostic=dict(http_status=403, error_code="AccessDenied")
    )
    supervisor.batch_error(batch, original, state="failed")
    monkeypatch.setattr(
        daemon,
        "remove_staging",
        lambda _: (_ for _ in ()).throw(PermissionError(13, "staging locked")),
    )
    assert not supervisor.cleanup(batch)
    errors = supervisor.snapshot()["batch_errors"]
    assert {e["error_info"]["component"] for e in errors} == {"local", "obs"}
    assert supervisor.store.get(batch["id"])["error_info"] == original
    supervisor.store.close()


def test_status_lock_permission_is_not_reported_as_stopped(tmp_path, monkeypatch):
    from cloudrobo_ingestion import lifecycle

    monkeypatch.setattr(lifecycle, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(
        lifecycle, "running", lambda _: (_ for _ in ()).throw(PermissionError(13, "lock denied"))
    )
    state = lifecycle.status()
    assert state["state"] == "unknown"
    text = CliRunner().invoke(ingestion, ["status"])
    assert "状态无法确定" in text.output and "本地处理异常" in text.output


def test_cli_yaml_error_is_explained(config_file):
    config_file.write_text("source_dir: [broken")
    result = CliRunner().invoke(ingestion, ["start", "--config", str(config_file)])
    assert result.exit_code != 0
    assert "本地处理异常" in result.output and "读取配置" in result.output
    assert "Traceback" not in result.output


def test_startup_log_failure_names_path(config_file, tmp_path, monkeypatch):
    from cloudrobo_ingestion import daemon, lifecycle

    monkeypatch.setenv("CLOUDROBO_INGESTION_RUNTIME_DIR", str(tmp_path / "runtime"))
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime/status.json").write_text(
        json.dumps(dict(work_dir="previous-work", batch_errors=["old failure"]))
    )
    monkeypatch.setattr(
        daemon,
        "RotatingFileHandler",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError(13, "log access denied")),
    )
    with pytest.raises(DiagnosticError) as caught:
        daemon.main(config_file, tmp_path / "handshake.json")
    info = caught.value.info
    assert info["stage"] == "logging"
    assert info["context"]["path"].endswith("ingestion.log")
    assert lifecycle.status()["error_info"]["error_id"] == info["error_id"]
    assert lifecycle.status()["work_dir"] == str(tmp_path / "work")
    assert "batch_errors" not in lifecycle.status()


def test_status_write_failure_preserves_primary_disk_error(config, monkeypatch):
    from cloudrobo_ingestion import daemon

    config = replace(config, run_mode="upload_only")
    supervisor = daemon.Supervisor(config, DisabledReporter(), config.work_dir / "status.json")
    original = daemon.write_json
    calls = []

    def write(path, value):
        calls.append(1)
        if len(calls) == 1:
            raise OSError(28, "No space left on device")
        original(path, value)

    monkeypatch.setattr(daemon, "write_json", write)
    with pytest.raises(DiagnosticError) as caught:
        supervisor.run()
    state = json.loads((config.work_dir / "status.json").read_text(encoding="utf-8"))
    assert state["state"] == "failed"
    assert state["error_info"]["code"] == "LOCAL_DISK_FULL"
    assert state["error_info"]["stage"] == "status"
    assert state["error_info"]["error_id"] == caught.value.info["error_id"]


def test_ipc_error_keeps_actual_failure_stage(config):
    from cloudrobo_ingestion.daemon import Supervisor

    class BrokenPipe:
        def poll(self):
            raise OSError(9, "Bad file descriptor")

    supervisor = Supervisor(config, DisabledReporter(), config.work_dir / "status.json")
    supervisor.pipe = BrokenPipe()
    try:
        with pytest.raises(DiagnosticError) as caught:
            supervisor._drain_worker_events()
        assert caught.value.info["stage"] == "ipc"
        assert caught.value.info["details"]["errno"] == 9
    finally:
        supervisor.store.close()
