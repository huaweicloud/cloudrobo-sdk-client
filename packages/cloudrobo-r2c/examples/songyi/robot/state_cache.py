"""Process-wide robot state cache shared by observation and runtime paths.

The observation path (adapter ``get_observation``) and the runtime
publisher both need robot state. Reads go through this cache so the two
paths never issue concurrent Thrift RPCs against each other: whoever
reads latest wins, the other side reuses the frame while it is fresh.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

JOINT_COUNT = 28


class StateCache:
    """Latest robot state with per-field-group freshness tracking."""

    def __init__(self, max_age_ms: int = 60) -> None:
        self._lock = threading.Lock()
        self._max_age_s = max_age_ms / 1000.0
        self._position: Optional[List[float]] = None
        self._velocity: Optional[List[float]] = None
        self._torque: Optional[List[float]] = None
        self._acceleration: Optional[List[float]] = None
        self._temperature: Optional[List[float]] = None
        self._current: Optional[List[float]] = None
        self._stamps: Dict[str, float] = {}

    def update(
        self,
        position: Optional[List[float]] = None,
        velocity: Optional[List[float]] = None,
        torque: Optional[List[float]] = None,
        acceleration: Optional[List[float]] = None,
        temperature: Optional[List[float]] = None,
        current: Optional[List[float]] = None,
    ) -> None:
        now = time.monotonic()
        with self._lock:
            if position is not None:
                self._position = list(position)
                self._stamps["position"] = now
            if velocity is not None:
                self._velocity = list(velocity)
                self._stamps["velocity"] = now
            if torque is not None:
                self._torque = list(torque)
                self._stamps["torque"] = now
            if acceleration is not None:
                self._acceleration = list(acceleration)
                self._stamps["acceleration"] = now
            if temperature is not None:
                self._temperature = list(temperature)
                self._stamps["temperature"] = now
            if current is not None:
                self._current = list(current)
                self._stamps["current"] = now

    def _is_fresh(self, key: str, now: float) -> bool:
        stamp = self._stamps.get(key)
        # 严格小于：max_age=0 表示"不缓存"（每 tick 重取）。Windows 的
        # time.monotonic 走 GetTickCount64（~15.6ms 粒度），若用 <= 则
        # 同一刻度内的 0 岁龄会被误判新鲜（跨平台差异，实测踩坑）
        return stamp is not None and (now - stamp) < self._max_age_s

    _KEYS = (
        "position",
        "velocity",
        "torque",
        "acceleration",
        "temperature",
        "current",
    )

    def get(
        self,
        fetchers: Optional[Dict[str, Callable[[], List[float]]]] = None,
        *,
        force: bool = False,
    ) -> Dict[str, List[float]]:
        """Return cached state, refreshing stale groups via *fetchers*.

        *fetchers* maps group name -> callable producing the value list;
        a group is fetched only when its cached value is missing or older
        than ``max_age_ms``.

        ``force=True``（观测新鲜度）：忽略新鲜度阈值，provided
        fetcher 的组**全部重取**（读取失败/为空回落旧缓存值），未提供
        fetcher 的组仍返回缓存快照——get_observation 用它保证位姿/关节
        为最近一次真实读取。
        """
        out: Dict[str, List[float]] = {}
        now = time.monotonic()
        with self._lock:
            for key in self._KEYS:
                value: Optional[List[float]] = getattr(self, f"_{key}")
                if value is not None and (force or self._is_fresh(key, now)):
                    out[key] = list(value)
            if force:
                stale = [key for key in self._KEYS if key in (fetchers or {})]
            else:
                stale = [key for key in self._KEYS if key not in out]
        for key in stale:
            fetcher = (fetchers or {}).get(key)
            if fetcher is None:
                continue
            try:
                values = fetcher()
            except Exception:
                logger.debug("state fetcher %r failed, skipped", key, exc_info=True)
                continue
            if not values:
                continue
            self.update(**{key: values})
            out[key] = list(values)
        return out

    def positions(self) -> Optional[List[float]]:
        with self._lock:
            return list(self._position) if self._position is not None else None

    def clear(self) -> None:
        with self._lock:
            self._position = None
            self._velocity = None
            self._torque = None
            self._acceleration = None
            self._temperature = None
            self._current = None
            self._stamps.clear()
