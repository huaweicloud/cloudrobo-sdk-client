"""Real SDK + spawned worker against local OBS multipart protocol simulation."""

import logging
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

import pytest
from cloudrobo_ingestion.daemon import Supervisor
from cloudrobo_ingestion.progress import DisabledReporter, Reporter, message_class


@pytest.mark.parametrize("run_mode", ["upload_and_report", "upload_only"])
@pytest.mark.parametrize("http_status", [200, 403])
def test_real_obs_sdk_in_spawned_worker(config, batch_dir, monkeypatch, run_mode, http_status):
    uploaded = []

    class OBSHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, body, status=200):
            body = body.encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", '"local-etag"')
            self.send_header("x-obs-request-id", "test-request-id")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if "uploads" in self.path:
                self.reply(
                    "<InitiateMultipartUploadResult><Bucket>bucket</Bucket>"
                    "<Key>key</Key><UploadId>local-upload-id</UploadId></InitiateMultipartUploadResult>"
                )
            else:
                if http_status != 200:
                    self.reply(
                        "<Error><Code>AccessDenied</Code><Message>Permission denied</Message>"
                        "<RequestId>test-request-id</RequestId></Error>",
                        status=http_status,
                    )
                    return
                self.reply(
                    "<CompleteMultipartUploadResult><Location>local</Location>"
                    "<Bucket>bucket</Bucket><Key>key</Key><ETag>local-etag</ETag></CompleteMultipartUploadResult>"
                )

        def do_PUT(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            uploaded.append((unquote(self.path), data))
            self.reply("")

        def do_DELETE(self):
            self.reply("")

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), OBSHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HUAWEI_CLOUD_AK", "test-ak")
    monkeypatch.setenv("HUAWEI_CLOUD_SK", "test-sk")
    monkeypatch.setenv("CLOUDROBO_OBS_ENDPOINT", f"http://127.0.0.1:{server.server_port}")
    config = replace(config, run_mode=run_mode)
    if run_mode == "upload_only":
        from cloudrobo_ingestion import progress

        def forbidden():
            raise AssertionError("upload_only serialized protobuf")

        monkeypatch.setattr(progress, "message_class", forbidden)
    reporter = Reporter("unused", config.topic) if config.reports else DisabledReporter()
    s = Supervisor(config, reporter, config.work_dir / "status.json")
    worker_logger = logging.getLogger("cloudrobo_ingestion.worker")
    previous_level = worker_logger.level
    handler = logging.FileHandler(config.work_dir / "ingestion.log", encoding="utf-8")
    worker_logger.addHandler(handler)
    worker_logger.setLevel(logging.INFO)
    try:
        s.initialize()
        s.scan()
        s.launch_next()
        deadline = time.monotonic() + 15
        while s.process is not None and time.monotonic() < deadline:
            s.drain()
            time.sleep(0.02)
        assert s.process is None
        batch = s.store.rows()[0]
        assert batch["state"] == ("done" if http_status == 200 else "failed"), batch["error"]
        assert uploaded[0][1] == b"customer-data"
        assert "/prefix/批次001/data/file-000.parquet" in uploaded[0][0]
        if config.reports:
            assert message_class().FromString(batch["pending"]).task_state == int(
                http_status == 200
            )
        else:
            assert batch["pending"] is None
            assert s.snapshot()["reporter"]["enabled"] is False
        assert not (config.work_dir / "staging" / batch["id"]).exists()
        assert not (config.work_dir / "checkpoints" / f"{batch['id']}.json").exists()
        log = (config.work_dir / "ingestion.log").read_text(encoding="utf-8")
        assert "Uploading file" in log
        assert batch["id"] in log and "data/file-000.parquet" in log
        if http_status != 200:
            assert batch["error_info"]["component"] == "obs"
            assert batch["error_info"]["code"] == "OBS_ACCESS_DENIED"
            assert batch["error_info"]["error_id"] in log
            assert s.snapshot()["error_info"] == batch["error_info"]
            for expected in ("403", "AccessDenied", "test-request-id"):
                assert expected in batch["error"]
                assert expected in log
            assert '"stage": "uploading"' in log
        assert "test-ak" not in log and "test-sk" not in log
    finally:
        worker_logger.removeHandler(handler)
        worker_logger.setLevel(previous_level)
        handler.close()
        if s.process:
            s.process.terminate()
            s.process.join(timeout=2)
            s.pipe.close()
        s.store.close()
        server.shutdown()
        server.server_close()


class WindowsClosePipe:
    """Exercise Windows poll() closure semantics with real spawn IPC on every OS."""

    def __init__(self, connection):
        self.connection = connection
        self.pending = []
        self.closed_reads = 0

    def poll(self):
        if self.pending:
            return True
        if not self.connection.poll():
            return False
        try:
            self.pending.append(self.connection.recv())
        except EOFError:
            self.closed_reads += 1
            exc = BrokenPipeError(32, "管道已结束")
            exc.winerror = 109
            raise exc
        return True

    def recv(self):
        return self.pending.pop(0)

    def send(self, message):
        self.connection.send(message)

    def close(self):
        self.connection.close()


def test_wrong_endpoint_real_sdk_windows_pipe_and_real_zenoh(config, batch_dir, monkeypatch):
    import json
    import socket

    import yaml
    from test_worker_supervisor import successful_child

    zenoh = pytest.importorskip("zenoh")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        obs_endpoint = f"http://127.0.0.1:{probe.getsockname()[1]}"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        router = f"tcp/127.0.0.1:{probe.getsockname()[1]}"
    monkeypatch.setenv("HUAWEI_CLOUD_AK", "test-ak")
    monkeypatch.setenv("HUAWEI_CLOUD_SK", "test-sk")
    monkeypatch.setenv("CLOUDROBO_OBS_ENDPOINT", obs_endpoint)
    for index in range(7):
        (batch_dir / f"queued-{index}.parquet").write_bytes(b"customer-data")
    z = zenoh.Config()
    z.insert_json5("listen/endpoints", json.dumps([router]))
    z.insert_json5("scouting/multicast/enabled", "false")
    session = zenoh.open(z)
    messages = []
    subscriber = session.declare_subscriber(
        config.topic,
        lambda sample: messages.append(message_class().FromString(sample.payload.to_bytes())),
    )
    path = config.work_dir / "zenoh-test.yaml"
    path.write_text(
        yaml.safe_dump(
            dict(
                project_id="r2c",
                device_id="local",
                client_id="pipe-regression",
                mode="client",
                endpoint_role="connect",
                endpoints=[router],
            )
        ),
        encoding="utf-8",
    )
    reporter = Reporter(path, config.topic, interval=0.05)
    s = Supervisor(config, reporter, config.work_dir / "status.json")
    reporter.start()
    try:
        s.initialize()
        s.scan()
        s.launch_next()
        first_pipe = s.pipe = WindowsClosePipe(s.pipe)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            s.drain()
            if s.process is None and any(
                all(n.result_state == 3 for n in m.nodes) for m in messages
            ):
                break
            time.sleep(0.02)
        assert s.process is None
        failure = next(m for m in messages if all(n.result_state == 3 for n in m.nodes))
        assert len(failure.nodes) == 8 and failure.task_state == 0
        assert first_pipe.closed_reads <= 1
        batch = s.store.rows()[0]
        assert batch["state"] == "failed" and batch["retry_at"] > time.time()
        assert batch["error_info"]["component"] == "obs"
        assert batch["error_info"]["code"] == "OBS_CONNECT_FAILED"
        assert s.snapshot()["error_info"]["component"] == "obs"
        assert not s.stop_event.is_set()
        s.launch_next()
        assert s.process is None
        # A subsequent attempt uses a fresh pipe and can recover with the same identities.
        s.store.update(batch["id"], retry_at=0)
        s.worker_target = successful_child
        s.launch_next()
        assert not s.pipe_eof
        s.pipe = WindowsClosePipe(s.pipe)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            s.drain()
            if s.process is None and any(m.task_state == 1 for m in messages):
                break
            time.sleep(0.02)
        assert s.process is None and s.store.get(batch["id"])["state"] == "done"
        final = next(m for m in messages if m.task_state == 1)
        assert [n.file_id for n in final.nodes] == [n.file_id for n in failure.nodes]
        assert final.timestamp == failure.timestamp
        assert s.snapshot()["error_info"] is None
    finally:
        if s.process:
            s.process.terminate()
            s.process.join(timeout=2)
            s.pipe.close()
        reporter.close()
        s.store.close()
        subscriber.undeclare()
        session.close()
