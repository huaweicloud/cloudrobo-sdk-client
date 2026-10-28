"""Shared upload history, deferred results, and OBS target changes."""

import builtins
import json
import shutil
import time
from dataclasses import replace

import pytest
from cloudrobo_ingestion.daemon import Supervisor
from cloudrobo_ingestion.filesystem import manifest
from cloudrobo_ingestion.progress import BatchProgress, DisabledReporter, Reporter, message_class
from cloudrobo_ingestion.store import BatchStore
from cloudrobo_ingestion.worker import run_worker
from delivery_helpers import complete_result
from test_worker_supervisor import Connection, drain_until_done, failed_child, successful_child


def supervisor(config, worker=successful_child):
    reporter = Reporter("unused", config.topic) if config.reports else DisabledReporter()
    return Supervisor(config, reporter, config.work_dir / "status.json", worker_target=worker)


def complete(s):
    s.scan()
    s.launch_next()
    drain_until_done(s)
    return s.store.rows()[0]


def test_success_history_shared_in_both_directions(config, batch_dir):
    first = supervisor(config)
    original = complete(first)
    first.store.close()
    for mode in ("upload_only", "upload_and_report"):
        s = supervisor(replace(config, run_mode=mode))
        try:
            s.initialize()
            s.scan()
            s.launch_next()
            assert s.process is None
            row = s.store.rows()[0]
            assert (row["id"], row["timestamp"], row["attempts"]) == (
                original["id"],
                original["timestamp"],
                1,
            )
        finally:
            s.store.close()


def test_original_report_task_completed_without_reporting_then_source_removed(
    config, batch_dir, monkeypatch
):
    first = supervisor(config, failed_child)
    failed = complete(first)
    old_message = message_class().FromString(failed["pending"])
    assert old_message.nodes[0].result_state == 3
    first.store.close()

    # Even previously enrolled tasks can finish without any report dependency.
    with monkeypatch.context() as guard:
        original_import = builtins.__import__

        def no_reporting(name, *args, **kwargs):
            if name.startswith(("cloudrobo_r2c", "zenoh", "google.protobuf")):
                raise AssertionError("upload_only loaded reporting dependency")
            return original_import(name, *args, **kwargs)

        guard.setattr(builtins, "__import__", no_reporting)
        from cloudrobo_ingestion import progress

        guard.setattr(progress, "message_class", lambda: pytest.fail("serialized in upload_only"))
        pure = supervisor(replace(config, run_mode="upload_only", task_type="", collect_method=""))
        try:
            pure.initialize()
            done = complete(pure)
            assert done["state"] == "done" and done["pending"] is None
            assert done["final_snapshot"]["nodes"][0]["result_state"] == 2
            assert done["task_type"] == config.task_type
            assert pure.snapshot()["reporter"]["enabled"] is False
            assert not (config.work_dir / "staging" / done["id"]).exists()
        finally:
            pure.store.close()
    shutil.rmtree(batch_dir)

    restored = supervisor(config)
    try:
        restored.initialize()
        restored.scan()
        restored.launch_next()
        assert restored.process is None
        batch = restored.store.rows()[0]
        payload, terminal = restored.reporter.slots[batch["id"]]
        final = message_class().FromString(payload)
        assert terminal and final.task_state == 1
        assert final.source == "r2c/local" and final.asset_path == old_message.asset_path
        assert final.timestamp == old_message.timestamp
        assert final.nodes[0].file_id == old_message.nodes[0].file_id
        assert final.nodes[0].enqueue_time == old_message.nodes[0].enqueue_time
        assert final.nodes[0].progress == 100 and final.nodes[0].result_state == 2
        # An old periodic/failure acknowledgement cannot discard the final result.
        restored.reporter.acks.put((batch["id"], failed["pending"]))
        restored.drain()
        assert restored.store.get(batch["id"])["final_snapshot"] is not None
        complete_result(restored, batch["id"])
        batch = restored.store.get(batch["id"])
        assert batch["final_snapshot"] is batch["pending"] is None
        assert batch["state"] == "done"
    finally:
        restored.store.close()


def test_upload_only_new_completed_batch_never_backfilled(config, batch_dir):
    pure = supervisor(replace(config, run_mode="upload_only", task_type="", collect_method=""))
    original = complete(pure)
    assert not original["report_enabled"]
    assert original["pending"] is original["final_snapshot"] is None
    pure.store.close()
    s = supervisor(config)
    try:
        s.initialize()
        s.scan()
        s.launch_next()
        assert s.process is None and not s.reporter.slots
        assert s.store.rows()[0]["id"] == original["id"]
        assert not s.store.rows()[0]["report_enabled"]
    finally:
        s.store.close()


def test_unfinished_upload_only_batch_enrolled_and_metadata_frozen(config, batch_dir):
    pure_config = replace(config, run_mode="upload_only", task_type="existing", collect_method="")
    pure = supervisor(pure_config)
    pure.scan()
    original = pure.store.rows()[0]
    before = pure.progress[original["id"]].snapshot()
    pure.store.close()
    s = supervisor(config)
    try:
        s.scan()
        row = s.store.rows()[0]
        after = s.progress[row["id"]].snapshot()
        assert row["report_enabled"]
        assert row["task_type"] == "existing"
        assert row["collect_method"] == config.collect_method
        assert row["id"] == original["id"] and after["timestamp"] == before["timestamp"]
        assert after["nodes"][0]["file_id"] == before["nodes"][0]["file_id"]
    finally:
        s.store.close()
    store = BatchStore(replace(config, task_type="changed", collect_method="changed"))
    try:
        row = store.get(original["id"])
        assert row["task_type"] == "existing" and row["collect_method"] == config.collect_method
    finally:
        store.close()


@pytest.mark.parametrize("target", ["obs://new/prefix/", "obs://bucket/new/", "obs://new/new/"])
def test_target_changes_unfinished_only_and_invalidates_stale_pending(config, target, caplog):
    store = BatchStore(config)
    unfinished = store.add("retry", config)
    done = store.add("completed", config)
    store.update(unfinished["id"], state="failed", attempts=1, pending=b"old-failure")
    store.update(done["id"], state="done", pending=b"old-success")
    store.close()
    caplog.set_level("INFO")
    changed = replace(config, obs_path=target)
    s = supervisor(changed)
    try:
        retry = s.store.get(unfinished["id"])
        completed = s.store.get(done["id"])
        assert retry["asset_path"] == target + "retry/" and retry["pending"] is None
        assert retry["id"] == unfinished["id"] and retry["timestamp"] == unfinished["timestamp"]
        assert completed["asset_path"] == done["asset_path"]
        assert completed["pending"] == b"old-success"
        assert s.store.add("new", changed)["asset_path"] == target + "new/"
        assert "受影响未完成批次：1" in caplog.text
    finally:
        s.store.close()


def test_completed_deferred_result_keeps_original_target(config, batch_dir):
    s = supervisor(config)
    row = complete(s)
    s.store.close()
    changed = supervisor(replace(config, obs_path="obs://new/new/"))
    try:
        changed.initialize()
        payload, _ = changed.reporter.slots[row["id"]]
        assert message_class().FromString(payload).asset_path == row["asset_path"]
        assert changed.store.get(row["id"])["final_snapshot"]["asset_path"] == row["asset_path"]
    finally:
        changed.store.close()


@pytest.mark.parametrize("cleanup_ok", [False, True])
def test_worker_uses_batch_target_and_original_checkpoint(
    config, batch_dir, monkeypatch, cleanup_ok
):
    from cloudrobo_core.sdk import obs_client

    calls, aborted = [], []

    class SDK:
        def abortMultipartUpload(self, bucket, key, upload_id):
            aborted.append((bucket, key, upload_id))
            return type("Response", (), dict(status=204 if cleanup_ok else 403))()

    class OBS:
        abort_checkpoint_upload = obs_client.OBSClient.abort_checkpoint_upload

        def __init__(self, **kwargs):
            self.bucket_name = kwargs["bucket_name"]
            self.client = SDK()

        def upload_file(self, path, key, **kwargs):
            calls.append((self.bucket_name, key))
            return True

        def close(self):
            pass

    monkeypatch.setattr(obs_client, "OBSClient", OBS)
    store = BatchStore(config)
    batch = store.add("批次001", config)
    store.close()
    # Deliberately differ from current config to detect mixed bucket/prefix sources.
    batch["asset_path"] = "obs://new-bucket/new-prefix/批次001/"
    checkpoint = config.work_dir / "checkpoints" / f"{batch['id']}.json"
    checkpoint.parent.mkdir()
    checkpoint.write_text(
        json.dumps(
            dict(bucketName="old-bucket", objectKey="old-prefix/file", uploadId="old-upload")
        )
    )
    conn = Connection()
    try:
        run_worker(config, batch, manifest(batch_dir), conn)
        result = next(e for e in conn.messages if e["event"] == "result")
        assert result["state"] == ("done" if cleanup_ok else "failed")
        assert aborted and all(
            item == ("old-bucket", "old-prefix/file", "old-upload") for item in aborted
        )
        assert calls == (
            [("new-bucket", "new-prefix/批次001/data/file-000.parquet")] if cleanup_ok else []
        )
        assert checkpoint.exists() is not cleanup_ok
        if not cleanup_ok:
            assert any(e["event"] == "cleanup_error" for e in conn.messages)
    finally:
        conn.close()


def test_final_serialization_retries_without_changing_upload_outcome(
    config, batch_dir, monkeypatch
):
    from cloudrobo_ingestion import daemon

    original = daemon.serialize_snapshot
    monkeypatch.setattr(
        daemon, "serialize_snapshot", lambda _: (_ for _ in ()).throw(ValueError("broken"))
    )
    s = supervisor(config)
    try:
        done = complete(s)
        assert done["state"] == "done" and done["pending"] is None
        assert done["final_snapshot"] is not None
        assert s.snapshot()["error_info"]["stage"] == "serialize"
        monkeypatch.setattr(daemon, "serialize_snapshot", original)
        s.store.update(done["id"], report_next_ms=0)
        s.next_final_retry = 0
        s.drain()
        assert s.store.get(done["id"])["pending"] is not None
        assert s.snapshot()["error_info"] is None
    finally:
        s.store.close()


def test_deferred_result_survives_offline_restart_and_publish_ack(config, batch_dir):
    store = BatchStore(config)
    row = store.add("批次001", config)
    p = BatchProgress(row, manifest(batch_dir), "r2c/local")
    for entry in p.entries.values():
        p.update(entry.path, 2, entry.size)
    p.finished = True
    store.update(row["id"], state="done", final_snapshot=p.snapshot())
    store.close()
    offline = supervisor(config)
    offline.initialize()
    offline.reporter.factory = lambda _: (_ for _ in ()).throw(ConnectionRefusedError("offline"))
    offline.reporter.start()
    try:
        deadline = time.monotonic() + 3
        while not offline.reporter.diagnostics()["error"] and time.monotonic() < deadline:
            offline.drain()
            time.sleep(0.01)
        assert offline.reporter.diagnostics()["error"]
        assert offline.reporter.diagnostics()["pending_batches"] == 1
        assert offline.store.get(row["id"])["final_snapshot"] is not None
    finally:
        offline.reporter.close()
        offline.store.close()
    sent = []

    class Transport:
        def publish(self, topic, payload):
            sent.append((topic, payload))

        def close(self):
            pass

    reporter = Reporter("unused", config.topic, factory=lambda _: Transport(), interval=0.02)
    s = Supervisor(config, reporter, config.work_dir / "status.json")
    s.result_delay = lambda: 0.03
    s.initialize()
    reporter.start()
    try:
        deadline = time.monotonic() + 3
        while s.store.get(row["id"])["final_snapshot"] is not None and time.monotonic() < deadline:
            s.drain()
            time.sleep(0.01)
        assert len(sent) == 3 and all(
            message_class().FromString(item[1]).task_state == 1 for item in sent
        )
        assert s.store.get(row["id"])["final_snapshot"] is None
        assert s.store.get(row["id"])["pending"] is None
    finally:
        reporter.close()
        s.store.close()
