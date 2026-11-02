"""Bounded filesystem-only scanner; the supervisor remains the sole DB writer."""

from __future__ import annotations

import os
import queue
import threading
import time

from .filesystem import MARKER, InterruptedBatch, RejectedBatch, is_link, manifest

SCAN_QUEUE_SIZE = 32
MANIFEST_WINDOW = 2


class ScanWorker:
    def __init__(self, root, max_files, known=(), interval=2):
        self.root, self.max_files, self.interval = root, max_files, interval
        self.known = set(known)
        self.failed = set()
        self.lock = threading.Lock()
        self.events = queue.Queue(maxsize=SCAN_QUEUE_SIZE)
        self.requests = queue.Queue(maxsize=MANIFEST_WINDOW)
        self.stop_event = threading.Event()
        self.rescan = threading.Event()
        self.thread = None

    def remember(self, path):
        with self.lock:
            self.known.add(path)

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name="ingestion-scanner", daemon=True)
            self.thread.start()

    def request_scan(self):
        self.rescan.set()
        self.start()

    def request_manifest(self, batch_id, path, purpose):
        self.start()
        self.requests.put_nowait((batch_id, path, purpose))

    def _emit(self, kind, **fields):
        while not self.stop_event.is_set():
            try:
                self.events.put(dict(kind=kind, **fields), timeout=0.05)
                return
            except queue.Full:
                continue

    def _discover(self):
        checked = 0
        missing = set(self.failed)
        self._emit("scan_start")
        try:
            if is_link(self.root):
                raise RejectedBatch("source_dir 不允许链接或 junction")
            with os.scandir(self.root) as children:
                self._emit("root_ok")
                for child in children:
                    if self.stop_event.is_set():
                        return
                    checked += 1
                    missing.discard(child.name)
                    if checked % 100 == 0:
                        self._emit("scan_progress", checked=checked)
                    with self.lock:
                        known = child.name in self.known
                    if not known:
                        path = self.root / child.name
                        try:
                            if not is_link(path) and path.is_dir():
                                try:
                                    (path / MARKER).lstat()
                                except FileNotFoundError:
                                    pass
                                else:
                                    self._emit("discovered", path=child.name)
                            self._directory_ok(child.name)
                        except FileNotFoundError:
                            self._directory_ok(child.name)
                        except OSError as exc:
                            self.failed.add(child.name)
                            self._emit("directory_error", path=child.name, error=exc)
                    # Manifest requests have priority between directory checks.
                    self._manifest_request()
            for path in missing:
                self._directory_ok(path)
            self._emit("scan_complete", checked=checked)
        except (OSError, RejectedBatch) as exc:
            self._emit("root_error", error=exc, checked=checked)

    def _directory_ok(self, path):
        if path in self.failed:
            self.failed.discard(path)
            self._emit("directory_ok", path=path)

    def _manifest_request(self):
        try:
            batch_id, path, purpose = self.requests.get_nowait()
        except queue.Empty:
            return False
        try:
            entries = manifest(self.root / path, self.max_files, stop=self.stop_event)
        except InterruptedBatch:
            return True
        except (OSError, RejectedBatch) as exc:
            self._emit("manifest", batch_id=batch_id, purpose=purpose, error=exc)
        else:
            self._emit("manifest", batch_id=batch_id, purpose=purpose, entries=entries)
        return True

    def _run(self):
        next_scan = 0
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                # A steady manifest backlog must not postpone root health checks
                # and discovery indefinitely. Discovery itself serves requests
                # between directory checks, so neither workload starves the other.
                if self.rescan.is_set() or now >= next_scan:
                    self.rescan.clear()
                    self._discover()
                    next_scan = now + self.interval
                elif not self._manifest_request():
                    self.stop_event.wait(0.02)
        except Exception as exc:
            self._emit("fatal", error=exc)

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
