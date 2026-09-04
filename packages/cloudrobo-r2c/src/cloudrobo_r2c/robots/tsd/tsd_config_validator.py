"""TSD adapter 配置校验。

职责：
1. 约束 ``robot_tsd_config.yaml`` / ``robot_tsd_dummy_config.yaml``
   中 ``hardware.custom_config`` 的字段合法性
2. 在不引入 Pydantic 等额外依赖的前提下，提供默认值和范围检查
3. 让 adapter 在 connect() 前尽早暴露配置错误，而不是连到一半再失败
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

_CONFIG_SCHEMA: Dict[str, tuple[type[Any], Any, Any, Any, str]] = {
    "ip": (
        str,
        "192.168.1.6",
        None,
        None,
        "Robot controller IP address.",
    ),
    "default_speed": (
        int,
        30,
        1,
        100,
        "Default speed percentage applied by set_speed().",
    ),
    "reconnect_max_retries": (
        int,
        3,
        0,
        10,
        "Maximum reconnect attempts after a network fault.",
    ),
    "reconnect_delay_s": (
        float,
        1.0,
        0.1,
        30.0,
        "Base delay in seconds for exponential backoff reconnects.",
    ),
    "auto_enable_servo": (
        bool,
        True,
        None,
        None,
        "Enable servo automatically during connect().",
    ),
    "auto_set_mode": (
        bool,
        True,
        None,
        None,
        "Set system mode automatically during connect().",
    ),
    "auto_mode_value": (
        int,
        100,
        0,
        255,
        "Mode value used when auto_set_mode is enabled.",
    ),
    "auto_set_speed": (
        bool,
        True,
        None,
        None,
        "Apply default_speed automatically during connect().",
    ),
    "mock_mode": (
        bool,
        False,
        None,
        None,
        "When True, skip real robot connection entirely and use mock observations.",
    ),
}


def validate_tsd_config(config: Mapping[str, Any]) -> Dict[str, Any]:
    """校验并标准化 TSD adapter 配置。

    返回结果会：
    1. 为缺省字段补默认值
    2. 把字符串/数字形式的输入规范化为目标类型
    3. 对需要的字段执行范围检查
    """
    result: Dict[str, Any] = {}

    for field_name, (field_type, default, min_val, max_val, desc) in (
        _CONFIG_SCHEMA.items()
    ):
        raw = config.get(field_name)
        if raw is None:
            result[field_name] = default
            continue

        value = _coerce_value(field_name, raw, field_type)
        if min_val is not None and value < min_val:
            raise ValueError(
                f"Config '{field_name}' must be >= {min_val}, got {value}. "
                f"Description: {desc}"
            )
        if max_val is not None and value > max_val:
            raise ValueError(
                f"Config '{field_name}' must be <= {max_val}, got {value}. "
                f"Description: {desc}"
            )
        result[field_name] = value

    return result


def _coerce_value(field_name: str, raw: Any, field_type: type[Any]) -> Any:
    """把原始配置值转换成目标类型。"""
    if field_type is bool:
        return _to_bool(field_name, raw)
    if field_type is int:
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Config '{field_name}' must be int, got {type(raw).__name__}: {raw!r}"
            ) from exc
    if field_type is float:
        try:
            return float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Config '{field_name}' must be float, got {type(raw).__name__}: {raw!r}"
            ) from exc
    if field_type is str:
        return str(raw)
    return raw


def _to_bool(field_name: str, raw: Any) -> bool:
    """解析布尔配置。

    允许的字符串输入：
    - true / false
    - 1 / 0
    - yes / no
    - on / off
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError(
        f"Config '{field_name}' must be bool, got {type(raw).__name__}: {raw!r}"
    )
