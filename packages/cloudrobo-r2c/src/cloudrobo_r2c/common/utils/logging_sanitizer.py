"""Helpers for safe, compact logging of observation payloads."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping


def fmt_size(byte_count: int) -> str:
    """Format a byte count as a human-readable string (B / KB / MB / GB)."""
    if byte_count < 1024:
        return f"{byte_count} B"
    if byte_count < 1024 * 1024:
        return f"{byte_count / 1024:.1f} KB"
    if byte_count < 1024 * 1024 * 1024:
        return f"{byte_count / (1024 * 1024):.1f} MB"
    return f"{byte_count / (1024 * 1024 * 1024):.2f} GB"


_IMAGE_KEYS = {"images", "image", "raw_images", "h264_images"}

# Key substrings that suggest the value contains image/array data.
# Used as a fallback when the exact key is not in _IMAGE_KEYS.
_IMAGE_KEY_PATTERNS = (
    "image", "camera", "color", "depth", "mask", "picture",
    "photo", "video", "frame", "pic",
)

# Lists / tuples longer than this are summarised as metadata rather than
# logged element-by-element.
_LARGE_SEQUENCE_THRESHOLD = 100


def summarize_observation_for_log(observation: Any) -> Any:
    """Summarize an observation payload for logging.

    For image fields and large binary/array values, only metadata is kept
    to avoid logging large raw bytes/arrays.
    """
    if is_dataclass(observation):
        return _summarize_mapping(asdict(observation))
    if isinstance(observation, Mapping):
        return _summarize_mapping(observation)
    return _summarize_value(observation, in_image_field=False)


def _is_image_key(key: str) -> bool:
    """Return True when *key* likely names an image / large-array field."""
    lower = key.lower()
    if lower in _IMAGE_KEYS:
        return True
    return any(pattern in lower for pattern in _IMAGE_KEY_PATTERNS)


def _summarize_mapping(mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    summarized = {}
    for key, value in mapping.items():
        in_image_field = _is_image_key(str(key))
        summarized[key] = _summarize_value(value, in_image_field=in_image_field)
    return summarized


def _summarize_value(value: Any, *, in_image_field: bool) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _summarize_value(
                nested,
                in_image_field=in_image_field or _is_image_key(str(key)),
            )
            for key, nested in value.items()
        }
    # ROS2 messages and similar slot-based objects ¡ª treat as mappings
    # so that large fields (e.g. Image.data) are summarised as metadata.
    if hasattr(value, "__slots__"):
        slots = getattr(value, "__slots__", ())
        result: dict[str, Any] = {"_type": type(value).__name__}
        for slot in slots:
            try:
                result[slot] = _summarize_value(
                    getattr(value, slot),
                    in_image_field=in_image_field or _is_image_key(str(slot)),
                )
            except Exception:
                result[slot] = "<error>"
        return result
    if isinstance(value, (list, tuple)):
        n = len(value)
        if n > _LARGE_SEQUENCE_THRESHOLD:
            return _sequence_metadata(value)
        return [
            _summarize_value(item, in_image_field=in_image_field) for item in value
        ]
    # Always summarize large binary/array types to avoid flooding logs,
    # even when the parent key is not in _IMAGE_KEYS (e.g. "front" / "wrist").
    if _is_large_value(value):
        return _image_metadata(value)
    # Only summarise complex values under an image key ¡ª leave scalars
    # (int / float / str / bool / None) intact so field names like
    # "height", "width", "encoding" are still human-readable in logs.
    if in_image_field and not _is_primitive(value):
        return _image_metadata(value)
    return value


_PRIMITIVE_TYPES = (int, float, str, bool, type(None))


def _is_primitive(value: Any) -> bool:
    """Return True for simple scalar types that are safe to log as-is."""
    return isinstance(value, _PRIMITIVE_TYPES)


def _is_large_value(value: Any) -> bool:
    """Return True for value types that are too large to log verbatim."""
    if hasattr(value, "shape"):  # numpy / torch tensor
        try:
            shape = value.shape
        except AttributeError:
            logger.debug("Object has 'shape' attribute but access raised AttributeError: %s", type(value).__name__)
        else:
            return len(shape) >= 2  # 2D+ arrays
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value) > 256
    return False


def _sequence_metadata(value: Any) -> Mapping[str, Any]:
    """Return compact metadata for a list / tuple, no element content."""
    metadata: dict[str, Any] = {
        "type": type(value).__name__,
        "len": len(value),
    }
    # Sample first element type if available
    if len(value) > 0:
        try:
            metadata["element_type"] = type(value[0]).__name__
        except (IndexError, TypeError):
            pass
    return metadata


def _image_metadata(value: Any) -> Mapping[str, Any]:
    metadata = {"type": type(value).__name__}
    shape = getattr(value, "shape", None)
    if shape is not None:
        try:
            metadata["shape"] = tuple(shape)
        except TypeError:
            metadata["shape"] = shape
    dtype = getattr(value, "dtype", None)
    if dtype is not None:
        metadata["dtype"] = str(dtype)
    if isinstance(value, (bytes, bytearray, memoryview)):
        metadata["bytes"] = len(value)
        metadata["size"] = fmt_size(len(value))
    elif hasattr(value, "__len__"):
        try:
            metadata["len"] = len(value)
        except TypeError:
            pass
    return metadata