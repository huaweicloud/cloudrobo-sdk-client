"""Reusable validation helpers for wrapper model parsing."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple, Type, Union


from google.protobuf.timestamp_pb2 import Timestamp


class ValidationError(ValueError):
    """Raised when incoming wrapper data does not meet expected schema."""


def _typename(expected_type: Union[Type[Any], Tuple[Type[Any], ...]]) -> str:
    if isinstance(expected_type, tuple):
        return " | ".join(t.__name__ for t in expected_type)
    return expected_type.__name__


def validate_type(
    value: Any,
    expected_type: Union[Type[Any], Tuple[Type[Any], ...]],
    field_name: str,
    model_name: str,
) -> None:
    """Validate value type with a consistent error message."""
    if not isinstance(value, expected_type):
        raise ValidationError(
            f"{model_name}: Field '{field_name}' must be {_typename(expected_type)}, "
            f"got {type(value).__name__}"
        )


def validate_required_fields(
    data: Dict[str, Any], required_fields: Iterable[str], model_name: str
) -> None:
    """Ensure all required field names exist in the input dict."""
    missing = [name for name in required_fields if name not in data]
    if missing:
        raise ValidationError(
            f"{model_name}: Missing required field(s): {', '.join(missing)}"
        )


def validate_timestamp(value: Any, field_name: str, model_name: str) -> None:
    """Validate timestamp fields as non-negative integer."""
    validate_type(value, int, field_name, model_name)
    if value < 0:
        raise ValidationError(
            f"{model_name}: Field '{field_name}' must be >= 0, got {value}"
        )


def validate_list_items_type(
    value: Any,
    item_type: Union[Type[Any], Tuple[Type[Any], ...]],
    field_name: str,
    model_name: str,
) -> None:
    """Validate a list and its element types."""
    validate_type(value, list, field_name, model_name)
    for idx, item in enumerate(value):
        if not isinstance(item, item_type):
            raise ValidationError(
                f"{model_name}: Field '{field_name}[{idx}]' must be {_typename(item_type)}, "
                f"got {type(item).__name__}"
            )


def validate_string(
    value: Any, field_name: str, model_name: str, allow_empty: bool = True
) -> None:
    """Validate string field with optional empty-value restriction."""
    validate_type(value, str, field_name, model_name)
    if not allow_empty and value == "":
        raise ValidationError(f"{model_name}: Field '{field_name}' cannot be empty")


def validate_dict_field(
    data: Dict[str, Any], field_name: str, model_name: str
) -> Dict[str, Any]:
    """Validate a dict field in a parent dictionary and return it."""
    value = data.get(field_name)
    validate_type(value, dict, field_name, model_name)
    return value


def validate_optional_list_field(
    data: Dict[str, Any], field_name: str, model_name: str
) -> List[Any]:
    """Validate an optional list field in a parent dictionary and return list value."""
    value = data.get(field_name, [])
    if value and not isinstance(value, list):
        raise ValidationError(
            f"{model_name}: Field '{field_name}' must be a list, got {type(value).__name__}"
        )
    return value


def validate_numeric_sequence(
    value: Any,
    field_name: str,
    model_name: str,
    *,
    expected_length: int,
    allow_tuple: bool = False,
) -> List[float]:
    """Validate fixed-size numeric sequence and return normalized float list."""
    expected_type: Union[Type[Any], Tuple[Type[Any], ...]] = (
        (list, tuple) if allow_tuple else list
    )
    validate_type(value, expected_type, field_name, model_name)
    if len(value) != expected_length:
        raise ValidationError(
            f"{model_name}: Field '{field_name}' must contain exactly {expected_length} elements, got {len(value)}"
        )

    normalized: List[float] = []
    for idx, item in enumerate(value):
        if not isinstance(item, (int, float)):
            raise ValidationError(
                f"{model_name}: Field '{field_name}[{idx}]' must be int | float, "
                f"got {type(item).__name__}"
            )
        normalized.append(float(item))

    return normalized


def validate_pose7d(value: Any, field_name: str, model_name: str) -> List[float]:
    """Validate Pose7D-like payload (7 numeric elements) and return normalized floats."""
    return validate_numeric_sequence(value, field_name, model_name, expected_length=7)


def normalize_proto_timestamp(value: Any, field_name: str, model_name: str) -> Any:
    """Normalize timestamp input into protobuf Timestamp message."""
    ts = Timestamp()
    if isinstance(value, int):
        validate_timestamp(value, field_name, model_name)
        ts.FromMilliseconds(value)
        return ts

    if isinstance(value, Timestamp):
        ts.CopyFrom(value)
        return ts

    raise ValidationError(
        f"{model_name}: Field '{field_name}' must be int | Timestamp, got {type(value).__name__}"
    )


def proto_timestamp_to_millis(value: Any, field_name: str, model_name: str) -> int:
    """Convert protobuf Timestamp field to milliseconds."""
    if not isinstance(value, Timestamp):
        raise ValidationError(
            f"{model_name}: Field '{field_name}' must be Timestamp, got {type(value).__name__}"
        )
    return value.ToMilliseconds()
