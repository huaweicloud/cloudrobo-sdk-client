"""Command data model (downlink teleop control)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict

from .validators import (
    ValidationError,
    validate_required_fields,
    validate_string,
    validate_type,
)

logger = logging.getLogger(__name__)

try:
    from ..generated import command_pb2
except Exception:
    command_pb2 = None

# Known command types
TELEOP_ENTER = "teleop_enter"
TELEOP_EXIT = "teleop_exit"


@dataclass
class CommandMessage:
    timestamp: int
    command_type: str
    # 数据回流开关（0=正常回流，1=跳过回流；语义由接收方定义）
    skip_data_reflux: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CommandMessage":
        validate_type(data, dict, "data", "CommandMessage")
        validate_required_fields(data, ["timestamp", "command_type"], "CommandMessage")
        if not isinstance(data["timestamp"], int) or isinstance(data["timestamp"], bool):
            raise ValidationError(
                "CommandMessage: Field 'timestamp' must be int (epoch ms), "
                f"got {type(data['timestamp']).__name__}"
            )
        validate_string(data["command_type"], "command_type", "CommandMessage")
        skip_reflux = data.get("skip_data_reflux", 0)
        if not isinstance(skip_reflux, int) or isinstance(skip_reflux, bool):
            raise ValidationError(
                "CommandMessage: Field 'skip_data_reflux' must be int, "
                f"got {type(skip_reflux).__name__}"
            )
        return cls(
            timestamp=data["timestamp"],
            command_type=data["command_type"],
            skip_data_reflux=skip_reflux,
        )

    @classmethod
    def from_protobuf(cls, payload: bytes) -> "CommandMessage":
        if not command_pb2:
            raise ImportError("Protobuf code not generated")

        pb = command_pb2.Command()
        pb.ParseFromString(payload)
        return cls(
            timestamp=pb.timestamp,
            command_type=pb.command_type,
            skip_data_reflux=int(pb.skip_data_reflux),
        )

    def to_protobuf(self) -> Any:
        if not command_pb2:
            raise ImportError("Protobuf code not generated")

        pb = command_pb2.Command()
        pb.timestamp = int(self.timestamp)
        pb.command_type = self.command_type
        pb.skip_data_reflux = int(self.skip_data_reflux)
        return pb

    def serialize(self) -> bytes:
        return self.to_protobuf().SerializeToString()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "command_type": self.command_type,
            "skip_data_reflux": self.skip_data_reflux,
        }
