"""Killable, spawn-safe batch copy/upload worker."""

from __future__ import annotations

import json
import threading
import time
from urllib.parse import urlsplit

from .diagnostics import DiagnosticError, make_error, summary
from .filesystem import InterruptedBatch, RejectedBatch, checked_copy


def run_worker(config, batch, entries, connection, cleanup_only=False):
    from cloudrobo_core.sdk.obs_diagnostics import credential_values, exception_details, redact

    stop = threading.Event()
    sending = threading.Lock()

    def send(event, **fields):
        try:
            with sending:
                connection.send(dict(event=event, **fields))
        except (OSError, EOFError):
            stop.set()

    def watch_parent():
        try:
            while connection.recv() != "stop":
                pass
        except (EOFError, OSError):
            # Parent died: do not leave blocked OBS SDK threads behind.
            import os

            os._exit(1)
        stop.set()

    threading.Thread(target=watch_parent, name="ingestion-parent-watch", daemon=True).start()
    checkpoint = config.work_dir / "checkpoints" / f"{batch['id']}.json"
    staging = config.work_dir / "staging" / batch["id"]
    client = None
    active = None
    stage = "initializing"
    upload_error = None
    obs_endpoint = None
    obs_secrets = ()
    target = urlsplit(batch["asset_path"])
    bucket, prefix = target.netloc, target.path.lstrip("/")

    def failure(exc=None, diagnostic=None):
        component = (
            "obs" if stage in ("obs_initializing", "uploading", "checkpoint_cleanup") else "local"
        )
        if component == "obs" and isinstance(exc, (TypeError, AttributeError)):
            component = "local"
        return make_error(
            component,
            stage,
            exc,
            diagnostic=diagnostic,
            secrets=credential_values(client) + obs_secrets,
            context=dict(
                batch_id=batch["id"],
                batch_path=batch["path"],
                file=active,
                endpoint=getattr(client, "endpoint", obs_endpoint),
                asset_path=batch["asset_path"],
                source_path=str(config.source_dir / batch["path"]),
                staging=str(staging),
                checkpoint=str(checkpoint),
            ),
        )

    def log(level, message, **details):
        context = dict(
            batch_id=batch["id"],
            batch_path=batch["path"],
            stage=stage,
            file=active,
            source_path=str(config.source_dir / batch["path"]),
            staging=str(staging),
            checkpoint=str(checkpoint),
            asset_path=batch["asset_path"],
            **details,
        )
        # Only the parent writes/rotates the file, including on Windows spawn.
        send(
            "log",
            level=level,
            message=redact(
                message + " " + json.dumps(context, ensure_ascii=False),
                credential_values(client) + obs_secrets,
                limit=16000,
            ),
        )

    try:
        log("INFO", "Starting batch worker", cleanup_only=cleanup_only)
        import os

        from cloudrobo_core.sdk import Config
        from cloudrobo_core.sdk.obs_client import OBSClient

        credentials = Config()
        obs_secrets = (credentials.ak or "", credentials.sk or "")
        obs_endpoint = os.environ.get("CLOUDROBO_OBS_ENDPOINT") or credentials.endpoints.get(
            "cloudrobo-obs", ""
        ).replace("{region}", credentials.region)

        if checkpoint.exists():
            stage = "checkpoint_cleanup"
            # abort_checkpoint_upload uses the original bucket/key inside the checkpoint.
            client = OBSClient(config=credentials, bucket_name=bucket, timeout=5)
            if not client.abort_checkpoint_upload(str(checkpoint)):
                raise OSError("无法确认 OBS 分段上传已清理")
        if cleanup_only:
            send("cleaned")
            return
        stage = "copying"
        log("INFO", "Copying batch", files=len(entries))
        checked_copy(
            config.source_dir / batch["path"],
            staging,
            entries,
            stop,
            max_files=config.max_files_per_batch,
        )
        if config.validation_enabled:
            stage = "validating"
            send("phase", state="validating")
            from .validation import validate_lerobot

            validate_lerobot(staging, stop)
        if stop.is_set():
            raise InterruptedBatch()
        if entries:
            stage = "obs_initializing"
            # Cleanup used a short timeout; uploads need their normal timeout.
            if client is not None:
                client.close()
            client = OBSClient(config=credentials, bucket_name=bucket, timeout=30)
        stage = "uploading"
        send("phase", state="uploading")
        for entry in entries:
            stage = "preparing_file"
            if stop.is_set():
                raise InterruptedBatch()
            active = entry.path
            send("file", path=active, state=1, transferred=0, begin=True)
            last = [0.0]

            def progress(transferred, total, elapsed, path=entry.path, last=last):
                now = time.monotonic()
                if now - last[0] >= 0.2:
                    last[0] = now
                    send("file", path=path, state=1, transferred=transferred)
                if stop.is_set():
                    raise InterruptedBatch()

            key = prefix + entry.path
            log(
                "INFO",
                "Uploading file",
                local_path=str(staging / entry.path),
                object_key=key,
                size=entry.size,
                endpoint=getattr(client, "endpoint", ""),
            )
            stage = "uploading"
            if not client.upload_file(
                str(staging / entry.path),
                key,
                progress_callback=progress,
                checkpoint_file=str(checkpoint),
            ):
                if stop.is_set():
                    raise InterruptedBatch()
                upload_error = getattr(client, "last_upload_error", None)
                raise DiagnosticError(failure(diagnostic=upload_error))
            send("file", path=entry.path, state=2, transferred=entry.size)
            log("INFO", "File uploaded")
            active = None
        log("INFO", "Batch upload completed")
        send("result", state="done", error="")
    except InterruptedBatch:
        send("result", state="interrupted", error="上传已中断")
    except RejectedBatch as exc:
        info = failure(exc)
        log(
            "WARNING",
            "Batch rejected",
            diagnostic=exception_details(exc, credential_values(client)),
        )
        log("WARNING", summary(info), error_info=info)
        send("result", state="rejected", error=summary(info), error_info=info)
    except Exception as exc:
        if active:
            send("file", path=active, state=3, transferred=0)
        info = exc.info if isinstance(exc, DiagnosticError) else failure(exc)
        log("ERROR", "Batch failed: " + summary(info), error_info=info)
        send(
            "result",
            state="failed",
            error=summary(info),
            error_info=info,
        )
    finally:
        if client:
            stage = "checkpoint_cleanup"
            try:
                if checkpoint.exists() and not client.abort_checkpoint_upload(str(checkpoint)):
                    info = failure(OSError("无法确认 OBS 分段上传已清理"))
                    log("WARNING", summary(info), error_info=info)
                    send("cleanup_error", error_info=info)
            except Exception as exc:
                info = failure(exc)
                send("cleanup_error", error_info=info)
                log(
                    "ERROR",
                    summary(info),
                    diagnostic=exception_details(exc, credential_values(client)),
                )
            try:
                stage = "close"
                client.close()
            except Exception as exc:
                info = failure(exc)
                log(
                    "ERROR",
                    "OBS client close failed: " + summary(info),
                    error_info=info,
                )
        connection.close()
