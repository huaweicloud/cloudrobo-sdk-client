import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from cloudrobo_ingestion.daemon import Supervisor
from cloudrobo_ingestion.filesystem import checked_copy, manifest
from cloudrobo_ingestion.progress import Reporter, message_class
from cloudrobo_ingestion.store import BatchStore
from cloudrobo_ingestion.worker import run_worker


class Connection:
    def __init__(self):
        self.messages = []
        self.closed = threading.Event()

    def send(self, message):
        self.messages.append(message)

    def recv(self):
        self.closed.wait()
        return "stop"

    def close(self):
        self.closed.set()


@pytest.mark.parametrize("success", [True, False])
@pytest.mark.parametrize("file_count", [1, 101])
def test_worker_obs_and_progress(config, batch_dir, monkeypatch, success, file_count):
    from cloudrobo_core.sdk import obs_client

    calls = []

    class OBS:
        def __init__(self, **kwargs):
            pass

        def upload_file(self, path, key, **kwargs):
            assert Path(path).is_relative_to(config.work_dir)
            kwargs["progress_callback"](4, 13, 0.1)
            calls.append(key)
            return success

        def close(self):
            pass

    monkeypatch.setattr(obs_client, "OBSClient", OBS)
    config = replace(config, max_files_per_batch=file_count)
    for i in range(1, file_count):
        (batch_dir / f"data/file-{i:03}.parquet").write_bytes(b"customer-data")
    store = BatchStore(config)
    batch = store.add("批次001", config)
    conn = Connection()
    entries = manifest(batch_dir, config.max_files_per_batch)
    run_worker(config, batch, entries, conn)
    expected = [f"prefix/批次001/{e.path}" for e in entries]
    assert calls == (expected if success else expected[:1])
    result = [e for e in conn.messages if e["event"] == "result"][0]
    assert result["state"] == ("done" if success else "failed")
    assert any(e["event"] == "file" and e["state"] == 1 for e in conn.messages)
    assert (batch_dir / "data/file-000.parquet").read_bytes() == b"customer-data"
    store.close()


@pytest.mark.parametrize("mode", ["upload_and_report", "upload_only"])
def test_supervisor_checks_limit_on_discovery_and_before_upload(config, batch_dir, mode):
    from cloudrobo_ingestion.progress import DisabledReporter

    config = replace(config, run_mode=mode, max_files_per_batch=1)
    reporter = Reporter("unused", config.topic) if config.reports else DisabledReporter()
    s = Supervisor(config, reporter, config.work_dir / "status.json")
    try:
        # Initially within the limit; launch must recheck rather than trust scan.
        s.scan()
        assert len(s.progress) == 1
        (batch_dir / "extra.parquet").touch()
        s.launch_next()
        assert s.process is None
        assert s.store.rows()[0]["state"] == "rejected"
        assert "配置上限 1" in s.store.rows()[0]["error"]
        assert not list((config.work_dir / "staging").iterdir())
        assert s.progress[s.store.rows()[0]["id"]].summary()["failed_files"] == 1
        s.store.close()
        s = Supervisor(config, reporter, config.work_dir / "status.json")
        s.scan()
        assert s.store.rows()[0]["state"] == "rejected"
        assert not s.progress  # Cannot build a new legal list after restarting.
    finally:
        s.store.close()


def test_worker_reports_windows_error_with_stack_and_stage(config, batch_dir, monkeypatch):
    from cloudrobo_core.sdk import obs_client

    class OBS:
        def __init__(self, **kwargs):
            self.access_key, self.secret_key = "test-private-ak", "test-private-sk"

        def upload_file(self, *args, **kwargs):
            error = PermissionError(13, "checkpoint locked test-private-sk")
            error.winerror = 32
            raise error

        def close(self):
            raise OSError("close failed test-private-ak")

    monkeypatch.setattr(obs_client, "OBSClient", OBS)
    store = BatchStore(config)
    batch = store.add("批次001", config)
    conn = Connection()
    run_worker(config, batch, manifest(batch_dir), conn)
    result = next(e for e in conn.messages if e["event"] == "result")
    assert result["error_info"]["component"] == "local"
    assert result["error_info"]["stage"] == "upload_local_io"
    assert "PermissionError" in result["error"]
    assert result["error_info"]["details"]["winerror"] == 32
    assert result["error_info"]["details"]["errno"] == 13
    logs = "\n".join(e["message"] for e in conn.messages if e["event"] == "log")
    assert "stack" in logs and "upload_file" in logs
    assert "OBS client close failed" in logs
    assert "test-private-ak" not in str(conn.messages)
    assert "test-private-sk" not in str(conn.messages)
    store.close()


def test_copy_failure_is_identified_before_obs_upload(config, batch_dir, monkeypatch):
    from cloudrobo_ingestion import worker

    def fail_copy(*args, **kwargs):
        raise PermissionError(13, "source cannot be read", str(batch_dir / "data/file-000.parquet"))

    monkeypatch.setattr(worker, "checked_copy", fail_copy)
    store = BatchStore(config)
    batch = store.add("批次001", config)
    conn = Connection()
    run_worker(config, batch, manifest(batch_dir), conn)
    result = next(e for e in conn.messages if e["event"] == "result")
    assert "stage=copying" in result["error"]
    assert "source cannot be read" in result["error"]
    logs = "\n".join(e["message"] for e in conn.messages if e["event"] == "log")
    assert "source_path" in logs and "file-000.parquet" in logs
    assert "Uploading file" not in logs
    store.close()


def successful_child(config, batch, entries, connection, cleanup_only):
    if cleanup_only:
        connection.send(dict(event="cleaned"))
    else:
        checked_copy(
            config.source_dir / batch["path"],
            config.work_dir / "staging" / batch["id"],
            entries,
            threading.Event(),
            max_files=config.max_files_per_batch,
        )
        for e in entries:
            connection.send(dict(event="file", path=e.path, state=2, transferred=e.size))
        connection.send(dict(event="result", state="done", error=""))
    connection.close()


def failed_child(config, batch, entries, connection, cleanup_only):
    from cloudrobo_ingestion.diagnostics import make_error, summary

    (config.work_dir / "staging" / batch["id"]).mkdir()
    info = make_error(
        "obs", "uploading", diagnostic=dict(http_status=403, error_code="AccessDenied")
    )
    connection.send(dict(event="result", state="failed", error=summary(info), error_info=info))
    connection.close()


def blocked_child(config, batch, entries, connection, cleanup_only):
    (config.work_dir / "staging" / batch["id"]).mkdir()
    time.sleep(60)


def crashed_child(config, batch, entries, connection, cleanup_only):
    raise SystemExit(7)


def drain_until_done(s):
    deadline = time.monotonic() + 10
    while s.process is not None and time.monotonic() < deadline:
        s.drain()
        time.sleep(0.02)
    assert s.process is None


def test_unexpected_worker_exit_reports_exit_code(config, batch_dir):
    s = Supervisor(
        config,
        Reporter("unused", config.topic),
        config.work_dir / "status.json",
        worker_target=crashed_child,
    )
    try:
        s.scan()
        s.launch_next()
        drain_until_done(s)
        info = s.snapshot()["error_info"]
        assert info["component"] == "local" and info["stage"] == "worker_exit"
        assert info["context"]["exit_code"] == 7
        batch = s.store.rows()[0]
        assert batch["state"] == "failed" and batch["retry_at"] > time.time()
        assert message_class().FromString(batch["pending"]).nodes[0].result_state == 3
    finally:
        s.store.close()


def test_supervisor_success_cleanup_and_skip_restart(config, batch_dir):
    reporter = Reporter("unused", config.topic)
    s = Supervisor(
        config, reporter, config.work_dir / "status.json", worker_target=successful_child
    )
    s.initialize()
    s.scan()
    s.launch_next()
    drain_until_done(s)
    b = s.store.rows()[0]
    assert b["state"] == "done" and not b["cleanup_pending"]
    assert not (config.work_dir / "staging" / b["id"]).exists()
    assert message_class().FromString(b["pending"]).task_state == 1
    s.store.close()
    restarted = Supervisor(
        config, reporter, config.work_dir / "status.json", worker_target=successful_child
    )
    restarted.initialize()
    restarted.scan()
    restarted.launch_next()
    assert restarted.process is None
    assert restarted.store.rows()[0]["id"] == b["id"]
    restarted.store.close()


def test_supervisor_failure_retries_after_recopy(config, batch_dir, caplog):
    for i in range(2):
        (batch_dir / "data" / f"queued-{i}.parquet").write_bytes(b"sample")
    r = Reporter("unused", config.topic)
    s = Supervisor(config, r, config.work_dir / "status.json", worker_target=failed_child)
    s.scan()
    s.launch_next()
    drain_until_done(s)
    b = s.store.rows()[0]
    assert b["state"] == "failed" and b["retry_at"] > time.time()
    assert s.snapshot()["error_info"]["component"] == "obs"
    assert [n.result_state for n in message_class().FromString(b["pending"]).nodes] == [3, 3, 3]
    assert not (config.work_dir / "staging" / b["id"]).exists()
    s.launch_next()
    assert s.process is None
    s.store.update(b["id"], retry_at=0)
    s.worker_target = successful_child
    caplog.set_level("INFO")
    s.launch_next()
    drain_until_done(s)
    new = s.store.rows()[0]
    assert new["state"] == "done" and new["attempts"] == 2
    assert new["id"] == b["id"] and new["timestamp"] == b["timestamp"]
    assert new["error_info"] is None and s.snapshot()["error_info"] is None
    assert "异常已恢复" in caplog.text
    s.store.close()


@pytest.mark.parametrize("phase", ["queued", "copying", "validating", "uploading"])
def test_recovery_cleans_interrupted_staging(config, batch_dir, phase):
    store = BatchStore(config)
    b = store.add("批次001", config)
    stage = config.work_dir / "staging" / b["id"]
    stage.mkdir(parents=True)
    (stage / "leftover").write_bytes(b"partial")
    store.update(b["id"], state=phase, attempts=2, cleanup_pending=1)
    store.close()
    s = Supervisor(config, Reporter("unused", config.topic), config.work_dir / "status.json")
    s.initialize()
    # Startup does not walk every historical staging path; scheduling cleans
    # pending leftovers incrementally before admitting a new upload.
    s.launch_next()
    assert not stage.exists()
    recovered = s.store.get(b["id"])
    assert recovered["state"] == "queued" and recovered["attempts"] == 2
    assert recovered["timestamp"] == b["timestamp"] and not recovered["cleanup_pending"]
    s.store.close()


def test_recovery_preserves_committed_success_before_cleanup_and_report(config, batch_dir):
    from cloudrobo_ingestion.progress import BatchProgress

    store = BatchStore(config)
    batch = store.add("批次001", config)
    progress = BatchProgress(batch, manifest(batch_dir), "r2c/local")
    for entry in progress.entries.values():
        progress.update(entry.path, 2, entry.size)
    progress.finished = True
    payload = progress.serialize()
    stage = config.work_dir / "staging" / batch["id"]
    stage.mkdir(parents=True)
    (stage / "leftover").write_bytes(b"uploaded")
    store.update(
        batch["id"],
        state="done",
        attempts=1,
        cleanup_pending=1,
        final_snapshot=progress.snapshot(),
        pending=payload,
    )
    store.close()  # Model a committed upload with cleanup/publication still outstanding.

    reporter = Reporter("unused", config.topic)
    s = Supervisor(config, reporter, config.work_dir / "status.json")
    try:
        s.initialize()
        s.scan()
        s.launch_next()
        restored = s.store.get(batch["id"])
        assert not stage.exists() and s.process is None and not s.progress
        assert restored["state"] == "done" and restored["attempts"] == 1
        assert restored["timestamp"] == batch["timestamp"]
        assert not restored["cleanup_pending"]
        assert reporter.slots[batch["id"]] == (payload, True)
        reporter.acks.put((batch["id"], payload))
        s.drain()
        restored = s.store.get(batch["id"])
        assert restored["pending"] is restored["final_snapshot"] is None
        assert restored["state"] == "done" and not reporter.slots
    finally:
        s.store.close()


def test_stop_terminates_blocked_upload(config, batch_dir):
    from cloudrobo_ingestion.diagnostics import make_error

    r = Reporter("unused", config.topic, factory=lambda _: None)
    s = Supervisor(config, r, config.work_dir / "status.json", worker_target=blocked_child)
    s.scan()
    previous = make_error("obs", "uploading", TimeoutError("timed out"))
    s.batch_error(s.store.rows()[0], previous)
    s.launch_next()
    r.start()
    s.stop_event.set()
    start = time.monotonic()
    s.run()
    assert time.monotonic() - start < 9
    assert s.process is None
    store = BatchStore(config)
    b = store.rows()[0]
    assert b["state"] == "interrupted"
    assert b["error_info"]["error_id"] == previous["error_id"]
    assert not (config.work_dir / "staging" / b["id"]).exists()
    store.close()
