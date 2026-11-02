"""Stable per-file identity, bounded snapshots and independent Zenoh publishing."""

from __future__ import annotations

import collections
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .diagnostics import DiagnosticError, ErrorTracker, log_error, make_error, summary
from .store import now_ms

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResultToken:
    version: str
    send_round: int


@dataclass(frozen=True)
class PublishReceipt:
    batch_id: str
    payload: bytes
    token: ResultToken
    completed_at: float
    duration_ms: float


def message_class():
    from cloudrobo_r2c.common.models.generated.data_upload_progress_pb2 import DataUploadProgress

    return DataUploadProgress


def serialize_snapshot(snapshot):
    return message_class()(**snapshot).SerializeToString()


def data_type(name):
    ext = PurePosixPath(name).suffix.lower()
    if ext in {".mp4", ".avi", ".mov", ".mkv", ".webm"}:
        return 0
    if ext in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}:
        return 1
    if ext in {".pcd", ".ply", ".las", ".laz"}:
        return 2
    if ext in {".txt", ".json", ".jsonl", ".csv", ".yaml", ".yml", ".md", ".log"}:
        return 3
    return 4


@dataclass
class Node:
    path: str
    size: int
    file_id: str
    start_time: int = 0
    state: int = 0
    transferred: int = 0
    samples: collections.deque = field(default_factory=collections.deque)

    def update(self, state, transferred=0, at=None):
        at = time.monotonic() if at is None else at
        # A late callback cannot undo completion or a reported attempt failure.
        if self.state in (2, 3) and state == 1:
            return
        if self.state == state == 1:
            transferred = max(self.transferred, transferred)
        if state == 1 and self.state != 1:
            self.start_time = now_ms()
            self.samples.clear()
            self.samples.append((at, 0))
        self.state = state
        self.transferred = min(self.size, max(0, transferred))
        self.samples.append((at, self.transferred))
        while len(self.samples) > 1 and self.samples[0][0] < at - 5:
            self.samples.popleft()

    def values(self, batch, at):
        rate = 0.0
        if self.state == 1 and self.samples and at - self.samples[-1][0] <= 5:
            oldest = self.samples[0]
            elapsed = at - oldest[0]
            if elapsed > 0:
                rate = max(0, self.transferred - oldest[1]) / elapsed
        estimate = (self.size - self.transferred) * 1000 / rate if rate else 0
        eta_ms = int(estimate) if 0 <= estimate <= 2**63 - 1 else 0
        return dict(
            file_id=self.file_id,
            task_name=PurePosixPath(self.path).name,
            data_type=data_type(self.path),
            size=self.size,
            enqueue_time=batch["timestamp"],
            start_time=self.start_time,
            result_state=self.state,
            progress=100.0
            if self.state == 2
            else (min(99.9, 100 * self.transferred / self.size) if self.size else 0.0),
            avg_bps=rate,
            eta_ms=eta_ms,
            task_type=batch["task_type"],
            collect_method=batch["collect_method"],
        )


class BatchProgress:
    def __init__(self, batch, entries, source, clock=None, failed=False):
        self.batch, self.source = dict(batch), source
        self.clock = clock or time.monotonic
        self.nodes = {
            e.path: Node(e.path, e.size, str(uuid.uuid5(uuid.UUID(batch["id"]), e.path)))
            for e in entries
        }
        self.entries = {e.path: e for e in entries}
        self.finished = False
        self.lock = threading.Lock()
        if failed:
            self.mark_failed()

    def update(self, path, state, transferred=0, begin=False):
        with self.lock:
            if begin:
                # A real new attempt is different from a late SDK callback.
                old = self.nodes[path]
                self.nodes[path] = Node(old.path, old.size, old.file_id)
            self.nodes[path].update(state, transferred, at=self.clock())

    def mark_failed(self):
        """The whole attempt failed, including files it could not start."""
        with self.lock:
            self.finished = False
            for node in self.nodes.values():
                if node.state != 2:
                    node.update(3, node.transferred, at=self.clock())

    def prepare(self, entries, failed=False):
        """Retain observed states during recopy/init; reset only on real file start."""
        with self.lock:
            nodes = {}
            for entry in entries:
                old = self.nodes.get(entry.path)
                nodes[entry.path] = (
                    old
                    if old and self.entries.get(entry.path) == entry
                    else Node(
                        entry.path,
                        entry.size,
                        str(uuid.uuid5(uuid.UUID(self.batch["id"]), entry.path)),
                    )
                )
            self.nodes = nodes
            self.entries = {e.path: e for e in entries}
            self.finished = False
        if failed:
            self.mark_failed()

    def snapshot(self):
        with self.lock:
            at = self.clock()
            values = [n.values(self.batch, at) for n in self.nodes.values()]
            finished = self.finished
        return dict(
            source=self.source,
            timestamp=self.batch["timestamp"],
            asset_path=self.batch["asset_path"],
            task_state=int(finished),
            nodes=values,
        )

    def serialize(self):
        return serialize_snapshot(self.snapshot())

    def summary(self):
        with self.lock:
            counts = collections.Counter(n.state for n in self.nodes.values())
            return dict(
                files=len(self.nodes),
                bytes=sum(n.size for n in self.nodes.values()),
                uploaded_bytes=sum(n.transferred for n in self.nodes.values()),
                done_files=counts[2],
                failed_files=counts[3],
                current_files=[n.path for n in self.nodes.values() if n.state == 1],
            )


class Reporter:
    """One slot per batch. Never run transport calls in the upload/control loop."""

    def __init__(self, config_path, topic, factory=None, interval=1.0, endpoints=None, clock=None):
        self.config_path, self.topic, self.interval = config_path, topic, interval
        self.factory = factory or self._transport
        self.lock = threading.Lock()
        self.slots = {}
        self.expirations = {}
        self.tokens = {}
        self.clock = clock or time.monotonic
        self.min_publish_gap = (
            0.0  # Customer supervisor enables pacing; Mock keeps its script pace.
        )
        self.last_started = {}
        self.last_publish = float("-inf")
        self.serialization_retry = {}
        self.acks = queue.Queue()
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.status = {"connected": False, "last_published_ms": 0, "error": ""}
        self.endpoints = list(endpoints or [])
        self.errors = ErrorTracker(logger)
        self.serialization_errors = {}
        self.thread = threading.Thread(target=self._run, name="ingestion-reporter", daemon=True)

    @staticmethod
    def _transport(path):
        from cloudrobo_r2c.common.config import ClientConfig

        c = ClientConfig.from_yaml(path, None, None, None)
        return Reporter.connect_transport(c)

    @staticmethod
    def connect_transport(c):
        from cloudrobo_r2c.transport import ZenohTransport

        c.validate()
        t = ZenohTransport()
        try:
            t.connect(c)
        except Exception as exc:
            info = make_error(
                "report",
                "connect",
                exc,
                diagnostic=getattr(exc, "connection_info", lambda: {})(),
                context=dict(endpoints=c.connect.endpoints if c.connect else c.endpoints),
            )
            try:
                t.close()
            except Exception as close_exc:
                log_error(logger, make_error("report", "close", close_exc), logging.WARNING)
            raise DiagnosticError(info) from exc
        return t

    def set(self, batch_id, snapshot, terminal=False, expires_at_ms=0, token=None):
        with self.lock:
            self.slots[batch_id] = (snapshot, terminal)
            self.expirations[batch_id] = expires_at_ms
            self.tokens[batch_id] = token
        self.wake.set()

    def remove(self, batch_id):
        with self.lock:
            self.slots.pop(batch_id, None)
            self.expirations.pop(batch_id, None)
            self.tokens.pop(batch_id, None)
            self.serialization_retry.pop(batch_id, None)
            tracker = self.serialization_errors.pop(batch_id, None)
            if tracker:
                tracker.recovered()

    def contains(self, batch_id):
        with self.lock:
            return batch_id in self.slots

    def _publishable(self, batch_id, slot):
        with self.lock:
            if self.slots.get(batch_id) is not slot:
                return False
            expires = self.expirations.get(batch_id, 0)
            return not expires or now_ms() < expires

    def diagnostics(self):
        with self.lock:
            errors = ([self.errors.info] if self.errors.info else []) + [
                t.info for t in self.serialization_errors.values() if t.info
            ]
            return dict(
                self.status,
                enabled=True,
                pending_batches=len(self.slots),
                error=summary(errors[0]) if errors else "",
                error_info=errors[0] if errors else None,
                errors=errors,
            )

    def _ready(self, now) -> list[tuple]:
        """Return up to one eligible slot; waking never changes the due time."""
        selected = []
        with self.lock:
            self.last_started = {
                key: at for key, at in self.last_started.items() if now - at < self.interval
            }
            due = []
            for batch_id, slot in self.slots.items():
                expiry = self.expirations.get(batch_id, 0)
                if expiry and now_ms() >= expiry:
                    continue
                at = max(
                    self.last_started.get(batch_id, float("-inf")) + self.interval,
                    self.serialization_retry.get(batch_id, float("-inf")),
                    self.last_publish + self.min_publish_gap,
                )
                if at <= now:
                    due.append((slot[1], at, batch_id, slot, self.tokens.get(batch_id)))
            if due:
                _, _, batch_id, slot, token = min(due, key=lambda item: item[:2])
                selected.append((batch_id, slot, token))
        return selected

    def _run(self):
        transport = None
        retry_at = 0.0
        try:
            while not self.stop_event.is_set():
                self.wake.clear()
                now = self.clock()
                ready = self._ready(now) if now >= retry_at else []
                if not ready:
                    self.wake.wait(0.02)
                    continue
                batch_id, slot, token = ready[0]
                if not self._publishable(batch_id, slot):
                    self.wake.wait(0.02)
                    continue
                snapshot, terminal = slot
                try:
                    payload = snapshot if isinstance(snapshot, bytes) else snapshot.serialize()
                    with self.lock:
                        self.serialization_retry.pop(batch_id, None)
                        tracker = self.serialization_errors.pop(batch_id, None)
                        if tracker:
                            tracker.recovered()
                except Exception as exc:
                    info = make_error(
                        "local", "serialize", exc, context=dict(batch_id=batch_id, topic=self.topic)
                    )
                    with self.lock:
                        if self.slots.get(batch_id) is slot:
                            self.serialization_errors.setdefault(
                                batch_id, ErrorTracker(logger)
                            ).failed(info)
                            self.serialization_retry[batch_id] = self.clock() + self.interval
                    continue
                stage = "connect"
                try:
                    if transport is None:
                        transport = self.factory(self.config_path)
                    stage = "publish"
                    if self.stop_event.is_set():
                        break
                    connection_info = getattr(transport, "connection_info", lambda: None)()
                    if connection_info and connection_info.get("connected") is False:
                        raise ConnectionError("Zenoh link disconnected")
                    if not self._publishable(batch_id, slot):
                        continue
                    started = self.clock()
                    with self.lock:
                        self.last_started[batch_id] = self.last_publish = started
                    if token is not None:
                        logger.info(
                            "发布结果 batch_id=%s version=%s round=%s bytes=%s",
                            batch_id,
                            token.version,
                            token.send_round,
                            len(payload),
                        )
                    transport.publish(self.topic, payload)
                    completed = self.clock()
                    with self.lock:
                        self.errors.recovered()
                        self.status.update(connected=True, last_published_ms=now_ms(), error="")
                        if terminal and self.slots.get(batch_id) is slot:
                            self.slots.pop(batch_id)
                            self.expirations.pop(batch_id, None)
                            self.tokens.pop(batch_id, None)
                    self.acks.put(
                        PublishReceipt(
                            batch_id, payload, token, completed, (completed - started) * 1000
                        )
                        if token is not None
                        else (batch_id, payload)
                    )
                    retry_at = 0
                except Exception as exc:
                    info = (
                        exc.info
                        if isinstance(exc, DiagnosticError)
                        else make_error(
                            "report",
                            stage,
                            exc,
                            diagnostic=getattr(transport, "connection_info", lambda: {})()
                            if transport
                            else None,
                            context=dict(
                                endpoints=self.endpoints,
                                topic=self.topic,
                                batch_id=batch_id,
                                result_version=token.version if token else None,
                                result_round=token.send_round if token else None,
                            ),
                        )
                    )
                    info["context"].setdefault("topic", self.topic)
                    info["context"].setdefault("endpoints", self.endpoints)
                    info["context"].setdefault("batch_id", batch_id)
                    if token is not None:
                        info["context"].setdefault("result_version", token.version)
                        info["context"].setdefault("result_round", token.send_round)
                    with self.lock:
                        self.errors.failed(info)
                        self.status.update(connected=False)
                    if transport:
                        try:
                            transport.close()
                        except Exception as close_exc:
                            log_error(
                                logger, make_error("report", "close", close_exc), logging.WARNING
                            )
                    transport = None
                    retry_at = self.clock() + 2
        except Exception as exc:
            with self.lock:
                self.status.update(connected=False)
                self.errors.failed(
                    make_error("local", "runtime", exc, context=dict(thread="ingestion-reporter"))
                )
        finally:
            if transport:
                try:
                    transport.close()
                except Exception as exc:
                    log_error(logger, make_error("report", "close", exc), logging.WARNING)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.wake.set()
        self.thread.join(timeout=2)


class DisabledReporter:
    """Same supervisor interface, with no serialization, transport or background thread."""

    def __init__(self):
        self.acks = queue.Queue()

    def set(self, *args, **kwargs):
        pass

    def remove(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def close(self):
        pass

    def diagnostics(self):
        return dict(
            enabled=False,
            state="disabled",
            connected=False,
            pending_batches=0,
            last_published_ms=0,
            error="",
            error_info=None,
            errors=[],
        )
