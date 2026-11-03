"""Helpers for safe, compact logging of observation payloads."""

from __future__ import annotations

import array
import logging
from dataclasses import asdict, is_dataclass
from typing import Any, Mapping

logger = logging.getLogger(__name__)


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
    "image",
    "camera",
    "color",
    "depth",
    "mask",
    "picture",
    "photo",
    "video",
    "frame",
    "pic",
    "img",
    "cam",
    "rgb",
    "bgr",
    "gray",
    "ir",
)

# Lists / tuples longer than this are summarised as metadata rather than
# logged element-by-element.
_LARGE_SEQUENCE_THRESHOLD = 100

# bytes / bytearray / memoryview longer than this are summarised as metadata.
_LARGE_BYTES_THRESHOLD = 256

# Any non-primitive value whose repr exceeds this length is summarised as
# metadata — the universal safety net against log flooding by unknown types.
_MAX_REPR_LENGTH = 200


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
    # ROS2 messages and similar slot-based objects — treat as mappings
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
        return [_summarize_value(item, in_image_field=in_image_field) for item in value]
    # Always summarize large binary/array types to avoid flooding logs,
    # even when the parent key is not in _IMAGE_KEYS (e.g. "front" / "wrist").
    if _is_large_value(value):
        return _image_metadata(value)
    # Only summarise complex values under an image key — leave scalars
    # (int / float / str / bool / None) intact so field names like
    # "height", "width", "encoding" are still human-readable in logs.
    if in_image_field and not _is_primitive(value):
        return _image_metadata(value)
    # Universal repr safety net: any value that survived all type-specific
    # branches but is still too large to log verbatim is summarised. This
    # catches exotic types (e.g. future ROS2 buffer representations) without
    # relying on field-name heuristics.
    if _is_repr_too_long(value):
        return _fallback_metadata(value)
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
            logger.debug(
                "Object has 'shape' attribute but access raised AttributeError: %s",
                type(value).__name__,
            )
        else:
            return len(shape) >= 2  # 2D+ arrays
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value) > _LARGE_BYTES_THRESHOLD
    # array.array — the actual Python type of ROS2 sensor_msgs.msg.Image.data.
    # Not a list/tuple, not bytes, has no ``shape``/``__slots__``, so it would
    # otherwise fall through every type-specific branch and flood the log.
    if isinstance(value, array.array):
        return len(value) > _LARGE_SEQUENCE_THRESHOLD
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
    metadata: dict[str, Any] = {"type": type(value).__name__}
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
    elif isinstance(value, array.array):
        total_bytes = len(value) * value.itemsize
        metadata["len"] = len(value)
        metadata["typecode"] = value.typecode
        metadata["itemsize"] = value.itemsize
        metadata["bytes"] = total_bytes
        metadata["size"] = fmt_size(total_bytes)
    elif hasattr(value, "__len__"):
        try:
            metadata["len"] = len(value)
        except TypeError:
            pass
    return metadata


def _is_repr_too_long(value: Any) -> bool:
    """Return True when ``repr(value)`` would flood logs.

    Primitives (int / float / str / bool / None) are always safe and skipped,
    so this only fires on exotic / large objects that fell through every
    type-specific branch in :func:`_summarize_value`.
    """
    if _is_primitive(value):
        return False
    try:
        return len(repr(value)) > _MAX_REPR_LENGTH
    except Exception:
        return True


def _fallback_metadata(value: Any) -> Mapping[str, Any]:
    """Last-resort metadata for values too large to log verbatim."""
    meta: dict[str, Any] = {"type": type(value).__name__}
    shape = getattr(value, "shape", None)
    if shape is not None:
        try:
            meta["shape"] = tuple(shape)
        except TypeError:
            meta["shape"] = shape
    dtype = getattr(value, "dtype", None)
    if dtype is not None:
        meta["dtype"] = str(dtype)
    if hasattr(value, "__len__"):
        try:
            meta["len"] = len(value)
        except TypeError:
            pass
    meta["repr_truncated"] = _safe_repr_head(value, 80)
    return meta


def _safe_repr_head(value: Any, n: int) -> str:
    """Return at most *n* characters of ``repr(value)``, with ellipsis."""
    try:
        s = repr(value)
    except Exception:
        return "<repr-failed>"
    return s[:n] + "…" if len(s) > n else s
