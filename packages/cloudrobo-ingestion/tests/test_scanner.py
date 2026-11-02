"""Fault isolation, bounded preparation/outbox, and responsive filesystem discovery."""

import json
import queue
import threading
import time
from pathlib import Path

import pytest
from click.testing import CliRunner
from cloudrobo_ingestion.cli import ingestion
from cloudrobo_ingestion.daemon import Supervisor
from cloudrobo_ingestion.progress import Reporter, message_class
from cloudrobo_ingestion.scanner import MANIFEST_WINDOW, SCAN_QUEUE_SIZE, ScanWorker
from cloudrobo_ingestion.store import BatchStore, now_ms
from delivery_helpers import complete_result
from test_worker_supervisor import drain_until_done, successful_child


def batch(root, name, *, ready=b""):
    path = root / name
    path.mkdir()
    (path / "file.txt").write_bytes(b"data")
    (path / "__UPLOAD_READY__").write_bytes(ready)
    return path


def supervisor(config, reporter=None):
    return Supervisor(
        config,
        reporter or Reporter("unused", config.topic),
        config.work_dir / "status.json",
        worker_target=successful_child,
    )


def pump(s, condition, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s.drain()
        if condition():
            return
        time.sleep(0.005)
    raise AssertionError("Scanner did not reach expected state")


def paused_upload(config, batch_record, entries, connection, cleanup_only=False):
    connection.send(dict(event="phase", state="uploading"))
    for entry in entries:
        connection.send(dict(event="file", path=entry.path, state=1, transferred=0, begin=True))
    while not (config.work_dir / "finish-upload").exists():
        time.sleep(0.02)
    for entry in entries:
        connection.send(dict(event="file", path=entry.path, state=2, transferred=entry.size))
    connection.send(dict(event="result", state="done", error=""))
    connection.close()


@pytest.mark.parametrize("problem", ["directory", "marker", "disappeared"])
def test_one_bad_directory_does_not_hide_other_batches(config, monkeypatch, caplog, problem):
    batch(config.source_dir, "before")
    bad = batch(config.source_dir, "bad")
    batch(config.source_dir, "after")
    denied = bad / "__UPLOAD_READY__" if problem == "marker" else bad
    original = Path.lstat
    blocked = [True]

    def lstat(path, *args, **kwargs):
        if path == denied and blocked[0]:
            if problem == "disappeared":
                raise FileNotFoundError(2, "removed during discovery")
            raise PermissionError(13, "test access denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    caplog.set_level("INFO")
    s = supervisor(config)
    try:
        s.scan()
        assert {r["path"] for r in s.store.rows()} == {"before", "after"}
        state = s.snapshot()
        assert not state["counts"].get("failed")
        if problem == "disappeared":
            assert not state["scan_errors"] and state["scan"]["error_count"] == 0
        else:
            info = state["scan_errors"][0]
            assert info["component"] == "local" and info["stage"] == "scan"
            assert info["context"]["path"] == str(bad)
            assert "继续尝试" in info["hint"] and info["error_id"] in caplog.text
            from cloudrobo_ingestion import lifecycle

            monkeypatch.setattr(lifecycle, "status", lambda: state | {"running": True})
            output = CliRunner().invoke(ingestion, ["status"])
            assert str(bad) in output.output and "扫描异常: 1" in output.output
        blocked[0] = False
        s.scan()
        assert len(s.store.rows()) == 3
        assert not s.snapshot()["scan_errors"]
        assert s.snapshot()["scan"]["pending_manifest_batches"] == 1
        assert len(s.progress) == MANIFEST_WINDOW
    finally:
        s.store.close()


def test_rejected_batch_hint_and_repair_still_requires_restart(config, caplog):
    bad = batch(config.source_dir, "bad", ready=b"not empty")
    batch(config.source_dir, "good")
    s = supervisor(config)
    try:
        s.scan()
        row = next(r for r in s.store.rows() if r["path"] == "bad")
        assert row["state"] == "rejected"
        hint = row["error_info"]["hint"]
        assert "手动解决" in hint and "重启" in hint and "本次运行不再处理" in hint
        assert hint in caplog.text
        assert s.snapshot()["batch_errors"][0]["error_info"]["hint"] == hint
        (bad / "__UPLOAD_READY__").write_bytes(b"")
        s.scan()
        assert s.store.get(row["id"])["state"] == "rejected"
    finally:
        s.store.close()
    restored = supervisor(config)
    try:
        restored.scan()
        after = restored.store.get(row["id"])
        assert after["state"] == "queued" and after["timestamp"] == row["timestamp"]
        assert row["id"] in restored.progress
    finally:
        restored.store.close()


def test_source_failure_pauses_new_uploads_and_recovers(config, monkeypatch):
    batch(config.source_dir, "one")
    s = supervisor(config)
    original = Path.lstat
    blocked = [False]

    def lstat(path, *args, **kwargs):
        if path == config.source_dir and blocked[0]:
            raise PermissionError(13, "source offline")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    try:
        s.scan()
        blocked[0] = True
        s.scan()
        s.launch_next()
        assert s.process is None and not s.source_available
        assert s.snapshot()["scan"]["state"] == "error"
        assert s.snapshot()["scan_errors"][0]["context"]["path"] == str(config.source_dir)
        blocked[0] = False
        s.scan()
        assert not s.snapshot()["scan_errors"]
        s.launch_next()
        drain_until_done(s)
        assert s.store.rows()[0]["state"] == "done"
    finally:
        if s.process:
            s.process.terminate()
            s.process.join()
        s.store.close()


def test_root_failure_does_not_stop_active_upload_or_progress(config, monkeypatch):
    batch(config.source_dir, "one")
    messages = []

    class Transport:
        def publish(self, topic, payload):
            messages.append(message_class().FromString(payload))

        def close(self):
            pass

    reporter = Reporter("unused", config.topic, factory=lambda _: Transport(), interval=0.02)
    s = supervisor(config, reporter)
    s.worker_target = paused_upload
    reporter.start()
    try:
        s.scan()
        s.launch_next()
        pump(s, lambda: bool(s.snapshot()["progress"].get("current_files")))
        pid = s.process.pid
        original = Path.lstat

        def lstat(path, *args, **kwargs):
            if path == config.source_dir:
                raise PermissionError(13, "source offline")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", lstat)
        s.scan()
        assert not s.source_available and s.process.pid == pid and s.process.is_alive()
        index = len(messages)
        pump(s, lambda: len(messages) >= index + 2)
        assert any(n.result_state == 1 for m in messages[index:] for n in m.nodes)
        (config.work_dir / "finish-upload").touch()
        pump(s, lambda: s.process is None and any(m.task_state == 1 for m in messages))
        assert s.snapshot()["counts"] == {"done": 1}
    finally:
        if s.process:
            s.process.terminate()
            s.process.join()
        reporter.close()
        s.store.close()


def test_scan_diagnostics_are_bounded_deduplicated_and_prioritize_root_error(
    config, monkeypatch, caplog
):
    for i in range(60):
        (config.source_dir / f"denied-{i:02}").mkdir()
    original = Path.lstat
    deny_root = [False]

    def lstat(path, *args, **kwargs):
        if path.parent == config.source_dir or (deny_root[0] and path == config.source_dir):
            raise PermissionError(13, f"denied {path.name}")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    s = supervisor(config)
    try:
        s.scan()
        state = s.snapshot()
        assert state["scan"]["error_count"] == 60 and len(state["scan_errors"]) == 50
        assert not s.store.rows()
        count = len(caplog.records)
        s.scan()
        assert len(caplog.records) == count
        deny_root[0] = True
        s.scan()
        state = s.snapshot()
        assert state["scan"]["error_count"] == 61 and len(state["scan_errors"]) == 50
        assert state["scan_errors"][0]["context"]["path"] == str(config.source_dir)
    finally:
        s.store.close()


def test_scanner_does_not_misclassify_programming_errors_as_filesystem_errors(config, monkeypatch):
    def broken(_):
        raise RuntimeError("unexpected scanner bug")

    monkeypatch.setattr("cloudrobo_ingestion.scanner.is_link", broken)
    s = supervisor(config)
    try:
        with pytest.raises(RuntimeError, match="unexpected scanner bug"):
            s.scan()
        assert not s.snapshot()["scan_errors"]
    finally:
        s.store.close()


def test_continuous_manifest_requests_do_not_starve_periodic_discovery(config, monkeypatch):
    scanner = ScanWorker(config.source_dir, config.max_files_per_batch)
    clock = [0.0]
    scans = []
    monkeypatch.setattr("cloudrobo_ingestion.scanner.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(scanner, "_discover", lambda: scans.append(clock[0]))

    def prepare_next():
        clock[0] += 1
        if clock[0] >= 5:
            scanner.stop_event.set()
        return True

    monkeypatch.setattr(scanner, "_manifest_request", prepare_next)
    scanner._run()
    assert scans == [0, 2, 4]


def test_blocked_scanner_does_not_block_status_publishing_or_shutdown(config, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == config.source_dir:
            entered.set()
            release.wait(10)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    sent = []

    class Transport:
        def publish(self, topic, payload):
            sent.append(payload)

        def close(self):
            pass

    reporter = Reporter("unused", config.topic, factory=lambda _: Transport(), interval=0.02)
    reporter.set("heartbeat", b"sample")
    reporter.start()
    s = supervisor(config, reporter)
    try:
        s.scan(wait=False)
        assert entered.wait(3)
        pump(s, lambda: len(sent) >= 2)
        assert s.snapshot()["scan"]["state"] == "scanning"
        assert s.store.db.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0
        s.stop_event.set()
        start = time.monotonic()
        s.scanner.close()
        assert time.monotonic() - start < 1.5
    finally:
        release.set()
        reporter.close()
        s.store.close()
    assert not s.scanner.thread.is_alive()


def test_result_queue_and_consumer_budget_are_bounded(config):
    s = supervisor(config)
    try:
        for i in range(SCAN_QUEUE_SIZE):
            s.scanner.events.put_nowait(dict(kind="discovered", path=f"batch-{i}"))
        with pytest.raises(queue.Full):
            s.scanner.events.put_nowait(dict(kind="scan_start"))
        s._consume_scan()
        assert 0 < len(s.store.rows()) <= SCAN_QUEUE_SIZE
        assert len(s.progress) == 0  # Discovery alone does not build all file nodes.
    finally:
        s.store.close()


@pytest.mark.parametrize("count", [1000, 10000])
def test_history_scan_uses_path_cache_without_per_directory_database_writes(config, count):
    store = BatchStore(config)
    rows = []
    for i in range(count):
        name = f"history-{i:05}"
        (config.source_dir / name).mkdir()
        rows.append((name, name, f"obs://bucket/{name}/", i))
    with store.db:
        store.db.executemany(
            "INSERT INTO batches(id,path,asset_path,timestamp,state,task_type,collect_method) "
            "VALUES(?,?,?,?,'done','','')",
            rows,
        )
    store.close()
    s = supervisor(config)
    statements = []
    s.store.db.set_trace_callback(statements.append)
    try:
        s.scan()
        assert s.snapshot()["counts"] == {"done": count}
        assert s.snapshot()["scan"]["checked_dirs"] == count
        assert not s.progress
        assert not any(q.startswith(("INSERT", "UPDATE")) for q in statements)
    finally:
        s.store.close()


def test_backlog_has_two_manifests_and_one_outbox_even_while_offline(config):
    for i in range(1000):
        batch(config.source_dir, f"pending-{i:04}")
    reporter = Reporter(
        "unused",
        config.topic,
        factory=lambda _: (_ for _ in ()).throw(ConnectionRefusedError("offline")),
    )
    reporter.start()
    s = supervisor(config, reporter)
    main_thread = threading.get_ident()
    original = s.store.add

    def add(path, config):
        assert threading.get_ident() == main_thread
        return original(path, config)

    s.store.add = add
    try:
        s.scan(wait=False)
        high_water = [0, 0, 0]

        def discovered():
            high_water[0] = max(high_water[0], s.scanner.events.qsize())
            high_water[1] = max(high_water[1], len(set(s.progress) | set(s.preparing)))
            high_water[2] = max(high_water[2], len(reporter.slots))
            return s.scan_rounds > 0 and s.snapshot()["counts"].get("queued") == 1000

        pump(s, discovered, timeout=60)
        for _ in range(5):
            s.launch_next()
            drain_until_done(s)
            assert len(s.progress) <= 2 and len(reporter.slots) <= 3
        state = s.snapshot()
        assert state["counts"] == {"done": 5, "queued": 995}
        assert state["scan"]["pending_manifest_batches"] >= 993
        assert (
            s.store.db.execute(
                "SELECT COUNT(*) FROM batches WHERE final_snapshot IS NOT NULL"
            ).fetchone()[0]
            == 5
        )
        assert high_water[0] <= 32 and high_water[1] <= 2 and high_water[2] <= 3
    finally:
        if s.process:
            s.process.terminate()
            s.process.join()
        reporter.close()
        s.store.close()


def test_large_outbox_is_loaded_one_result_at_a_time_and_progresses(config):
    store = BatchStore(config)
    snapshot = json.dumps(
        dict(
            source="r2c/local",
            timestamp=1,
            nodes=[],
            task_state=1,
            asset_path="obs://bucket/completed/",
        )
    )
    with store.db:
        store.db.executemany(
            "INSERT INTO batches(id,path,asset_path,timestamp,state,final_snapshot,task_type,collect_method) "
            "VALUES(?,?,?,?,'done',?,'','')",
            [
                (str(i), str(i), "obs://bucket/completed/", now_ms() + i, snapshot)
                for i in range(1000)
            ],
        )
    store.close()
    sent = []

    class Transport:
        def publish(self, topic, payload):
            sent.append(payload)

        def close(self):
            pass

    reporter = Reporter("unused", config.topic, factory=lambda _: Transport(), interval=0.02)
    s = supervisor(config, reporter)
    s.initialize()
    assert len(reporter.slots) == 1
    first = s.outbox_id
    reporter.acks.put((first, b"older-failure-snapshot"))
    s.drain()
    assert s.outbox_id == first and len(reporter.slots) == 1
    assert s.store.get(first)["final_snapshot"] is not None
    reporter.start()
    try:
        for i in range(5):
            s.next_final_retry = 0
            pump(
                s,
                lambda: (
                    len(sent) >= i + 1
                    and s.store.db.execute(
                        "SELECT COUNT(*) FROM batches WHERE report_count>0"
                    ).fetchone()[0]
                    >= i + 1
                ),
            )
            assert len(reporter.slots) <= 1 and not s.progress
        assert len(sent) >= 5
        remaining = s.store.db.execute(
            "SELECT COUNT(*) FROM batches WHERE report_count>0"
        ).fetchone()[0]
        assert remaining >= 5
    finally:
        reporter.close()
        s.store.close()


def test_unserializable_outbox_result_does_not_starve_later_results(config, monkeypatch):
    from cloudrobo_ingestion import daemon

    store = BatchStore(config)
    ids = []
    for name in ("bad", "good"):
        row = store.add(name, config)
        ids.append(row["id"])
        store.update(
            row["id"],
            state="done",
            final_snapshot=dict(
                source="r2c/local",
                timestamp=row["timestamp"],
                nodes=[],
                task_state=1,
                asset_path=f"obs://bucket/{name}/",
            ),
        )
    store.close()
    serialize = daemon.serialize_snapshot

    def broken(snapshot):
        if snapshot["asset_path"].endswith("bad/"):
            raise ValueError("broken final result")
        return serialize(snapshot)

    monkeypatch.setattr(daemon, "serialize_snapshot", broken)
    s = supervisor(config)
    try:
        s.initialize()
        assert ids[0] in s.final_errors
        s.drain()
        assert s.outbox_id == ids[1] and len(s.reporter.slots) == 1
        complete_result(s, ids[1])
        assert s.store.get(ids[1])["final_snapshot"] is None
        monkeypatch.setattr(daemon, "serialize_snapshot", serialize)
        s.store.update(ids[0], report_next_ms=0)
        s.next_final_retry = 0
        s.drain()
        assert s.outbox_id == ids[0] and len(s.reporter.slots) == 1
        assert not s.final_errors
    finally:
        s.store.close()
