"""Worker pipe EOF must not kill the supervisor or hide an upload failure."""

import errno
import json
import time
from collections import deque

import pytest
from cloudrobo_ingestion.daemon import Supervisor
from cloudrobo_ingestion.diagnostics import DiagnosticError, make_error, summary
from cloudrobo_ingestion.progress import DisabledReporter, Reporter, message_class
from cloudrobo_ingestion.store import BatchStore


def closed_error(kind):
    if kind == "eof":
        return EOFError()
    if kind in (109, 232, 233):
        exc = BrokenPipeError(errno.EPIPE, "管道已结束")
        exc.winerror = kind
        return exc
    return OSError(kind, "peer closed")


class ClosingPipe:
    def __init__(self, events, kind, where):
        self.events = deque(events)
        self.kind, self.where = kind, where
        self.closure_reads = 0
        self.closed = False

    def _closed_error(self) -> Exception:
        self.closure_reads += 1
        assert self.closure_reads == 1, "Supervisor read a closed channel again"
        return closed_error(self.kind)

    def poll(self) -> bool:
        if self.events or self.where == "recv":
            return True
        raise self._closed_error()

    def recv(self) -> dict:
        if self.events:
            return self.events.popleft()
        raise self._closed_error()

    def send(self, message):
        pass

    def close(self):
        self.closed = True


class Child:
    def __init__(self, alive=False, exitcode=0):
        self.alive, self.exitcode = alive, exitcode
        self.closed = False

    def is_alive(self):
        return self.alive

    def join(self):
        assert not self.alive

    def close(self):
        self.closed = True


def attach(s, events, kind, where, *, alive=False, exitcode=0):
    batch = s.store.rows()[0]
    s.active = batch["id"]
    s.store.update(batch["id"], state="uploading", attempts=1)
    path = next(iter(s.progress[batch["id"]].nodes))
    s.progress[batch["id"]].update(path, 1, 0, begin=True)
    s.reporter.set(batch["id"], s.progress[batch["id"]])
    pipe = s.pipe = ClosingPipe(events, kind, where)
    child = s.process = Child(alive, exitcode)
    return batch, pipe, child


def wait_for(predicate):
    end = time.monotonic() + 3
    while time.monotonic() < end:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    raise AssertionError("Expected published state was not observed")


@pytest.mark.parametrize("kind", ["eof", 109, 232, 233, errno.EPIPE, errno.ECONNRESET])
@pytest.mark.parametrize("where", ["poll", "recv"])
def test_closed_channel_preserves_obs_failure_and_publishes_failed_files(
    config, batch_dir, kind, where
):
    for i in range(7):
        (batch_dir / f"queued-{i}.parquet").write_bytes(b"data")
    messages = []

    class Transport:
        def publish(self, topic, payload):
            messages.append(message_class().FromString(payload))

        def close(self):
            pass

    reporter = Reporter("unused", config.topic, factory=lambda _: Transport(), interval=0.02)
    s = Supervisor(config, reporter, config.work_dir / "status.json")
    s.scan()
    info = make_error("obs", "uploading", ConnectionRefusedError("Connection refused"))
    batch, pipe, child = attach(s, [], kind, where, alive=True)
    reporter.start()
    try:
        wait_for(lambda: any(m.nodes[0].result_state == 1 for m in messages))
        pipe.events.append(
            dict(event="result", state="failed", error=summary(info), error_info=info)
        )
        s.drain()
        assert s.pipe_eof and pipe.closure_reads == 1
        assert s.process is child and child.alive  # Result is visible before worker cleanup/exit.
        assert s.store.get(batch["id"])["state"] == "failed"
        assert s.snapshot()["error_info"] == info
        assert s.snapshot()["progress"]["failed_files"] == 8
        wait_for(lambda: any(all(n.result_state == 3 for n in m.nodes) for m in messages))
        for _ in range(3):
            s.drain()
        assert pipe.closure_reads == 1
        child.alive = False
        s.drain()
        assert s.process is s.pipe is None and pipe.closed and child.closed
        row = s.store.get(batch["id"])
        assert row["state"] == "failed" and row["retry_at"] > time.time()
        assert row["error_info"] == info and s.error_info is None
        assert not s.stop_event.is_set()
        s.launch_next()  # Backoff, rather than immediate retry hiding the failure.
        assert s.process is None
        # Failure is published once; backoff must not schedule duplicate rounds.
        assert row["report_kind"] == "failure"
        assert row["report_count"] <= 1
        assert all(n.result_state == 3 for n in messages[-1].nodes)
    finally:
        reporter.close()
        s.store.close()


@pytest.mark.parametrize("where", ["poll", "recv"])
def test_success_result_followed_by_windows_close_stays_successful(config, batch_dir, where):
    reporter = Reporter("unused", config.topic)
    s = Supervisor(config, reporter, config.work_dir / "status.json")
    try:
        s.scan()
        p = next(iter(s.progress.values()))
        events = [
            dict(event="file", path=n.path, state=2, transferred=n.size) for n in p.nodes.values()
        ]
        events.append(dict(event="result", state="done", error=""))
        batch, pipe, _ = attach(s, events, 109, where)
        s.drain()
        row = s.store.get(batch["id"])
        assert row["state"] == "done" and row["error_info"] is None
        assert message_class().FromString(row["pending"]).task_state == 1
        assert pipe.closed and pipe.closure_reads == 1
    finally:
        s.store.close()


@pytest.mark.parametrize("kind", ["eof", 109, errno.ECONNRESET])
def test_close_without_result_marks_failed_then_reaps_crashed_worker(config, batch_dir, kind):
    s = Supervisor(config, DisabledReporter(), config.work_dir / "status.json")
    try:
        s.scan()
        batch, pipe, child = attach(s, [], kind, "poll", alive=True, exitcode=7)
        s.drain()
        assert s.progress[batch["id"]].summary()["failed_files"] == 1
        child.alive = False
        s.drain()
        row = s.store.get(batch["id"])
        assert row["state"] == "failed" and row["retry_at"] > time.time()
        assert row["error_info"]["stage"] == "worker_exit"
        assert row["error_info"]["context"]["exit_code"] == 7
        assert pipe.closure_reads == 1 and not s.stop_event.is_set()
    finally:
        s.store.close()


def test_cleanup_only_result_followed_by_close_does_not_replace_original_error(config, batch_dir):
    s = Supervisor(config, DisabledReporter(), config.work_dir / "status.json")
    try:
        s.scan()
        info = make_error("obs", "uploading", TimeoutError("timed out"))
        batch, pipe, _ = attach(s, [dict(event="cleaned")], 109, "poll")
        s.store.update(
            batch["id"], state="failed", error=summary(info), error_info=info, cleanup_pending=1
        )
        s.cleanup_only = True
        s.drain()
        row = s.store.get(batch["id"])
        assert row["state"] == "failed" and row["error_info"] == info
        assert row["cleanup_pending"] == 0 and pipe.closed
    finally:
        s.store.close()


def test_actual_ipc_fault_still_reported_and_shutdown_writes_current_status(config, batch_dir):
    reporter = Reporter("unused", config.topic, factory=lambda _: None)
    s = Supervisor(config, reporter, config.work_dir / "status.json")
    s.scan()
    # EBADF is a real local programming/resource fault, not a peer-closed signal.
    batch, pipe, child = attach(s, [], errno.EBADF, "poll")
    reporter.start()
    with pytest.raises(DiagnosticError) as caught:
        s.run()
    assert caught.value.info["stage"] == "ipc"
    assert pipe.closure_reads == 1 and pipe.closed and child.closed
    assert not reporter.thread.is_alive()
    status = json.loads((config.work_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed" and status["error_info"]["stage"] == "ipc"
    assert status["active_batch"] is None and status["counts"] == {"interrupted": 1}
    store = BatchStore(config)
    try:
        assert store.get(batch["id"])["state"] == "interrupted"
    finally:
        store.close()
