"""Deterministic timelines, wire anomalies, bounded publication and isolation."""

import builtins
import json
import math
import queue
from collections import Counter

import pytest
from cloudrobo_ingestion.progress import message_class
from cloudrobo_ingestion_mock.runtime import MockRunner
from cloudrobo_ingestion_mock.scenarios import FUNCTIONAL, SCENARIOS, Packet, Scenarios
from google.protobuf.message import DecodeError


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def wait(self, seconds):
        self.now += seconds


def test_default_event_output_preserves_unicode_with_ascii_json(capsys):
    runner = MockRunner(None, "r2c/local")
    runner.event("step", description="超市取货／遥操接管🤖")
    output = capsys.readouterr().out
    assert output.isascii()
    assert json.loads(output)["description"] == "超市取货／遥操接管🤖"


class AckReporter:
    topic = "r2c/local/data_ingestion/upload_status"

    def __init__(self, clock, *, offline_until=0):
        self.clock = clock
        self.offline_until = offline_until
        self.acks = queue.Queue()
        self.sent = []
        self.slot = None
        self.max_pending = 0
        self.closed = False

    def start(self):
        pass

    def set(self, key, payload, terminal=False):
        assert terminal
        assert self.slot is None
        self.slot = key, payload
        self.max_pending = max(self.max_pending, 1)

    def poll(self):
        if self.slot and self.clock() >= self.offline_until:
            key, payload = self.slot
            self.sent.append((self.clock(), key, payload))
            self.acks.put((key, payload))
            self.slot = None

    def close(self):
        self.closed = True

    def diagnostics(self):
        return dict(error="offline")


def execute(name, *, file_count=100, scale=1, repeat=1, offline_until=0, emit=None):
    clock = Clock()
    reporter = AckReporter(clock, offline_until=offline_until)
    events = []

    def wait(seconds):
        clock.wait(seconds)
        reporter.poll()

    def record(event):
        # NaN/Infinity in protocol samples must not leak into invalid JSON logs.
        json.dumps(event, allow_nan=False)
        events.append(event)
        if emit:
            emit(event)

    runner = MockRunner(
        reporter,
        "r2c/local",
        scenario=name,
        file_count=file_count,
        time_scale=scale,
        repeat=repeat,
        emit=record,
        clock=clock,
        wait=wait,
    )
    return runner, reporter, events, clock


def messages(reporter):
    return [message_class().FromString(payload) for _, _, payload in reporter.sent]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_every_scenario_finishes_single_run(scenario):
    runner, reporter, events, _ = execute(scenario, file_count=3, scale=10)
    runner.run()
    assert reporter.closed and runner.sent > 0 and reporter.max_pending == 1
    assert events[-1]["event"] == "run_complete"
    assert len([e for e in events if e["event"] == "iteration_complete"]) == 1
    if scenario != "malformed":
        assert messages(reporter)


def test_overview_classification_types_sizes_and_batch_states():
    runner, reporter, _, _ = execute("overview")
    runner.run()
    data = messages(reporter)
    assert {node.data_type for m in data for node in m.nodes} == set(range(5))
    assert len({(n.task_type, n.collect_method) for m in data for n in m.nodes}) == 3
    first = data[0]
    assert len({n.size for n in first.nodes}) > 3 and any(n.size == 0 for n in first.nodes)
    assert Counter(n.task_name for n in first.nodes)["片段.mp4"] == 2
    finals = [m for m in data if m.task_state == 1]
    assert len(finals) == 3 and len({m.asset_path for m in finals}) == 3
    assert all(m.source == "r2c/local" for m in data)
    assert all(n.progress == 100 and n.result_state == 2 for m in finals for n in m.nodes)


def test_retry_preserves_identity_failure_and_actual_restart():
    runner, reporter, events, _ = execute("retry", scale=100)
    runner.run()
    data = messages(reporter)
    failed = [m for m in data if [n.result_state for n in m.nodes] == [2, 3, 3]]
    assert len(failed) >= 3
    assert all(m.timestamp == data[0].timestamp for m in data)
    assert all([n.file_id for n in m.nodes] == [n.file_id for n in data[0].nodes] for m in data)
    assert all(
        [n.enqueue_time for n in m.nodes] == [n.enqueue_time for n in data[0].nodes] for m in data
    )
    sends = [e for e in events if e["event"] == "send"]
    index = max(i for i, e in enumerate(sends) if e["step"] == "failed")
    assert [n.result_state for n in data[index + 1].nodes] == [1, 3, 3]
    assert data[-1].task_state == 1
    failure_times = [
        e["elapsed_seconds"] for e in events if e.get("step") == "failed" and e["event"] == "send"
    ]
    assert failure_times[-1] - failure_times[0] >= 3


def test_persistent_failure_is_successful_test_with_failed_upload_state():
    runner, reporter, events, _ = execute("persistent-failure")
    runner.run()
    assert all(n.result_state == 3 for n in messages(reporter)[-1].nodes)
    assert all(m.task_state == 0 for m in messages(reporter))
    assert events[-1]["event"] == "run_complete"


@pytest.mark.parametrize("scale", [1, 2])
def test_rates_follow_real_clock_stall_and_recovery(scale):
    runner, reporter, events, _ = execute("variable-speed", scale=scale)
    runner.run()
    sends = [e for e in events if e["event"] == "send"]
    data = messages(reporter)
    slow = [m.nodes[0].avg_bps for e, m in zip(sends, data) if e["step"] == "slow"]
    assert max(slow) == pytest.approx(scale * 1024 * 1024, rel=0.1)
    stalled = [m.nodes[0] for e, m in zip(sends, data) if e["step"] == "stalled"]
    assert len(stalled) >= 6 and stalled[-1].avg_bps == stalled[-1].eta_ms == 0
    assert len({n.progress for n in stalled}) == 1
    assert data[-1].task_state == 1


@pytest.mark.parametrize("count", [100, 1000, 5000])
def test_stress_samples_30_seconds_and_has_complete_nodes(count):
    runner, reporter, events, _ = execute("stress", file_count=count, scale=100)
    runner.run()
    sends = [e for e in events if e["event"] == "send" and e["step"] == "stress-30s"]
    assert len(sends) >= 30
    assert sends[-1]["elapsed_seconds"] - sends[0]["elapsed_seconds"] >= 30 - 1e-9
    assert all(len(m.nodes) == count for m in messages(reporter))
    assert messages(reporter)[-1].task_state == 1
    assert all(e["build_ms"] >= 0 and e["size_bytes"] > 0 for e in sends)


def test_duplicate_and_out_of_order_bytes_are_not_coalesced():
    runner, reporter, _, _ = execute("duplicate")
    runner.run()
    raw = [p for _, _, p in reporter.sent]
    assert len(raw) == 4 and raw[0] == raw[1] and raw[2] == raw[3]
    runner, reporter, _, _ = execute("out-of-order")
    runner.run()
    raw = [p for _, _, p in reporter.sent]
    assert len(raw) == 6 and raw[0] == raw[2] and raw[3] == raw[5]
    data = messages(reporter)
    assert data[0].nodes[0].progress < data[1].nodes[0].progress
    assert data[3].task_state == 1 and data[4].task_state == 0
    assert len({m.nodes[0].file_id for m in data}) == 1


def test_invalid_fields_parse_and_are_each_followed_by_valid_control():
    runner, reporter, _, _ = execute("invalid-fields")
    runner.run()
    data = messages(reporter)
    assert len(data) >= 40 and len(data) % 2 == 0
    for invalid, control in zip(data[::2], data[1::2]):
        assert control.source == "r2c/local" and control.task_state == 1
        assert control.nodes[0].progress == 100 and control.nodes[0].result_state == 2
        if invalid.timestamp >= 0:
            assert control.timestamp == invalid.timestamp
    values = [m.nodes[0].progress for m in data[::2]]
    assert any(math.isnan(x) for x in values) and any(math.isinf(x) for x in values)
    assert any(x < 0 for x in values) and any(x > 100 for x in values)


def test_malformed_samples_and_controls():
    runner, reporter, _, _ = execute("malformed")
    runner.run()
    assert len(reporter.sent) == 4
    for i, (_, _, raw) in enumerate(reporter.sent):
        if i % 2 == 0:
            with pytest.raises(DecodeError):
                message_class().FromString(raw)
        else:
            assert message_class().FromString(raw).task_state == 1


def test_functional_suite_does_not_send_protocol_or_stress_cases():
    runner, _, events, _ = execute("functional-suite", scale=100)
    runner.run()
    steps = {e.get("step") for e in events}
    assert {"queued", "failed", "stalled", "finish"} <= steps
    assert not any(
        s and (s.startswith("invalid") or "stress" in s or "truncated" in s) for s in steps
    )
    assert set(FUNCTIONAL) == {"overview", "retry", "persistent-failure", "variable-speed"}


def test_repeat_generates_fresh_identity():
    runner, reporter, _, _ = execute("duplicate", repeat=2)
    runner.run()
    data = messages(reporter)
    assert data[0].nodes[0].file_id != data[4].nodes[0].file_id
    assert data[0].timestamp < data[4].timestamp
    assert data[0].asset_path != data[4].asset_path


def test_no_obs_customer_files_database_or_daemon_access(monkeypatch, tmp_path):
    import sqlite3

    from cloudrobo_ingestion import filesystem, lifecycle
    from cloudrobo_ingestion.store import BatchStore

    original = builtins.__import__

    def guard(name, *args, **kwargs):
        if name == "obs" or name.startswith("obs.") or name.endswith("obs_client"):
            raise AssertionError("Mock accessed OBS")
        return original(name, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("Mock accessed customer/daemon/database state")

    monkeypatch.setattr(builtins, "__import__", guard)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(BatchStore, "__init__", forbidden)
    monkeypatch.setattr(lifecycle, "runtime_dir", forbidden)
    monkeypatch.setattr(filesystem, "discover", forbidden)
    monkeypatch.setattr(filesystem, "checked_copy", forbidden)
    monkeypatch.chdir(tmp_path)
    runner, _, _, _ = execute("functional-suite", scale=100)
    runner.run()
    assert not list(tmp_path.iterdir())


def test_offline_timeout_keeps_one_message_no_new_batch():
    runner, reporter, events, clock = execute("overview", offline_until=float("inf"))
    with pytest.raises(TimeoutError, match="30"):
        runner.run()
    assert 30 <= clock() < 31 and reporter.closed and reporter.max_pending == 1
    assert len([e for e in events if e["event"] == "send"]) == 1
    assert events[-1]["event"] == "run_failed"


def test_offline_recovery_resumes_current_message():
    runner, reporter, events, _ = execute("duplicate", offline_until=5)
    runner.run()
    assert len(reporter.sent) == 4 and reporter.max_pending == 1
    assert events[-1]["event"] == "run_complete"


def test_infinite_repeat_can_be_stopped_and_reporter_closes():
    runner, reporter, events, _ = execute("duplicate", repeat=0)

    def stop_after_two(event):
        if event["event"] == "iteration_complete" and event["iteration"] == 2:
            runner.stop_event.set()

    old_emit = runner.emit
    runner.emit = lambda e: (old_emit(e), stop_after_two(e))
    with pytest.raises(KeyboardInterrupt):
        runner.run()
    assert reporter.closed and events[-1]["event"] == "interrupted"
    assert len(reporter.sent) == 8


def test_stop_during_publish_wait():
    runner, reporter, events, _ = execute("duplicate", offline_until=float("inf"))
    runner.wait = lambda _: runner.stop_event.set()
    with pytest.raises(KeyboardInterrupt):
        runner.run()
    assert reporter.closed and events[-1]["event"] == "interrupted"


def test_queued_phases_unaffected_by_large_time_multiplier():
    for scale in (1, 3, 100):
        phases = Scenarios("r2c/local", lambda: 0, time_scale=scale).steps("retry")
        first = next(phases)
        assert first.duration == 3
        assert isinstance(next(Scenarios("r2c/local", lambda: 0).steps("duplicate")), Packet)


def test_fractional_phase_duration_finishes_without_clock_spin():
    runner, reporter, events, _ = execute("retry", scale=3)
    runner.run()
    assert messages(reporter)[-1].task_state == 1
    assert events[-1]["event"] == "run_complete"


def test_real_reporter_retries_without_losing_script_packets():
    import time

    from cloudrobo_ingestion.progress import Reporter

    sent, events = [], []
    attempts = 0

    class Transport:
        def publish(self, topic, payload):
            sent.append(payload)

        def close(self):
            pass

    def factory(_):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionRefusedError("offline")
        return Transport()

    reporter = Reporter(
        None, "r2c/local/data_ingestion/upload_status", factory=factory, interval=0.02
    )
    runner = MockRunner(
        reporter, "r2c/local", scenario="duplicate", emit=events.append, publish_timeout=5
    )
    start = time.monotonic()
    runner.run()
    assert time.monotonic() - start >= 2
    assert attempts == 2 and len(sent) == 4
    assert sent[0] == sent[1] and sent[2] == sent[3]
    assert reporter.diagnostics()["pending_batches"] == 0
    assert events[-1]["event"] == "run_complete"
