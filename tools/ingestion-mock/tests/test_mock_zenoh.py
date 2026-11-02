import socket

import pytest
from cloudrobo_ingestion.progress import message_class


@pytest.mark.parametrize("scenario", ["duplicate", "out-of-order", "malformed"])
@pytest.mark.parametrize("io_encoding", ["utf-8", "cp1252"])
def test_foreground_module_to_local_zenoh(scenario, io_encoding, tmp_path, monkeypatch):
    import json
    import subprocess
    import sys
    import time

    from google.protobuf.message import DecodeError

    zenoh = pytest.importorskip("zenoh")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        endpoint = f"tcp/127.0.0.1:{probe.getsockname()[1]}"
    cfg = zenoh.Config()
    cfg.insert_json5("listen/endpoints", json.dumps([endpoint]))
    cfg.insert_json5("scouting/multicast/enabled", "false")
    session = zenoh.open(cfg)
    topic = "r2c/local/data_ingestion/upload_status"
    received = []
    subscriber = session.declare_subscriber(
        topic, lambda sample: received.append(sample.payload.to_bytes())
    )
    runtime = tmp_path / "customer-runtime"
    runtime.mkdir()
    sentinel = runtime / "status.json"
    sentinel.write_text('{"running":true,"pid":123}')
    monkeypatch.setenv("CLOUDROBO_INGESTION_RUNTIME_DIR", str(runtime))
    monkeypatch.delenv("HUAWEI_CLOUD_AK", raising=False)
    monkeypatch.delenv("HUAWEI_CLOUD_SK", raising=False)
    # Exercise redirected Windows stdout even on UTF-8 Linux/macOS runners.
    monkeypatch.setenv("PYTHONIOENCODING", io_encoding)
    output = tmp_path / "events.jsonl"
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "cloudrobo_ingestion_mock",
                "--scenario",
                scenario,
                "--endpoint",
                endpoint,
                "--output",
                str(output),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        events = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        sends = [e for e in events if e["event"] == "local_published"]
        deadline = time.monotonic() + 2
        while len(received) < len(sends) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(received) == len(sends)
        import hashlib

        assert [hashlib.sha256(p).hexdigest() for p in received] == [e["sha256"] for e in sends]
        assert events[-1]["event"] == "run_complete"
        assert sentinel.read_text() == '{"running":true,"pid":123}'
        assert not (tmp_path / "batches.sqlite3").exists()
        assert not (tmp_path / "staging").exists()
        if scenario == "malformed":
            with pytest.raises(DecodeError):
                message_class().FromString(received[0])
            assert message_class().FromString(received[1]).task_state == 1
        elif scenario == "duplicate":
            assert received[0] == received[1] and received[2] == received[3]
        else:
            assert message_class().FromString(received[3]).task_state == 1
            assert message_class().FromString(received[4]).task_state == 0
    finally:
        subscriber.undeclare()
        session.close()
