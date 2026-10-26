"""Route configuration data structures for multi-route support (v3.4 schema)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from cloudrobo_r2c.core.config_mapper import TransformSpec, _to_transform_specs_or_none

VALID_PROTOCOL_TYPES = frozenset({"http", "https", "ws", "wss"})
VALID_TIME_UNITS = frozenset({"seconds", "minutes", "hours"})

# 连接策略可取值：
#   eager        —— 启动时即建连，长驻
#   lazy_resident—— 首次收到观测数据时才建连，连接后长驻
#   lazy_fresh   —— 每次收到观测数据才建连，推理完成后断开连接（默认）
VALID_CONNECTION_STRATEGIES = frozenset({"eager", "lazy_resident", "lazy_fresh"})


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    """Parse a value into a bool, tolerating common string forms.

    Accepts ``True``/``False`` booleans, and the string forms
    ``"true"``/``"false"`` (case-insensitive, also ``"1"``/``"0"``).
    Anything else falls back to ``default``.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _parse_route_transforms(
    value: Any,
) -> Optional[List[TransformSpec]]:
    """Parse a serial/deserial transform entry into a list of TransformSpec.

    Supports the same shorthand formats as ``config_mapper``:
      - plain string name e.g. ``"json_pack"``
      - single-key mapping e.g. ``{"json_pack": {"ensure_ascii": false}}``
      - list pipeline mixing the above, e.g. ``["json_pack", {"x": {...}}]``

    For backward compatibility, the legacy dict-key form
    ``{"name": ..., "config": ...}`` (single or as list items) is also accepted.
    """
    if value is None:
        return None
    if isinstance(value, Mapping) and "name" in value:
        return [TransformSpec(name=str(value["name"]), config=value.get("config"))]
    if (
        isinstance(value, list)
        and value
        and all(isinstance(i, Mapping) and "name" in i for i in value)
    ):
        return [
            TransformSpec(name=str(i["name"]), config=i.get("config")) for i in value
        ]
    return _to_transform_specs_or_none(value)


@dataclass(frozen=True)
class ProtocolConfig:
    """Protocol configuration for a route."""

    type: Optional[str] = None
    origin_endpoint: Optional[str] = field(default_factory=str)
    endpoint: Optional[str] = field(default_factory=str, repr=False)
    method: str = "POST"
    headers: Dict[str, str] = field(default_factory=dict, repr=False)
    timeout: int = 20
    tls: Optional[Dict[str, Any]] = field(default_factory=dict, repr=False)
    probe: bool = False
    connect_strategy: str = "lazy_fresh"

    def __post_init__(self) -> None:
        if self.type is not None and self.type not in VALID_PROTOCOL_TYPES:
            raise ValueError(
                f"Invalid protocol type: {self.type!r}. "
                f"Must be one of {sorted(VALID_PROTOCOL_TYPES)} or None."
            )
        if self.connect_strategy not in VALID_CONNECTION_STRATEGIES:
            raise ValueError(
                f"Invalid connection strategy: {self.connect_strategy!r}. "
                f"Must be one of {sorted(VALID_CONNECTION_STRATEGIES)}."
            )

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> Optional["ProtocolConfig"]:
        if data is None or not data:
            return None
        tls_data = data.get("tls")
        tls: Optional[Dict[str, Any]] = None
        if tls_data and isinstance(tls_data, Mapping):
            tls = dict(tls_data)
        headers_data = data.get("headers", {})
        headers: Dict[str, str] = {}
        if headers_data and isinstance(headers_data, Mapping):
            headers = {str(k): str(v) for k, v in headers_data.items()}
        # ``probe`` only has meaning for WebSocket clients (see policy_client).
        # It controls whether, after connecting, we perform one recv to confirm
        # the model is alive (the received data is simply discarded).
        probe = _parse_bool(data.get("probe", False), default=False)
        # 连接策略：控制 WebSocket 连接的建立与释放时机（见 ws_policy_client）。
        # 仅在缺省时回退到默认值 "lazy_fresh"；非法取值由 __post_init__ 校验并抛错。
        connect_strategy = str(data.get("connect_strategy", "lazy_fresh"))
        # NOTE: any legacy "api_key" key in old files is deliberately ignored.
        # The api_key field was removed to avoid leaking secrets through the
        # persisted config file; it must not be parsed or retained.
        return cls(
            type=data.get("type"),
            endpoint=data.get("endpoint"),
            method=str(data.get("method", "POST")),
            headers=headers,
            timeout=int(data.get("timeout", 30)),
            tls=tls,
            probe=probe,
            connect_strategy=connect_strategy,
        )


@dataclass(frozen=True)
class RouteConfig:
    """Configuration for a single route."""

    name: str
    description: Optional[str] = None
    protocol: Optional[ProtocolConfig] = None
    r2c_to_model: Optional[List[Mapping[str, Any]]] = None
    model_to_r2c: Optional[List[Mapping[str, Any]]] = None
    serial_transform: Optional[List[TransformSpec]] = None
    deserial_transform: Optional[List[TransformSpec]] = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("RouteConfig name must be a non-empty string.")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RouteConfig":
        name = data["name"]
        if not name:
            raise ValueError("RouteConfig name must be a non-empty string.")
        protocol = ProtocolConfig.from_dict(data.get("protocol"))
        r2c_to_model: Optional[List[Mapping[str, Any]]] = None
        r2c_raw = data.get("r2c_to_model")
        if r2c_raw:
            if not isinstance(r2c_raw, list):
                raise ValueError("r2c_to_model must be a list of mappings")
            r2c_to_model = [dict(m) for m in r2c_raw]
        model_to_r2c: Optional[List[Mapping[str, Any]]] = None
        model_raw = data.get("model_to_r2c")
        if model_raw:
            if not isinstance(model_raw, list):
                raise ValueError("model_to_r2c must be a list of mappings")
            model_to_r2c = [dict(m) for m in model_raw]
        return cls(
            name=str(name),
            description=data.get("description"),
            protocol=protocol,
            r2c_to_model=r2c_to_model,
            model_to_r2c=model_to_r2c,
            serial_transform=_parse_route_transforms(data.get("serial_transform")),
            deserial_transform=_parse_route_transforms(
                data.get("deserial_transform")
            ),
        )


@dataclass(frozen=True)
class ModelConfig:
    """Deprecated model configuration (v2.2 compatibility)."""

    type: Optional[str] = None
    policy_type: Optional[str] = None
    pretrained_model_path: Optional[str] = None
    device: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> Optional["ModelConfig"]:
        if data is None or not data:
            return None
        pretrained = data.get("pretrained_model")
        path: Optional[str] = None
        if isinstance(pretrained, Mapping):
            path = pretrained.get("path")
        elif isinstance(pretrained, str):
            path = pretrained
        return cls(
            type=data.get("type"),
            policy_type=data.get("policy_type"),
            pretrained_model_path=path,
            device=data.get("device"),
        )
