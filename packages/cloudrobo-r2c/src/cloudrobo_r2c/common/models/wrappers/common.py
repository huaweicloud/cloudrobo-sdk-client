"""Common base data model definitions."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List

import numpy as np

logger = logging.getLogger(__name__)

# ── DType name ↔ proto enum mapping ────────────────────────────────
# Populated lazily on first use to avoid circular import issues.

_DTYPE_NAME_TO_ENUM: Dict[str, int] | None = None
_DTYPE_ENUM_TO_NAME: Dict[int, str] | None = None


def _ensure_dtype_maps() -> None:
    global _DTYPE_NAME_TO_ENUM, _DTYPE_ENUM_TO_NAME
    if _DTYPE_NAME_TO_ENUM is not None:
        return
    try:
        from cloudrobo_r2c.common.models.generated import common_pb2  # type: ignore

        _DTYPE_NAME_TO_ENUM = {
            "FLOAT32": common_pb2.FLOAT32,
            "FLOAT64": common_pb2.FLOAT64,
            "INT32": common_pb2.INT32,
            "INT64": common_pb2.INT64,
            "UINT8": common_pb2.UINT8,
            "BOOL": common_pb2.BOOL,
            "STRING": common_pb2.STRING,
            "BYTES": common_pb2.BYTES,
        }
    except Exception:
        logger.warning("Could not import common_pb2; DType enum mapping unavailable.")
        _DTYPE_NAME_TO_ENUM = {}
    _DTYPE_ENUM_TO_NAME = {v: k for k, v in _DTYPE_NAME_TO_ENUM.items()}


DTYPE_TO_NUMPY: Dict[str, Any] = {
    "FLOAT32": np.float32,
    "FLOAT64": np.float64,
    "INT32": np.int32,
    "INT64": np.int64,
    "UINT8": np.uint8,
    "BOOL": np.bool_,
}

NUMPY_TO_DTYPE: Dict[Any, str] = {
    np.float32: "FLOAT32",
    np.float64: "FLOAT64",
    np.int32: "INT32",
    np.int64: "INT64",
    np.uint8: "UINT8",
    np.bool_: "BOOL",
}

@dataclass
class Pose7D:
    """7-dimensional pose [tx, ty, tz, qx, qy, qz, qw]"""
    data: List[float] = field(default_factory=list)

    def __iter__(self) -> Iterator[float]:
        """Allow direct iteration of data, e.g.: for val in pose"""
        return iter(self.data)

    def __getitem__(self, index: int) -> float:
        """Allow indexed access, e.g.: pose[0]"""
        return self.data[index]

    def __len__(self) -> int:
        return len(self.data)

    def __repr__(self) -> str:
        """Display data list directly when printing, hide class wrapper details"""
        return repr(self.data)

@dataclass
class StampedPose7D:
    """7-dimensional pose with frame name"""
    frame_id: str
    pose: Pose7D

@dataclass
class Vector3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

@dataclass
class Quaternion:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    w: float = 1.0

@dataclass
class PoseWithCovariance:
    pose: Pose7D
    covariance: List[float] = field(default_factory=list)


# ── Extension mechanism ────────────────────────────────────────────


@dataclass
class ExtensionValue:
    """Self-describing extension value for user-defined fields.

    Carries dtype + shape metadata alongside the raw data payload so
    that any consumer can interpret the value without an external
    schema.

    ``dtype`` is a string matching the proto DType enum names:
    ``FLOAT32``, ``FLOAT64``, ``INT32``, ``INT64``, ``UINT8``,
    ``BOOL``, ``STRING``, ``BYTES``.

    ``shape`` follows NumPy semantics: ``[]`` = scalar, ``[N]`` = 1D,
    ``[H, W]`` = 2D, ``[H, W, C]`` = image.

    ``mime_type`` is optional and only meaningful when
    ``dtype == "BYTES"`` (e.g. ``"image/jpeg"``, ``"image/png"``).
    """

    dtype: str = ""
    shape: List[int] = field(default_factory=list)
    data: bytes = b""
    mime_type: str = ""

    # ── factory methods ─────────────────────────────────────────

    @classmethod
    def from_ndarray(cls, arr: "np.ndarray", mime_type: str = "") -> "ExtensionValue":
        """Build from a numpy ndarray, auto-deriving dtype and shape."""
        shape = list(arr.shape)
        np_dtype = arr.dtype.type
        dtype = NUMPY_TO_DTYPE.get(np_dtype)
        if dtype is None:
            raise ValueError(
                f"Unsupported ndarray dtype {arr.dtype}. "
                f"Supported: {list(NUMPY_TO_DTYPE.keys())}"
            )
        return cls(
            dtype=dtype,
            shape=shape,
            data=arr.tobytes(),
            mime_type=mime_type,
        )

    @classmethod
    def from_string(cls, s: str) -> "ExtensionValue":
        """Build from a UTF-8 string."""
        return cls(dtype="STRING", shape=[], data=s.encode("utf-8"))

    @classmethod
    def from_scalar(cls, value: float | int | bool) -> "ExtensionValue":
        """Build from a scalar value."""
        if isinstance(value, bool):
            return cls(
                dtype="BOOL", shape=[],
                data=b"\x01" if value else b"\x00",
            )
        if isinstance(value, int):
            return cls(
                dtype="INT64", shape=[],
                data=value.to_bytes(8, byteorder="little", signed=True),
            )
        if isinstance(value, float):
            import struct
            return cls(
                dtype="FLOAT64", shape=[],
                data=struct.pack("<d", value),
            )
        raise TypeError(f"Unsupported scalar type: {type(value)}")

    # ── consumer helpers ────────────────────────────────────────

    def to_ndarray(self) -> "np.ndarray":
        """Decode data into a numpy ndarray using dtype + shape.

        Returns a scalar for ``shape == []``.
        """
        np_dtype = DTYPE_TO_NUMPY.get(self.dtype)
        if np_dtype is None:
            raise ValueError(
                f"Cannot decode dtype={self.dtype!r} as ndarray. "
                f"Use .data directly for STRING / BYTES types."
            )
        return np.frombuffer(self.data, dtype=np_dtype).reshape(self.shape)

    def to_string(self) -> str:
        """Decode STRING-typed data as UTF-8."""
        if self.dtype != "STRING":
            raise ValueError(
                f"to_string() requires dtype='STRING', got {self.dtype!r}"
            )
        return self.data.decode("utf-8")

    def to_scalar(self) -> int | float | bool:
        """Decode a scalar extension value."""
        if self.shape:
            raise ValueError(
                f"to_scalar() requires shape=[], got shape={self.shape}"
            )
        if self.dtype == "BOOL":
            return self.data != b"\x00"
        if self.dtype == "INT64":
            return int.from_bytes(self.data, byteorder="little", signed=True)
        if self.dtype == "FLOAT64":
            import struct
            return struct.unpack("<d", self.data)[0]
        if self.dtype == "FLOAT32":
            import struct
            return struct.unpack("<f", self.data)[0]
        if self.dtype == "INT32":
            return int.from_bytes(self.data, byteorder="little", signed=True)
        if self.dtype == "UINT8":
            return int.from_bytes(self.data, byteorder="little", signed=False)
        raise ValueError(
            f"to_scalar() unsupported for dtype={self.dtype!r}"
        )

    # ── proto helpers (used by to_protobuf / from_pb_object) ───

    DType = None  # set lazily to common_pb2.DType on first use

    def to_proto_dtype(self) -> int:
        _ensure_dtype_maps()
        val = _DTYPE_NAME_TO_ENUM.get(self.dtype)  # type: ignore
        if val is None:
            raise ValueError(f"Unknown ExtensionValue dtype: {self.dtype!r}")
        return val

    @classmethod
    def from_proto_dtype(cls, pb_dtype: int) -> str:
        _ensure_dtype_maps()
        name = _DTYPE_ENUM_TO_NAME.get(pb_dtype)  # type: ignore
        if name is None:
            raise ValueError(f"Unknown proto DType enum value: {pb_dtype}")
        return name

    @classmethod
    def from_proto_extensions(
        cls, pb_extensions: Any
    ) -> Dict[str, "ExtensionValue"]:
        """Convert proto ``map<string, ExtensionValue>`` to Python dict."""
        result: Dict[str, ExtensionValue] = {}
        for key, pb_ev in pb_extensions.items():
            result[key] = cls(
                dtype=cls.from_proto_dtype(pb_ev.dtype),
                shape=list(pb_ev.shape),
                data=pb_ev.data,
                mime_type=pb_ev.mime_type,
            )
        return result
