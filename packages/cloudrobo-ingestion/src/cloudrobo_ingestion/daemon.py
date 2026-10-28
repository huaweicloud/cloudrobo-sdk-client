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
import random
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
from .filesystem import RejectedBatch, remove_staging
from .lifecycle import InstanceLock, private_dir, runtime_dir, write_json
from .progress import (
    BatchProgress,
    DisabledReporter,
    PublishReceipt,
    Reporter,
    ResultToken,
    message_class,
    serialize_snapshot,
)
from .scanner import MANIFEST_WINDOW, SCAN_QUEUE_SIZE, ScanWorker
from .store import BatchStore, now_ms, result_send_limit
from .worker import run_worker

logger = logging.getLogger(__name__)


class Supervisor:
    def __init__(self, config, reporter, status_path, worker_target=run_worker, *, clock=None):
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
        self.scanner = ScanWorker(config.source_dir, config.max_files_per_batch, self.store.paths())
        self.store.on_close = self.scanner.close
        self.running = False
        self.source_available = False
        self.preparing = {}
        self.launch_ready = None
        self.outbox_id = self.outbox_cursor = None
        self.outbox_token = None
        self.clock = clock or time.monotonic
        self.schedule_epoch_ms = now_ms()
        self.schedule_started = self.clock()
        self.result_delay = lambda: random.uniform(1, 5)
        if config.reports and isinstance(reporter, Reporter):
            self.reporter.min_publish_gap = min(0.2, self.reporter.interval / 5)
        self.scan_rounds = 0
        self.scan_status = dict(state="idle", last_completed_ms=0, checked_dirs=0)
        self.scan_errors = collections.OrderedDict()
        self.scan_error_paths = set()
        self.scan_logs = {}
        self.root_errors = ErrorTracker(logger)

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
        self._stop_periodic(batch_id)
        progress.mark_failed()
        if self.config.reports:
            self.expire_reports()
            self.store.save_result(batch_id, progress.snapshot(), "failure")
            self.queue_final(self.store.get(batch_id))

    def _stop_periodic(self, batch_id):
        if batch_id != self.outbox_id:
            self.reporter.remove(batch_id)

    def _cancel_result(self, batch_id, reason):
        if self.outbox_id == batch_id:
            self.reporter.remove(batch_id)
            self.outbox_id = self.outbox_token = None
        self.store.cancel_result(batch_id, reason=reason)
        tracker = self.final_errors.pop(batch_id, None)
        if tracker:
            tracker.recovered()

    def _schedule_ms(self, at=None):
        at = self.clock() if at is None else at
        return self.schedule_epoch_ms + int((at - self.schedule_started) * 1000)

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
                hint=(
                    "该批次已被拒绝，本次运行不再处理。请手动解决上述扫描问题，然后重启数据回流工具。"
                    if stage == "scan" and isinstance(exc, RejectedBatch)
                    else "请检查目录及权限，工具会继续尝试。"
                    if stage == "scan"
                    else None
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
        self.expire_reports()
        self._fill_outbox()

    def queue_final(self, batch):
        """Materialize the durable final result only when reporting is enabled."""
        if not self.config.reports or self.stop_event.is_set():
            return
        if batch["pending"] is None and batch["final_snapshot"] is None:
            return
        if batch["report_count"] >= result_send_limit(batch["report_kind"]):
            self.store.update(batch["id"], pending=None, final_snapshot=None, report_next_ms=0)
            return
        if batch["report_expires_ms"] and now_ms() >= batch["report_expires_ms"]:
            self.expire_reports()
            return
        batch_id = batch["id"]
        if self.outbox_id is not None or batch["report_next_ms"] > self._schedule_ms():
            return
        if not batch["report_version"]:
            # Also supports retained results materialized by older callers.
            self.store.update(
                batch_id,
                report_version=os.urandom(16).hex(),
                report_kind="success" if batch["state"] == "done" else "failure",
                report_expires_ms=batch["timestamp"] + 24 * 60 * 60 * 1000,
            )
            batch = self.store.get(batch_id)
            if now_ms() >= batch["report_expires_ms"]:
                self.expire_reports()
                return
        try:
            payload = batch["pending"]
            if payload is None:
                snapshot = dict(batch["final_snapshot"], source=self.source)
                payload = serialize_snapshot(snapshot)
                self.store.update(batch_id, pending=payload)
        except Exception as exc:
            if isinstance(exc, sqlite3.Error):
                raise
            if len(self.final_errors) >= 50 and batch_id not in self.final_errors:
                self.final_errors.pop(next(iter(self.final_errors)))
            self.final_errors.setdefault(batch_id, ErrorTracker(logger)).failed(
                make_error("local", "serialize", exc, context=dict(batch_id=batch_id))
            )
            self.store.update(batch_id, report_next_ms=self._schedule_ms() + 1000)
            return
        tracker = self.final_errors.pop(batch_id, None)
        if tracker:
            tracker.recovered()
        self.outbox_id = batch_id
        self.outbox_token = ResultToken(batch["report_version"], batch["report_count"] + 1)
        self.reporter.set(
            batch_id,
            payload,
            terminal=True,
            expires_at_ms=batch["report_expires_ms"],
            token=self.outbox_token,
        )

    def _fill_outbox(self):
        if not self.config.reports or self.stop_event.is_set():
            return
        # A successful publisher removes its slot before the main thread sees
        # the receipt. Keep the round in flight until that receipt is consumed.
        if self.outbox_id is not None:
            return
        batch = self.store.outbox_next(after=self.outbox_cursor, due_ms=self._schedule_ms())
        if batch is None and self.outbox_cursor is not None:
            self.outbox_cursor = None
            batch = self.store.outbox_next(due_ms=self._schedule_ms())
        if batch is None:
            return
        self.outbox_cursor = (batch["timestamp"], batch["path"])
        self.queue_final(batch)

    def expire_reports(self):
        for batch_id in self.store.expire_reports():
            self.reporter.remove(batch_id)
            if self.outbox_id == batch_id:
                self.outbox_id = self.outbox_token = None
            tracker = self.final_errors.pop(batch_id, None)
            if tracker:
                tracker.recovered()

    def scan(self, wait=None):
        """Request discovery; direct callers can wait, while run() never blocks."""
        target = self.scan_rounds + 1
        if not self.running or self.scanner.thread is None:
            self.scanner.request_scan()
        if wait if wait is not None else not self.running:
            self._wait_scan(lambda: self.scan_rounds >= target)
            self._fill_window()
            self._wait_scan(lambda: not self.preparing)

    def _wait_scan(self, predicate):
        deadline = time.monotonic() + 30
        while not predicate() and not self.stop_event.is_set():
            self._consume_scan()
            if time.monotonic() > deadline:
                raise TimeoutError("等待目录扫描结果超时")
            time.sleep(0.005)

    def _scan_error(self, path, exc):
        self.scan_error_paths.add(path)
        info = make_error(
            "local",
            "scan",
            exc,
            context=dict(path=str(self.config.source_dir / path)),
            hint="请检查目录及权限，工具会继续尝试。",
        )
        signature = (type(exc).__name__, str(exc), getattr(exc, "errno", None))
        previous = self.scan_logs.get(path)
        now = time.monotonic()
        if previous and previous[0] == signature:
            info["error_id"] = previous[3]
        if previous is None or previous[0] != signature or now - previous[1] >= 60:
            if previous and previous[0] == signature:
                info["context"]["repeat_count"] = previous[2] + 1
            log_error(logger, info)
            self.scan_logs[path] = (signature, now, 0, info["error_id"])
        else:
            self.scan_logs[path] = (signature, previous[1], previous[2] + 1, previous[3])
        self.scan_errors[path] = info
        self.scan_errors.move_to_end(path)
        while len(self.scan_errors) > 50:
            self.scan_errors.popitem(last=False)

    def _consume_scan(self):
        deadline = time.monotonic() + 0.05
        for _ in range(SCAN_QUEUE_SIZE):
            if self.stop_event.is_set() or time.monotonic() >= deadline:
                break
            try:
                event = self.scanner.events.get_nowait()
            except queue.Empty:
                break
            kind = event["kind"]
            if kind == "fatal":
                raise event["error"]
            if kind == "scan_start":
                self.scan_status.update(state="scanning", checked_dirs=0)
            elif kind == "root_ok":
                self.source_available = True
                self.root_errors.recovered()
            elif kind == "root_error":
                self.source_available = False
                self.scan_rounds += 1
                self.scan_status.update(state="error", checked_dirs=event["checked"])
                self.root_errors.failed(
                    make_error(
                        "local",
                        "scan",
                        event["error"],
                        context=dict(path=str(self.config.source_dir)),
                        hint="请检查源目录及权限，工具会继续尝试；恢复前暂停启动新上传批次。",
                    )
                )
            elif kind == "scan_progress":
                self.scan_status["checked_dirs"] = event["checked"]
            elif kind == "scan_complete":
                self.scan_rounds += 1
                self.scan_status.update(
                    state="idle", last_completed_ms=now_ms(), checked_dirs=event["checked"]
                )
            elif kind == "directory_error":
                self._scan_error(event["path"], event["error"])
            elif kind == "directory_ok":
                path = event["path"]
                self.scan_error_paths.discard(path)
                self.scan_errors.pop(path, None)
                old = self.scan_logs.pop(path, None)
                if old:
                    logger.info("扫描异常已恢复 path=%s 错误 ID=%s", path, old[3])
            elif kind == "discovered":
                self.store.add(event["path"], self.config)
                self.scanner.remember(event["path"])
            elif kind == "manifest":
                self._manifest_result(event)

    def _manifest_result(self, event):
        batch_id = event["batch_id"]
        purpose = self.preparing.pop(batch_id, None)
        if purpose is None:
            return
        batch = self.store.get(batch_id)
        if "error" in event:
            exc = event["error"]
            self.batch_failure(
                batch,
                "scan",
                exc,
                state="rejected" if isinstance(exc, RejectedBatch) else "failed",
                pending=None,
                retry_at=0 if isinstance(exc, RejectedBatch) else time.time() + 5,
            )
            return
        entries = event["entries"]
        failed = batch["attempts"] > 0 or bool(batch["error_info"])
        p = self.progress.get(batch_id)
        if p is None:
            p = BatchProgress(batch, entries, self.source, failed=failed)
        else:
            p.prepare(entries, failed=failed)
        self.progress[batch_id] = p
        self.store.update(batch_id, files=len(entries), bytes=sum(e.size for e in entries))
        if purpose == "launch":
            self.launch_ready = batch_id

    def _make_room(self, batch_id):
        if batch_id in self.progress or batch_id in self.preparing:
            return True
        if len(set(self.progress) | set(self.preparing)) < MANIFEST_WINDOW:
            return True
        for old in list(self.progress):
            if old != self.active and old not in self.preparing:
                self.progress.pop(old)
                self._stop_periodic(old)
                if self.launch_ready == old:
                    self.launch_ready = None
                return True
        return False

    def _request_manifest(self, batch, purpose):
        batch_id = batch["id"]
        if batch_id in self.preparing or not self._make_room(batch_id):
            return False
        if purpose == "launch":
            self._cancel_result(batch_id, "开始新的上传尝试")
        self.scanner.request_manifest(batch_id, batch["path"], purpose)
        self.preparing[batch_id] = purpose
        return True

    def _fill_window(self):
        if not self.source_available or self.stop_event.is_set():
            return
        excluded = set(self.progress) | set(self.preparing)
        for batch in self.store.candidates(excluded, limit=MANIFEST_WINDOW):
            # Prefetch must not evict an earlier queued candidate merely because
            # a later one was discovered. Failed/rejected slots may be released.
            if len(set(self.progress) | set(self.preparing)) >= MANIFEST_WINDOW:
                disposable = next(
                    (
                        k
                        for k in self.progress
                        if k != self.active
                        and k not in self.preparing
                        and self.store.get(k)["state"] in ("failed", "rejected")
                    ),
                    None,
                )
                if disposable is None:
                    break
                self.progress.pop(disposable)
                self._stop_periodic(disposable)
            self._request_manifest(batch, "prepare")

    @property
    def source(self):
        if not self.config.reports:
            return ""
        return f"{self.config.zenoh['project_id']}/{self.config.zenoh['device_id']}"

    def launch_next(self, wait=None):
        self._launch_next_once()
        if wait if wait is not None else not self.running:
            deadline = time.monotonic() + 30
            while self.process is None and self.preparing and not self.stop_event.is_set():
                self._consume_scan()
                self._launch_next_once()
                if time.monotonic() > deadline:
                    raise TimeoutError("等待批次文件清单超时")
                time.sleep(0.005)

    def _launch_next_once(self):
        if self.process is not None or self.stop_event.is_set():
            return
        for candidate in self.store.candidates(limit=2, cleanup=True):
            batch = self.store.get(candidate["id"])
            if self.cleanup(batch):
                continue
            stage = self.config.work_dir / "staging" / batch["id"]
            if stage.exists() or stage.is_symlink():
                self.store.update(batch["id"], retry_at=time.time() + 5)
                continue
            self._launch(batch, [], cleanup_only=True)
            return
        if not self.source_available:
            return
        if self.launch_ready is not None:
            batch_id, self.launch_ready = self.launch_ready, None
            batch = self.store.get(batch_id)
            if batch["state"] in ("done", "rejected"):
                return
            entries = list(self.progress[batch_id].entries.values())
            self.store.update(
                batch_id,
                state="copying",
                attempts=batch["attempts"] + 1,
                files=len(entries),
                bytes=sum(e.size for e in entries),
                pending=None,
                cleanup_pending=1,
            )
            self._launch(batch, entries)
            return
        candidates = self.store.candidates(limit=1)
        if candidates:
            self._request_manifest(candidates[0], "launch")

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
                        if event.get("begin") and self.config.reports:
                            self.reporter.set(self.active, self.progress[self.active])
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
        self._consume_scan()
        self._drain_worker_events()
        if self.process and not self.process.is_alive():
            self.process.join()
            # The child may have sent its final log/result after the first poll.
            self._drain_worker_events()
            self._finish()
        outbox_acked = False
        while True:
            try:
                receipt = self.reporter.acks.get_nowait()
            except queue.Empty:
                break
            if not isinstance(receipt, PublishReceipt):
                # Progress receipts and Mock's legacy one-shot receipts do not
                # count toward a versioned result's delivery budget.
                continue
            limit = result_send_limit(self.store.get(receipt.batch_id)["report_kind"])
            next_ms = (
                self._schedule_ms(receipt.completed_at) + int(self.result_delay() * 1000)
                if receipt.token.send_round < limit
                else 0
            )
            acknowledged = self.store.acknowledge_result(receipt, next_ms)
            if acknowledged:
                logger.info(
                    "结果本地发布完成 batch_id=%s version=%s round=%s/%s bytes=%s duration_ms=%.2f next_ms=%s",
                    receipt.batch_id,
                    receipt.token.version,
                    receipt.token.send_round,
                    limit,
                    len(receipt.payload),
                    receipt.duration_ms,
                    next_ms,
                )
            if receipt.batch_id == self.outbox_id and receipt.token == self.outbox_token:
                self.reporter.remove(receipt.batch_id)
                self.outbox_id = self.outbox_token = None
                outbox_acked = True
        self._fill_window()
        if outbox_acked:
            self._fill_outbox()
        if self.config.reports and time.monotonic() >= self.next_final_retry:
            self.next_final_retry = time.monotonic() + 0.05
            self.expire_reports()
            self._fill_outbox()

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
            self._stop_periodic(self.active)
            p = self.progress.get(self.active)
            if p:
                if state in ("failed", "rejected"):
                    self.fail_progress(self.active)
                else:
                    with p.lock:
                        p.finished = state == "done"
                        if state == "interrupted":
                            for node in p.nodes.values():
                                if node.state == 1:
                                    node.state = 0
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
            )
            if state == "done":
                self._stop_periodic(self.active)
                self._cancel_result(self.active, "本次上传成功，替代旧结果")
                if p and batch["report_enabled"]:
                    self.store.save_result(self.active, p.snapshot(), "success")
                    self.queue_final(self.store.get(self.active))
            elif state == "interrupted":
                self._stop_periodic(self.active)
                self._cancel_result(self.active, "用户停止，未完成批次下次重新处理")
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
        counts, batch_errors = self.store.summary()
        active = self.store.get(self.active) if self.active else None
        for item in batch_errors:
            item["error"] = summary(item["error_info"])
        current = self.error_info
        if current is None:
            current = next((tracker.info for tracker in self.final_errors.values()), None)
        if current is None and batch_errors:
            current = batch_errors[0]["error_info"]
        if current is None:
            current = self.root_errors.info or next(iter(self.scan_errors.values()), None)
        scan_errors = list(self.scan_errors.values())
        if self.root_errors.info:
            scan_errors = [self.root_errors.info] + scan_errors[-49:]
        pending = sum(counts.get(s, 0) for s in ("queued", "failed", "interrupted"))
        prepared = sum(
            1
            for k in self.progress
            if k != self.active
            and self.store.get(k)["state"] in ("queued", "failed", "interrupted")
        )
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
            reporter=dict(
                self.reporter.diagnostics(),
                pending_result_batches=self.store.pending_results(),
            ),
            batch_errors=batch_errors[-50:],
            scan=dict(
                self.scan_status,
                error_count=len(self.scan_error_paths) + bool(self.root_errors.info),
                pending_manifest_batches=max(0, pending - prepared),
            ),
            scan_errors=scan_errors[-50:],
        )

    def run(self):
        failed = False
        next_scan = next_status = 0
        self.running = True
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
            for batch_id in [self.active] if self.active in self.progress else []:
                with contextlib.suppress(Exception):
                    self.fail_progress(batch_id)
            raise DiagnosticError(self.error_info) from exc
        finally:
            self.scanner.close()
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
                self.running = False


def main(config_path, handshake, effective_path=None):
    root = runtime_dir()
    private_dir(root)
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
                with tempfile.TemporaryDirectory(prefix="zenoh-", dir=config.work_dir) as z_dir:
                    z_path = Path(z_dir) / "config.yaml"
                    fd = os.open(z_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        z = dict(config.zenoh)
                        z.setdefault("publisher_priority", "BACKGROUND")
                        z.setdefault("publisher_congestion_control", "DROP")
                        z.setdefault(
                            "connect",
                            dict(endpoints=z["endpoints"], timeout_ms=2000, exit_on_failure=True),
                        )
                        yaml.safe_dump(z, f, allow_unicode=True)
                    zenoh_config = ClientConfig.from_yaml(z_path, None, None, None)
                    zenoh_config.validate()
                # Reconnect from the loaded configuration, so the temporary YAML
                # is no longer needed while the daemon is running.
                reporter = Reporter(
                    None,
                    config.topic,
                    factory=lambda _: Reporter.connect_transport(zenoh_config),
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


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
