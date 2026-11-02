import os
import socket
import time

import pytest
import yaml
from cloudrobo_ingestion.lifecycle import running, start, status, stop


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for daemon state")


def unused_endpoint():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"tcp/127.0.0.1:{s.getsockname()[1]}"


def install_import_guard(tmp_path, monkeypatch, condition):
    hook = tmp_path / "import-guard"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(
        "import builtins\n_original = builtins.__import__\n"
        "def _guard(name, *args, **kwargs):\n"
        f"    if {condition}:\n"
        "        raise AssertionError('forbidden import: ' + name)\n"
        "    return _original(name, *args, **kwargs)\n"
        "builtins.__import__ = _guard\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(hook) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setenv("CLOUDROBO_INGESTION_RUNTIME_DIR", str(tmp_path / "runtime"))


@pytest.mark.parametrize("run_mode", ["upload_only", "upload_and_report"])
def test_real_child_receives_effective_cli_configuration(
    config_file, tmp_path, monkeypatch, run_mode
):
    monkeypatch.setenv("CLOUDROBO_INGESTION_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("HUAWEI_CLOUD_AK", "test-ak")
    monkeypatch.setenv("HUAWEI_CLOUD_SK", "test-sk")
    before = config_file.read_bytes()
    work = tmp_path / f"{run_mode}-work"
    result = start(
        config_file,
        dict(
            run_mode=run_mode,
            work_dir=str(work),
            task_type="CLI-task",
            collect_method="CLI-method",
            zenoh_endpoint=(unused_endpoint(),),
        ),
    )
    try:
        assert result["run_mode"] == run_mode and result["work_dir"] == str(work)
        assert config_file.read_bytes() == before
        assert not (tmp_path / "work").exists()
        state = wait_for(lambda: status() if status().get("run_mode") == run_mode else None)
        assert state["work_dir"] == str(work) and state["running"]
        assert state["reporter"]["enabled"] is (run_mode == "upload_and_report")
        assert not list(work.glob("zenoh-*"))
        assert not list(work.glob("effective-*.json"))
        assert not list(work.glob("startup-*"))
        assert stop()["run_mode"] == run_mode
        assert not running()
    finally:
        if running():
            stop()


def test_pure_daemon_without_reporting_imports(config_file, tmp_path, monkeypatch):
    install_import_guard(
        tmp_path, monkeypatch, "name.startswith(('cloudrobo_r2c', 'zenoh', 'google.protobuf'))"
    )
    monkeypatch.setenv("HUAWEI_CLOUD_AK", "test-ak")
    monkeypatch.setenv("HUAWEI_CLOUD_SK", "test-sk")
    data = yaml.safe_load(config_file.read_text())
    del data["task_type"], data["collect_method"]
    data["run_mode"] = "upload_only"
    data["zenoh"] = "unused-invalid-settings"
    config_file.write_text(yaml.safe_dump(data))
    start(config_file)
    try:
        assert status()["run_mode"] == "upload_only"
        assert status()["reporter"]["enabled"] is False
    finally:
        stop()


def test_detached_daemon_enforces_cli_file_limit(config_file, tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDROBO_INGESTION_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("HUAWEI_CLOUD_AK", "test-ak")
    monkeypatch.setenv("HUAWEI_CLOUD_SK", "test-sk")
    monkeypatch.setenv("CLOUDROBO_OBS_ENDPOINT", "http://127.0.0.1:1")
    batch = tmp_path / "source/batch"
    batch.mkdir()
    (batch / "__UPLOAD_READY__").touch()
    (batch / "first.txt").write_text("first")
    (batch / "second.txt").write_text("second")
    data = yaml.safe_load(config_file.read_text())
    data["max_files_per_batch"] = 10
    config_file.write_text(yaml.safe_dump(data))
    before = config_file.read_bytes()
    start(config_file, dict(run_mode="upload_only", max_files_per_batch=1))
    try:
        state = wait_for(
            lambda: status() if status().get("counts", {}).get("rejected") == 1 else None
        )
        assert "配置上限 1" in state["batch_errors"][0]["error"]
        assert not list((tmp_path / "work/staging").iterdir())
        assert config_file.read_bytes() == before
    finally:
        stop()


def test_detached_switches_real_modes_using_one_work_dir(config_file, tmp_path, monkeypatch):
    from cloudrobo_ingestion.config import IngestionConfig
    from cloudrobo_ingestion.store import BatchStore

    monkeypatch.setenv("CLOUDROBO_INGESTION_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("HUAWEI_CLOUD_AK", "test-ak")
    monkeypatch.setenv("HUAWEI_CLOUD_SK", "test-sk")
    config = IngestionConfig.load(config_file)
    try:
        start(config_file)
        assert status()["run_mode"] == "upload_and_report"
        stop()
        # An empty batch exercises copy/success/cleanup without an OBS server.
        batch = config.source_dir / "empty-batch"
        batch.mkdir()
        (batch / "__UPLOAD_READY__").touch()
        pure = start(config_file, dict(run_mode="upload_only"))
        assert pure["work_dir"] == str(config.work_dir)
        wait_for(lambda: status().get("counts", {}).get("done") == 1)
        stop()
        start(config_file)
        state = wait_for(lambda: status() if status().get("counts", {}).get("done") == 1 else None)
        assert state["run_mode"] == "upload_and_report"
        assert not state["reporter"]["pending_batches"]
        stop()
        store = BatchStore(config)
        try:
            row = store.rows()[0]
            assert row["attempts"] == 1 and row["state"] == "done"
            assert not row["report_enabled"]
            assert row["pending"] is row["final_snapshot"] is None
        finally:
            store.close()
    finally:
        if running():
            stop()
