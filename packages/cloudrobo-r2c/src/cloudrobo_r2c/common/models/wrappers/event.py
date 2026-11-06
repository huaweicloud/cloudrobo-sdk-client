"""Event data model (alarm/event reporting)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .validators import (
    ValidationError,
    validate_list_items_type,
    validate_required_fields,
    validate_string,
    validate_type,
)

logger = logging.getLogger(__name__)

try:
    from ..generated import event_pb2
except ImportError:
    event_pb2 = None


@dataclass
class EventMetadata:
    abstract: str = ""  # 事件摘要（原 code 字段更名，2026-09 schema）
    type: str = ""  # "event" | "alarm"
    severity: str = ""  # Critical | Major | Minor | Info
    content: str = ""
    starts_at: int = 0
    ends_at: int = 0

    @classmethod
    def _safe_int(cls, value: Any, default: int = 0) -> int:
        """安全整型转换：None 或不可转换时回退到默认值。"""
        if value is None or isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EventMetadata":
        validate_type(data, dict, "data", "EventMetadata")
        return cls(
            abstract=data.get("abstract", ""),
            type=data.get("type", ""),
            severity=data.get("severity", ""),
            content=data.get("content", ""),
            starts_at=cls._safe_int(data.get("starts_at"), 0),
            ends_at=cls._safe_int(data.get("ends_at"), 0),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "abstract": self.abstract,
            "type": self.type,
            "severity": self.severity,
            "content": self.content,
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
        }


@dataclass
class EventMessage:
    timestamp: int
    # 数据来源（发布时由 session._ensure_state_source 填充）
    source: str = ""
    # 事件唯一标识（uuid 等，2026-09 schema）
    event_id: str = ""
    metadata: List[EventMetadata] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EventMessage":
        validate_type(data, dict, "data", "EventMessage")
        validate_required_fields(data, ["timestamp"], "EventMessage")
        if isinstance(data["timestamp"], bool) or not isinstance(data["timestamp"], int):
            raise ValidationError(
                "EventMessage: Field 'timestamp' must be int (epoch ms), "
                f"got {type(data['timestamp']).__name__}"
            )

        raw_metadata = data.get("metadata", [])
        if not isinstance(raw_metadata, list):
            raise ValidationError(
                "EventMessage: Field 'metadata' must be a list, "
                f"got {type(raw_metadata).__name__}"
            )

        return cls(
            timestamp=data["timestamp"],
            source=data.get("source", ""),
            event_id=data.get("event_id", ""),
            metadata=[EventMetadata.from_dict(m) for m in raw_metadata],
        )

    @classmethod
    def from_protobuf(cls, payload: bytes) -> "EventMessage":
        if not event_pb2:
            raise ImportError("Protobuf code not generated")

        pb = event_pb2.Event()
        pb.ParseFromString(payload)

        return cls(
            timestamp=pb.timestamp,
            source=pb.source,
            event_id=pb.event_id,
            metadata=[
                EventMetadata(
                    abstract=m.abstract,
                    type=m.type,
                    severity=m.severity,
                    content=m.content,
                    starts_at=m.starts_at,
                    ends_at=m.ends_at,
                )
                for m in pb.metadata
            ],
        )

    def to_protobuf(self) -> Any:
        if not event_pb2:
            raise ImportError("Protobuf code not generated")

        pb = event_pb2.Event()
        pb.source = self.source
        pb.timestamp = int(self.timestamp)
        pb.event_id = self.event_id
        for m in self.metadata:
            pb_meta = pb.metadata.add()
            pb_meta.abstract = m.abstract
            pb_meta.type = m.type
            pb_meta.severity = m.severity
            pb_meta.content = m.content
            pb_meta.starts_at = m.starts_at
            pb_meta.ends_at = m.ends_at
        return pb

    def serialize(self) -> bytes:
        return self.to_protobuf().SerializeToString()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "timestamp": self.timestamp,
            "event_id": self.event_id,
            "metadata": [m.to_dict() for m in self.metadata],
        }
