"""Custom-topic binding model: ``hardware.topics`` bindings + validation.

Parses and validates the robot config's ``hardware.topics`` mapping
(topic template -> binding spec). Bindings always run on the owner
process's main session; topic templates are resolved against that
session's identity. Callback names (``collector`` / ``on_message``)
resolve to attributes of a callback owner (the hardware adapter).
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping

logger = logging.getLogger(__name__)

PUBLISH = "publish"
SUBSCRIBE = "subscribe"
_VALID_DIRECTIONS = (PUBLISH, SUBSCRIBE)

_WRAPPER_REQUIRED_ATTRS = ("from_dict", "from_protobuf", "serialize", "to_dict")


def _require_mapping(value: Any, where: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a mapping, got {type(value).__name__}")
    return value


def _require_str(value: Any, where: str, allow_empty: bool = False) -> str:
    if value is None:
        if allow_empty:
            return ""
        raise ValueError(f"{where} is required")
    if not isinstance(value, str):
        raise ValueError(f"{where} must be a string, got {type(value).__name__}")
    if not value.strip() and not allow_empty:
        raise ValueError(f"{where} must not be empty")
    return value


@dataclass
class TopicBinding:
    """One custom topic binding: direction + proto + rate + callback name."""

    topic_template: str
    direction: str
    proto_path: str = ""
    rate_hz: float = 0.0
    collector: str = ""
    on_message: str = ""
    translator_cfg: Dict[str, Any] = field(default_factory=dict)


def resolve_class(path: str, where: str) -> type:
    """Resolve a proto reference: registered name first, then import path.

    Registered names (``r2c_sdk.protos`` entry points, e.g.
    a vendor adapter's ``<vendor>.waist_command``) take priority; anything
    else is treated as a plain ``module:ClassName`` (or dotted) path.
    """
    from cloudrobo_r2c.common.proto_registry import ensure_sdk_builtin, resolve_registered

    registered = resolve_registered(path, where)
    if registered is not None:
        return registered
    if ":" in path:
        module_path, _, class_name = path.partition(":")
    else:
        module_path, _, class_name = path.rpartition(".")
    if not module_path or not class_name:
        raise ValueError(f"{where}: invalid class path {path!r}")
    # 强制实现约束：裸路径仅限 SDK 内建 proto（第三方须经 entry_point 注册）
    ensure_sdk_builtin(module_path, where)
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ValueError(f"{where}: cannot import module {module_path!r}: {exc}") from exc
    cls = getattr(module, class_name, None)
    if cls is None:
        raise ValueError(f"{where}: {module_path!r} has no attribute {class_name!r}")
    return cls


def resolve_wrapper_class(proto_path: str, where: str) -> type:
    """Resolve a binding proto（统一解析收敛，§12-8）.

    wrapper 类校验四方法；生成的 pb2 消息类适配为
    :class:`~cloudrobo_r2c.common.proto_registry.Pb2MessageAdapter`
    （TopicRuntime 不再自行判别）。
    """
    from cloudrobo_r2c.common.proto_registry import resolve_proto_class

    return resolve_proto_class(proto_path, where, mode="binding")


def validate_binding(binding: TopicBinding, where: str) -> None:
    """Fail-fast validate one binding (proto resolvable, direction fields)."""
    if not binding.proto_path:
        raise ValueError(
            f"{where}[{binding.topic_template!r}]: proto is required "
            "(no shared registry — declare proto per binding)"
        )
    resolve_wrapper_class(
        binding.proto_path, f"{where}[{binding.topic_template!r}]"
    )
    if binding.direction == PUBLISH:
        if not binding.collector:
            raise ValueError(
                f"{where}[{binding.topic_template!r}]: publish bindings "
                "require collector"
            )
        if binding.rate_hz <= 0:
            raise ValueError(
                f"{where}[{binding.topic_template!r}]: rate_hz must be > 0"
            )
    else:
        if not binding.on_message:
            raise ValueError(
                f"{where}[{binding.topic_template!r}]: subscribe bindings "
                "require on_message"
            )


def parse_topic_bindings(
    topics: Mapping[str, Any], where: str = "hardware.topics"
) -> List[TopicBinding]:
    """Parse and validate a ``hardware.topics`` mapping."""
    topics = _require_mapping(topics, where)
    bindings: List[TopicBinding] = []
    for template, data in topics.items():
        data = _require_mapping(data, f"{where}[{template!r}]")
        direction = _require_str(data.get("direction"), f"{where}[{template!r}].direction")
        if direction not in _VALID_DIRECTIONS:
            raise ValueError(
                f"{where}[{template!r}]: direction must be one of {_VALID_DIRECTIONS}"
            )
        translator_raw = _require_mapping(
            data.get("translator", {}) or {}, f"{where}[{template!r}].translator"
        )
        binding = TopicBinding(
            topic_template=template,
            direction=direction,
            proto_path=_require_str(data.get("proto"), f"{where}[{template!r}].proto"),
            rate_hz=float(data.get("rate_hz", 0.0) or 0.0),
            collector=_require_str(
                data.get("collector"), f"{where}[{template!r}].collector", True
            ),
            on_message=_require_str(
                data.get("on_message"), f"{where}[{template!r}].on_message", True
            ),
            translator_cfg=translator_raw,
        )
        validate_binding(binding, where)
        bindings.append(binding)

    seen: Dict[str, str] = {}
    for binding in bindings:
        if binding.topic_template in seen:
            raise ValueError(
                f"{where}: topic {binding.topic_template!r} declared twice "
                f"({seen[binding.topic_template]} vs {binding.direction})"
            )
        seen[binding.topic_template] = binding.direction

    return bindings
