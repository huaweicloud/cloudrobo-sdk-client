import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from cloudrobo_ingestion.cli import ingestion
from cloudrobo_ingestion.lifecycle import InstanceLock, running, start, status, stop, write_json


def test_atomic_status_retries_busy_windows_reader(tmp_path, monkeypatch):
    original = os.replace
    attempts = []

    def replace(source, dest):
        attempts.append(1)
        if len(attempts) == 1:
            raise PermissionError("busy reader")
        original(source, dest)

    monkeypatch.setattr(os, "replace", replace)
    path = tmp_path / "status.json"
    write_json(path, {"state": "running"})
    assert json.loads(path.read_text()) == {"state": "running"}
    assert len(attempts) == 2


def test_single_instance_lock(tmp_path):
    with InstanceLock(tmp_path / "instance.lock"):
        assert running(tmp_path)
        with pytest.raises(RuntimeError, match="运行"):
            with InstanceLock(tmp_path / "instance.lock"):
                pass
    assert not running(tmp_path)


def test_cli_status_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDROBO_INGESTION_RUNTIME_DIR", str(tmp_path))
    result = CliRunner().invoke(ingestion, ["status", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["running"] is False
    result = CliRunner().invoke(ingestion, ["stop"])
    assert result.exit_code == 0


@pytest.mark.parametrize("persistent", [False, True])
def test_status_retries_busy_windows_replacement(tmp_path, monkeypatch, persistent):
    from cloudrobo_ingestion import lifecycle

    monkeypatch.setenv("CLOUDROBO_INGESTION_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(lifecycle, "running", lambda _: True)
    write_json(tmp_path / "status.json", dict(run_mode="upload_and_report", error="上传失败"))
    original = Path.read_text
    attempts = []

    def read(path, *args, **kwargs):
        if path == tmp_path / "status.json":
            attempts.append(1)
            if persistent or len(attempts) < 3:
                raise PermissionError(13, "status replacement busy")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    state = status()
    assert state["running"]
    if persistent:
        assert len(attempts) == 5
        assert state["error_info"]["stage"] == "status"
    else:
        assert len(attempts) == 3
        assert state["run_mode"] == "upload_and_report"
        assert state["error"] == "上传失败"


def test_actual_detached_daemon_lifecycle(config_file, tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDROBO_INGESTION_RUNTIME_DIR", str(tmp_path / "runtime"))
    # No data/OBS requests: test the real lock, startup handshake and IPC control.
    monkeypatch.setenv("HUAWEI_CLOUD_AK", "test-ak")
    monkeypatch.setenv("HUAWEI_CLOUD_SK", "test-sk")
    result = start(config_file)
    try:
        assert result["pid"] != os.getpid()
        assert status()["running"]
        with pytest.raises(RuntimeError, match="运行"):
            start(config_file)
        assert stop()["running"] is False
        assert not running()
    finally:
        if running():
            stop()


def test_start_failure_propagates_and_releases_lock(config_file, tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDROBO_INGESTION_RUNTIME_DIR", str(tmp_path / "runtime"))
    # Config succeeds but the runtime binding check in the detached child fails.
    import yaml
    from cloudrobo_ingestion.config import IngestionConfig
    from cloudrobo_ingestion.store import BatchStore

    c = IngestionConfig.load(config_file)
    c.work_dir.mkdir()
    BatchStore(c).close()
    data = yaml.safe_load(config_file.read_text())
    other_source = config_file.parent / "different-source"
    other_source.mkdir()
    data["source_dir"] = str(other_source)
    config_file.write_text(yaml.safe_dump(data))
    monkeypatch.setenv("HUAWEI_CLOUD_AK", "test-ak")
    monkeypatch.setenv("HUAWEI_CLOUD_SK", "test-sk")
    with pytest.raises(RuntimeError, match="绑定"):
        start(config_file)
    assert not running()


@pytest.mark.parametrize(
    "failure, error",
    [
        ("serialize", OSError),
        ("spawn", OSError),
        ("handshake", json.JSONDecodeError),
        ("exit", RuntimeError),
        ("timeout", RuntimeError),
    ],
)
def test_start_removes_temporary_files_on_failure(
    config_file, tmp_path, monkeypatch, failure, error
):
    from cloudrobo_ingestion import lifecycle

    monkeypatch.setenv("CLOUDROBO_INGESTION_RUNTIME_DIR", str(tmp_path / "runtime"))
    process = Mock()
    process.poll.return_value = 1

    def popen(command, **kwargs):
        effective = Path(command[-1])
        assert effective.is_file()
        if os.name != "nt":
            assert stat.S_IMODE(effective.stat().st_mode) == 0o600
            assert stat.S_IMODE(effective.parent.stat().st_mode) == 0o700
        if failure == "spawn":
            raise OSError("spawn failed")
        if failure == "handshake":
            Path(command[-2]).write_text("invalid JSON", encoding="utf-8")
        return process

    monkeypatch.setattr(lifecycle.subprocess, "Popen", popen)
    if failure == "serialize":

        def dump(value, stream, **kwargs):
            stream.write("partial config")
            raise OSError("config write failed")

        monkeypatch.setattr(lifecycle.json, "dump", dump)
    if failure == "timeout":
        ticks = iter([0, 21])
        monkeypatch.setattr(lifecycle, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    with pytest.raises(error):
        start(config_file)
    work = tmp_path / "work"
    assert not list(work.glob("effective-*.json"))
    assert not list(work.glob("startup-*"))
    assert not running()
    if failure == "timeout":
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=3)


def test_daemon_reconnects_without_temporary_zenoh_config(config_file, tmp_path, monkeypatch):
    from cloudrobo_ingestion import daemon
    from cloudrobo_ingestion.diagnostics import DiagnosticError

    monkeypatch.setenv("CLOUDROBO_INGESTION_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("HUAWEI_CLOUD_AK", "test-ak")
    monkeypatch.setenv("HUAWEI_CLOUD_SK", "test-sk")
    original_init = daemon.Reporter.__init__
    configs = []
    transport = object()
    done = RuntimeError("reporter configuration checked")

    def connect(config):
        configs.append(config)
        return transport

    def init(reporter, *args, **kwargs):
        original_init(reporter, *args, **kwargs)
        assert not list((tmp_path / "work").glob("zenoh-*"))
        assert reporter.factory(reporter.config_path) is transport
        assert reporter.factory(reporter.config_path) is transport
        raise done

    monkeypatch.setattr(daemon.Reporter, "connect_transport", staticmethod(connect))
    monkeypatch.setattr(daemon.Reporter, "__init__", init)
    with pytest.raises(DiagnosticError) as caught:
        daemon.main(config_file, tmp_path / "handshake.json")
    assert caught.value.__cause__ is done
    assert len(configs) == 2 and configs[0] is configs[1]
    assert configs[0].publisher_priority == "BACKGROUND"


@pytest.mark.parametrize("failure", ["write", "parse", "validate"])
def test_daemon_removes_temporary_zenoh_config_on_failure(
    config_file, tmp_path, monkeypatch, failure
):
    from cloudrobo_ingestion import daemon
    from cloudrobo_ingestion.diagnostics import DiagnosticError
    from cloudrobo_r2c.common.config import ClientConfig

    monkeypatch.setenv("CLOUDROBO_INGESTION_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("HUAWEI_CLOUD_AK", "test-ak")
    monkeypatch.setenv("HUAWEI_CLOUD_SK", "test-sk")
    failure_error = OSError("temporary config failed")

    def fail(*args, **kwargs):
        paths = list((tmp_path / "work").glob("zenoh-*/config.yaml"))
        assert len(paths) == 1
        if os.name != "nt":
            assert stat.S_IMODE(paths[0].stat().st_mode) == 0o600
            assert stat.S_IMODE(paths[0].parent.stat().st_mode) == 0o700
        raise failure_error

    if failure == "write":
        monkeypatch.setattr(daemon.yaml, "safe_dump", fail)
    elif failure == "parse":
        monkeypatch.setattr(ClientConfig, "from_yaml", fail)
    else:
        monkeypatch.setattr(ClientConfig, "validate", fail)
    with pytest.raises(DiagnosticError) as caught:
        daemon.main(config_file, tmp_path / "handshake.json")
    assert caught.value.__cause__ is failure_error
    assert not list((tmp_path / "work").glob("zenoh-*"))
