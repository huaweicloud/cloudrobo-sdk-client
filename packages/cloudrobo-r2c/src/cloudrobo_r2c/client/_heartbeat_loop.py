"""Heartbeat background loop for periodic state reporting."""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Union

from cloudrobo_r2c.client._time_utils import _now_ms, _safe_error
from cloudrobo_r2c.common.models import Heartbeat

logger = logging.getLogger(__name__)

HeartbeatProvider = Callable[[], Union[Heartbeat, Dict[str, Any], bytes]]
PublishOnceFn = Callable[[Union[Heartbeat, Dict[str, Any], bytes]], None]


@dataclass
class HeartbeatReportStats:
    """Thread-safe updates guarded by an external lock."""

    sent_messages: int = 0
    sent_bytes: int = 0
    failed_messages: int = 0
    provider_errors: int = 0
    last_error: Optional[str] = None
    last_error_ts_ms: Optional[int] = None

    def reset(self) -> None:
        self.sent_messages = 0
        self.sent_bytes = 0
        self.failed_messages = 0
        self.provider_errors = 0
        self.last_error = None
        self.last_error_ts_ms = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sent_messages": self.sent_messages,
            "sent_bytes": self.sent_bytes,
            "failed_messages": self.failed_messages,
            "provider_errors": self.provider_errors,
            "last_error": self.last_error,
            "last_error_ts_ms": self.last_error_ts_ms,
        }


class HeartbeatLoop:
    """A lightweight periodic loop that calls provider() and publish_once()."""

    def __init__(
        self,
        provider: HeartbeatProvider,
        publish_once: PublishOnceFn,
        interval_ms: int,
        jitter_ms: int,
        stats: HeartbeatReportStats,
        lock: threading.Lock,
    ) -> None:
        if not callable(provider):
            raise TypeError("provider must be callable")
        if not callable(publish_once):
            raise TypeError("publish_once must be callable")
        if interval_ms <= 0:
            raise ValueError("interval_ms must be > 0")
        if jitter_ms < 0:
            raise ValueError("jitter_ms must be >= 0")

        self.provider = provider
        self.publish_once = publish_once
        self.interval_ms = int(interval_ms)
        self.jitter_ms = int(jitter_ms)
        self.stats = stats
        self._lock = lock

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._last_good: Optional[Union[Heartbeat, Dict[str, Any], bytes]] = None

        self._last_publish_log_ts_ms: int = 0
        self._publish_log_every_ms: int = 5000  

    def is_running(self) -> bool:
        t = self._thread
        return bool(t and t.is_alive() and not self._stop_event.is_set())

    def start(self) -> None:
        if self.is_running():
            raise RuntimeError("heartbeats already running")

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="r2c-heartbeats", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 2.0) -> None:
        self._stop_event.set()
        t = self._thread
        if not t:
            return
        t.join(timeout=timeout_s)
        if t.is_alive():
            logger.warning("HeartbeatLoop did not stop within %.2fs", timeout_s)

    # -------- internal --------

    def _record_provider_error(self, e: BaseException) -> None:
        with self._lock:
            self.stats.provider_errors += 1
            self.stats.last_error = _safe_error(e)
            self.stats.last_error_ts_ms = _now_ms()

    def _log_publish_error(self, e: BaseException) -> None:
        """Log publish exception (not silent), but do not terminate the loop."""
        now = _now_ms()
        msg = _safe_error(e)

        if now - self._last_publish_log_ts_ms >= self._publish_log_every_ms:
            logger.warning("Heartbeat publish failed (loop will continue): %s", msg)
            self._last_publish_log_ts_ms = now
        else:
            logger.debug("Heartbeat publish failed (suppressed): %s", msg)

    def _normalize_provider_output(
        self, obj: Union[Heartbeat, Dict[str, Any], bytes]
    ) -> Union[Heartbeat, Dict[str, Any], bytes]:
        if isinstance(obj, dict):
            data = dict(obj)
            ts = data.get("timestamp")
            if not ts:
                data["timestamp"] = _now_ms()
            return data

        if isinstance(obj, Heartbeat):
            if getattr(obj, "timestamp", 0) <= 0:
                obj.timestamp = _now_ms()
            return obj

        if isinstance(obj, (bytes, bytearray)):
            return bytes(obj)

        raise TypeError(f"provider returned unsupported type: {type(obj).__name__}")

    def _fallback_from_last_good(self) -> Optional[Union[Heartbeat, Dict[str, Any], bytes]]:
        last = self._last_good
        if last is None:
            return None

        if isinstance(last, Heartbeat):
            try:
                hb = Heartbeat(
                    timestamp=_now_ms(),
                    status=last.status,
                    mode=last.mode,
                    error_code=list(last.error_code),
                    battery=last.battery,
                )
                return hb
            except Exception:
                return last  

        if isinstance(last, dict):
            data = dict(last)
            data["timestamp"] = _now_ms()
            return data

        return last

    def _compute_sleep_s(self, next_tick: float) -> float:
        delay = next_tick - time.monotonic()
        if delay <= 0:
            return 0.0

        if self.jitter_ms <= 0:
            return delay

        jitter_s = random.uniform(-self.jitter_ms / 1000.0, self.jitter_ms / 1000.0)
        return max(0.0, delay + jitter_s)

    def _run(self) -> None:
        interval_s = self.interval_ms / 1000.0
        next_tick = time.monotonic()

        while not self._stop_event.is_set():
            next_tick += interval_s

            # 1) get latest state
            try:
                raw = self.provider()
                msg = self._normalize_provider_output(raw)
                self._last_good = msg
            except Exception as e:
                self._record_provider_error(e)
                msg = self._fallback_from_last_good()
                if msg is None:
                    sleep_s = self._compute_sleep_s(next_tick)
                    self._stop_event.wait(timeout=sleep_s)
                    continue

            # 2) publish
            try:
                self.publish_once(msg)
            except Exception as e:
                self._log_publish_error(e)

            # 3) sleep until next tick
            sleep_s = self._compute_sleep_s(next_tick)
            self._stop_event.wait(timeout=sleep_s)