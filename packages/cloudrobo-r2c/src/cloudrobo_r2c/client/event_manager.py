"""Push-based event publishing with rate limiting and buffering.

event 是自发事件（告警激活/解除等），**不走** ``hardware.topics`` 定频
轮询绑定：适配层任意位置经本模块单例 :data:`event_manager` 的
:meth:`EventManager.publish` 推送，topic 固定
``{project_id}/{device_id}/event``（占位符按 session 身份解析）。

语义：

- **调用不限频**：``publish`` 随时可调；内部默认限频 **2Hz**（代码级
  缺省 :data:`DEFAULT_MIN_INTERVAL_S`，不进 robot 配置）只约束**实际
  发布**——超频事件先进缓存，到点按 FIFO 补发。
- **缓存**：未 :meth:`EventManager.configure` 时 ``publish`` 自动入
  缓存（仅内存、不持久化、暂无上限）；configure 后按限频自动补发
  （启动早期告警不丢）；:meth:`EventManager.close` 丢弃未发缓存并打
  汇总日志。
- **容错**：发送失败仅告警日志、不中断调用方（该事件丢弃）。
- **登记簿**：:meth:`EventManager.active_events` 查询现有未清除告警
  （最近一条 ``ends_at=0`` 的事件，按 ``event_id`` 索引；解除事件
  ``ends_at>0`` 到达即出册）。

线程安全：全部状态经一把锁保护；补发用单发 ``threading.Timer``（有
缓存积压时按下次到期时间调度），无常驻线程。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

# 默认限频：2Hz（实际发布的最小间隔，秒）。代码级缺省——不进 robot 配置。
DEFAULT_MIN_INTERVAL_S = 0.5


class EventManager:
    """集中式 event 发布能力（限频 + 缓存补发 + 激活告警登记簿）。

    典型接线：边缘入口（cloudroboclient）在主 session 建立后调
    ``configure(session)``；适配层深处（AlarmManager、mock 事件源等）
    直接 ``publish(event_dict)``。
    """

    def __init__(self, *, min_interval_s: float = DEFAULT_MIN_INTERVAL_S) -> None:
        self._lock = threading.RLock()
        self._queue: deque = deque()
        self._session: Any = None
        self._topic: str = ""
        self._min_interval_s = max(float(min_interval_s), 0.0)
        self._last_sent = 0.0  # monotonic
        self._timer: Optional[threading.Timer] = None
        self._registry: Dict[str, Any] = {}  # event_id -> EventMessage（激活中）
        self._published = 0
        self._dropped_total = 0

    # ── lifecycle ────────────────────────────────────────────────────

    def configure(
        self, session: Any, *, min_interval_s: Optional[float] = None
    ) -> None:
        """绑定主 session（topic 解析为 ``{pid}/{did}/event``）并冲刷缓存。"""
        with self._lock:
            self._session = session
            self._topic = (
                f"{session.project_id}/{session.device_id}/event"
            )
            if min_interval_s is not None:
                self._min_interval_s = max(float(min_interval_s), 0.0)
            logger.info(
                "EventManager configured: topic=%s min_interval=%.3fs "
                "(buffered=%d)",
                self._topic,
                self._min_interval_s,
                len(self._queue),
            )
            self._drain_locked()

    def close(self) -> int:
        """停机：丢弃未发缓存并打汇总日志；清空登记簿。返回丢弃条数。"""
        with self._lock:
            timer, self._timer = self._timer, None
            if timer is not None:
                timer.cancel()
            dropped = len(self._queue)
            self._dropped_total += dropped
            self._queue.clear()
            self._session = None
            self._topic = ""
            self._registry.clear()
            logger.info(
                "EventManager closed: published=%d dropped_buffered=%d "
                "(total dropped=%d)",
                self._published,
                dropped,
                self._dropped_total,
            )
            return dropped

    @property
    def configured(self) -> bool:
        with self._lock:
            return self._session is not None

    # ── publish ──────────────────────────────────────────────────────

    def publish(self, event: Any) -> None:
        """推送一条 Event（dict / EventMessage / 同构 wrapper）。

        **调用不限频**：入缓存后尝试按限频冲刷；未 configure 时仅入
        缓存（configure 后自动补发）。不抛错（发送失败仅日志）。
        """
        wrapper = self._normalize(event)
        if wrapper is None:
            return
        with self._lock:
            self._queue.append(wrapper)
            self._drain_locked()

    def active_events(self) -> List[Dict[str, Any]]:
        """查询现有未清除告警（最近一条 ``ends_at=0`` 的事件，dict 形态）。"""
        with self._lock:
            return [wrapper.to_dict() for wrapper in self._registry.values()]

    # ── internals ────────────────────────────────────────────────────

    @staticmethod
    def _normalize(event: Any) -> Optional[Any]:
        """dict / EventMessage / 同构 wrapper → EventMessage（异常返回 None）。"""
        from cloudrobo_r2c.common.models import EventMessage

        if isinstance(event, EventMessage):
            return event
        if isinstance(event, Mapping):
            try:
                return EventMessage.from_dict(dict(event))
            except Exception:
                logger.warning(
                    "EventManager.publish: invalid event dict dropped", exc_info=True
                )
                return None
        to_dict = getattr(event, "to_dict", None)
        if callable(to_dict):
            try:
                return EventMessage.from_dict(dict(to_dict()))
            except Exception:
                logger.warning(
                    "EventManager.publish: invalid event wrapper dropped",
                    exc_info=True,
                )
                return None
        logger.warning(
            "EventManager.publish: unsupported event type %s; dropped",
            type(event).__name__,
        )
        return None

    def _drain_locked(self) -> None:
        """按限频冲刷缓存（锁内调用）；未 configure 或未到期则调度补发。"""
        while self._queue:
            if self._session is None:
                # 未接线：留缓存等 configure（启动早期告警不丢）
                return
            now = time.monotonic()
            due = self._last_sent + self._min_interval_s
            if now < due:
                self._schedule_timer_locked(due)
                return
            wrapper = self._queue.popleft()
            try:
                self._send_locked(wrapper)
            except Exception:
                # 容错丢弃：告警失败不打断主流程
                logger.warning(
                    "EventManager: event publish failed; dropped (event_id=%s)",
                    wrapper.event_id,
                    exc_info=True,
                )
                continue
            self._last_sent = time.monotonic()
            self._published += 1
            self._register_locked(wrapper)

    def _send_locked(self, wrapper: Any) -> None:
        ensure = getattr(self._session, "_ensure_state_source", None)
        if ensure is not None:
            # source 自动填充（{project_id}/{device_id}，同 TopicRuntime 路径）
            ensure(wrapper)
        self._session.transport.publish(self._topic, wrapper.serialize())
        logger.debug(
            "event TX: topic=%s event_id=%s payload=%s",
            self._topic,
            getattr(wrapper, "event_id", ""),
            wrapper.to_dict() if hasattr(wrapper, "to_dict") else repr(wrapper),
        )

    def _register_locked(self, wrapper: Any) -> None:
        """登记簿维护：告警（ends_at=0）入册；解除（ends_at>0）出册。"""
        event_id = str(wrapper.event_id or "")
        if not event_id:
            return
        metadata = wrapper.metadata or []
        ends_at = int(metadata[0].ends_at) if metadata else 0
        if ends_at:
            self._registry.pop(event_id, None)
        else:
            self._registry[event_id] = wrapper

    def _schedule_timer_locked(self, due: float) -> None:
        """缓存积压时按下次到期时间调度单发补发 Timer（仅一个在飞）。"""
        if self._timer is not None:
            return
        delay = max(due - time.monotonic(), 0.0)
        timer = threading.Timer(delay, self._on_timer)
        timer.daemon = True
        timer.name = "r2c-sdk-event-flush"
        self._timer = timer
        timer.start()

    def _on_timer(self) -> None:
        with self._lock:
            self._timer = None
            self._drain_locked()


# 模块单例：适配层任意位置 ``from cloudrobo_r2c.client.event_manager import
# event_manager`` 即可推送（configure 由边缘入口统一接线）。
event_manager = EventManager()
