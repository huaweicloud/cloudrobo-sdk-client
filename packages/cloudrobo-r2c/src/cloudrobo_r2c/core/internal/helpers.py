"""Shared helpers for core bridges/translators."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from itertools import count
from threading import Lock
from typing import Any, Dict, List, Mapping, Sequence

import numpy as np

_GLOBAL_OBSERVATION_ID_COUNTER = count(1)
_GLOBAL_OBSERVATION_ID_LOCK = Lock()


def to_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return _normalize_object_mapping(value.__dict__)
    raise TypeError("Expected mapping-like input")


def _normalize_object_mapping(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _normalize_object_mapping(item) for key, item in value.items()}
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_normalize_object_mapping(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_object_mapping(item) for item in value)
    if hasattr(value, "__dict__"):
        return _normalize_object_mapping(value.__dict__)
    return value


def lookup_dotted(mapping: Mapping[str, Any], dotted_key: str) -> Any:
    current: Any = mapping
    for part in dotted_key.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(dotted_key)
        current = current[part]
    return current


def assign_dotted(target: Dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    current = target
    for part in parts[:-1]:
        node = current.get(part)
        if not isinstance(node, dict):
            node = {}
            current[part] = node
        current = node
    current[parts[-1]] = value


def resolve_tensor(model_output_tensor: Any, source_tensor: str) -> Any:
    if isinstance(model_output_tensor, Mapping):
        return model_output_tensor[source_tensor]
    return model_output_tensor


def extract_named_joint_positions(latest_device_state: Any) -> Dict[str, float]:
    if latest_device_state is None:
        return {}
    mapping = to_mapping(latest_device_state)
    direct = mapping.get("named_joint_positions")
    if isinstance(direct, Mapping):
        return {str(k): float(v) for k, v in direct.items()}
    observation = mapping.get("observation")
    if isinstance(observation, Mapping):
        nested = observation.get("named_joint_positions")
        if isinstance(nested, Mapping):
            return {str(k): float(v) for k, v in nested.items()}
    return {
        str(key): float(value)
        for key, value in mapping.items()
        if isinstance(key, str) and isinstance(value, (int, float, np.floating))
    }


def first_non_empty_str(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        value_str = str(value)
        if value_str != "":
            return value_str
    return ""


def ensure_list_of_ints(values: Any) -> List[int]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return []
    return [int(item) for item in values]


def next_global_observation_id() -> int:
    """Return a process-wide monotonically increasing observation id."""
    with _GLOBAL_OBSERVATION_ID_LOCK:
        return int(next(_GLOBAL_OBSERVATION_ID_COUNTER))
