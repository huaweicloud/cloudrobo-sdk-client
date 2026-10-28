"""Deterministic pacing, durable result rounds and stale receipt protection."""

import time
from collections import deque
from dataclasses import replace

import pytest
from cloudrobo_ingestion.daemon import Supervisor
from cloudrobo_ingestion.filesystem import FileEntry
from cloudrobo_ingestion.progress import (
    BatchProgress,
    PublishReceipt,
    Reporter,
    ResultToken,
    message_class,
)
from cloudrobo_ingestion.store import DEFERRED_REPORT_TTL_MS, BatchStore
from test_worker_supervisor import successful_child


class ManualClock:
    def __init__(self):
        self.elapsed = 100.0
        self.wall = 2_000_000

    def monotonic(self):
        return self.elapsed

    def now_ms(self):
        return self.wall

    def advance(self, seconds):
        self.elapsed += seconds
        self.wall += round(seconds * 1000)


@pytest.fixture(name="clock")
def delivery_clock(monkeypatch):
    value = ManualClock()
    for module in ("store", "daemon", "progress"):
        monkeypatch.setattr(f"cloudrobo_ingestion.{module}.now_ms", value.now_ms)
    return value


def pump(supervisor, condition, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        supervisor.next_final_retry = 0
        supervisor.drain()
        if condition():
            return
        time.sleep(0.002)
    raise AssertionError("delivery did not reach expected state")


def make_supervisor(config, clock, *, delay=4):
    sent = []

    class Transport:
        def publish(self, topic, payload):
            sent.append((clock.monotonic(), message_class().FromString(payload)))

        def close(self):
            pass

    reporter = Reporter(
        "unused", config.topic, factory=lambda _: Transport(), clock=clock.monotonic
    )
    supervisor = Supervisor(
        config, reporter, config.work_dir / "status.json", clock=clock.monotonic
    )
    if delay is not None:
        supervisor.result_delay = lambda: delay
    return supervisor, sent


def enroll(supervisor, name, *, kind="success"):
    row = supervisor.store.add(name, supervisor.config)
    progress = BatchProgress(row, [FileEntry("big.mp4", 8 * 1024**3, 0, 0)], supervisor.source)
    progress.update("big.mp4", 2 if kind == "success" else 3, 8 * 1024**3)
    progress.finished = kind == "success"
    supervisor.store.update(row["id"], state="done" if kind == "success" else "failed")
    supervisor.store.save_result(row["id"], progress.snapshot(), kind)
    return row, progress


def receipt(supervisor):
    batch_id = supervisor.outbox_id
    return PublishReceipt(
        batch_id,
        supervisor.reporter.slots[batch_id][0],
        supervisor.outbox_token,
        supervisor.clock(),
        1.0,
    )


@pytest.mark.parametrize("kind", ["success", "failure"])
def test_success_repeats_three_times_but_failure_is_sent_once(config, clock, kind):
    supervisor, sent = make_supervisor(config, clock)
    row, _ = enroll(supervisor, "batch", kind=kind)
    supervisor.initialize()
    supervisor.reporter.start()
    try:
        limit = 3 if kind == "success" else 1
        for expected in range(1, limit + 1):
            pump(supervisor, lambda: supervisor.store.get(row["id"])["report_count"] == expected)
            assert len(sent) == expected
            assert supervisor.outbox_id is None
            if expected < limit:
                assert supervisor.store.get(row["id"])["final_snapshot"] is not None
                clock.advance(3.9)
                supervisor.drain()
                assert supervisor.outbox_id is None and len(sent) == expected
                clock.advance(0.1)
        final = supervisor.store.get(row["id"])
        assert final["pending"] is final["final_snapshot"] is None
        assert [at for at, _ in sent] == ([100, 104, 108] if kind == "success" else [100])
        assert len({message.SerializeToString() for _, message in sent}) == 1
        assert all(message.nodes[0].size == 8 * 1024**3 for _, message in sent)
        assert sent[-1][1].task_state == int(kind == "success")
        assert supervisor.snapshot()["reporter"]["pending_result_batches"] == 0
        clock.advance(20)
        supervisor.drain()
        time.sleep(0.04)
        assert len(sent) == limit  # Failure never receives delayed duplicate rounds.
    finally:
        supervisor.reporter.close()
        supervisor.store.close()


def test_wakes_cannot_bypass_batch_interval_or_global_spacing(config, clock):
    supervisor, sent = make_supervisor(config, clock)
    row = supervisor.store.add("active", config)
    progress = BatchProgress(row, [FileEntry("file", 100, 0, 0)], supervisor.source)
    supervisor.progress[row["id"]] = progress
    supervisor.active = row["id"]
    supervisor.reporter.set(row["id"], progress)
    final, _ = enroll(supervisor, "finished")
    supervisor.initialize()
    supervisor.reporter.start()
    try:
        pump(supervisor, lambda: len(sent) == 1)
        assert sent[0][1].asset_path.endswith("active/")  # Live data has priority.
        for index in range(10):
            progress.update("file", 1, index)
            supervisor.reporter.set(row["id"], progress)
        time.sleep(0.04)
        assert len(sent) == 1
        clock.advance(0.19)
        supervisor.drain()
        time.sleep(0.03)
        assert len(sent) == 1
        clock.advance(0.02)
        pump(supervisor, lambda: len(sent) == 2)
        assert sent[1][1].asset_path.endswith("finished/")
        clock.advance(0.78)
        supervisor.drain()
        time.sleep(0.03)
        assert len(sent) == 2
        clock.advance(0.02)
        pump(supervisor, lambda: len(sent) == 3)
        assert sent[2][1].asset_path.endswith("active/")
        assert sent[2][0] - sent[0][0] >= 1
        assert supervisor.store.get(final["id"])["report_count"] == 1
    finally:
        supervisor.reporter.close()
        supervisor.store.close()


def test_stale_and_duplicate_receipts_do_not_consume_rounds(config, clock):
    supervisor, _ = make_supervisor(config, clock)
    row, _ = enroll(supervisor, "batch")
    try:
        supervisor.initialize()
        first = receipt(supervisor)
        supervisor.reporter.acks.put((row["id"], first.payload))  # Old progress receipt.
        supervisor.drain()
        assert supervisor.store.get(row["id"])["report_count"] == 0
        supervisor.reporter.acks.put(first)
        supervisor.drain()
        clock.advance(4)
        supervisor.next_final_retry = 0
        supervisor.drain()
        second = receipt(supervisor)
        assert second.token.send_round == 2
        supervisor.reporter.acks.put(first)
        supervisor.reporter.acks.put(replace(second, token=ResultToken("obsolete", 2)))
        supervisor.drain()
        assert supervisor.store.get(row["id"])["report_count"] == 1
        assert supervisor.outbox_token == second.token
        supervisor.reporter.acks.put(second)
        supervisor.drain()
        assert supervisor.store.get(row["id"])["report_count"] == 2
    finally:
        supervisor.store.close()


@pytest.mark.parametrize("kind", ["success", "failure"])
@pytest.mark.parametrize("count", [0, 1, 2])
def test_all_results_expire_after_first_enqueue_without_changing_upload_state(
    config, clock, kind, count
):
    supervisor, _ = make_supervisor(config, clock)
    row, _ = enroll(supervisor, "batch", kind=kind)
    supervisor.store.update(row["id"], report_count=count, attempts=7, retry_at=123)
    try:
        clock.advance(DEFERRED_REPORT_TTL_MS / 1000 - 0.001)
        supervisor.initialize()
        assert supervisor.outbox_id == (None if kind == "failure" and count else row["id"])
        clock.advance(0.001)
        supervisor.expire_reports()
        restored = supervisor.store.get(row["id"])
        assert restored["pending"] is restored["final_snapshot"] is None
        assert restored["state"] == ("done" if kind == "success" else "failed")
        assert restored["attempts"] == 7 and restored["retry_at"] == 123
        assert not supervisor.reporter.slots
    finally:
        supervisor.store.close()


def test_restart_keeps_round_count_and_remaining_delay(config, clock):
    original, _ = make_supervisor(config, clock)
    row, _ = enroll(original, "batch")
    original.initialize()
    original.reporter.acks.put(receipt(original))
    original.drain()
    version = original.store.get(row["id"])["report_version"]
    original.store.close()
    clock.advance(2)
    restored, _ = make_supervisor(config, clock)
    try:
        restored.initialize()
        assert restored.outbox_id is None
        assert restored.store.get(row["id"])["report_count"] == 1
        clock.advance(2)
        restored.drain()
        assert restored.outbox_token == ResultToken(version, 2)
    finally:
        restored.store.close()


def test_cancel_old_failure_before_retry_and_ignore_its_receipt(config, clock):
    supervisor, _ = make_supervisor(config, clock)
    row, progress = enroll(supervisor, "batch", kind="failure")
    supervisor.progress[row["id"]] = progress
    supervisor.initialize()
    stale = receipt(supervisor)
    try:
        supervisor._cancel_result(row["id"], "retry")
        progress.finished = True
        progress.update("big.mp4", 2, 8 * 1024**3)
        supervisor.store.update(row["id"], state="done")
        supervisor.store.save_result(row["id"], progress.snapshot(), "success")
        supervisor.queue_final(supervisor.store.get(row["id"]))
        current = supervisor.outbox_token
        supervisor.reporter.acks.put(stale)
        supervisor.drain()
        assert supervisor.outbox_token == current and current != stale.token
        assert supervisor.store.get(row["id"])["report_count"] == 0
        assert supervisor.store.get(row["id"])["report_kind"] == "success"
    finally:
        supervisor.store.close()


def test_unexpired_legacy_result_is_adopted_without_resurrecting_cleared_history(config, clock):
    store = BatchStore(config)
    old = store.add("pending", config)
    cleared = store.add("cleared", config)
    store.update(old["id"], state="done", pending=b"legacy", final_snapshot={"source": ""})
    store.update(cleared["id"], state="done")
    for column in ("report_version", "report_kind", "report_count", "report_next_ms"):
        store.db.execute(f"ALTER TABLE batches DROP COLUMN {column}")
    store.db.commit()
    store.close()
    restored = BatchStore(config)
    try:
        row = restored.get(old["id"])
        assert row["report_version"] and row["report_count"] == 0
        assert row["report_expires_ms"] == old["timestamp"] + DEFERRED_REPORT_TTL_MS
        assert restored.get(cleared["id"])["final_snapshot"] is None
        assert restored.pending_results() == 1
    finally:
        restored.close()


class EventPipe:
    def __init__(self):
        self.events = deque()

    def poll(self):
        return bool(self.events)

    def recv(self):
        return self.events.popleft()


def test_only_file_begin_enables_periodic_progress_and_copy_leaves_room_for_old_results(
    config,
    batch_dir,
    clock,
):
    candidate = config.source_dir / "next"
    candidate.mkdir()
    (candidate / "__UPLOAD_READY__").touch()
    (candidate / "file.txt").write_bytes(b"data")
    supervisor, sent = make_supervisor(config, clock)
    supervisor.scan()
    assert len(supervisor.progress) == 2 and not supervisor.reporter.slots
    old, _ = enroll(supervisor, "previous")
    current = next(row for row in supervisor.store.rows() if row["path"] == batch_dir.name)
    supervisor.active = current["id"]
    pipe = supervisor.pipe = EventPipe()
    supervisor.initialize()
    supervisor.reporter.start()
    try:
        pump(supervisor, lambda: supervisor.store.get(old["id"])["report_count"] == 1)
        for phase in ("copying", "validating", "obs_initializing", "uploading"):
            pipe.events.append(dict(event="phase", state=phase))
            clock.advance(1)
            supervisor.drain()
        pump(supervisor, lambda: supervisor.store.get(old["id"])["report_count"] == 2)
        assert all(message.asset_path.endswith("previous/") for _, message in sent)
        path = next(iter(supervisor.progress[current["id"]].entries))
        pipe.events.append(dict(event="file", path=path, state=1, transferred=0, begin=True))
        supervisor.drain()
        clock.advance(0.21)
        pump(
            supervisor,
            lambda: any(message.asset_path.endswith(batch_dir.name + "/") for _, message in sent),
        )
        assert not any(message.asset_path.endswith("next/") for _, message in sent)
        assert supervisor.store.get(old["id"])["report_count"] == 2
    finally:
        supervisor.reporter.close()
        supervisor.store.close()


@pytest.mark.parametrize("phase", ["copying", "validating", "obs_initializing"])
def test_failure_before_any_file_upload_still_freezes_a_result(config, batch_dir, clock, phase):
    supervisor, sent = make_supervisor(config, clock)
    supervisor.scan()
    row = supervisor.store.rows()[0]
    try:
        assert not supervisor.reporter.slots
        supervisor.batch_failure(row, phase, OSError("test failure"), state="failed")
        supervisor.reporter.start()
        pump(supervisor, lambda: supervisor.store.get(row["id"])["report_count"] == 1)
        assert len(sent) == 1 and sent[0][1].nodes[0].result_state == 3
        assert sent[0][1].task_state == 0
        assert supervisor.store.get(row["id"])["report_kind"] == "failure"
    finally:
        supervisor.reporter.close()
        supervisor.store.close()


def test_publishing_error_does_not_count_and_random_delay_is_sampled_per_success(
    config, clock, monkeypatch
):
    supervisor, _ = make_supervisor(config, clock, delay=None)
    row, _ = enroll(supervisor, "batch")
    online = [False]
    calls = []

    class Transport:
        def publish(self, topic, payload):
            calls.append(clock.monotonic())
            if not online[0]:
                raise ConnectionError("offline")

        def close(self):
            pass

    supervisor.reporter.factory = lambda _: Transport()
    delays = iter([1, 5, 4])

    def draw(low, high):
        assert (low, high) == (1, 5)
        return next(delays)

    monkeypatch.setattr("cloudrobo_ingestion.daemon.random.uniform", draw)
    supervisor.initialize()
    supervisor.reporter.start()
    try:
        pump(supervisor, lambda: bool(supervisor.reporter.diagnostics()["error"]))
        assert supervisor.store.get(row["id"])["report_count"] == 0
        online[0] = True
        clock.advance(2)
        pump(supervisor, lambda: supervisor.store.get(row["id"])["report_count"] == 1)
        assert supervisor.store.get(row["id"])["report_next_ms"] == clock.wall + 1000
        clock.advance(1)
        pump(supervisor, lambda: supervisor.store.get(row["id"])["report_count"] == 2)
        assert supervisor.store.get(row["id"])["report_next_ms"] == clock.wall + 5000
        clock.advance(5)
        pump(supervisor, lambda: supervisor.store.get(row["id"])["report_count"] == 3)
        assert calls == [100, 102, 103, 108]
        assert supervisor.store.get(row["id"])["pending"] is None
    finally:
        supervisor.reporter.close()
        supervisor.store.close()


def test_empty_batch_never_has_periodic_slot_and_upload_does_not_wait_for_offline_result(
    config,
    clock,
):
    for name in ("a-empty", "b-empty"):
        root = config.source_dir / name
        root.mkdir()
        (root / "__UPLOAD_READY__").touch()
    supervisor, _ = make_supervisor(config, clock)
    supervisor.worker_target = successful_child
    supervisor.reporter.factory = lambda _: (_ for _ in ()).throw(ConnectionError("offline"))
    supervisor.reporter.start()
    try:
        supervisor.scan()
        assert not supervisor.reporter.slots
        for _ in range(2):
            supervisor.launch_next()
            pump(supervisor, lambda: supervisor.process is None)
        assert supervisor.snapshot()["counts"] == {"done": 2}
        assert supervisor.store.pending_results() == 2
        assert len(supervisor.reporter.slots) <= 1
        assert not supervisor.progress
    finally:
        if supervisor.process:
            supervisor.process.terminate()
            supervisor.process.join()
        supervisor.reporter.close()
        supervisor.store.close()


def test_live_schedule_uses_monotonic_time_when_wall_clock_changes(config, clock):
    supervisor, sent = make_supervisor(config, clock)
    row, _ = enroll(supervisor, "batch")
    supervisor.initialize()
    supervisor.reporter.start()
    try:
        pump(supervisor, lambda: supervisor.store.get(row["id"])["report_count"] == 1)
        clock.wall += 60_000
        supervisor.drain()
        time.sleep(0.04)
        assert supervisor.outbox_id is None and len(sent) == 1
        clock.advance(4)
        pump(supervisor, lambda: supervisor.store.get(row["id"])["report_count"] == 2)
        assert [at for at, _ in sent] == [100, 104]
    finally:
        supervisor.reporter.close()
        supervisor.store.close()


def test_expired_stale_receipt_does_not_strand_another_outbox_slot(config, clock):
    supervisor, _ = make_supervisor(config, clock)
    first, _ = enroll(supervisor, "first")
    second, _ = enroll(supervisor, "second")
    supervisor.initialize()
    stale = receipt(supervisor)
    supervisor._cancel_result(first["id"], "superseded")
    supervisor.queue_final(supervisor.store.get(second["id"]))
    assert supervisor.outbox_id == second["id"]
    clock.advance(DEFERRED_REPORT_TTL_MS / 1000)
    fresh, _ = enroll(supervisor, "fresh")
    try:
        supervisor.reporter.acks.put(stale)
        supervisor.next_final_retry = 0
        supervisor.drain()
        assert supervisor.store.get(second["id"])["pending"] is None
        assert second["id"] not in supervisor.reporter.slots
        assert supervisor.outbox_id == fresh["id"]
    finally:
        supervisor.store.close()


@pytest.mark.parametrize("published", [0, 1, 2])
def test_restart_discards_legacy_failure_repeats_but_keeps_unsent_failure(config, clock, published):
    original, _ = make_supervisor(config, clock)
    row, _ = enroll(original, "failed", kind="failure")
    original.store.update(row["id"], report_count=published, error="OBS failure", attempts=4)
    original.store.close()
    restored, sent = make_supervisor(config, clock)
    restored.initialize()
    restored.reporter.start()
    try:
        saved = restored.store.get(row["id"])
        assert saved["error"] == "OBS failure" and saved["attempts"] == 4
        if published:
            assert saved["pending"] is saved["final_snapshot"] is None
            assert restored.outbox_id is None
            clock.advance(10)
            restored.drain()
            assert not sent
        else:
            pump(restored, lambda: restored.store.get(row["id"])["report_count"] == 1)
            assert len(sent) == 1 and sent[0][1].nodes[0].result_state == 3
            assert restored.store.get(row["id"])["final_snapshot"] is None
    finally:
        restored.reporter.close()
        restored.store.close()


def test_failure_retries_publication_errors_without_scheduling_random_repeats(config, clock):
    supervisor, _ = make_supervisor(config, clock)
    row, _ = enroll(supervisor, "failed", kind="failure")
    supervisor.result_delay = lambda: pytest.fail("Failure must not schedule a random repeat")
    attempts = []

    class Transport:
        def publish(self, topic, payload):
            attempts.append(clock.monotonic())
            if len(attempts) == 1:
                raise ConnectionError("offline")

        def close(self):
            pass

    supervisor.reporter.factory = lambda _: Transport()
    supervisor.initialize()
    supervisor.reporter.start()
    try:
        pump(supervisor, lambda: bool(supervisor.reporter.diagnostics()["error"]))
        assert supervisor.store.get(row["id"])["report_count"] == 0
        clock.advance(2)
        pump(supervisor, lambda: supervisor.store.get(row["id"])["report_count"] == 1)
        assert supervisor.store.get(row["id"])["pending"] is None
        clock.advance(10)
        supervisor.drain()
        time.sleep(0.03)
        assert attempts == [100, 102]
    finally:
        supervisor.reporter.close()
        supervisor.store.close()
