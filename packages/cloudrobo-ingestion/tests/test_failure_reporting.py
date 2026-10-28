"""Exercise the actual parent/child events and serialized progress stream."""

import threading
import time

import pytest
from cloudrobo_ingestion.daemon import Supervisor
from cloudrobo_ingestion.diagnostics import make_error, summary
from cloudrobo_ingestion.filesystem import checked_copy
from cloudrobo_ingestion.progress import Reporter, message_class
from cloudrobo_ingestion.store import BatchStore


def wait_gate(config, name, connection):
    deadline = time.monotonic() + 10
    while not (config.work_dir / name).exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("Test gate timed out")
        if connection.poll(0.02) and connection.recv() == "stop":
            return False
    return True


def partial_failure_child(config, batch, entries, connection, cleanup_only):
    connection.send(dict(event="file", path=entries[0].path, state=2, transferred=entries[0].size))
    info = make_error("obs", "uploading", TimeoutError("timed out"))
    connection.send(dict(event="result", state="failed", error=summary(info), error_info=info))
    # Failure must be visible even while the worker is still cleaning up.
    wait_gate(config, "cleanup-done", connection)
    connection.close()


def gated_retry_child(config, batch, entries, connection, cleanup_only):
    connection.send(dict(event="phase", state="copying"))
    if not wait_gate(config, "copy-ready", connection):
        return
    checked_copy(
        config.source_dir / batch["path"],
        config.work_dir / "staging" / batch["id"],
        entries,
        threading.Event(),
        config.max_files_per_batch,
    )
    for index, entry in enumerate(entries):
        connection.send(dict(event="file", path=entry.path, state=1, transferred=0, begin=True))
        if not wait_gate(config, f"file-{index}-done", connection):
            return
        connection.send(dict(event="file", path=entry.path, state=2, transferred=entry.size))
    connection.send(dict(event="result", state="done", error=""))
    connection.close()


def pump(supervisor, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        supervisor.drain()
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("Expected progress was not observed")


def states(message):
    return [node.result_state for node in message.nodes]


def test_failed_state_is_published_before_retry_and_survives_preparation(config, batch_dir):
    for name in ("second.parquet", "third.parquet"):
        (batch_dir / "data" / name).write_bytes(b"sample")
    messages = []

    class Transport:
        def publish(self, topic, payload):
            messages.append(message_class().FromString(payload))

        def close(self):
            pass

    reporter = Reporter("unused", config.topic, factory=lambda _: Transport(), interval=0.02)
    s = Supervisor(
        config, reporter, config.work_dir / "status.json", worker_target=partial_failure_child
    )
    reporter.start()
    try:
        s.scan()
        s.launch_next()
        failure = pump(s, lambda: next((m for m in messages if states(m) == [2, 3, 3]), None))
        assert s.process.is_alive()  # Do not wait for worker cleanup to report failure.
        (config.work_dir / "cleanup-done").touch()
        pump(s, lambda: s.process is None)
        batch = s.store.rows()[0]
        assert batch["state"] == "failed" and batch["retry_at"] > time.time()
        s.store.update(batch["id"], retry_at=0)
        s.worker_target = gated_retry_child
        s.launch_next()
        # Recopy/initialization has no periodic messages; retry cancels old result rounds.
        index = len(messages)
        end = time.monotonic() + 0.15
        while time.monotonic() < end:
            s.drain()
            time.sleep(0.01)
        assert len(messages) == index
        assert not s.reporter.slots
        (config.work_dir / "copy-ready").touch()
        for i, expected in enumerate(([1, 3, 3], [2, 1, 3], [2, 2, 1])):
            index = len(messages)
            pump(s, lambda: any(states(m) == expected for m in messages[index:]))
            (config.work_dir / f"file-{i}-done").touch()
        pump(s, lambda: s.process is None)
        final = pump(s, lambda: next((m for m in messages if m.task_state == 1), None))
        assert states(final) == [2, 2, 2]
        assert [n.file_id for n in final.nodes] == [n.file_id for n in failure.nodes]
        assert [n.enqueue_time for n in final.nodes] == [n.enqueue_time for n in failure.nodes]
        assert final.timestamp == failure.timestamp
    finally:
        if s.process:
            s.process.terminate()
            s.process.join(timeout=2)
            s.pipe.close()
        reporter.close()
        s.store.close()


@pytest.mark.parametrize(
    "old_state,attempts,expected",
    [("uploading", 1, 3), ("failed", 1, 3), ("interrupted", 1, 3), ("queued", 0, 0)],
)
def test_restart_rebuilds_unfinished_without_per_file_snapshots(
    config, batch_dir, old_state, attempts, expected
):
    store = BatchStore(config)
    batch = store.add("批次001", config)
    store.update(batch["id"], state=old_state, attempts=attempts)
    store.close()
    s = Supervisor(config, Reporter("unused", config.topic), config.work_dir / "status.json")
    try:
        s.initialize()
        s.scan()
        progress = s.progress[batch["id"]]
        restored = message_class().FromString(progress.serialize())
        assert states(restored) == [expected]
        assert restored.timestamp == batch["timestamp"]
        assert (
            restored.nodes[0].start_time
            == restored.nodes[0].avg_bps
            == restored.nodes[0].eta_ms
            == 0
        )
        columns = {row[1] for row in s.store.db.execute("PRAGMA table_info(batches)")}
        assert {name for name in columns if "snapshot" in name} == {"final_snapshot"}
        assert s.store.get(batch["id"])["final_snapshot"] is None
    finally:
        s.store.close()


def test_worker_start_failure_reports_known_files(config, batch_dir, monkeypatch):
    class BrokenProcess:
        def start(self):
            raise OSError("process creation failed")

    reporter = Reporter("unused", config.topic)
    s = Supervisor(config, reporter, config.work_dir / "status.json")
    try:
        s.scan()
        monkeypatch.setattr(s.context, "Process", lambda **kwargs: BrokenProcess())
        s.launch_next()
        batch = s.store.rows()[0]
        assert batch["state"] == "failed"
        assert batch["error_info"]["stage"] == "worker_start"
        snapshot, terminal = reporter.slots[batch["id"]]
        assert states(message_class().FromString(snapshot)) == [3]
        assert terminal and s.process is None
    finally:
        s.store.close()
