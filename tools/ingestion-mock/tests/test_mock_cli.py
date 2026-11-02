import json

import pytest
import yaml
from click.testing import CliRunner
from cloudrobo_ingestion_mock.cli import connection_config, main


@pytest.mark.parametrize(
    "args",
    [
        ["--time-scale", "nan"],
        ["--time-scale", "inf"],
        ["--time-scale", "0"],
        ["--file-count", "0"],
        ["--file-count", "5001"],
        ["--repeat", "-1"],
        ["--scenario", "unknown"],
    ],
)
def test_invalid_arguments(args):
    assert CliRunner().invoke(main, args).exit_code != 0


def test_connection_defaults_and_override_preserves_advanced_options(tmp_path):
    c = connection_config()
    assert c.mode == "client" and c.endpoint_role == "connect"
    assert c.project_id == "r2c" and c.device_id == "local"
    assert c.resolved_endpoints() == ("tcp/127.0.0.1:7447",)
    path = tmp_path / "zenoh.yaml"
    path.write_text(
        yaml.safe_dump(
            dict(
                project_id="old",
                device_id="old",
                client_id="old",
                mode="peer",
                endpoints=["tcp/unused:7447"],
                connect=dict(
                    endpoints=["tcp/actual-old:7447"], timeout_ms=1234, exit_on_failure=True
                ),
                tls=dict(enabled=False, root_ca_certificate="original.pem"),
                publisher_priority="BACKGROUND",
            )
        )
    )
    before = path.read_bytes()
    c = connection_config(path, ("tcp/a:7447", "tcp/b:7447"), "p", "d", "c")
    assert c.endpoints == c.connect.endpoints == ("tcp/a:7447", "tcp/b:7447")
    assert c.connect.timeout_ms == 1234 and c.tls.root_ca_certificate == "original.pem"
    assert c.mode == "peer" and (c.project_id, c.device_id, c.client_id) == ("p", "d", "c")
    assert c.publisher_priority == "BACKGROUND" and path.read_bytes() == before
    assert connection_config(path).resolved_endpoints() == ("tcp/actual-old:7447",)


@pytest.mark.parametrize("field", ["project_id", "device_id", "client_id"])
def test_invalid_identity(field):
    with pytest.raises(ValueError, match=field):
        connection_config(**{field: "a/b"})


def test_cli_writes_jsonl_and_uses_overrides(tmp_path, monkeypatch):
    from cloudrobo_ingestion_mock import cli as mock

    received = []

    class Runner:
        def __init__(self, reporter, source, **kwargs):
            received.append((reporter, source, kwargs))
            self.emit = kwargs["emit"]

        def run(self):
            self.emit(dict(event="run_complete", messages=4, description="超市取货／遥操接管🤖"))

    monkeypatch.setattr(mock, "MockRunner", Runner)
    output = tmp_path / "results.jsonl"
    result = CliRunner().invoke(
        main,
        [
            "--scenario",
            "duplicate",
            "--project-id",
            "test",
            "--device-id",
            "device",
            "--endpoint",
            "tcp/127.0.0.1:9999",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    reporter, source, options = received[0]
    assert source == "test/device" and options["repeat"] == 1
    assert reporter.topic == "test/device/data_ingestion/upload_status"
    events = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert events[0]["endpoints"] == ["tcp/127.0.0.1:9999"]
    assert events[-1]["event"] == "run_complete"
    assert events[-1]["description"] == "超市取货／遥操接管🤖"
    assert "超市取货" in output.read_text(encoding="utf-8")
    assert result.output.isascii()
    assert [json.loads(line) for line in result.output.splitlines()] == events


@pytest.mark.parametrize("exception,code", [(KeyboardInterrupt, 130), (TimeoutError, 1)])
def test_cli_exit_codes(exception, code, monkeypatch):
    from cloudrobo_ingestion_mock import cli as mock

    class Runner:
        def __init__(self, *args, **kwargs):
            pass

        def run(self):
            raise exception()

    monkeypatch.setattr(mock, "MockRunner", Runner)
    assert CliRunner().invoke(main).exit_code == code
