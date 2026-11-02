import socket
import threading
import time

import pytest
import yaml
from cloudrobo_ingestion.progress import Reporter, message_class


def test_real_transport_protobuf_to_local_listener(tmp_path):
    zenoh = pytest.importorskip("zenoh")
    # A local listening Zenoh peer exercises the same client -> router wire path
    # without requiring a separately installed zenohd executable.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    cfg = zenoh.Config()
    cfg.insert_json5("listen/endpoints", json_string([f"tcp/127.0.0.1:{port}"]))
    cfg.insert_json5("scouting/multicast/enabled", "false")
    session = zenoh.open(cfg)
    received = []
    event = threading.Event()
    topic = "r2c/local/data_ingestion/upload_status"

    def on_sample(sample):
        received.append(message_class().FromString(sample.payload.to_bytes()))
        event.set()

    subscriber = session.declare_subscriber(topic, on_sample)
    path = tmp_path / "zenoh.yaml"
    path.write_text(
        yaml.safe_dump(
            dict(
                project_id="r2c",
                device_id="local",
                client_id="test-ingestion",
                mode="client",
                endpoint_role="connect",
                endpoints=[f"tcp/127.0.0.1:{port}"],
            )
        )
    )
    reporter = Reporter(path, topic, interval=0.1)
    reporter.start()
    payload = message_class()(
        source="r2c/local", timestamp=123, asset_path="obs://bucket/batch/", task_state=1
    )
    payload.nodes.add(
        file_id="stable-id",
        task_name="a.mp4",
        size=123,
        progress=100,
        result_state=2,
        task_type="超市取货",
        collect_method="遥操接管",
    )
    try:
        # Subscription discovery may race the first send; use periodic snapshots.
        reporter.set("one", payload.SerializeToString())
        assert event.wait(5)
        assert received[-1].nodes[0].task_name == "a.mp4"
        assert received[-1].task_state == 1
        assert reporter.diagnostics()["connected"]
    finally:
        reporter.close()
        subscriber.undeclare()
        session.close()


def json_string(value):
    import json

    return json.dumps(value)


def test_first_final_dropped_is_received_on_later_rounds(config, monkeypatch):
    from test_result_delivery import ManualClock, enroll, make_supervisor, pump

    zenoh = pytest.importorskip("zenoh")
    clock = ManualClock()
    for module in ("store", "daemon", "progress"):
        monkeypatch.setattr(f"cloudrobo_ingestion.{module}.now_ms", clock.now_ms)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    cfg = zenoh.Config()
    cfg.insert_json5("listen/endpoints", json_string([f"tcp/127.0.0.1:{port}"]))
    cfg.insert_json5("scouting/multicast/enabled", "false")
    router = zenoh.open(cfg)
    path = config.work_dir / "zenoh-test.yaml"
    path.write_text(
        yaml.safe_dump(
            dict(
                project_id="r2c",
                device_id="local",
                client_id="result-replay-test",
                mode="client",
                endpoint_role="connect",
                endpoints=[f"tcp/127.0.0.1:{port}"],
            )
        ),
        encoding="utf-8",
    )
    supervisor, _ = make_supervisor(config, clock)
    published_results = []

    class DropFirstResult:
        def __init__(self):
            self.transport = Reporter._transport(path)

        def connection_info(self):
            return self.transport.connection_info()

        def publish(self, topic, payload):
            if message_class().FromString(payload).source != "warmup":
                published_results.append(payload)
                if len(published_results) == 1:
                    # A local return does not imply delivery. Drop explicitly:
                    # declaring a subscriber later can still receive in-flight data.
                    return
            self.transport.publish(topic, payload)

        def close(self):
            self.transport.close()

    supervisor.reporter.factory = lambda _: DropFirstResult()
    row, _ = enroll(supervisor, "batch")
    received = []
    warmup_seen = threading.Event()

    def receive(sample):
        message = message_class().FromString(sample.payload.to_bytes())
        if message.source == "warmup":
            warmup_seen.set()
        else:
            received.append(message)

    subscriber = None
    supervisor.initialize()
    supervisor.reporter.start()
    try:
        # First local publish succeeds, but its result never enters the network.
        pump(supervisor, lambda: supervisor.store.get(row["id"])["report_count"] == 1, timeout=6)
        assert len(published_results) == 1
        assert not received
        subscriber = router.declare_subscriber(config.topic, receive)
        # Confirm subscription propagation without depending on a fixed sleep.
        warmup = message_class()(source="warmup").SerializeToString()
        supervisor.reporter.set("warmup", warmup)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            clock.advance(0.25)
            if warmup_seen.wait(0.05):
                break
        assert warmup_seen.is_set()
        supervisor.reporter.remove("warmup")
        clock.advance(4)
        pump(
            supervisor,
            lambda: supervisor.store.get(row["id"])["report_count"] == 2 and len(received) >= 1,
        )
        clock.advance(4)
        pump(
            supervisor,
            lambda: supervisor.store.get(row["id"])["report_count"] == 3 and len(received) >= 2,
        )
        assert len(published_results) == 3
        assert len(set(published_results)) == 1
        assert len(received) == 2
        assert all(
            message.task_state == 1 and message.nodes[0].result_state == 2 for message in received
        )
        assert received[0].SerializeToString() == received[1].SerializeToString()
        assert received[0].nodes[0].size == 8 * 1024**3
        assert supervisor.store.get(row["id"])["final_snapshot"] is None
    finally:
        supervisor.reporter.close()
        if subscriber is not None:
            subscriber.undeclare()
        supervisor.store.close()
        router.close()
