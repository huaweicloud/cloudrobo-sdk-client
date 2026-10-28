import builtins
import os
import stat
import threading

import pytest
import yaml
from cloudrobo_ingestion.config import IngestionConfig
from cloudrobo_ingestion.filesystem import RejectedBatch, checked_copy, discover, manifest


@pytest.mark.parametrize("field", ["task_type", "collect_method"])
@pytest.mark.parametrize("value", [None, 123, [], True, "", "中" * 65])
def test_invalid_classification(config_file, field, value):
    d = yaml.safe_load(config_file.read_text())
    d[field] = value
    config_file.write_text(yaml.safe_dump(d))
    with pytest.raises(ValueError, match=field):
        IngestionConfig.load(config_file)


@pytest.mark.parametrize("value", ["中", "中" * 64, "customer-defined"])
def test_valid_classification(config_file, value):
    d = yaml.safe_load(config_file.read_text())
    d["task_type"] = d["collect_method"] = value
    config_file.write_text(yaml.safe_dump(d))
    c = IngestionConfig.load(config_file)
    assert c.task_type == c.collect_method == value


def test_validation_disabled_does_not_import_optional_libraries(config_file, monkeypatch):
    original = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith(("pyarrow", "av")):
            raise ImportError(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    assert not IngestionConfig.load(config_file).validation_enabled
    d = yaml.safe_load(config_file.read_text())
    d["validation"] = {"enabled": True}
    config_file.write_text(yaml.safe_dump(d))
    with pytest.raises(ValueError, match="lerobot-validation"):
        IngestionConfig.load(config_file)


@pytest.mark.parametrize("work", ["source", "source/work", "."])
def test_directory_overlap(config_file, work):
    d = yaml.safe_load(config_file.read_text())
    d["work_dir"] = work
    config_file.write_text(yaml.safe_dump(d))
    with pytest.raises(ValueError, match="包含"):
        IngestionConfig.load(config_file)


def test_manifest_checked_copy_is_read_only(config, batch_dir):
    before = {p.relative_to(batch_dir): p.read_bytes() for p in batch_dir.rglob("*") if p.is_file()}
    assert list(discover(config.source_dir)) == [batch_dir]
    entries = manifest(batch_dir)
    assert [e.path for e in entries] == ["data/file-000.parquet"]
    target = config.work_dir / "staged"
    checked_copy(batch_dir, target, entries, threading.Event())
    assert (target / "data/file-000.parquet").read_bytes() == b"customer-data"
    assert not (target / "__UPLOAD_READY__").exists()
    assert before == {
        p.relative_to(batch_dir): p.read_bytes() for p in batch_dir.rglob("*") if p.is_file()
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions")
def test_checked_copy_creates_private_files_with_permissive_umask(config, batch_dir):
    entries = manifest(batch_dir)
    source = batch_dir / entries[0].path
    source.chmod(0o644)
    target = config.work_dir / "staged"
    previous_umask = os.umask(0)
    try:
        checked_copy(batch_dir, target, entries, threading.Event())
    finally:
        os.umask(previous_umask)
    copied = target / entries[0].path
    assert copied.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(copied.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert stat.S_IMODE(copied.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(source.stat().st_mode) == 0o644


@pytest.mark.parametrize("limit", [50, 2, 5001])
def test_manifest_limit_exact(config, limit):
    root = config.source_dir / "many"
    root.mkdir()
    (root / "__UPLOAD_READY__").touch()
    for i in range(limit):
        (root / f"{i}.txt").touch()
    assert len(manifest(root, limit)) == limit
    if limit == 50:
        assert len(manifest(root)) == 50
    (root / "overflow.txt").touch()
    with pytest.raises(RejectedBatch, match=str(limit)):
        manifest(root, limit)
    if limit == 50:
        with pytest.raises(RejectedBatch, match="50"):
            manifest(root)


def test_nested_marker_ignored_and_deep_data_copied(config, batch_dir):
    (batch_dir / "data/__UPLOAD_READY__").touch()
    entries = manifest(batch_dir)
    assert [e.path for e in entries] == ["data/file-000.parquet"]
    target = config.work_dir / "staged"
    checked_copy(batch_dir, target, entries, threading.Event())
    assert (target / "data/file-000.parquet").read_bytes() == b"customer-data"
    assert not list(target.rglob("__UPLOAD_READY__"))


@pytest.mark.parametrize("root_marker", [False, True])
def test_discovery_only_direct_children(config, root_marker):
    source = config.source_dir
    if root_marker:
        (source / "__UPLOAD_READY__").touch()
    for name in ("abc", "xyz"):
        batch = source / name
        (batch / "def").mkdir(parents=True)
        (batch / "__UPLOAD_READY__").touch()
        (batch / "def/__UPLOAD_READY__").touch()
    deep_only = source / "unmarked/inner"
    deep_only.mkdir(parents=True)
    (deep_only / "__UPLOAD_READY__").touch()
    assert list(discover(source)) == [source / "abc", source / "xyz"]


def test_discovery_root_and_deep_markers_alone_do_not_trigger(config):
    (config.source_dir / "__UPLOAD_READY__").touch()
    deep = config.source_dir / "abc/def"
    deep.mkdir(parents=True)
    (deep / "__UPLOAD_READY__").touch()
    assert list(discover(config.source_dir)) == []


def test_nonempty_marker_rejected(batch_dir):
    (batch_dir / "__UPLOAD_READY__").write_bytes(b"not-ready")
    with pytest.raises(RejectedBatch, match="空"):
        manifest(batch_dir)


def test_symlink_rejected(batch_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    try:
        (batch_dir / "link").symlink_to(outside)
    except OSError:
        pytest.skip("OS requires permission to create symlinks")
    with pytest.raises(RejectedBatch, match="链接"):
        manifest(batch_dir)


def test_changed_source_rejected(config, batch_dir):
    entries = manifest(batch_dir)
    (batch_dir / entries[0].path).write_bytes(b"new")
    with pytest.raises(RejectedBatch, match="变化"):
        checked_copy(batch_dir, config.work_dir / "staged", entries, threading.Event())


def test_discovery_ignores_unmarked_and_linked_directories(config, batch_dir):
    (config.source_dir / "unmarked").mkdir()
    try:
        (config.source_dir / "alias").symlink_to(batch_dir, target_is_directory=True)
    except OSError:
        pass
    assert list(discover(config.source_dir)) == [batch_dir]
