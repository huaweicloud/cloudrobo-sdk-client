"""Safe binary serialization for observation recordings.

Replaces pickle with a custom format that carries zero code-execution risk:

    [R2CR][version:4][frame_count:4][index...][frame_data...]

Only JSON (structure) and raw bytes (numpy arrays / binary blobs) are used.
"""

from __future__ import annotations

import json
import struct
from typing import Any, BinaryIO, Dict, List, Optional

import numpy as np

MAGIC = b"R2CR"
VERSION: int = 1
INDEX_ENTRY_STRUCT = struct.Struct("<QII")  # meta_offset:8, meta_len:4, total_len:4


# ---------------------------------------------------------------------------
#  Serialize
# ---------------------------------------------------------------------------

_ARRAY_T = "a"
_SCALAR_T = "s"
_BYTES_T = "b"
_LIST_T = "l"


def _offset_block_indices(desc: dict, offset: int) -> None:
    """Recursively add *offset* to every ``blk`` field in a meta descriptor tree."""
    if _ARRAY_T in desc:
        desc[_ARRAY_T]["blk"] += offset
    elif _BYTES_T in desc:
        desc[_BYTES_T]["blk"] += offset
    elif _LIST_T in desc:
        for item in desc[_LIST_T]:
            _offset_block_indices(item, offset)


def _flatten_frame(frame: Dict[str, Any], prefix: str = "") -> tuple[dict, List[bytes]]:
    """Flatten a nested dict into dotted-key meta entries + a list of binary data blocks."""
    meta: dict = {}
    data_blocks: List[bytes] = []

    for key, value in frame.items():
        full_key = f"{prefix}.{key}" if prefix else key

        if isinstance(value, np.ndarray):
            data_blocks.append(value.tobytes())
            meta[full_key] = {
                _ARRAY_T: {
                    "dtype": str(value.dtype),
                    "shape": list(value.shape),
                    "blk": len(data_blocks) - 1,
                }
            }
        elif isinstance(value, bytes):
            data_blocks.append(value)
            meta[full_key] = {_BYTES_T: {"blk": len(data_blocks) - 1}}
        elif isinstance(value, dict):
            sub_meta, sub_blocks = _flatten_frame(value, full_key)
            offset = len(data_blocks)
            for v in sub_meta.values():
                _offset_block_indices(v, offset)
            meta.update(sub_meta)
            data_blocks.extend(sub_blocks)
        elif isinstance(value, (list, tuple)):
            # Handle list of scalars (e.g. [1, 2, 3])
            # Check for nested arrays inside list — if so, flatten
            has_arrays = any(isinstance(v, np.ndarray) for v in value)
            has_bytes = any(isinstance(v, bytes) for v in value)
            if has_arrays or has_bytes:
                sub_list: list = []
                for item in value:
                    if isinstance(item, np.ndarray):
                        data_blocks.append(item.tobytes())
                        sub_list.append(
                            {
                                _ARRAY_T: {
                                    "dtype": str(item.dtype),
                                    "shape": list(item.shape),
                                    "blk": len(data_blocks) - 1,
                                }
                            }
                        )
                    elif isinstance(item, bytes):
                        data_blocks.append(item)
                        sub_list.append({_BYTES_T: {"blk": len(data_blocks) - 1}})
                    else:
                        sub_list.append({_SCALAR_T: item})
                meta[full_key] = {_LIST_T: sub_list}
            else:
                meta[full_key] = {_LIST_T: [{_SCALAR_T: v} for v in value]}
        else:
            meta[full_key] = {_SCALAR_T: value}

    return meta, data_blocks


def save_frames(file_path: str, frames: List[Dict[str, Any]]) -> None:
    """Save a list of observation frame dicts to a safe binary file."""
    frame_count = len(frames)

    # ---- Pass 1: serialize every frame in memory, build index ----
    index: List[tuple[int, int, int]] = []  # (offset, meta_len, total_len)
    frame_blobs: List[bytes] = []

    for frame in frames:
        meta, data_blocks = _flatten_frame(frame)
        meta_bytes = json.dumps(meta, ensure_ascii=False).encode("utf-8")

        # frame layout: meta_len(4) + meta_bytes + blk_count(4) + [blk_len(4)+blk_bytes]*
        header = struct.pack("<I", len(meta_bytes))
        header += meta_bytes
        header += struct.pack("<I", len(data_blocks))
        for blk in data_blocks:
            header += struct.pack("<I", len(blk))
        payload = header + b"".join(data_blocks)

        total_len = len(payload)
        meta_len = len(meta_bytes)
        frame_blobs.append(payload)
        index.append((0, meta_len, total_len))  # offset filled later

    # ---- Pass 2: write to disk ----
    with open(file_path, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<I", VERSION))
        f.write(struct.pack("<I", frame_count))

        index_start = f.tell()
        f.write(b"\x00" * (frame_count * INDEX_ENTRY_STRUCT.size))

        for i, blob in enumerate(frame_blobs):
            offset = f.tell()
            meta_len = index[i][1]
            total_len = index[i][2]
            index[i] = (offset, meta_len, total_len)
            f.write(blob)

        # Rewrite index
        f.seek(index_start)
        for offset, meta_len, total_len in index:
            f.write(INDEX_ENTRY_STRUCT.pack(offset, meta_len, total_len))


# ---------------------------------------------------------------------------
#  Deserialize
# ---------------------------------------------------------------------------


def _unflatten_frame(meta: dict, reader: _FrameReader) -> Dict[str, Any]:
    """Rebuild a nested dict from flat meta entries."""
    result: Dict[str, Any] = {}
    for key, desc in sorted(meta.items()):
        value = _resolve_value(desc, reader)
        _set_nested(result, key.split("."), value)
    return result


def _resolve_value(desc: dict, reader: _FrameReader) -> Any:
    """Resolve a meta descriptor to a Python value."""
    if _SCALAR_T in desc:
        return desc[_SCALAR_T]
    if _ARRAY_T in desc:
        info = desc[_ARRAY_T]
        raw = reader.read_block(info["blk"])
        return np.frombuffer(raw, dtype=np.dtype(info["dtype"])).reshape(info["shape"])
    if _BYTES_T in desc:
        return reader.read_block(desc[_BYTES_T]["blk"])
    if _LIST_T in desc:
        return [_resolve_value(item, reader) for item in desc[_LIST_T]]
    return desc


def _set_nested(d: Dict[str, Any], keys: List[str], value: Any) -> None:
    """Set a value at a dotted key path, creating intermediate dicts as needed."""
    *parents, last = keys
    for key in parents:
        if key not in d:
            d[key] = {}
        d = d[key]
    d[last] = value


class _FrameReader:
    """Read data blocks from a single frame's raw region."""

    def __init__(self, buf: bytes, offset: int, block_offsets: List[int]):
        self._buf = buf
        self._offset = offset
        self._block_offsets = block_offsets

    def read_block(self, index: int) -> bytes:
        start = self._offset + self._block_offsets[index]
        end = (
            self._offset + self._block_offsets[index + 1]
            if index + 1 < len(self._block_offsets)
            else len(self._buf)
        )
        return self._buf[start:end]


def load_frames(file_path: str) -> List[Dict[str, Any]]:
    """Load observation frames from a safe binary file.

    Only JSON and raw bytes are parsed — no code execution is possible.
    """
    with open(file_path, "rb") as f:
        magic = f.read(4)
        if magic != MAGIC:
            raise ValueError(
                f"Not a valid R2C recording file (expected magic {MAGIC!r}, got {magic!r})"
            )

        version = struct.unpack("<I", f.read(4))[0]
        if version != VERSION:
            raise ValueError(
                f"Unsupported recording version: {version} (expected {VERSION})"
            )

        frame_count = struct.unpack("<I", f.read(4))[0]
        index: List[tuple[int, int, int]] = []
        for _ in range(frame_count):
            entry = INDEX_ENTRY_STRUCT.unpack(f.read(INDEX_ENTRY_STRUCT.size))
            index.append(entry)

        frames: List[Dict[str, Any]] = []
        for meta_offset, meta_len, total_len in index:
            f.seek(meta_offset)

            actual_meta_len = struct.unpack("<I", f.read(4))[0]
            meta_bytes = f.read(actual_meta_len)
            meta = json.loads(meta_bytes.decode("utf-8"))

            blk_count = struct.unpack("<I", f.read(4))[0]
            block_lens: List[int] = []
            for _ in range(blk_count):
                block_lens.append(struct.unpack("<I", f.read(4))[0])

            # Calculate block offsets relative to first block start
            block_start = f.tell()
            block_offsets: List[int] = [0]
            acc = 0
            for bl in block_lens[:-1]:
                acc += bl
                block_offsets.append(acc)

            # Read the raw buffer for all blocks in this frame
            total_block_bytes = sum(block_lens)
            raw_buf = f.read(total_block_bytes)

            reader = _FrameReader(raw_buf, 0, block_offsets)
            frame = _unflatten_frame(meta, reader)
            frames.append(frame)

    return frames


def load_frame_meta_only(file_path: str, frame_index: int = 0) -> Optional[dict]:
    """Load only the structure metadata of a single frame (no array data)."""
    with open(file_path, "rb") as f:
        magic = f.read(4)
        if magic != MAGIC:
            return None

        version = struct.unpack("<I", f.read(4))[0]
        if version != VERSION:
            return None

        frame_count = struct.unpack("<I", f.read(4))[0]
        if frame_index < 0 or frame_index >= frame_count:
            return None

        # Read just the target index entry
        f.seek(4 + 4 + 4 + frame_index * INDEX_ENTRY_STRUCT.size)
        meta_offset, _meta_len, _total_len = INDEX_ENTRY_STRUCT.unpack(
            f.read(INDEX_ENTRY_STRUCT.size)
        )

        f.seek(meta_offset)
        actual_meta_len = struct.unpack("<I", f.read(4))[0]
        meta_bytes = f.read(actual_meta_len)
        return json.loads(meta_bytes.decode("utf-8"))
