import builtins
import json
from dataclasses import replace

import pytest
import yaml
from click.testing import CliRunner
from cloudrobo_ingestion.cli import ingestion
from cloudrobo_ingestion.config import IngestionConfig
from cloudrobo_ingestion.store import BatchStore
from cloudrobo_ingestion.workspace import check_mode


def update(path, **values):
    data = yaml.safe_load(path.read_text())
    data.update(values)
    path.write_text(yaml.safe_dump(data))


@pytest.mark.parametrize("mode", ["upload_and_report", "upload_only"])
@pytest.mark.parametrize("value", [None, True, False, "100", 1.5, 0, -1])
def test_invalid_batch_file_limit(config_file, mode, value):
    update(config_file, run_mode=mode, max_files_per_batch=value)
    with pytest.raises(ValueError, match="max_files_per_batch"):
        IngestionConfig.load(config_file)


def test_batch_file_limit_priority_and_effective_config(config_file):
    assert IngestionConfig.load(config_file).max_files_per_batch == 50
    update(config_file, max_files_per_batch=5001)
    assert (
        IngestionConfig.load(config_file, dict(max_files_per_batch=None)).max_files_per_batch
        == 5001
    )
    c = IngestionConfig.load(config_file, dict(max_files_per_batch=200))
    assert c.max_files_per_batch == 200
    assert IngestionConfig.from_mapping(c.to_mapping(), config_file.parent) == c
    assert yaml.safe_load(config_file.read_text())["max_files_per_batch"] == 5001


@pytest.mark.parametrize("limit", ["1", "5001", "0", "-1"])
def test_cli_batch_file_limit(config_file, monkeypatch, limit):
    from cloudrobo_ingestion import lifecycle

    received = []

    def start(path, overrides=None):
        received.append(IngestionConfig.load(path, overrides))
        return dict(pid=123, work_dir="work", run_mode="upload_and_report")

    monkeypatch.setattr(lifecycle, "start", start)
    result = CliRunner().invoke(
        ingestion, ["start", "--config", str(config_file), "--max-files-per-batch", limit]
    )
    if int(limit) > 0:
        assert result.exit_code == 0, result.output
        assert received[0].max_files_per_batch == int(limit)
    else:
        assert result.exit_code != 0
        assert not received


def test_legacy_config_defaults_to_combined(config_file):
    c = IngestionConfig.load(config_file)
    assert c.run_mode == "upload_and_report" and c.uploads and c.reports


@pytest.mark.parametrize("value", ["upload", "client", "", None, 12])
def test_unknown_run_mode(config_file, value):
    update(config_file, run_mode=value)
    with pytest.raises(ValueError, match="run_mode"):
        IngestionConfig.load(config_file)


def test_upload_only_ignores_unused_report_settings(config_file, monkeypatch):
    data = yaml.safe_load(config_file.read_text())
    del data["task_type"], data["collect_method"]
    data.update(run_mode="upload_only", zenoh="intentionally invalid")
    config_file.write_text(yaml.safe_dump(data))
    original = builtins.__import__

    def guard(name, *args, **kwargs):
        if name.startswith(("cloudrobo_r2c", "zenoh", "google.protobuf")):
            raise AssertionError("upload_only imported report dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard)
    c = IngestionConfig.load(config_file, dict(zenoh_mode="client"))
    assert c.uploads and not c.reports
    assert c.task_type == c.collect_method == ""
    assert c.zenoh == {}
    assert IngestionConfig.from_mapping(c.to_mapping(), config_file.parent) == c


@pytest.mark.parametrize("value", [None, 0, "", "a" * 65])
def test_upload_only_validates_metadata_when_provided(config_file, value):
    update(config_file, run_mode="upload_only", task_type=value)
    with pytest.raises(ValueError, match="task_type"):
        IngestionConfig.load(config_file)


def test_cli_merge_replaces_endpoints_and_preserves_advanced_options(config_file):
    update(
        config_file,
        zenoh=dict(
            mode="peer",
            endpoints=["tcp/old:7447"],
            connect=dict(endpoints=["tcp/actual-old:7447"], timeout_ms=1234, exit_on_failure=True),
            tls=dict(enabled=False, root_ca_certificate="/path/to/ca.pem"),
            publisher_priority="BACKGROUND",
        ),
    )
    before = config_file.read_bytes()
    c = IngestionConfig.load(
        config_file,
        dict(
            zenoh_mode="client",
            zenoh_endpoint=("tcp/first:7447", "tcp/second:7447"),
            zenoh_project_id="test-project",
            zenoh_device_id="test-device",
            zenoh_client_id="test-client",
            task_type="new-task",
            collect_method="new-method",
            validation_enabled=False,
        ),
    )
    assert c.zenoh["mode"] == "client"
    assert (
        c.zenoh["endpoints"]
        == c.zenoh["connect"]["endpoints"]
        == ["tcp/first:7447", "tcp/second:7447"]
    )
    assert c.zenoh["connect"]["timeout_ms"] == 1234
    assert c.zenoh["tls"]["root_ca_certificate"] == "/path/to/ca.pem"
    assert c.zenoh["publisher_priority"] == "BACKGROUND"
    assert c.task_type == "new-task" and c.collect_method == "new-method"
    assert c.topic == "test-project/test-device/data_ingestion/upload_status"
    assert config_file.read_bytes() == before
    untouched = IngestionConfig.load(config_file, dict(zenoh_mode=None, zenoh_endpoint=()))
    assert untouched.zenoh["mode"] == "peer"
    assert untouched.zenoh["connect"]["endpoints"] == ["tcp/actual-old:7447"]


def test_cli_relative_paths_use_cwd_not_yaml_directory(config_file, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "cli-source").mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    c = IngestionConfig.load(config_file, dict(source_dir="cli-source", work_dir="cli-work"))
    assert c.source_dir == elsewhere / "cli-source" and c.work_dir == elsewhere / "cli-work"
    plain = IngestionConfig.load(config_file)
    assert plain.source_dir == config_file.parent / "source"
    assert plain.work_dir == config_file.parent / "work"


def test_cli_passes_only_explicit_overrides(config_file, monkeypatch):
    from cloudrobo_ingestion import lifecycle

    received = []

    def start(path, overrides=None):
        received.append((path, overrides))
        return dict(pid=123, work_dir="work", run_mode="upload_only")

    monkeypatch.setattr(lifecycle, "start", start)
    result = CliRunner().invoke(
        ingestion,
        [
            "start",
            "--config",
            str(config_file),
            "--run-mode",
            "upload_only",
            "--no-validate-lerobot",
            "--zenoh-mode",
            "peer",
            "--zenoh-endpoint",
            "tcp/a:7447",
            "--zenoh-endpoint",
            "tcp/b:7447",
        ],
    )
    assert result.exit_code == 0, result.output
    overrides = received[0][1]
    assert overrides["validation_enabled"] is False
    assert overrides["run_mode"] == "upload_only"
    assert overrides["zenoh_mode"] == "peer"
    assert overrides["zenoh_endpoint"] == ("tcp/a:7447", "tcp/b:7447")
    assert overrides["source_dir"] is None and overrides["task_type"] is None
    assert "upload_only" in result.output


def test_cli_absent_flags_preserve_config(config_file, monkeypatch):
    from cloudrobo_ingestion import lifecycle

    captured = []

    def start(path, overrides=None):
        captured.append(overrides)
        return dict(pid=123, work_dir="existing-work", run_mode="upload_and_report")

    monkeypatch.setattr(lifecycle, "start", start)
    result = CliRunner().invoke(ingestion, ["start", "--config", str(config_file)])
    assert result.exit_code == 0, result.output
    assert captured[0]["validation_enabled"] is None
    assert captured[0]["run_mode"] is None
    assert captured[0]["max_files_per_batch"] is None
    assert captured[0]["zenoh_endpoint"] == ()


def test_work_dir_purpose_and_unsupported_old_marker(config):
    store = BatchStore(config)
    b = store.add("batch", config)
    store.update(b["id"], state="done", pending=b"original-pending")
    store.close()
    check_mode(replace(config, run_mode="upload_only"))
    recovered = BatchStore(config)
    assert recovered.get(b["id"])["pending"] == b"original-pending"
    assert recovered.get(b["id"])["state"] == "done"
    recovered.close()
    marker = config.work_dir / "mode.json"
    marker.write_text(json.dumps(dict(run_mode="upload_and_report")))
    with pytest.raises(ValueError, match="版本不支持"):
        check_mode(config)
    marker.unlink()
    with pytest.raises(ValueError, match="缺少用途标记"):
        check_mode(config)


def test_two_real_modes_share_success_history(config):
    first = BatchStore(config)
    a = first.add("same-source-batch", config)
    first.update(a["id"], state="done", pending=b"real-final")
    first.close()
    pure = replace(config, run_mode="upload_only", task_type="", collect_method="")
    second = BatchStore(pure)
    b = second.add("same-source-batch", pure)
    assert a["id"] == b["id"] and a["timestamp"] == b["timestamp"]
    assert b["state"] == "done" and b["pending"] == b"real-final"
    second.close()
    first = BatchStore(config)
    assert first.get(a["id"])["pending"] == b"real-final"
    first.close()


@pytest.mark.parametrize("data", [dict(run_mode="mock_report"), dict(mock={})])
def test_customer_config_rejects_old_mock(config_file, data):
    config = yaml.safe_load(config_file.read_text()) | data
    config_file.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="run_mode|独立开发工具"):
        IngestionConfig.load(config_file)


def test_customer_help_does_not_expose_mock():
    result = CliRunner().invoke(ingestion, ["start", "--help"])
    assert result.exit_code == 0 and "mock" not in result.output
    result = CliRunner().invoke(ingestion, ["start", "--mock-file-count", "1"])
    assert result.exit_code != 0
