"""Runtime for a process's custom topics: publish loops + subscriptions.

- publish: fixed-rate loop (keep-latest skip-frame), translator with
  ``_``-variable routing, proto wrapper serialization.
- subscribe: parse via proto wrapper, optional translator (variable
  side-effects), then invoke the callback owner's ``on_message`` with
  the parsed wrapper object.

All bindings run on one (main) session; callback names resolve to
attributes of the callback owner (the hardware adapter).
"""

from __future__ import annotations

import logging
import threading
import time

from typing import Any, Callable, Dict, List, Mapping, Optional

from cloudrobo_r2c.client.session import R2CSession
from cloudrobo_r2c.client.topic_config import (
    PUBLISH,
    SUBSCRIBE,
    TopicBinding,
    parse_topic_bindings,
)
from cloudrobo_r2c.client.topic_resolver import TopicResolver
from cloudrobo_r2c.common.proto_registry import (
    Pb2MessageAdapter as _Pb2MessageAdapter,  # noqa: F401 (back-compat alias)
    Pb2MessageView as _Pb2MessageView,  # noqa: F401 (back-compat alias)
    resolve_proto_class,
)
from cloudrobo_r2c.translators.configurable import ConfigurableTranslator

logger = logging.getLogger(__name__)


class _PubLoopState:
    __slots__ = ("thread", "stop_event")

    def __init__(self) -> None:
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()


class TopicRuntime:
    """Own and drive one process's custom topic bindings (single session)."""

    def __init__(
        self,
        bindings: List[TopicBinding],
        session: R2CSession,
        callback_owner: Any = None,
        process_name: str = "app",
    ) -> None:
        self._process_name = process_name
        self._session = session
        self._resolver = TopicResolver(session.project_id, session.device_id)
        self._callback_owner = callback_owner

        self._bindings: Dict[str, TopicBinding] = {}
        self._wrappers: Dict[str, type] = {}
        self._translators: Dict[str, Optional[ConfigurableTranslator]] = {}
        for binding in bindings:
            self._bindings[binding.topic_template] = binding
            # 统一 proto 解析（§12-8 五份收敛）：wrapper 校验四方法，
            # pb2 消息类适配为 Pb2MessageAdapter（binding 模式）
            self._wrappers[binding.topic_template] = resolve_proto_class(
                binding.proto_path,
                f"{process_name}[{binding.topic_template!r}]",
                mode="binding",
            )
            self._translators[binding.topic_template] = (
                ConfigurableTranslator(
                    binding.translator_cfg,
                    name=f"{process_name}:{binding.topic_template}",
                )
                if binding.translator_cfg
                else None
            )

        self._functions: Dict[str, Callable[..., Any]] = {}
        for binding in bindings:
            func_name = (
                binding.collector if binding.direction == PUBLISH else binding.on_message
            )
            if callback_owner is None:
                raise ValueError(
                    f"{process_name}[{binding.topic_template!r}]: callback "
                    f"{func_name!r} requires a callback owner (hardware adapter)"
                )
            func = getattr(callback_owner, func_name, None)
            if not callable(func):
                raise ValueError(
                    f"{process_name}[{binding.topic_template!r}]: callback "
                    f"owner {type(callback_owner).__name__} has no callable "
                    f"{func_name!r}"
                )
            self._functions[binding.topic_template] = func

        self._pub_states: Dict[str, _PubLoopState] = {}

    def start(self) -> None:
        for binding in self._bindings.values():
            if binding.direction == PUBLISH:
                self._start_pub_loop(binding)
            else:
                self._start_subscription(binding)
        logger.info(
            "TopicRuntime started (process=%s, topics=%d)",
            self._process_name,
            len(self._bindings),
        )

    def stop(self) -> None:
        for state in self._pub_states.values():
            state.stop_event.set()
        for state in self._pub_states.values():
            if state.thread and state.thread.is_alive():
                state.thread.join(timeout=5.0)
        self._pub_states.clear()
        logger.info("TopicRuntime stopped (process=%s)", self._process_name)

    # ── publish ──────────────────────────────────────────────────────

    def _start_pub_loop(self, binding: TopicBinding) -> None:
        state = _PubLoopState()
        session = self._session
        topic = self._resolver.resolve(binding.topic_template)
        collector = self._functions[binding.topic_template]
        wrapper_cls = self._wrappers[binding.topic_template]
        translator = self._translators[binding.topic_template]
        period = 1.0 / binding.rate_hz

        def _loop() -> None:
            tick = 0
            next_deadline = time.monotonic()
            while not state.stop_event.is_set():
                delay = next_deadline - time.monotonic()
                if delay > 0:
                    if state.stop_event.wait(delay):
                        return
                # Recompute from actual time: late ticks are dropped
                # (keep-latest skip-frame), never burst-published.
                next_deadline = max(
                    next_deadline + period, time.monotonic() + period * 0.5
                )
                try:
                    payload = self._collect_one(
                        collector, translator, wrapper_cls, binding, tick,
                        session,
                    )
                    if payload is not None:
                        session.transport.publish(topic, payload)
                except Exception:
                    logger.exception(
                        "publish tick failed (topic=%s, tick=%d); frame skipped",
                        topic,
                        tick,
                    )
                tick += 1

        state.thread = threading.Thread(
            target=_loop,
            name=f"{self._process_name}:pub:{binding.topic_template}",
            daemon=True,
        )
        state.thread.start()
        self._pub_states[binding.topic_template] = state
        logger.info(
            "publishing %s @ %.1fHz",
            topic,
            binding.rate_hz,
        )

    def _collect_one(
        self,
        collector: Callable[..., Any],
        translator: Optional[ConfigurableTranslator],
        wrapper_cls: type,
        binding: TopicBinding,
        tick: int,
        session: Any = None,
    ) -> Optional[bytes]:
        data = collector(tick)
        if data is None:
            return None
        if hasattr(data, "to_dict"):
            data = data.to_dict()
        if not isinstance(data, dict):
            raise TypeError(
                f"collector for {binding.topic_template!r} must return a dict "
                f"or wrapper, got {type(data).__name__}"
            )

        # 注：binding 级 granularity 已移除（采集降采样由机器人适配层
        # 自行实现）；发布频率由 binding.rate_hz 控制。

        if translator is not None:
            mapped = translator.apply(data)
            if mapped is None:
                return None
            data = mapped

        wrapper = wrapper_cls.from_dict(data)
        # current_state/taskprocess/event 等状态消息：填充 source 字段
        ensure_source = getattr(session, "_ensure_state_source", None)
        if ensure_source is not None:
            ensure_source(wrapper)
        return wrapper.serialize()

    # ── subscribe ────────────────────────────────────────────────────

    def _start_subscription(self, binding: TopicBinding) -> None:
        session = self._session
        topic = self._resolver.resolve(binding.topic_template)
        wrapper_cls = self._wrappers[binding.topic_template]
        translator = self._translators[binding.topic_template]
        on_message = self._functions[binding.topic_template]

        def _handler(payload: bytes) -> None:
            try:
                message = wrapper_cls.from_protobuf(payload)
                if translator is not None:
                    mapped = translator.apply(message.to_dict())
                    if mapped is None:
                        return
                    message = wrapper_cls.from_dict(mapped)
                on_message(message)
            except Exception:
                logger.exception(
                    "subscription callback failed (topic=%s, payload=%d bytes)",
                    topic,
                    len(payload),
                )

        session.transport.subscribe(topic, _handler)
        logger.info("subscribed %s", topic)


def start_topic_runtime(
    topic_bindings: Mapping[str, Any] | List[TopicBinding],
    session: R2CSession,
    callback_owner: Any = None,
    process_name: str = "app",
) -> TopicRuntime:
    """Build and start a TopicRuntime for one process's ``hardware.topics``.

    ``topic_bindings`` accepts either the raw ``hardware.topics`` mapping
    (topic template -> binding spec) or ready-made ``TopicBinding`` list;
    callback names resolve against ``callback_owner`` (the hardware
    adapter).
    """
    if isinstance(topic_bindings, Mapping):
        bindings = parse_topic_bindings(topic_bindings)
    else:
        bindings = list(topic_bindings)
    runtime = TopicRuntime(
        bindings,
        session,
        callback_owner=callback_owner,
        process_name=process_name,
    )
    runtime.start()
    return runtime
