"""Predefined value transformers for config-driven mapping rules."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Dict, Mapping, Sequence
import array

logger = logging.getLogger(__name__)

from cloudrobo_r2c.core.interfaces import IValueTransformer


def _encode_ndarray_png(value: Any) -> bytes:
    import cv2
    import numpy as np

    if not isinstance(value, np.ndarray):
        raise TypeError("ndarray_to_png expects a numpy.ndarray input")
    ok, encoded = cv2.imencode(".png", value)
    if not ok:
        raise ValueError("Failed to encode ndarray as PNG")
    return encoded.tobytes()


def _encode_ndarray_jpeg(value: Any, quality: int = 95) -> bytes:
    import cv2
    import numpy as np

    if not isinstance(value, np.ndarray):
        raise TypeError("ndarray_to_jpeg expects a numpy.ndarray input")
    ok, encoded = cv2.imencode(
        ".jpg",
        value,
        [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)],
    )
    if not ok:
        raise ValueError("Failed to encode ndarray as JPEG")
    return encoded.tobytes()


def _encode_ndarray_bytes(value: Any) -> bytes:
    """Return the raw in-memory bytes backing a numpy ndarray via .tobytes().

    .. warning::

        The output does **not** include dtype or shape metadata.  The consumer
        must already know the expected layout (or receive it out-of-band) in
        order to reconstruct the array via ``np.frombuffer(buf, dtype=…)``.
    """
    import numpy as np

    if not isinstance(value, np.ndarray):
        raise TypeError("ndarray_to_bytes expects a numpy.ndarray input")
    return value.tobytes()


def _quality_from_config(config: Any, default: int) -> int:
    """Extract quality parameter from config, falling back to default.

    Config may be:
      - None / empty            → use *default*
      - int                     → use the int directly
      - Mapping (dict, etc.)    → extract ``config["quality"]`` (or *default* if absent)
    """
    if config is None:
        return default
    if isinstance(config, int):
        return int(config)
    if isinstance(config, Mapping):
        val = config.get("quality")
        if isinstance(val, (int, float)):
            return int(val)
        return default
    return default


def _validate_image_codec_config(config: Any, *, name: str = "") -> None:
    """Validate config for image codec transformers (JPEG, WebP, etc.).

    Accepts: None, an int (quality 0-100), or a mapping with an optional
    ``quality`` key (0-100).
    """
    if config is None:
        return
    quality: object = None
    if isinstance(config, int):
        quality = config
    elif isinstance(config, Mapping):
        quality = config.get("quality")
        if quality is not None and not isinstance(quality, (int, float)):
            raise ValueError(
                f"{name} config: 'quality' must be an integer (0-100), "
                f"got {type(quality).__name__}"
            )
    else:
        raise ValueError(
            f"{name} config must be an integer (quality) or a mapping "
            f"with an optional 'quality' key, got {type(config).__name__}"
        )
    if quality is not None:
        q = int(quality)
        if q < 0 or q > 100:
            raise ValueError(
                f"{name} config: quality must be 0-100, got {q}"
            )


def _ros_image_to_ndarray(value: Any) -> Any:
    import numpy as np

    for required in ("height", "width", "encoding", "data"):
        if not hasattr(value, required):
            raise TypeError(
                "ros_image_to_ndarray expects a sensor_msgs.msg.Image-like input"
            )

    height = int(value.height)
    width = int(value.width)
    encoding = str(value.encoding).strip().lower()
    if height <= 0 or width <= 0:
        raise ValueError("sensor_msgs.msg.Image height/width must be > 0")

    encoding_map = {
        "rgb8": (np.uint8, 3),
        "bgr8": (np.uint8, 3),
        "rgba8": (np.uint8, 4),
        "bgra8": (np.uint8, 4),
        "mono8": (np.uint8, 1),
        "8uc1": (np.uint8, 1),
        "mono16": (np.uint16, 1),
        "16uc1": (np.uint16, 1),
    }
    if encoding not in encoding_map:
        raise ValueError(f"Unsupported sensor_msgs.msg.Image encoding: {encoding}")

    dtype, channels = encoding_map[encoding]
    endian = ">" if bool(getattr(value, "is_bigendian", False)) else "<"
    if np.dtype(dtype).itemsize > 1:
        dtype = np.dtype(dtype).newbyteorder(endian)
    raw = np.frombuffer(bytes(value.data), dtype=dtype)
    expected = height * width * channels
    if raw.size < expected:
        raise ValueError(
            "sensor_msgs.msg.Image data is too small for the declared image shape"
        )
    raw = raw[:expected]
    if channels == 1:
        return raw.reshape((height, width))
    return raw.reshape((height, width, channels))


def _encode_ndarray_webp(value: Any, quality: int = 80) -> bytes:
    import cv2
    import numpy as np

    if not isinstance(value, np.ndarray):
        raise TypeError("ndarray_to_webp expects a numpy.ndarray input")
    ok, encoded = cv2.imencode(
        ".webp",
        value,
        [int(cv2.IMWRITE_WEBP_QUALITY), int(quality)],
    )
    if not ok:
        raise ValueError("Failed to encode ndarray as WebP")
    return encoded.tobytes()


def _ros_compressed_image_to_ndarray(value: Any) -> Any:
    import cv2
    import numpy as np

    if not hasattr(value, "data"):
        raise TypeError(
            "ros_compressed_image_to_ndarray expects "
            "a sensor_msgs.msg.CompressedImage-like input"
        )
    encoded = np.frombuffer(bytes(value.data), dtype=np.uint8)
    decoded = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    if decoded is None:
        raise ValueError("Failed to decode sensor_msgs.msg.CompressedImage bytes")
    return decoded


def _list_to_ros_float64_multi_array(value: Any) -> Any:
    try:
        from std_msgs.msg import Float64MultiArray
    except ImportError as e:  # pragma: no cover - depends on ROS runtime
        raise ImportError(
            "list_to_ros_float64_multi_array requires std_msgs.msg.Float64MultiArray"
        ) from e

    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError(
            "list_to_ros_float64_multi_array expects a sequence of numeric values"
        )

    sequence = list(value)
    message = Float64MultiArray()
    message.data = [float(item) for item in sequence]
    return message


def _list_to_ros_joint_state(value: Any, config: Any = None) -> Any:
    try:
        from sensor_msgs.msg import JointState
    except ImportError as e:  # pragma: no cover - depends on ROS runtime
        raise ImportError(
            "list_to_ros_joint_state requires sensor_msgs.msg.JointState"
        ) from e

    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError("list_to_ros_joint_state expects a sequence of joint values")

    sequence = list(value)
    message = JointState()
    message.position = [float(item) for item in sequence]

    cfg = config if isinstance(config, Mapping) else {}
    if "names" in cfg:
        message.name = [str(item) for item in cfg["names"]]
    if "velocity" in cfg:
        message.velocity = [float(item) for item in cfg["velocity"]]
    if "effort" in cfg:
        message.effort = [float(item) for item in cfg["effort"]]

    return message


def _list_to_ros_move_request(value: Any, config: Any = None) -> Any:
    try:
        move_module = import_module("jaka_msgs.srv")
        move_type = getattr(move_module, "Move")
        request = move_type.Request()
    except (
        ImportError,
        AttributeError,
    ) as e:  # pragma: no cover - depends on ROS runtime
        raise ImportError(
            "list_to_ros_move_request requires jaka_msgs.srv.Move to be available"
        ) from e

    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError("list_to_ros_move_request expects a sequence of joint values")

    pose = list(value)
    if len(pose) != 6:
        raise ValueError(
            f"list_to_ros_move_request expects exactly 6 joint values, got {len(pose)}"
        )

    cfg = config if isinstance(config, Mapping) else {}
    request.pose = [float(item) for item in pose]
    request.has_ref = bool(cfg.get("has_ref", False))
    request.ref_joint = [float(item) for item in cfg.get("ref_joint", [])]
    request.mvvelo = float(cfg.get("speed", 0.5))
    request.mvacc = float(cfg.get("acc", 3.5))
    request.mvtime = float(cfg.get("mvtime", 0.0))
    request.mvradii = float(cfg.get("mvradii", 0.0))
    request.coord_mode = int(cfg.get("coord_mode", 0))
    request.index = int(cfg.get("index", 0))
    return request


# Keys that are known to be joint-data arrays (parallel to "name"/"names").
# Used to emit a clear warning when a known joint-data field has a
# mismatched array length instead of silently passing through.
_JOINT_DATA_KEYS: frozenset[str] = frozenset({
    "name", "names",
    "position", "velocity", "effort", "acceleration",
    "temperature", "motor_current", "status",
})


def _select_joints_by_name(value: Any, config: Any = None) -> Any:
    """Filter and reorder joint-state arrays by name.

    *value* must be a ``Mapping`` containing at least a ``"name"`` or
    ``"names"`` key whose value is a list of joint names.  All other
    list-valued keys that have the same length as the names list are
    filtered/reordered in the same way.

    *config* must be a ``Mapping`` with a ``"names"`` key — the
    ordered list of joint names to keep.

    When a requested joint is missing from *value*, a warning is
    logged and ``0.0`` is substituted for numeric fields.
    """
    if not isinstance(value, Mapping):
        return value

    # Guard against None or non-Mapping config (Bug fix: was crashing).
    if not isinstance(config, Mapping):
        logger.warning(
            "select_joints_by_name: config is not a mapping (%s); "
            "passing through unchanged",
            type(config).__name__ if config is not None else "NoneType",
        )
        return value

    target_names: list[str] = [
        str(n).strip() for n in config.get("names", []) if str(n).strip()
    ]
    if not target_names:
        return value

    # Resolve the source names list: prefer "name", then "names".
    # Validate that the resolved value is actually a list — a string or
    # other non-list truthy value would silently break the length-based
    # heuristic below (Bug fix).
    raw_names = value.get("name")
    if not isinstance(raw_names, list):
        raw_names = value.get("names")
    if not isinstance(raw_names, list):
        logger.warning(
            "select_joints_by_name: input has no list-typed 'name'/'names' "
            "field (got %s); passing through unchanged",
            type(raw_names).__name__,
        )
        return value

    source_names: list[str] = [str(n) for n in raw_names]

    if not source_names:
        logger.warning(
            "select_joints_by_name: input 'name'/'names' list is empty; "
            "passing through unchanged"
        )
        return value

    # Build index map: target_name → source_index (or -1 if missing)
    name_to_source_idx: dict[str, int] = {
        name: idx for idx, name in enumerate(source_names)
    }
    indices: list[int] = []
    missing: list[str] = []
    for target in target_names:
        idx = name_to_source_idx.get(target, -1)
        if idx == -1:
            missing.append(target)
        indices.append(idx)

    if missing:
        raise ValueError(
            "select_joints_by_name: the following joints were not found "
            "in the source message:\n"
            f"  requested: {target_names}\n"
            f"  available: {source_names}\n"
            f"  missing:   {missing}\n"
            "Please check your subscription config — either fix the "
            "'names' list in select_joints_by_name, or ensure the ROS2 "
            "topic publishes the expected joint names."
        )

    result: dict[str, Any] = {}
    for key, val in value.items():
        if not isinstance(val, (list, array.array)):
            result[key] = val
            continue

        if len(val) != len(source_names):
            # Length mismatch — this list is NOT a joint-array parallel
            # to source_names.  Most often these are metadata fields
            # (e.g. "header" sub-objects).
            #
            # However, if *key* is a known joint-data field we emit a
            # warning because the input may be malformed (the ROS2 spec
            # requires parallel arrays).  This was a silent failure in
            # the original code.
            if key in _JOINT_DATA_KEYS:
                logger.warning(
                    "select_joints_by_name: joint-data field %r has "
                    "length %d but source_names has %d elements; "
                    "passing through unchanged (input may be malformed)",
                    key, len(val), len(source_names),
                )
            result[key] = val
            continue

        is_name_field = key in ("name", "names")
        filtered: list[Any] = []
        for i, idx in enumerate(indices):
            if idx >= 0:
                filtered.append(val[idx])
            elif is_name_field:  # pragma: no cover
                filtered.append(target_names[i])
            else:  # pragma: no cover
                filtered.append(0.0)
        result[key] = filtered

    return result


def _scalar_to_step_motor_gripper(value: Any, config: Any = None) -> Any:
    try:
        from step_motor.msg import Motor
    except ImportError as e:  # pragma: no cover - depends on ROS runtime
        raise ImportError(
            "scalar_to_step_motor_gripper requires step_motor.msg.Motor"
        ) from e

    numeric = float(value)
    if config is None:
        cfg: Mapping[str, Any] = {}
    elif isinstance(config, Mapping):
        cfg = config
    else:
        raise TypeError("scalar_to_step_motor_gripper config must be a mapping")

    open_threshold = float(cfg.get("open_threshold", 80.0))
    close_threshold = float(cfg.get("close_threshold", 20.0))
    if open_threshold < close_threshold:
        raise ValueError("open_threshold must be >= close_threshold")

    def _build(dir_value: int) -> Any:
        message = Motor()
        message.id = int(cfg.get("id", 1))
        message.speed = int(cfg.get("speed", 200))
        message.dir = int(dir_value)
        message.mode = int(cfg.get("mode", 2))
        message.angle = int(cfg.get("angle", 18750))
        message.state = int(cfg.get("state", 0))
        message.sub_divide = int(cfg.get("sub_divide", 32))
        return message

    if numeric > open_threshold:
        return _build(0)
    if numeric < close_threshold:
        return _build(1)
    return None


def _ros_message_to_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return {k: _normalize_ros_value(v) for k, v in value.items()}

    fields = getattr(value, "__slots__", None)
    if fields is None:
        payload = getattr(value, "__dict__", None)
        if isinstance(payload, Mapping):
            return {k: _normalize_ros_value(v) for k, v in payload.items()}
        return {"value": value}

    result: Dict[str, Any] = {}
    for field_name in fields:
        clean_name = field_name[1:] if field_name.startswith("_") else field_name
        field_value = getattr(value, field_name, getattr(value, clean_name, None))
        result[clean_name] = _normalize_ros_value(field_value)
    return result


def _normalize_ros_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _normalize_ros_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_ros_value(v) for v in value]
    if isinstance(value, tuple):
        return [_normalize_ros_value(v) for v in value]
    if hasattr(value, "__slots__") or hasattr(value, "__dict__"):
        return _ros_message_to_mapping(value)
    return value


def _slice_sequence(value: Any, config: Any = None) -> Any:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError("slice expects list/tuple-like input")
    if not isinstance(config, Mapping):
        raise TypeError("slice config must be a mapping with start/end")

    start = config.get("start")
    end = config.get("end")
    if start is not None:
        start = int(start)
    if end is not None:
        end = int(end)
    return list(value[start:end])


def _index_sequence(value: Any, config: Any = None) -> Any:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError("index expects list/tuple-like input")
    if not isinstance(config, Mapping):
        raise TypeError("index config must be a mapping with index")
    if "index" not in config:
        raise ValueError("index config must include 'index'")
    return value[int(config["index"])]


@dataclass(frozen=True)
class ToFloatTransformer(IValueTransformer):
    """Convert a scalar value to float."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return float(value)


@dataclass(frozen=True)
class ToIntTransformer(IValueTransformer):
    """Convert a scalar value to int."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return int(value)


@dataclass(frozen=True)
class ToStrTransformer(IValueTransformer):
    """Convert a value to string."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return str(value)


@dataclass(frozen=True)
class ToBoolTransformer(IValueTransformer):
    """Convert a value to bool with safe string parsing."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            truthy = {"1", "true", "t", "yes", "y", "on"}
            falsy = {"0", "false", "f", "no", "n", "off", ""}
            if normalized in truthy:
                return True
            if normalized in falsy:
                return False
            raise ValueError(f"Cannot convert string to bool: {value!r}")
        return bool(value)


@dataclass(frozen=True)
class ToListTransformer(IValueTransformer):
    """Convert a sequence-like value to list."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        if isinstance(value, list):
            return value
        if isinstance(value, (str, bytes, bytearray)):
            raise TypeError("to_list does not accept str/bytes input")
        return list(value)


@dataclass(frozen=True)
class SliceTransformer(IValueTransformer):
    """Slice a sequence by [start, end) and return a new list."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _slice_sequence(value, config=config)

    @staticmethod
    def validate_config(config: Any) -> None:
        if not isinstance(config, dict):
            raise ValueError("slice config must be a mapping with 'start' and/or 'end'")
        keys = {"start", "end"} & set(config.keys())
        if not keys:
            raise ValueError("slice config must contain 'start' and/or 'end'")
        for k in keys:
            v = config[k]
            if not isinstance(v, int) or v < 0:
                raise ValueError(
                    f"slice config '{k}' must be a non-negative integer, got {v!r}"
                )


@dataclass(frozen=True)
class IndexTransformer(IValueTransformer):
    """Read an element from a sequence by configured index."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _index_sequence(value, config=config)

    @staticmethod
    def validate_config(config: Any) -> None:
        if not isinstance(config, dict) or "index" not in config:
            raise ValueError("index config must be a mapping with 'index' key")
        idx = config["index"]
        if not isinstance(idx, int) or idx < 0:
            raise ValueError(
                f"index config 'index' must be a non-negative integer, got {idx!r}"
            )


@dataclass(frozen=True)
class ArrayToListTransformer(IValueTransformer):
    """Convert array-like value (e.g. numpy.ndarray) to list."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        tolist = getattr(value, "tolist", None)
        if callable(tolist):
            return tolist()
        if isinstance(value, (list, tuple)):
            return list(value)
        raise TypeError("array_to_list expects an array-like input with tolist()")


@dataclass(frozen=True)
class ListToNdarrayTransformer(IValueTransformer):
    """Convert a sequence or numeric value to a numpy ndarray."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        import numpy as np

        if isinstance(value, np.ndarray):
            return value
        if isinstance(value, (str, bytes, bytearray)):
            raise TypeError("list_to_ndarray expects a sequence of numeric values")
        return np.asarray(value, dtype=np.float32)


@dataclass(frozen=True)
class ListToRosFloat64MultiArrayTransformer(IValueTransformer):
    """Convert a sequence into std_msgs.msg.Float64MultiArray."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _list_to_ros_float64_multi_array(value)


@dataclass(frozen=True)
class ListToRosJointStateTransformer(IValueTransformer):
    """Convert a sequence into sensor_msgs.msg.JointState (position field)."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _list_to_ros_joint_state(value, config=config)

    @staticmethod
    def validate_config(config: Any) -> None:
        if config is None:
            return
        if not isinstance(config, dict):
            raise ValueError(
                "list_to_ros_joint_state config must be a mapping or None"
            )
        for key in ("names", "velocity", "effort"):
            if key in config and not isinstance(config[key], list):
                raise ValueError(
                    f"list_to_ros_joint_state config '{key}' must be a list"
                )


@dataclass(frozen=True)
class SelectJointsByNameTransformer(IValueTransformer):
    """Filter and reorder joint-state arrays by configured joint names.

    Input is expected to be a ``Mapping`` with parallel arrays keyed by
    ``"name"`` (or ``"names"``), ``"position"``, ``"velocity"``,
    ``"effort"``, etc.  The config must contain a ``"names"`` list
    specifying which joints to keep and in what order.

    Example config::

        {"names": ["joint_1", "joint_3", "joint_5"]}

    When a requested joint name is **not** present in the input, a
    warning is logged and a default of ``0.0`` is inserted.
    """

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _select_joints_by_name(value, config=config)

    @staticmethod
    def validate_config(config: Any) -> None:
        if config is None:
            raise ValueError(
                "select_joints_by_name config is required (e.g. "
                "{names: [joint_1, joint_2]})"
            )
        if not isinstance(config, dict):
            raise ValueError(
                "select_joints_by_name config must be a mapping"
            )
        names = config.get("names")
        if not isinstance(names, list) or not names:
            raise ValueError(
                "select_joints_by_name config must contain a non-empty "
                "'names' list"
            )
        for i, name in enumerate(names):
            if not isinstance(name, str) or not name.strip():
                raise ValueError(
                    f"select_joints_by_name config 'names[{i}]' must be "
                    f"a non-empty string, got {name!r}"
                )


@dataclass(frozen=True)
class ListToRosMoveRequestTransformer(IValueTransformer):
    """Convert a 6-DoF joint list into jaka_msgs.srv.Move.Request."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _list_to_ros_move_request(value, config=config)

    @staticmethod
    def validate_config(config: Any) -> None:
        if config is None:
            return
        if not isinstance(config, dict):
            raise ValueError(
                "list_to_ros_move_request config must be a mapping or None"
            )
        for num_key in ("speed", "acc", "mvtime", "mvradii"):
            if num_key in config and not isinstance(config[num_key], (int, float)):
                raise ValueError(
                    f"list_to_ros_move_request config '{num_key}' must be numeric"
                )


class ScalarToStepMotorGripperTransformer(IValueTransformer):
    """Map a scalar gripper ratio into step_motor.msg.Motor open/close command."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _scalar_to_step_motor_gripper(value, config)

    @staticmethod
    def validate_config(config: Any) -> None:
        if config is not None and not isinstance(config, dict):
            raise ValueError(
                "scalar_to_step_motor_gripper config must be a mapping"
            )


class ScalarToStepMotorGripperDebounceTransformer(IValueTransformer):
    """Emit gripper open/close only after N consecutive threshold crossings."""

    def __init__(self) -> None:
        self._open_count = 0
        self._close_count = 0

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        numeric = float(value)
        if config is None:
            cfg: Mapping[str, Any] = {}
        elif isinstance(config, Mapping):
            cfg = config
        else:
            raise TypeError(
                "scalar_to_step_motor_gripper_debounce config must be a mapping"
            )

        open_threshold = float(cfg.get("open_threshold", 80.0))
        close_threshold = float(cfg.get("close_threshold", 20.0))
        consecutive_required = int(cfg.get("consecutive_required", 10))
        if open_threshold < close_threshold:
            raise ValueError("open_threshold must be >= close_threshold")
        if consecutive_required <= 0:
            raise ValueError("consecutive_required must be > 0")

        if numeric > open_threshold:
            self._open_count += 1
            self._close_count = 0
            if self._open_count >= consecutive_required:
                self._open_count = 0
                return _scalar_to_step_motor_gripper(numeric, cfg)
            return None

        if numeric < close_threshold:
            self._close_count += 1
            self._open_count = 0
            if self._close_count >= consecutive_required:
                self._close_count = 0
                return _scalar_to_step_motor_gripper(numeric, cfg)
            return None

        self._open_count = 0
        self._close_count = 0
        return None

    @staticmethod
    def validate_config(config: Any) -> None:
        if config is not None and not isinstance(config, dict):
            raise ValueError(
                "scalar_to_step_motor_gripper_debounce config must be a mapping"
            )
        cfg = config or {}
        open_threshold = float(cfg.get("open_threshold", 80.0))
        close_threshold = float(cfg.get("close_threshold", 20.0))
        consecutive_required = int(cfg.get("consecutive_required", 10))
        if open_threshold < close_threshold:
            raise ValueError("open_threshold must be >= close_threshold")
        if consecutive_required <= 0:
            raise ValueError("consecutive_required must be > 0")


@dataclass(frozen=True)
class NdarrayToPngTransformer(IValueTransformer):
    """Encode HWC uint8 ndarray as PNG bytes."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _encode_ndarray_png(value)


@dataclass(frozen=True)
class NdarrayToJpegTransformer(IValueTransformer):
    """Encode HWC uint8 ndarray as JPEG bytes."""

    quality: int = 95

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _encode_ndarray_jpeg(
            value, quality=_quality_from_config(config, self.quality)
        )

    @staticmethod
    def validate_config(config: Any) -> None:
        _validate_image_codec_config(config, name="ndarray_to_jpeg")


@dataclass(frozen=True)
class RosImageToNdarrayTransformer(IValueTransformer):
    """Decode sensor_msgs.msg.Image data into ndarray."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _ros_image_to_ndarray(value)


@dataclass(frozen=True)
class RosImageToPngTransformer(IValueTransformer):
    """Encode sensor_msgs.msg.Image data as PNG bytes."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _encode_ndarray_png(_ros_image_to_ndarray(value))


@dataclass(frozen=True)
class RosImageToJpegTransformer(IValueTransformer):
    """Encode sensor_msgs.msg.Image data as JPEG bytes."""

    quality: int = 95

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _encode_ndarray_jpeg(
            _ros_image_to_ndarray(value),
            quality=_quality_from_config(config, self.quality),
        )

    @staticmethod
    def validate_config(config: Any) -> None:
        _validate_image_codec_config(config, name="ros_image_to_jpeg")


@dataclass(frozen=True)
class RosCompressedImageToNdarrayTransformer(IValueTransformer):
    """Decode sensor_msgs.msg.CompressedImage data into ndarray."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _ros_compressed_image_to_ndarray(value)


@dataclass(frozen=True)
class RosCompressedImageToPngTransformer(IValueTransformer):
    """Encode sensor_msgs.msg.CompressedImage data as PNG bytes."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _encode_ndarray_png(_ros_compressed_image_to_ndarray(value))


@dataclass(frozen=True)
class RosCompressedImageToJpegTransformer(IValueTransformer):
    """Encode sensor_msgs.msg.CompressedImage data as JPEG bytes."""

    quality: int = 95

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _encode_ndarray_jpeg(
            _ros_compressed_image_to_ndarray(value),
            quality=_quality_from_config(config, self.quality),
        )

    @staticmethod
    def validate_config(config: Any) -> None:
        _validate_image_codec_config(config, name="ros_compressed_image_to_jpeg")


@dataclass(frozen=True)
class NdarrayToWebpTransformer(IValueTransformer):
    """Encode HWC uint8 ndarray as WebP bytes.

    WebP typically achieves 30-40% smaller file size than JPEG at equivalent
    visual quality, at the cost of higher encoding time (~10-15x slower).
    Preferred for bandwidth-constrained scenarios.
    """

    quality: int = 80

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _encode_ndarray_webp(
            value, quality=_quality_from_config(config, self.quality)
        )

    @staticmethod
    def validate_config(config: Any) -> None:
        _validate_image_codec_config(config, name="ndarray_to_webp")


@dataclass(frozen=True)
class NdarrayToBytesTransformer(IValueTransformer):
    """Convert numpy ndarray to raw bytes via :meth:`ndarray.tobytes`.

    The output is a flat ``bytes`` object representing the raw C-contiguous
    memory buffer.  dtype and shape metadata are *not* preserved — the
    downstream consumer is responsible for interpreting the buffer correctly.
    """

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _encode_ndarray_bytes(value)


@dataclass(frozen=True)
class RosImageToWebpTransformer(IValueTransformer):
    """Encode sensor_msgs.msg.Image data as WebP bytes."""

    quality: int = 80

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _encode_ndarray_webp(
            _ros_image_to_ndarray(value),
            quality=_quality_from_config(config, self.quality),
        )

    @staticmethod
    def validate_config(config: Any) -> None:
        _validate_image_codec_config(config, name="ros_image_to_webp")


@dataclass(frozen=True)
class RosCompressedImageToWebpTransformer(IValueTransformer):
    """Encode sensor_msgs.msg.CompressedImage data as WebP bytes."""

    quality: int = 80

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _encode_ndarray_webp(
            _ros_compressed_image_to_ndarray(value),
            quality=_quality_from_config(config, self.quality),
        )

    @staticmethod
    def validate_config(config: Any) -> None:
        _validate_image_codec_config(config, name="ros_compressed_image_to_webp")


@dataclass(frozen=True)
class RosMessageToMappingTransformer(IValueTransformer):
    """Convert ROS-like message objects into recursively normalized mappings."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return _ros_message_to_mapping(value)


@dataclass(frozen=True)
class IdentityTransformer(IValueTransformer):
    """Return the input value unchanged."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        return value


@dataclass(frozen=True)
class PngToNdarrayTransformer(IValueTransformer):
    """Decode PNG bytes into HWC uint8 ndarray."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        import cv2
        import numpy as np

        if not isinstance(value, (bytes, bytearray)):
            raise TypeError("png_to_ndarray expects bytes input")
        encoded = np.frombuffer(value, dtype=np.uint8)
        decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if decoded is None:
            raise ValueError("Failed to decode PNG bytes")
        return decoded


@dataclass(frozen=True)
class JpegToNdarrayTransformer(IValueTransformer):
    """Decode JPEG bytes into HWC uint8 ndarray."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        import cv2
        import numpy as np

        if isinstance(value, np.ndarray):
            return value
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError("jpeg_to_ndarray expects bytes input")
        encoded = np.frombuffer(value, dtype=np.uint8)
        decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if decoded is None:
            raise ValueError("Failed to decode JPEG bytes")
        return decoded


@dataclass(frozen=True)
class WebpToNdarrayTransformer(IValueTransformer):
    """Decode WebP bytes into HWC uint8 ndarray."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        import cv2
        import numpy as np

        if isinstance(value, np.ndarray):
            return value
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError("webp_to_ndarray expects bytes input")
        encoded = np.frombuffer(value, dtype=np.uint8)
        decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if decoded is None:
            raise ValueError("Failed to decode WebP bytes")
        return decoded


@dataclass(frozen=True)
class DecodeImageTransformer(IValueTransformer):
    """Decode bytes-like image data into ndarray with configurable color order."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        import cv2
        import numpy as np

        if not isinstance(value, (bytes, bytearray)):
            raise TypeError("decode_image expects bytes input")

        target_mode = str(config or "bgr").strip().lower()
        if target_mode not in {"bgr", "rgb", "gray"}:
            raise ValueError("decode_image config must be one of: bgr, rgb, gray")

        encoded = np.frombuffer(value, dtype=np.uint8)
        if target_mode == "gray":
            decoded = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE)
        else:
            decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if decoded is None:
            raise ValueError("Failed to decode image bytes")
        if target_mode == "rgb":
            return cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB)
        return decoded

    @staticmethod
    def validate_config(config: Any) -> None:
        target = str(config or "bgr").strip().lower()
        if target not in {"bgr", "rgb", "gray"}:
            raise ValueError(
                f"decode_image config must be 'bgr', 'rgb', or 'gray', got {config!r}"
            )


@dataclass(frozen=True)
class BgrToRgbTransformer(IValueTransformer):
    """Convert a BGR ndarray image to RGB."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        import cv2
        import numpy as np

        if not isinstance(value, np.ndarray):
            raise TypeError("bgr_to_rgb expects numpy.ndarray input")
        return cv2.cvtColor(value, cv2.COLOR_BGR2RGB)


@dataclass(frozen=True)
class RgbToBgrTransformer(IValueTransformer):
    """Convert an RGB ndarray image to BGR."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        import cv2
        import numpy as np

        if not isinstance(value, np.ndarray):
            raise TypeError("rgb_to_bgr expects numpy.ndarray input")
        return cv2.cvtColor(value, cv2.COLOR_RGB2BGR)


@dataclass(frozen=True)
class ResizeTransformer(IValueTransformer):
    """Resize image ndarray with [height, width] target shape."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        import cv2
        import numpy as np

        if not isinstance(value, np.ndarray):
            raise TypeError("resize expects numpy.ndarray input")
        if not isinstance(config, Sequence) or isinstance(
            config, (str, bytes, bytearray)
        ):
            raise TypeError("resize config must be [height, width]")
        if len(config) != 2:
            raise ValueError(
                "resize config must contain exactly two items: [height, width]"
            )
        height, width = int(config[0]), int(config[1])
        if height <= 0 or width <= 0:
            raise ValueError("resize dimensions must be > 0")
        return cv2.resize(value, (width, height), interpolation=cv2.INTER_LINEAR)

    @staticmethod
    def validate_config(config: Any) -> None:
        if not isinstance(config, (list, tuple)) or isinstance(
            config, (str, bytes, bytearray)
        ):
            raise ValueError("resize config must be [height, width]")
        if len(config) != 2:
            raise ValueError("resize config must be [height, width]")
        try:
            h, w = int(config[0]), int(config[1])
        except (ValueError, TypeError):
            raise ValueError(
                f"resize dimensions must be integers, got {config[0]!r}, {config[1]!r}"
            )
        if h <= 0 or w <= 0:
            raise ValueError(
                f"resize dimensions must be > 0, got [{h}, {w}]"
            )


_REGION_PRESETS: Dict[str, tuple] = {
    "top_left":      (0.0, 0.5, 0.0, 0.5),
    "top_right":     (0.0, 0.5, 0.5, 1.0),
    "bottom_left":   (0.5, 1.0, 0.0, 0.5),
    "bottom_right":  (0.5, 1.0, 0.5, 1.0),
    "left_half":     (0.0, 1.0, 0.0, 0.5),
    "right_half":    (0.0, 1.0, 0.5, 1.0),
    "top_half":      (0.0, 0.5, 0.0, 1.0),
    "bottom_half":   (0.5, 1.0, 0.0, 1.0),
    "center":        (0.25, 0.75, 0.25, 0.75),
}


def _parse_crop_config(config: Any) -> tuple:
    """Parse crop config into (region_name, raw_box).

    Returns (str | None, sequence | None).
    """
    if isinstance(config, str):
        return config.strip().lower(), None

    if isinstance(config, Mapping):
        keys = set(config.keys())
        if keys & {"region"} and keys & {"box"}:
            raise ValueError("crop config: 'region' and 'box' are mutually exclusive")
        if keys & {"region"}:
            return str(config["region"]).strip().lower(), None
        if keys & {"box"}:
            return None, config["box"]
        return None, None

    raise TypeError(
        "crop config must be a string (region name) or mapping with 'region' or 'box'"
    )


def _resolve_crop_region(region_name: str, h: int, w: int) -> tuple:
    """Resolve a named region preset to pixel coordinates ``(y1, y2, x1, x2)``."""
    if region_name not in _REGION_PRESETS:
        raise ValueError(
            f"crop: unknown region '{region_name}', "
            f"supported: {', '.join(sorted(_REGION_PRESETS))}"
        )
    y1_r, y2_r, x1_r, x2_r = _REGION_PRESETS[region_name]
    return (int(y1_r * h), int(y2_r * h), int(x1_r * w), int(x2_r * w))


def _resolve_crop_box_explicit(raw_box: Any, h: int, w: int) -> tuple:
    """Resolve an explicit pixel box ``[y1, y2, x1, x2]`` with bounds validation."""
    if not isinstance(raw_box, Sequence) or isinstance(raw_box, (str, bytes, bytearray)):
        raise TypeError("crop box must be a sequence of [y1, y2, x1, x2]")
    if len(raw_box) != 4:
        raise ValueError("crop box must contain exactly four values: [y1, y2, x1, x2]")
    y1, y2, x1, x2 = int(raw_box[0]), int(raw_box[1]), int(raw_box[2]), int(raw_box[3])
    if y1 < 0 or y1 >= h or y2 <= 0 or y2 > h:
        raise ValueError(f"crop box y [{y1}, {y2}) is out of bounds for image height {h}")
    if x1 < 0 or x1 >= w or x2 <= 0 or x2 > w:
        raise ValueError(f"crop box x [{x1}, {x2}) is out of bounds for image width {w}")
    if y1 >= y2 or x1 >= x2:
        raise ValueError(f"crop box must have y1 < y2 and x1 < x2, got [{y1}, {y2}, {x1}, {x2}]")
    return (y1, y2, x1, x2)


def _resolve_crop_box(value: Any, config: Any) -> tuple:
    """Resolve crop box from config dict or region preset string.

    Returns ``(y1, y2, x1, x2)`` in pixel coordinates.
    """
    import numpy as np

    if not isinstance(value, np.ndarray):
        raise TypeError("crop expects numpy.ndarray input")
    h, w = value.shape[:2]

    region_name, raw_box = _parse_crop_config(config)

    if region_name is not None:
        return _resolve_crop_region(region_name, h, w)

    if raw_box is not None:
        return _resolve_crop_box_explicit(raw_box, h, w)

    raise ValueError("crop config must specify 'region' or 'box'")


@dataclass(frozen=True)
class CropTransformer(IValueTransformer):
    """Crop an ndarray image to a region.

    Config may be:

    - A **region name** string: ``"bottom_left"``, ``"top_right"``, etc.
      (9 presets covering halves, quarters, and center.)
    - A mapping with ``region`` key:
      ``{region: "bottom_left"}``
    - A mapping with ``box`` key for explicit pixel coordinates:
      ``{box: [y1, y2, x1, x2]}``  (y-first numpy slice order).
    """

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        y1, y2, x1, x2 = _resolve_crop_box(value, config)
        return value[y1:y2, x1:x2, ...]

    @staticmethod
    def validate_config(config: Any) -> None:
        if isinstance(config, str):
            name = config.strip().lower()
            if name not in _REGION_PRESETS:
                raise ValueError(
                    f"crop: unknown region {name!r}, "
                    f"supported: {', '.join(sorted(_REGION_PRESETS))}"
                )
            return
        if isinstance(config, dict):
            if "region" in config and "box" in config:
                raise ValueError(
                    "crop config: 'region' and 'box' are mutually exclusive"
                )
            if "region" in config:
                region = str(config["region"]).strip().lower()
                if region not in _REGION_PRESETS:
                    raise ValueError(
                        f"crop: unknown region {region!r}"
                    )
            if "box" in config:
                box = config["box"]
                if not isinstance(box, (list, tuple)) or len(box) != 4:
                    raise ValueError(
                        "crop config: 'box' must be [y1, y2, x1, x2]"
                    )
                for v in box:
                    if not isinstance(v, (int, float)):
                        raise ValueError(
                            f"crop config: box values must be numbers, got {v!r}"
                        )
            return
        raise ValueError(
            "crop config must be a region name string or "
            "a mapping with 'region' or 'box'"
        )


@dataclass(frozen=True)
class FormatTransformer(IValueTransformer):
    """Convert ndarray layout between HWC and CHW."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        import numpy as np

        if not isinstance(value, np.ndarray):
            raise TypeError("format expects numpy.ndarray input")
        target_layout = str(config or "").strip().upper()
        if target_layout not in {"HWC", "CHW"}:
            raise ValueError("format config must be one of: HWC, CHW")
        if value.ndim != 3:
            raise ValueError("format transform currently expects a 3D image tensor")

        if target_layout == "CHW":
            return np.transpose(value, (2, 0, 1))
        return np.transpose(value, (1, 2, 0))

    @staticmethod
    def validate_config(config: Any) -> None:
        target = str(config or "").strip().upper()
        if target not in {"HWC", "CHW"}:
            raise ValueError(
                f"format config must be 'HWC' or 'CHW', got {config!r}"
            )


@dataclass(frozen=True)
class NormalizeTransformer(IValueTransformer):
    """Normalize ndarray values with configurable strategy."""

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        import numpy as np

        if not isinstance(value, np.ndarray):
            raise TypeError("normalize expects numpy.ndarray input")
        if not isinstance(config, Mapping):
            raise TypeError("normalize config must be a mapping")

        norm_type = str(config.get("type", "scale")).strip().lower()
        if norm_type != "scale":
            raise ValueError("normalize.type currently only supports 'scale'")

        target_range = config.get("range", [0.0, 1.0])
        if not isinstance(target_range, Sequence) or len(target_range) != 2:
            raise ValueError("normalize.range must be a [min, max] sequence")
        target_min = float(target_range[0])
        target_max = float(target_range[1])

        arr = value.astype(np.float32, copy=False)
        source_min = float(config.get("source_min", 0.0))
        source_max = float(config.get("source_max", 255.0))
        if source_max <= source_min:
            raise ValueError("normalize source_max must be greater than source_min")

        arr = (arr - source_min) / (source_max - source_min)
        arr = np.clip(arr, 0.0, 1.0)
        return arr * (target_max - target_min) + target_min

    @staticmethod
    def validate_config(config: Any) -> None:
        if not isinstance(config, dict):
            raise ValueError("normalize config must be a mapping")
        norm_type = str(config.get("type", "scale")).strip().lower()
        if norm_type != "scale":
            raise ValueError(
                f"normalize.type must be 'scale', got {norm_type!r}"
            )
        target_range = config.get("range", [0.0, 1.0])
        if not isinstance(target_range, (list, tuple)) or len(target_range) != 2:
            raise ValueError("normalize.range must be [min, max]")
        t_min, t_max = float(target_range[0]), float(target_range[1])
        if t_min >= t_max:
            raise ValueError(
                f"normalize.range max must be > min, got [{t_min}, {t_max}]"
            )
        s_min = float(config.get("source_min", 0.0))
        s_max = float(config.get("source_max", 255.0))
        if s_min >= s_max:
            raise ValueError(
                f"normalize source_max must be > source_min, "
                f"got source_min={s_min}, source_max={s_max}"
            )


@dataclass(frozen=True)
class SO101GripperValueToJointTransformer(IValueTransformer):
    """SO101夹爪值转换器。

    将模型输出的夹爪值转换为SO101机器人实际需要的关节值。
    转换公式: joint_value = radians(action_value / 2.5 - 10)

    参数:
        action_value: 模型输出的夹爪值（如0.5表示打开，-0.17表示关闭）

    返回:
        实际关节值（弧度）
    """

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        """将夹爪值转换为关节值。

        Args:
            value: 模型输出的夹爪值
            config: 转换器配置（当前未使用）

        Returns:
            转换后的关节值（弧度）
        """
        # 确保输入是数值类型
        action_value = float(value)

        # 应用转换公式: joint_value = radians(action_value / 2.5 - 10)
        joint_value = math.radians(action_value / 2.5 - 10)

        return joint_value


@dataclass(frozen=True)
class SO101GripperJointToValueTransformer(IValueTransformer):
    """SO101关节值到夹爪值转换器（反向转换）。

    将SO101机器人实际关节值转换为模型期望的夹爪值。
    转换公式: gripper_value = (degrees(joint_value) + 10) * 2.5

    参数:
        joint_value: 实际关节值（弧度）

    返回:
        模型期望的夹爪值
    """

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        """将关节值转换为夹爪值。

        Args:
            value: 实际关节值（弧度）
            config: 转换器配置（当前未使用）

        Returns:
            转换后的夹爪值
        """
        # 确保输入是数值类型
        joint_value = float(value)

        # 应用转换公式: gripper_value = (degrees(joint_value) + 10) * 2.5
        gripper_value = (math.degrees(joint_value) + 10) * 2.5

        # 限制在合理范围内（根据实际需求调整）
        gripper_value = max(0.0, min(gripper_value, 100.0))

        return gripper_value


@dataclass(frozen=True)
class QinglongExtractGripperStatesTransformer(IValueTransformer):
    """青龙机器人夹爪状态提取转换器。

    从完整的关节状态中提取左右夹爪的状态，并转换为二值化状态。
    根据原有代码逻辑: joint_value * 2000 < 90 ? 0 : 100

    参数:
        value: 完整的关节位置列表
        config: 配置参数，可包含夹爪关节索引信息

    返回:
        [left_gripper_state, right_gripper_state] 二值化状态列表
    """

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        """从关节状态中提取夹爪状态。

        Args:
            value: 关节位置列表
            config: 配置参数，可指定夹爪关节索引

        Returns:
            [left_gripper_state, right_gripper_state] 二值化状态
        """
        import numpy as np

        # 确保输入是列表或数组
        if isinstance(value, (list, tuple)):
            joint_positions = list(value)
        elif hasattr(value, 'tolist'):
            joint_positions = value.tolist()
        else:
            raise TypeError("extract_gripper_states expects list or array input")

        # 青龙机器人的夹爪关节索引（根据配置文件）
        # left_gripper_left_finger_joint, right_gripper_left_finger_joint
        # 这些索引需要根据实际的joint_states中的顺序确定
        cfg = config if isinstance(config, Mapping) else {}
        left_gripper_idx = cfg.get("left_gripper_idx", 14)  # 默认左夹爪索引
        right_gripper_idx = cfg.get("right_gripper_idx", 15)  # 默认右夹爪索引

        # 提取夹爪关节位置
        left_gripper_pos = float(joint_positions[left_gripper_idx]) if left_gripper_idx < len(joint_positions) else 0.0
        right_gripper_pos = float(joint_positions[right_gripper_idx]) if right_gripper_idx < len(joint_positions) else 0.0

        # 转换为二值化状态: joint_value * 2000 < 90 ? 0 : 100
        left_gripper_state = 0 if left_gripper_pos * 2000 < 90 else 100
        right_gripper_state = 0 if right_gripper_pos * 2000 < 90 else 100

        return [left_gripper_state, right_gripper_state]


@dataclass(frozen=True)
class QinglongGripperRealTransform(IValueTransformer):
    """青龙机器人真实夹爪转换器。

    将模型输出的夹爪值转换为真实夹爪关节指令。
    根据原有代码: gripper_value / 2000.0

    参数:
        value: 模型输出的夹爪值（0-100范围）
        config: 配置参数

    返回:
        [left_finger_joint, right_finger_joint] 真实夹爪关节指令
    """

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        """将模型夹爪值转换为真实夹爪关节指令。

        Args:
            value: 模型输出的夹爪值
            config: 配置参数

        Returns:
            真实夹爪关节指令列表
        """
        # 确保输入是数值类型
        gripper_value = float(value)

        # 转换为真实关节指令: gripper_value / 2000.0
        real_joint_value = gripper_value / 2000.0

        # 返回左右手指关节指令（相同值）
        return [real_joint_value, real_joint_value]


@dataclass(frozen=True)
class QinglongGripperVisualTransform(IValueTransformer):
    """青龙机器人视觉夹爪转换器。

    将模型输出的夹爪值转换为视觉夹爪关节指令。
    根据原有代码逻辑:
    - width = gripper_value / 1000
    - command_joint = width * -10
    - 返回8个视觉关节的指令

    参数:
        value: 模型输出的夹爪值（0-100范围）
        config: 配置参数

    返回:
        8个视觉夹爪关节指令的列表
    """

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        """将模型夹爪值转换为视觉夹爪关节指令。

        Args:
            value: 模型输出的夹爪值
            config: 配置参数

        Returns:
            8个视觉夹爪关节指令列表
        """
        # 确保输入是数值类型
        gripper_value = float(value)

        # 根据原有代码: gripper_exp / 1000
        width = gripper_value / 1000.0

        # 计算基础关节指令: command_joint = width * -10
        command_joint = width * -10

        # 根据 get_visual_joints_values_for_action 方法生成8个关节指令
        visual_joints = [
            command_joint,           # Left_1_Joint
            command_joint * -1,      # Left_2_Joint
            command_joint * -0.2,    # Left_in_Joint
            command_joint * -1,      # Left_up_Joint
            command_joint * 1,       # Right_1_Joint
            command_joint * 1,       # Right_2_Joint
            command_joint * 0.2,     # Right_in_Joint
            command_joint * -1       # Right_up_Joint
        ]

        return visual_joints


@dataclass(frozen=True)
class Moz1GripperValueToJointsTransformer(IValueTransformer):
    """Moz1机器人夹爪值转换器。

    将gripper的单一值转换为8个关节的值。
    根据moz1_gripper_controller.cpp的换算逻辑：
    输入参数 x
    输出8维数组: [-5.236x, 0, 0, -8.03x, 5.236x, 0, 0, 8.03x]

    注意：模型输出值范围是0-100，需要先除以1000后再进行转换

    参数:
        value: gripper的输入值（模型输出值，范围0-100）
        config: 配置参数

    返回:
        8个关节的值列表
    """

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        """将gripper单一值转换为8个关节值。

        Args:
            value: gripper的输入值（模型输出值，范围0-100）
            config: 配置参数

        Returns:
            8个关节的值列表
        """
        # 确保输入是数值类型
        gripper_value = float(value)

        # 根据原始代码逻辑，模型输出值需要除以1000
        gripper_value = gripper_value / 1000.0

        # 根据 moz1_gripper_controller.cpp 的逻辑转换
        # 输出8维数组: [-5.236x, 0, 0, -8.03x, 5.236x, 0, 0, 8.03x]
        return [
            -5.235 * gripper_value,     # narrow1_joint
            0.0,                        # narrow2_joint
            0.0,                        # narrow3_joint
            -8.03 * gripper_value,      # narrow_loop_joint
            5.235 * gripper_value,      # wide1_joint
            0.0,                        # narrow2_joint
            0.0,                        # narrow3_joint
            8.03 * gripper_value        # wide_loop_joint
        ]


@dataclass(frozen=True)
class Moz1ExtractGripperStatesTransformer(IValueTransformer):
    """Moz1机器人夹爪状态提取转换器。

    从完整的关节状态中提取左右夹爪的状态，并转换为二值化状态。
    根据原有代码逻辑: 使用 wide1_joint 的值来反推
    gripper_state = 0 if gripper_value * 200 < 99 else 100

    参数:
        value: 完整的关节位置列表
        config: 配置参数，可包含夹爪关节索引信息

    返回:
        [left_gripper_state, right_gripper_state] 二值化状态列表
    """

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        """从关节状态中提取夹爪状态。

        Args:
            value: 关节位置列表
            config: 配置参数，可指定夹爪关节索引

        Returns:
            [left_gripper_state, right_gripper_state] 二值化状态
        """
        # 确保输入是列表或数组
        if isinstance(value, (list, tuple)):
            joint_positions = list(value)
        elif hasattr(value, 'tolist'):
            joint_positions = value.tolist()
        else:
            raise TypeError("moz1_extract_gripper_states expects list or array input")

        # Moz1机器人的夹爪关节索引（根据配置文件）
        # left_hand_wide1_joint, right_hand_wide1_joint
        # 这些索引需要根据实际的joint_states中的顺序确定
        cfg = config if isinstance(config, Mapping) else {}
        left_gripper_idx = cfg.get("left_gripper_wide1_idx", 20)  # 默认左夹爪wide1_joint索引
        right_gripper_idx = cfg.get("right_gripper_wide1_idx", 28)  # 默认右夹爪wide1_joint索引

        # 提取夹爪关节位置
        left_gripper_pos = float(joint_positions[left_gripper_idx]) if left_gripper_idx < len(joint_positions) else 0.0
        right_gripper_pos = float(joint_positions[right_gripper_idx]) if right_gripper_idx < len(joint_positions) else 0.0

        # 转换为二值化状态: gripper_value * 200 < 99 ? 0 : 100
        left_gripper_state = 0 if left_gripper_pos * 200 < 99 else 100
        right_gripper_state = 0 if right_gripper_pos * 200 < 99 else 100

        return [left_gripper_state, right_gripper_state]


@dataclass(frozen=True)
class JakaGripperValueToJointsTransformer(IValueTransformer):
    """Jaka机器人夹爪值转换器。

    将gripper的单一值转换为2个关节的值。
    根据jaka_gripper_controller.cpp的换算逻辑：
    输入参数 x (范围0-0.1)
    输出2维数组: [5.76*(x-0.1), -5.76*(x-0.1)]

    注意：模型输出值范围是0-100，需要先除以1000后再进行转换

    参数:
        value: gripper的输入值（模型输出值，范围0-100）
        config: 配置参数

    返回:
        2个关节的值列表 [left_finger_joint, right_finger_joint]
    """

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        """将gripper单一值转换为2个关节值。

        Args:
            value: gripper的输入值（模型输出值，范围0-100）
            config: 配置参数

        Returns:
            2个关节的值列表 [left_finger_joint, right_finger_joint]
        """
        # 确保输入是数值类型
        gripper_value = float(value)

        # 根据原始代码逻辑，模型输出值需要除以1000
        gripper_value = gripper_value / 1000.0

        # 根据 jaka_gripper_controller.cpp 的逻辑转换
        # 输出2维数组: [5.76*(x-0.1), -5.76*(x-0.1)]
        return [
            5.76 * (gripper_value - 0.1),   # left_finger_joint
            -5.76 * (gripper_value - 0.1)   # right_finger_joint
        ]


@dataclass(frozen=True)
class JakaExtractGripperStateTransformer(IValueTransformer):
    """Jaka机器人夹爪状态提取转换器。

    从完整的关节状态中提取夹爪的状态，并转换为0-100范围的值。
    根据原有代码逻辑: gripper_value = left_finger_joint / 5.76 + 0.1
    然后转换为0-100范围: gripper_value * 1000

    参数:
        value: 完整的关节位置列表
        config: 配置参数，可包含夹爪关节索引信息

    返回:
        [gripper_state] 0-100范围的夹爪状态列表
    """

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        """从关节状态中提取夹爪状态。

        Args:
            value: 关节位置列表
            config: 配置参数，可指定夹爪关节索引

        Returns:
            [gripper_state] 0-100范围的夹爪状态
        """
        # 确保输入是列表或数组
        if isinstance(value, (list, tuple)):
            joint_positions = list(value)
        elif hasattr(value, 'tolist'):
            joint_positions = value.tolist()
        else:
            raise TypeError("jaka_extract_gripper_state expects list or array input")

        # Jaka机器人的夹爪关节索引（根据配置文件）
        # left_finger_joint, right_finger_joint
        # 这些索引需要根据实际的joint_states中的顺序确定
        cfg = config if isinstance(config, Mapping) else {}
        left_finger_idx = cfg.get("left_finger_idx", 6)  # 默认左手指关节索引

        # 提取左手指关节位置
        left_finger_pos = float(joint_positions[left_finger_idx]) if left_finger_idx < len(joint_positions) else 0.0

        # 根据原始代码逻辑反推夹爪值: gripper_value = left_finger_joint / 5.76 + 0.1
        gripper_value = left_finger_pos / 5.76 + 0.1

        # 转换到0-100的范围
        gripper_state = gripper_value * 1000

        return [gripper_state]



def _validate_targeted_config(
    config: dict, *, name: str, expect_values: bool = True
) -> None:
    """Validate the common ``{indices|names}`` config structure.

    Shared by :class:`SubtractTransformer` and :class:`EulerModuloTransformer`.
    """
    if expect_values:
        if "values" not in config:
            raise ValueError(f"{name} dict config requires 'values'")
        values = config["values"]
        if not isinstance(values, list) or not all(
            isinstance(v, (int, float)) for v in values
        ):
            raise ValueError(f"{name} 'values' must be a list of numbers")

    has_indices = "indices" in config
    has_names = "names" in config
    if has_indices and has_names:
        raise ValueError(
            f"{name} config: 'indices' and 'names' are mutually exclusive"
        )
    if not has_indices and not has_names:
        raise ValueError(f"{name} config requires 'indices' or 'names'")

    if has_indices:
        indices = config["indices"]
        if not isinstance(indices, list) or not all(
            isinstance(i, int) and i >= 0 for i in indices
        ):
            raise ValueError(
                f"{name} 'indices' must be a list of non-negative ints"
            )

    if has_names:
        names = config["names"]
        if not isinstance(names, list) or not all(
            isinstance(n, str) for n in names
        ):
            raise ValueError(f"{name} 'names' must be a list of strings")
        if "names_path" not in config:
            raise ValueError(f"{name} 'names' requires 'names_path'")


@dataclass(frozen=True)
class SubtractTransformer(IValueTransformer):
    r"""Element-wise subtraction: ``output = value - config``.

    **Legacy modes** (backward compatible):

    - *value* is a list, *config* is a list of same length →
      ``value[i] - config[i]`` for all *i*.
    - *value* is a scalar, *config* is a scalar → ``value - config``.
    - *config* is ``None`` → pass-through.

    **Targeted modes** (new):

    - *config* = ``{values: [v0, v1, ...], indices: [i0, i1, ...]}`` →
      subtract only at positions *i0, i1, ...*; others pass through.
    - *config* = ``{values: [...], names: [...], names_path: "..."}`` →
      subtract at positions matching *names* (requires *context*).
    """

    @staticmethod
    def validate_config(config: Any) -> None:
        if config is None:
            return
        if isinstance(config, (int, float)):
            return
        if isinstance(config, list):
            for v in config:
                if not isinstance(v, (int, float)):
                    raise ValueError(
                        f"subtract config list must contain only numbers, got {v!r}"
                    )
            return
        if isinstance(config, dict):
            _validate_targeted_config(config, name="subtract")
            return
        raise ValueError(
            f"subtract config must be a number, list of numbers, or dict, "
            f"got {type(config).__name__}"
        )

    def transform(
        self, value: Any, config: Any = None, context: Any = None
    ) -> Any:
        if config is None:
            return value
        if value is None:
            return value

        # Dict config: targeted subtraction
        if isinstance(config, dict):
            if not isinstance(value, list):
                raise TypeError(
                    "subtract dict config requires a list value, "
                    f"got {type(value).__name__}"
                )
            indices = self._resolve_indices(config, context, len(value))
            values = config["values"]
            if len(indices) != len(values):
                raise ValueError(
                    f"subtract: got {len(indices)} targets but "
                    f"{len(values)} values"
                )
            result = list(value)
            for idx, val in zip(indices, values):
                if val != 0:
                    result[idx] = result[idx] - val
            return result

        # Legacy list-vs-list
        if isinstance(value, list):
            if not isinstance(config, list):
                raise TypeError(
                    "subtract expects config to be a list when value is a list"
                )
            if len(value) != len(config):
                raise ValueError(
                    f"subtract length mismatch: "
                    f"value has {len(value)} elements, "
                    f"config has {len(config)} elements"
                )
            return [
                v - c if c != 0 else v
                for v, c in zip(value, config)
            ]

        # Legacy scalar
        if isinstance(value, (int, float)):
            if not isinstance(config, (int, float)):
                raise TypeError(
                    "subtract expects config to be numeric when value is scalar"
                )
            return value - config

        raise TypeError(
            f"subtract expects a list or numeric value, got {type(value).__name__}"
        )

    @staticmethod
    def _resolve_indices(
        config: dict, context: Any, value_len: int
    ) -> list[int]:
        if "indices" in config:
            indices = [int(i) for i in config["indices"]]
            for i in indices:
                if i >= value_len:
                    raise ValueError(
                        f"subtract index {i} out of range "
                        f"(value length={value_len})"
                    )
            return indices
        target_names = set(config["names"])
        names_path = config["names_path"]
        if context is None:
            raise ValueError(
                "subtract with 'names' requires context (source payload)"
            )
        from cloudrobo_r2c.core.internal.helpers import lookup_dotted
        all_names = lookup_dotted(context, names_path)
        if not isinstance(all_names, list):
            raise TypeError(
                f"subtract names_path {names_path!r} "
                f"must resolve to a list, got {type(all_names).__name__}"
            )
        return [i for i, name in enumerate(all_names) if name in target_names]


@dataclass(frozen=True)
class EulerModuloTransformer(IValueTransformer):
    """Element-wise modulo for Euler angles (or any periodic value).

    Config:
        modulus: float — the modulus (e.g. ``2 * pi``)
        indices: list[int] — which positions to apply modulo to
        names: list[str] — which names to apply modulo to
          (requires ``names_path`` to resolve names → indices from *context*)
        names_path: str — dotted path into *context* to find the names list

    ``indices`` and ``names`` are mutually exclusive.
    Elements not in the target set pass through unchanged.
    """

    @staticmethod
    def validate_config(config: Any) -> None:
        if not isinstance(config, dict):
            raise ValueError(
                "euler_modulo config must be a mapping with 'modulus' "
                "and 'indices' or 'names'"
            )
        if "modulus" not in config:
            raise ValueError("euler_modulo config requires 'modulus'")
        modulus = config["modulus"]
        if not isinstance(modulus, (int, float)) or modulus <= 0:
            raise ValueError(
                f"euler_modulo 'modulus' must be a positive number, got {modulus!r}"
            )
        _validate_targeted_config(config, name="euler_modulo", expect_values=False)

    def transform(
        self, value: Any, config: Any = None, context: Any = None
    ) -> Any:
        if config is None:
            return value
        if value is None:
            return value
        if not isinstance(value, list):
            raise TypeError(
                f"euler_modulo expects a list value, got {type(value).__name__}"
            )

        indices = self._resolve_indices(config, context, len(value))
        modulus = float(config["modulus"])
        result = list(value)
        for i in indices:
            result[i] = result[i] % modulus
        return result

    @staticmethod
    def _resolve_indices(
        config: dict, context: Any, value_len: int
    ) -> list[int]:
        """Resolve which positions to modulo from config."""
        if "indices" in config:
            indices = [int(i) for i in config["indices"]]
            for i in indices:
                if i >= value_len:
                    raise ValueError(
                        f"euler_modulo index {i} out of range "
                        f"(value length={value_len})"
                    )
            return indices

        # names mode
        target_names = set(config["names"])
        names_path = config["names_path"]
        if context is None:
            raise ValueError(
                "euler_modulo with 'names' requires context (source payload)"
            )
        from cloudrobo_r2c.core.internal.helpers import lookup_dotted
        all_names = lookup_dotted(context, names_path)
        if not isinstance(all_names, list):
            raise TypeError(
                f"euler_modulo names_path {names_path!r} "
                f"must resolve to a list, got {type(all_names).__name__}"
            )
        indices = []
        for i, name in enumerate(all_names):
            if name in target_names:
                indices.append(i)
        return indices


DEFAULT_TRANSFORMERS: Dict[str, IValueTransformer] = {
    "identity": IdentityTransformer(),
    "to_float": ToFloatTransformer(),
    "to_int": ToIntTransformer(),
    "to_str": ToStrTransformer(),
    "to_bool": ToBoolTransformer(),
    "to_list": ToListTransformer(),
    "slice": SliceTransformer(),
    "index": IndexTransformer(),
    "subtract": SubtractTransformer(),
    "euler_modulo": EulerModuloTransformer(),
    "array_to_list": ArrayToListTransformer(),
    "list_to_ndarray": ListToNdarrayTransformer(),
    "list_to_ros_float64_multi_array": ListToRosFloat64MultiArrayTransformer(),
    "list_to_ros_joint_state": ListToRosJointStateTransformer(),
    "select_joints_by_name": SelectJointsByNameTransformer(),
    "list_to_ros_move_request": ListToRosMoveRequestTransformer(),
    "scalar_to_step_motor_gripper": ScalarToStepMotorGripperTransformer(),
    "scalar_to_step_motor_gripper_debounce": (
        ScalarToStepMotorGripperDebounceTransformer()
    ),
    "ndarray_to_png": NdarrayToPngTransformer(),
    "ndarray_to_jpeg": NdarrayToJpegTransformer(),
    "ndarray_to_bytes": NdarrayToBytesTransformer(),
    "ndarray_to_webp": NdarrayToWebpTransformer(),
    "ros_image_to_ndarray": RosImageToNdarrayTransformer(),
    "ros_image_to_png": RosImageToPngTransformer(),
    "ros_image_to_jpeg": RosImageToJpegTransformer(),
    "ros_image_to_webp": RosImageToWebpTransformer(),
    "ros_compressed_image_to_ndarray": RosCompressedImageToNdarrayTransformer(),
    "ros_compressed_image_to_png": RosCompressedImageToPngTransformer(),
    "ros_compressed_image_to_jpeg": RosCompressedImageToJpegTransformer(),
    "ros_compressed_image_to_webp": RosCompressedImageToWebpTransformer(),
    "ros_compress_image_to_ndarray": RosCompressedImageToNdarrayTransformer(),
    "ros_compress_image_to_png": RosCompressedImageToPngTransformer(),
    "ros_compress_image_to_jpeg": RosCompressedImageToJpegTransformer(),
    "ros_compress_image_to_webp": RosCompressedImageToWebpTransformer(),
    "ros_message_to_mapping": RosMessageToMappingTransformer(),
    "png_to_ndarray": PngToNdarrayTransformer(),
    "jpeg_to_ndarray": JpegToNdarrayTransformer(),
    "webp_to_ndarray": WebpToNdarrayTransformer(),
    "decode_image": DecodeImageTransformer(),
    "bgr_to_rgb": BgrToRgbTransformer(),
    "rgb_to_bgr": RgbToBgrTransformer(),
    "resize": ResizeTransformer(),
    "format": FormatTransformer(),
    "normalize": NormalizeTransformer(),
    "so101_gripper_value_to_joint": SO101GripperValueToJointTransformer(),
    "so101_gripper_joint_to_value": SO101GripperJointToValueTransformer(),
    "extract_gripper_states": QinglongExtractGripperStatesTransformer(),
    "qinglong_gripper_real_transform": QinglongGripperRealTransform(),
    "qinglong_gripper_visual_transform": QinglongGripperVisualTransform(),
    "moz1_gripper_value_to_joints": Moz1GripperValueToJointsTransformer(),
    "moz1_extract_gripper_states": Moz1ExtractGripperStatesTransformer(),
    "jaka_gripper_value_to_joints": JakaGripperValueToJointsTransformer(),
    "jaka_extract_gripper_state": JakaExtractGripperStateTransformer(),
    "crop": CropTransformer(),
}


_TRANSFORMER_ENTRY_POINT_GROUP = "r2c_sdk.transformers"


class TransformerRegistry:
    """Discover and cache value transformers from builtins + entry_points.

    Builtin transformers (from ``DEFAULT_TRANSFORMERS``) always take
    priority over external entry_point registrations.  When an external
    transformer collides with a builtin name, a warning is logged and the
    builtin is kept.
    """

    _scanned: bool = False
    _entry_points: dict[str, Any] = {}
    _entry_cache: dict[str, IValueTransformer] = {}
    _builtins: dict[str, IValueTransformer] = dict(DEFAULT_TRANSFORMERS)

    @classmethod
    def _ensure_scanned(cls) -> None:
        if cls._scanned:
            return
        cls._scanned = True
        try:
            from importlib.metadata import entry_points as _entry_points

            for ep in _entry_points(group=_TRANSFORMER_ENTRY_POINT_GROUP):
                cls._entry_points[ep.name] = ep
        except Exception:
            logger.debug(
                "Failed to scan entry_point group %r",
                _TRANSFORMER_ENTRY_POINT_GROUP,
                exc_info=True,
            )

    @classmethod
    def _load_entry(cls, name: str) -> IValueTransformer | None:
        ep = cls._entry_points.get(name)
        if ep is None:
            return None
        try:
            transformer_cls = ep.load()
        except Exception:
            logger.warning(
                "Failed to load transformer entry_point %r; skipping.",
                name,
                exc_info=True,
            )
            return None
        if not isinstance(transformer_cls, type) or not issubclass(
            transformer_cls, IValueTransformer
        ):
            logger.warning(
                "Transformer entry_point %r is not a subclass of "
                "IValueTransformer; skipping.",
                name,
            )
            return None
        return transformer_cls()

    @classmethod
    def lookup(cls, name: str) -> IValueTransformer | None:
        """Return transformer instance for *name*, or ``None``."""
        # builtins always win
        builtin = cls._builtins.get(name)
        if builtin is not None:
            return builtin

        cls._ensure_scanned()
        if name in cls._entry_cache:
            return cls._entry_cache[name]

        instance = cls._load_entry(name)
        if instance is not None:
            cls._entry_cache[name] = instance
        return instance

    @classmethod
    def available_transformers(cls) -> list[str]:
        """Return all available transformer names (builtins + entry_points)."""
        cls._ensure_scanned()
        names = set(cls._builtins.keys()) | set(cls._entry_points.keys())
        return sorted(names)

    @classmethod
    def _detect_collisions(cls) -> None:
        """Log a warning for each entry_point that shadows a builtin name."""
        cls._ensure_scanned()
        for name in cls._entry_points:
            if name in cls._builtins:
                logger.warning(
                    "Transformer %r from entry_point is ignored "
                    "because a builtin with the same name exists.",
                    name,
                )


# Trigger collision detection when this module is imported.
_TransformerRegistry = TransformerRegistry
_TransformerRegistry._detect_collisions()


def build_transformer_registry(
    overrides: Mapping[str, IValueTransformer] | None = None,
) -> Dict[str, IValueTransformer]:
    """Return transformer lookup dict: builtins + entry_points + overrides.

    Priority: explicit *overrides* > builtin > entry_point.
    """
    registry = dict(_TransformerRegistry._builtins)
    _TransformerRegistry._ensure_scanned()
    for name in _TransformerRegistry._entry_points:
        if name in registry:
            continue  # builtin wins
        instance = _TransformerRegistry.lookup(name)
        if instance is not None:
            registry[name] = instance
    if overrides:
        registry.update(overrides)
    return registry
