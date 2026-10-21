"""Local supervisor. Database and durable state have exactly one writer."""

from __future__ import annotations

import collections
import contextlib
import errno
import json
import logging
import multiprocessing
import os
import queue
import signal
import sqlite3
import sys
import tempfile
import threading
import time
from logging.handlers import RotatingFileHandler
from multiprocessing.connection import Listener
from pathlib import Path

import yaml

from .config import IngestionConfig
from .diagnostics import DiagnosticError, ErrorTracker, log_error, make_error, summary
from .filesystem import RejectedBatch, discover, manifest, remove_staging
from .lifecycle import InstanceLock, private_dir, runtime_dir, write_json
from .progress import BatchProgress, DisabledReporter, Reporter, message_class, serialize_snapshot
from .store import BatchStore, now_ms
from .worker import run_worker

logger = logging.getLogger(__name__)


class Supervisor:
    def __init__(self, config, reporter, status_path, worker_target=run_worker):
        self.config, self.reporter, self.status_path = config, reporter, status_path
        self.worker_target = worker_target
        for name in ("staging", "checkpoints"):
            private_dir(config.work_dir / name)
        self.store = BatchStore(config)
        self.store.recover()
        self.progress = {}
        self.stop_event = threading.Event()
        self.process = self.pipe = None
        self.pipe_eof = False
        self.active = self.result = None
        self.cleanup_only = False
        self.last_error = ""
        self.error_info = None
        self.final_errors = {}
        self.next_final_retry = 0.0
        self.context = multiprocessing.get_context("spawn")

    def batch_error(self, batch, info, *, cleanup=False, **updates):
        if cleanup:
            updates["cleanup_error_info"] = info
        else:
            updates.update(error=summary(info), error_info=info)
        self.store.update(batch["id"], **updates)
        log_error(logger, info)
        if not cleanup or batch["state"] != "done":
            self.fail_progress(batch["id"])

    def fail_progress(self, batch_id):
        progress = self.progress.get(batch_id)
        if progress is None or progress.finished:
            return
        progress.mark_failed()
        # Wake the publisher before persisting; a DB fault must not hide failure.
        self.reporter.set(batch_id, progress)
        if self.config.reports:
            try:
                payload = progress.serialize()
            except Exception as exc:
                log_error(
                    logger, make_error("local", "serialize", exc, context=dict(batch_id=batch_id))
                )
            else:
                self.store.update(batch_id, pending=payload)

    def batch_failure(self, batch, stage, exc, **updates):
        self.batch_error(
            batch,
            make_error(
                "local",
                stage,
                exc,
                context=dict(
                    batch_id=batch["id"],
                    batch_path=batch["path"],
                    source_dir=str(self.config.source_dir),
                ),
            ),
            **updates,
        )

    def cleanup(self, batch):
        try:
            remove_staging(self.config.work_dir / "staging" / batch["id"])
            pending = (self.config.work_dir / "checkpoints" / f"{batch['id']}.json").exists()
            self.store.update(batch["id"], cleanup_pending=int(pending))
            if not pending and batch.get("cleanup_error_info"):
                logger.info("异常已恢复：%s", summary(batch["cleanup_error_info"]))
                self.store.update(batch["id"], cleanup_error_info=None)
            return not pending
        except Exception as exc:
            info = make_error(
                "local",
                "cleanup",
                exc,
                context=dict(
                    batch_id=batch["id"], path=str(self.config.work_dir / "staging" / batch["id"])
                ),
            )
            self.batch_error(batch, info, cleanup=True, cleanup_pending=1)
            return False

    def initialize(self):
        for batch in self.store.rows():
            self.cleanup(batch)
            if self.config.reports and batch["final_snapshot"] is not None:
                self.queue_final(batch)
            elif self.config.reports and batch["pending"] is not None:
                self.reporter.set(batch["id"], batch["pending"], terminal=batch["state"] == "done")

    def queue_final(self, batch):
        """Materialize the durable final result only when reporting is enabled."""
        if not self.config.reports:
            return
        batch_id = batch["id"]
        try:
            payload = batch["pending"]
            if payload is None:
                snapshot = dict(batch["final_snapshot"], source=self.source)
                payload = serialize_snapshot(snapshot)
                self.store.update(batch_id, pending=payload)
        except Exception as exc:
            if isinstance(exc, sqlite3.Error):
                raise
            self.final_errors.setdefault(batch_id, ErrorTracker(logger)).failed(
                make_error("local", "serialize", exc, context=dict(batch_id=batch_id))
            )
            return
        tracker = self.final_errors.pop(batch_id, None)
        if tracker:
            tracker.recovered()
        self.reporter.set(batch_id, payload, terminal=True)

    def scan(self):
        for source in discover(self.config.source_dir):
            if self.stop_event.is_set():
                break
            relative = source.relative_to(self.config.source_dir).as_posix()
            batch = self.store.add(relative, self.config)
            if batch["state"] in ("done", "rejected") or batch["id"] in self.progress:
                continue
            try:
                entries = manifest(source, self.config.max_files_per_batch)
                progress = BatchProgress(
                    batch,
                    entries,
                    self.source,
                    failed=batch["attempts"] > 0 or bool(batch["error_info"]),
                )
                self.progress[batch["id"]] = progress
                self.store.update(
                    batch["id"], files=len(entries), bytes=sum(e.size for e in entries)
                )
                self.reporter.set(batch["id"], progress)
            except RejectedBatch as exc:
                self.batch_failure(batch, "scan", exc, state="rejected", pending=None)
            except OSError as exc:
                self.batch_failure(
                    batch,
                    "scan",
                    exc,
                    state="failed",
                    retry_at=time.time() + 5,
                )

    @property
    def source(self):
        if not self.config.reports:
            return ""
        return f"{self.config.zenoh['project_id']}/{self.config.zenoh['device_id']}"

    def launch_next(self):
        for batch in self.store.rows():
            if self.stop_event.is_set():
                return
            if batch["retry_at"] > time.time() or batch["state"] == "rejected":
                continue
            if batch["cleanup_pending"]:
                # Retry local cleanup; if an OBS checkpoint remains, ask the child
                # to abort it without blocking the supervisor/control loop.
                self.cleanup(batch)
                stage = self.config.work_dir / "staging" / batch["id"]
                if stage.exists() or stage.is_symlink():
                    self.store.update(batch["id"], retry_at=time.time() + 5)
                    continue
                self._launch(batch, [], cleanup_only=True)
                return
            if batch["state"] == "done":
                continue
            try:
                entries = manifest(
                    self.config.source_dir / batch["path"], self.config.max_files_per_batch
                )
            except RejectedBatch as exc:
                self.batch_failure(batch, "scan", exc, state="rejected", pending=None)
                continue
            except OSError as exc:
                self.batch_failure(
                    batch,
                    "scan",
                    exc,
                    state="failed",
                    retry_at=time.time() + 5,
                )
                continue
            failed = batch["attempts"] > 0 or bool(batch["error_info"])
            p = self.progress.get(batch["id"])
            if p is None:
                p = BatchProgress(batch, entries, self.source, failed=failed)
            else:
                p.prepare(entries, failed=failed)
            self.progress[batch["id"]] = p
            self.reporter.set(batch["id"], p)
            self.store.update(
                batch["id"],
                state="copying",
                attempts=batch["attempts"] + 1,
                files=len(entries),
                bytes=sum(e.size for e in entries),
                pending=None,
                cleanup_pending=1,
            )
            self._launch(batch, entries)
            return

    def _launch(self, batch, entries, cleanup_only=False):
        parent, child = self.context.Pipe()
        self.active, self.result, self.cleanup_only = batch["id"], None, cleanup_only
        self.pipe_eof = False
        self.pipe = parent
        self.process = self.context.Process(
            target=self.worker_target,
            args=(self.config, batch, entries, child, cleanup_only),
            daemon=True,
        )
        try:
            self.process.start()
        except Exception as exc:
            parent.close()
            self.process = self.pipe = None
            self.batch_failure(batch, "worker_start", exc, state="failed", retry_at=time.time() + 5)
        finally:
            child.close()

    def _drain_worker_events(self):
        if self.pipe and not self.pipe_eof:
            try:
                while self.pipe.poll():
                    event = self.pipe.recv()
                    kind = event["event"]
                    if kind == "file":
                        self.progress[self.active].update(
                            event["path"],
                            event["state"],
                            event["transferred"],
                            begin=event.get("begin", False),
                        )
                    elif kind == "phase":
                        self.store.update(self.active, state=event["state"])
                    elif kind == "log":
                        logging.getLogger("cloudrobo_ingestion.worker").log(
                            {
                                "INFO": logging.INFO,
                                "WARNING": logging.WARNING,
                                "ERROR": logging.ERROR,
                            }.get(event["level"], logging.INFO),
                            "%s",
                            event["message"],
                        )
                    elif kind in ("result", "cleaned"):
                        self.result = event
                        if event.get("state") in ("failed", "rejected"):
                            self.fail_progress(self.active)
                            if not self.cleanup_only:
                                info = event.get("error_info")
                                self.store.update(
                                    self.active,
                                    state=event["state"],
                                    error=summary(info) if info else event.get("error", ""),
                                    error_info=info,
                                )
                    elif kind == "cleanup_error":
                        self.batch_error(
                            self.store.get(self.active),
                            event["error_info"],
                            cleanup=True,
                            cleanup_pending=1,
                        )
            except EOFError:
                self._worker_pipe_closed()
            except OSError as exc:
                # Windows poll()/recv() may report peer closure as WinError 109
                # rather than EOFError. POSIX can use EPIPE/ECONNRESET instead.
                # Stop reading once closed; draining it again during shutdown
                # would mask the worker result and prevent final status writes.
                if getattr(exc, "winerror", None) in (109, 232, 233) or exc.errno in (
                    errno.EPIPE,
                    errno.ECONNRESET,
                ):
                    self._worker_pipe_closed()
                    return
                self.pipe_eof = True
                raise DiagnosticError(
                    make_error("local", "ipc", exc, context=dict(batch_id=self.active))
                ) from exc

    def _worker_pipe_closed(self):
        self.pipe_eof = True
        if self.result is None:
            # A worker that vanished without a result must not leave its last
            # uploading snapshot visible while we wait to reap the process.
            self.fail_progress(self.active)

    def drain(self):
        self._drain_worker_events()
        if self.process and not self.process.is_alive():
            self.process.join()
            # The child may have sent its final log/result after the first poll.
            self._drain_worker_events()
            self._finish()
        while True:
            try:
                batch_id, payload = self.reporter.acks.get_nowait()
            except queue.Empty:
                break
            self.store.acknowledge(batch_id, payload)
        if self.config.reports and time.monotonic() >= self.next_final_retry:
            self.next_final_retry = time.monotonic() + 1
            for batch in self.store.finals_to_prepare():
                self.queue_final(batch)

    def _finish(self):
        batch = self.store.get(self.active)
        result = self.result
        if result is None:
            if self.stop_event.is_set():
                result = dict(state="interrupted", error="上传已中断")
            else:
                info = make_error(
                    "local",
                    "worker_exit",
                    context=dict(
                        batch_id=batch["id"],
                        batch_path=batch["path"],
                        exit_code=self.process.exitcode,
                    ),
                    message=f"上传子进程异常退出，退出码 {self.process.exitcode}",
                    hint="请根据错误 ID 查看子进程最后的日志，检查依赖、内存及系统进程终止记录。",
                )
                result = dict(state="failed", error=summary(info), error_info=info)
                log_error(logger, info)
        if self.cleanup_only:
            if result.get("event") == "cleaned":
                if batch.get("cleanup_error_info"):
                    logger.info("异常已恢复：%s", summary(batch["cleanup_error_info"]))
                self.store.update(
                    self.active, cleanup_pending=0, retry_at=0, cleanup_error_info=None
                )
            else:
                self.store.update(
                    self.active,
                    cleanup_pending=1,
                    retry_at=time.time() + 5,
                    cleanup_error_info=result.get("error_info"),
                )
        else:
            state = result["state"]
            p = self.progress.get(self.active)
            final_snapshot = None
            if p:
                if state in ("failed", "rejected"):
                    p.mark_failed()
                else:
                    with p.lock:
                        p.finished = state == "done"
                        if state == "interrupted":
                            for node in p.nodes.values():
                                if node.state == 1:
                                    node.state = 0
                if state == "done" and batch["report_enabled"]:
                    final_snapshot = p.snapshot()
                try:
                    payload = p.serialize() if self.config.reports and state != "done" else None
                except Exception as exc:
                    info = make_error("local", "serialize", exc, context=dict(batch_id=batch["id"]))
                    log_error(logger, info)
                    payload = None
                    # Reporter owns serialization retries; upload outcome is unchanged.
                    self.reporter.set(self.active, p, terminal=state == "done")
            else:
                payload = None
            delay = (
                min(300, 5 * 2 ** min(max(batch["attempts"] - 1, 0), 6)) if state == "failed" else 0
            )
            info = result.get("error_info")
            if state == "interrupted" and info is None:
                info = batch.get("error_info")
            self.store.update(
                self.active,
                state=state,
                error=summary(info) if info else result.get("error", ""),
                error_info=info,
                retry_at=time.time() + delay,
                pending=payload,
                final_snapshot=final_snapshot,
            )
            if final_snapshot is not None:
                self.queue_final(self.store.get(self.active))
            elif payload is not None:
                self.reporter.set(
                    self.active, payload if state == "done" else p, terminal=state == "done"
                )
            if state == "done":
                if batch.get("error_info"):
                    logger.info("异常已恢复：%s", summary(batch["error_info"]))
                self.progress.pop(self.active, None)
            self.cleanup(self.store.get(self.active))
            if result.get("error"):
                self.last_error = result["error"]
                logger.warning("Batch %s: %s", batch["path"], self.last_error)
        self.pipe.close()
        self.process.close()
        self.process = self.pipe = self.active = self.result = None

    def snapshot(self, state="running"):
        rows = self.store.rows()
        counts = dict(collections.Counter(r["state"] for r in rows))
        active = self.store.get(self.active) if self.active else None
        batch_errors = [
            dict(path=r["path"], state=r["state"], error=summary(info), error_info=info)
            for r in rows
            for info in (r["error_info"], r["cleanup_error_info"])
            if info
        ]
        current = self.error_info
        if current is None:
            current = next((tracker.info for tracker in self.final_errors.values()), None)
        if current is None and batch_errors:
            current = batch_errors[-1]["error_info"]
        return dict(
            state=state,
            run_mode=self.config.run_mode,
            pid=os.getpid(),
            updated_ms=now_ms(),
            source_dir=str(self.config.source_dir),
            work_dir=str(self.config.work_dir),
            obs_path=self.config.obs_path,
            counts=counts,
            active_batch=active["path"] if active else None,
            progress=self.progress[self.active].summary() if self.active in self.progress else {},
            error=summary(current) if current else "",
            error_info=current,
            reporter=self.reporter.diagnostics(),
            batch_errors=batch_errors[-50:],
        )

    def run(self):
        failed = False
        next_scan = next_status = 0
        try:
            self.initialize()
            while not self.stop_event.is_set():
                self.drain()
                now = time.monotonic()
                if now >= next_scan:
                    try:
                        self.scan()
                        if self.error_info and self.error_info["stage"] == "scan":
                            logger.info("异常已恢复：%s", summary(self.error_info))
                            self.error_info = None
                    except (OSError, RejectedBatch) as exc:
                        self.error_info = make_error(
                            "local", "scan", exc, context=dict(path=str(self.config.source_dir))
                        )
                        log_error(logger, self.error_info)
                    next_scan = now + 2
                if self.process is None:
                    self.launch_next()
                if now >= next_status:
                    try:
                        write_json(self.status_path, self.snapshot())
                    except Exception as exc:
                        raise DiagnosticError(
                            make_error(
                                "local", "status", exc, context=dict(path=str(self.status_path))
                            )
                        ) from exc
                    next_status = now + 1
                self.stop_event.wait(0.05)
        except Exception as exc:
            failed = True
            self.stop_event.set()
            self.error_info = (
                exc.info
                if isinstance(exc, DiagnosticError)
                else make_error(
                    "local", "database" if isinstance(exc, sqlite3.Error) else "runtime", exc
                )
            )
            log_error(logger, self.error_info)
            for batch_id in self.progress:
                with contextlib.suppress(Exception):
                    self.fail_progress(batch_id)
            raise DiagnosticError(self.error_info) from exc
        finally:
            if self.process:
                with contextlib.suppress(OSError):
                    self.pipe.send("stop")
                deadline = time.monotonic() + 5
                while self.process and self.process.is_alive() and time.monotonic() < deadline:
                    self.drain()
                    time.sleep(0.05)
                if self.process:
                    if self.process.is_alive():
                        self.process.terminate()
                        self.process.join(timeout=2)
                        if self.process.is_alive():
                            self.process.kill()
                            self.process.join()
                    self.drain()
            self.reporter.close()
            self.drain()
            try:
                write_json(self.status_path, self.snapshot("failed" if failed else "stopped"))
            finally:
                self.store.close()


def main(config_path, handshake, effective_path=None):
    root = runtime_dir()
    private_dir(root)
    z_path = None
    supervisor = listener = None
    config = None
    stage = "config"
    try:
        with InstanceLock(root / "instance.lock"):
            config = (
                IngestionConfig.from_mapping(
                    json.loads(Path(effective_path).read_text(encoding="utf-8")),
                    Path(config_path).resolve().parent,
                )
                if effective_path
                else IngestionConfig.load(config_path)
            )
            private_dir(config.work_dir)
            from .workspace import check_mode

            check_mode(config)
            stage = "logging"
            handler = RotatingFileHandler(
                config.work_dir / "ingestion.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            logging.basicConfig(
                level=logging.INFO,
                handlers=[handler],
                format="%(asctime)s %(levelname)s %(name)s %(message)s",
            )
            logger.info("Starting ingestion run_mode=%s", config.run_mode)
            if config.uploads:
                stage = "obs_initializing"
                from cloudrobo_core.sdk import Config

                credentials = Config()
                if not credentials.ak or not credentials.sk:
                    raise ValueError("OBS 凭证缺失，请配置 HUAWEI_CLOUD_AK / HUAWEI_CLOUD_SK")
            # Fail early on missing generated protobuf/runtime libraries, even
            # if the first batch has not arrived yet. Router availability is optional.
            reporter = DisabledReporter()
            if config.reports:
                stage = "serialize"
                message_class()
                stage = "report_config"
                from cloudrobo_r2c.common.config import ClientConfig
                from cloudrobo_r2c.transport import ZenohTransport

                del ZenohTransport
                fd, name = tempfile.mkstemp(prefix="zenoh-", suffix=".yaml", dir=config.work_dir)
                z_path = Path(name)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    z = dict(config.zenoh)
                    z.setdefault("publisher_priority", "BACKGROUND")
                    z.setdefault("publisher_congestion_control", "DROP")
                    z.setdefault(
                        "connect",
                        dict(endpoints=z["endpoints"], timeout_ms=2000, exit_on_failure=True),
                    )
                    yaml.safe_dump(z, f, allow_unicode=True)
                ClientConfig.from_yaml(z_path, None, None, None).validate()
                reporter = Reporter(
                    z_path,
                    config.topic,
                    endpoints=config.zenoh.get("connect", {}).get(
                        "endpoints", config.zenoh["endpoints"]
                    ),
                )
            stage = "database"
            supervisor = Supervisor(config, reporter, root / "status.json")
            stage = "control"
            token = os.urandom(32)
            listener = Listener(("127.0.0.1", 0), authkey=token)
            write_json(root / "control.json", dict(port=listener.address[1], token=token.hex()))

            def control():
                while not supervisor.stop_event.is_set():
                    try:
                        c = listener.accept()
                        try:
                            if c.poll(2) and c.recv() == "stop":
                                supervisor.stop_event.set()
                                c.send("stopping")
                        finally:
                            c.close()
                    except (OSError, EOFError, multiprocessing.AuthenticationError):
                        if supervisor.stop_event.is_set():
                            break

            threading.Thread(target=control, name="ingestion-control", daemon=True).start()
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: supervisor.stop_event.set())
            reporter.start()
            write_json(root / "status.json", supervisor.snapshot())
            write_json(
                handshake,
                dict(pid=os.getpid(), work_dir=str(config.work_dir), run_mode=config.run_mode),
            )
            stage = "runtime"
            supervisor.run()
    except Exception as exc:
        info = getattr(supervisor, "error_info", None) or (
            exc.info
            if isinstance(exc, DiagnosticError)
            else make_error(
                "obs"
                if stage == "obs_initializing"
                else "report"
                if stage == "report_config"
                else "local",
                stage,
                exc,
                context=dict(
                    config=str(config_path),
                    path=str(config.work_dir / "ingestion.log")
                    if stage == "logging"
                    else str(root / "status.json"),
                ),
            )
        )
        log_error(logger, info)
        failure_status = dict(state="failed", running=False, error=summary(info), error_info=info)
        if supervisor:
            with contextlib.suppress(OSError, ValueError):
                existing = json.loads((root / "status.json").read_text(encoding="utf-8"))
                failure_status = existing | failure_status
        if config:
            failure_status.update(
                run_mode=config.run_mode,
                work_dir=str(config.work_dir),
                source_dir=str(config.source_dir) if config.source_dir else None,
                obs_path=config.obs_path,
                pid=os.getpid(),
                updated_ms=now_ms(),
            )
        with contextlib.suppress(OSError):
            write_json(root / "status.json", failure_status)
        with contextlib.suppress(OSError):
            write_json(handshake, dict(error=summary(info), error_info=info))
        raise DiagnosticError(info) from exc
    finally:
        if supervisor:
            supervisor.stop_event.set()
        if listener:
            try:
                listener.close()
            except OSError as exc:
                log_error(logger, make_error("local", "control", exc), logging.WARNING)
        if z_path:
            try:
                z_path.unlink(missing_ok=True)
            except OSError as exc:
                log_error(
                    logger,
                    make_error("local", "cleanup", exc, context=dict(path=str(z_path))),
                    logging.WARNING,
                )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
