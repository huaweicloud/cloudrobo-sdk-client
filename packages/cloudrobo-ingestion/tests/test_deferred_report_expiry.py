"""Only upload_only completion backfills expire, using stable first enqueue time."""

import shutil
import time
from dataclasses import replace

import pytest
from cloudrobo_ingestion.progress import Reporter, message_class
from cloudrobo_ingestion.store import DEFERRED_REPORT_TTL_MS as DAY
from test_mode_switching import complete, supervisor


@pytest.fixture
def clock(monkeypatch):
    value = [2 * DAY]
    for module in ("store", "daemon", "progress"):
        monkeypatch.setattr(f"cloudrobo_ingestion.{module}.now_ms", lambda: value[0])
    return value


def enroll(config):
    s = supervisor(config)
    try:
        s.scan()
        return s.store.rows()[0]
    finally:
        s.store.close()


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for reporter")


@pytest.mark.parametrize("age", [DAY - 1, DAY, DAY + 1])
def test_switch_back_uses_first_enqueue_time_at_24_hour_boundary(
    config, batch_dir, clock, age, caplog
):
    original = enroll(config)
    clock[0] = original["timestamp"] + DAY - 3600000
    pure = supervisor(replace(config, run_mode="upload_only"))
    try:
        done = complete(pure)
        assert done["report_expires_ms"] == original["timestamp"] + DAY
        assert done["final_snapshot"] is not None
    finally:
        pure.store.close()
    shutil.rmtree(batch_dir)  # Backfill does not depend on retained source files.
    clock[0] = original["timestamp"] + age
    caplog.set_level("INFO")
    restored = supervisor(config)
    try:
        restored.initialize()
        restored.scan()
        restored.launch_next()
        row = restored.store.get(original["id"])
        assert row["state"] == "done" and row["attempts"] == 1
        assert row["timestamp"] == original["timestamp"] and restored.process is None
        if age < DAY:
            payload, terminal = restored.reporter.slots[row["id"]]
            final = message_class().FromString(payload)
            assert terminal and final.task_state == 1
            assert final.timestamp == original["timestamp"]
        else:
            assert not restored.reporter.slots
            assert row["pending"] is row["final_snapshot"] is None
            assert "补报已过期" in caplog.text
    finally:
        restored.store.close()


def test_already_old_batch_completed_in_upload_only_is_not_retained(
    config, batch_dir, clock, monkeypatch
):
    original = enroll(config)
    clock[0] = original["timestamp"] + DAY + 1
    monkeypatch.setattr(
        "cloudrobo_ingestion.progress.message_class",
        lambda: pytest.fail("upload_only must not import/serialize protobuf"),
    )
    pure = supervisor(replace(config, run_mode="upload_only"))
    try:
        done = complete(pure)
        assert done["state"] == "done"
        assert done["pending"] is done["final_snapshot"] is None
        assert done["report_expires_ms"] == original["timestamp"] + DAY
        pure.scan()
        pure.launch_next()
        assert pure.process is None  # Expiring the outbox must not schedule a re-upload.
    finally:
        pure.store.close()


@pytest.mark.parametrize("failure", ["connection", "serialization"])
def test_pending_backfill_expires_while_default_mode_keeps_running(
    config, batch_dir, clock, monkeypatch, failure
):
    original = enroll(config)
    pure = supervisor(replace(config, run_mode="upload_only"))
    complete(pure)
    pure.store.close()
    clock[0] = original["timestamp"] + DAY - 1000
    restored = supervisor(config)
    if failure == "serialization":
        monkeypatch.setattr(
            "cloudrobo_ingestion.daemon.serialize_snapshot",
            lambda _: (_ for _ in ()).throw(ValueError("broken serializer")),
        )
    restored.reporter.factory = lambda _: (_ for _ in ()).throw(ConnectionRefusedError("offline"))
    restored.initialize()
    restored.reporter.start()
    try:
        if failure == "connection":
            wait_for(lambda: restored.reporter.diagnostics()["error"])
        else:
            assert restored.final_errors
        clock[0] = original["timestamp"] + DAY
        restored.next_final_retry = 0
        restored.drain()
        row = restored.store.get(original["id"])
        assert row["pending"] is row["final_snapshot"] is None
        assert row["state"] == "done" and not restored.reporter.slots
        assert not restored.final_errors
    finally:
        restored.reporter.close()
        restored.store.close()
    # Restarting does not give the expired result another 24 hours.
    restarted = supervisor(config)
    try:
        restarted.initialize()
        restarted.scan()
        restarted.launch_next()
        assert not restarted.reporter.slots and restarted.process is None
        assert restarted.store.get(original["id"])["attempts"] == 1
    finally:
        restarted.store.close()


def test_reporter_checks_expiry_again_after_connect_without_acknowledging_it(clock):
    clock[0] = 3 * DAY - 1
    sent = []

    class Transport:
        def publish(self, topic, payload):
            sent.append(payload)

        def close(self):
            pass

    def connect(_):
        clock[0] = 3 * DAY  # A slow connect crosses the last valid millisecond.
        return Transport()

    reporter = Reporter("unused", "topic", factory=connect, interval=0.02)
    reporter.set("deferred", b"expired", terminal=True, expires_at_ms=3 * DAY)
    reporter.set("normal", b"fresh", terminal=True)
    reporter.start()
    try:
        wait_for(lambda: not reporter.acks.empty())
        assert sent == [b"fresh"]
        assert reporter.acks.get_nowait() == ("normal", b"fresh")
        assert reporter.acks.empty()  # Dropping a result is not successful publication.
    finally:
        reporter.close()


@pytest.mark.parametrize("completed", [False, True])
def test_normal_mode_reporting_is_not_limited_by_backfill_ttl(config, batch_dir, clock, completed):
    original = supervisor(config)
    try:
        if completed:
            row = complete(original)
        else:
            original.scan()
            row = original.store.rows()[0]
        assert row["report_expires_ms"] == 0
    finally:
        original.store.close()
    clock[0] = row["timestamp"] + 2 * DAY
    restored = supervisor(config)
    try:
        restored.initialize()
        restored.scan()
        assert row["id"] in restored.reporter.slots
    finally:
        restored.store.close()


@pytest.mark.parametrize("mode", ["upload_only", "upload_and_report"])
def test_existing_database_keeps_history_and_assigns_ttl_only_to_deferred_snapshots(
    config, batch_dir, clock, mode
):
    original = enroll(config)
    s = supervisor(replace(config, run_mode=mode))
    try:
        done = complete(s)
        s.store.db.execute("ALTER TABLE batches DROP COLUMN report_expires_ms")
        s.store.db.commit()
    finally:
        s.store.close()
    clock[0] = original["timestamp"] + DAY
    restored = supervisor(config)
    try:
        restored.initialize()
        row = restored.store.get(done["id"])
        assert row["state"] == "done" and row["attempts"] == 1
        if mode == "upload_only":
            assert row["report_expires_ms"] == original["timestamp"] + DAY
            assert row["pending"] is row["final_snapshot"] is None
            assert not restored.reporter.slots
        else:
            assert row["report_expires_ms"] == 0 and restored.reporter.slots
    finally:
        restored.store.close()
