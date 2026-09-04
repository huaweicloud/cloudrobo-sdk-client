"""Joint state data model definitions."""

from __future__ import annotations

import logging
from google.protobuf.timestamp_pb2 import Timestamp
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .validators import (
    validate_optional_list_field,
    normalize_proto_timestamp,
    validate_type,
)

logger = logging.getLogger(__name__)

try:
    from ..generated import joint_state_pb2
except Exception:
    joint_state_pb2 = None


@dataclass
class JointObservation:
    names: List[str] = field(default_factory=list)
    status: List[str] = field(default_factory=list)
    position: List[float] = field(default_factory=list)
    velocity: List[float] = field(default_factory=list)
    acceleration: List[float] = field(default_factory=list)
    temperature: List[float] = field(default_factory=list)
    effort: List[float] = field(default_factory=list)
    motor_current: List[float] = field(default_factory=list)


@dataclass
class JointInstruction:
    target_position: List[float] = field(default_factory=list)
    target_velocity: List[float] = field(default_factory=list)
    target_effort: List[float] = field(default_factory=list)


@dataclass
class JointStateMessage:
    """Detailed joint state message (JointStateMessage)."""

    timestamp: Timestamp
    source: str = ""
    observation: JointObservation = field(default_factory=JointObservation)
    instruction: JointInstruction = field(default_factory=JointInstruction)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> JointStateMessage:
        """Construct JointStateMessage object from dict."""
        validate_type(data, dict, "data", "JointStateMessage")

        timestamp = data.get("timestamp")
        timestamp = normalize_proto_timestamp(
            timestamp, "timestamp", "JointStateMessage"
        )
        source = data.get("source", "")
        if source:
            validate_type(source, str, "source", "JointStateMessage")

        observation = cls._parse_observation(data.get("observation", {}))
        instruction = cls._parse_instruction(data.get("instruction", {}))
        return cls(
            timestamp=timestamp,
            source=source,
            observation=observation,
            instruction=instruction,
        )

    @staticmethod
    def _parse_observation(obs_data: Dict[str, Any]) -> JointObservation:
        if not obs_data:
            return JointObservation()

        validate_type(obs_data, dict, "observation", "JointStateMessage")
        fields = {
            k: validate_optional_list_field(obs_data, k, "JointStateMessage")
            for k in ["names", "position", "velocity", "effort"]
        }
        return JointObservation(**fields)

    @staticmethod
    def _parse_instruction(inst_data: Dict[str, Any]) -> JointInstruction:
        if not inst_data:
            return JointInstruction()

        validate_type(inst_data, dict, "instruction", "JointStateMessage")
        fields = {
            k: validate_optional_list_field(inst_data, k, "JointStateMessage")
            for k in ["target_position", "target_velocity", "target_effort"]
        }
        return JointInstruction(**fields)

    @classmethod
    def from_protobuf(cls, payload: bytes) -> JointStateMessage:
        if not joint_state_pb2:
            raise ImportError("Protobuf code not generated")

        pb = joint_state_pb2.JointStateMessage()
        pb.ParseFromString(payload)

        obs = JointObservation(
            names=list(pb.observation.names),
            status=list(pb.observation.status),
            position=list(pb.observation.position),
            velocity=list(pb.observation.velocity),
            acceleration=list(pb.observation.acceleration),
            temperature=list(pb.observation.temperature),
            effort=list(pb.observation.effort),
            motor_current=list(pb.observation.motor_current),
        )

        instr = JointInstruction(
            target_position=list(pb.instruction.target_position),
            target_velocity=list(pb.instruction.target_velocity),
            target_effort=list(pb.instruction.target_effort),
        )

        return cls(
            timestamp=normalize_proto_timestamp(
                pb.timestamp, "timestamp", "JointStateMessage"
            ),
            source=getattr(pb, "source", ""),
            observation=obs,
            instruction=instr,
        )

    def to_protobuf(self) -> Any:
        if not joint_state_pb2:
            raise ImportError("Protobuf code not generated")

        pb = joint_state_pb2.JointStateMessage()
        pb.timestamp.CopyFrom(
            normalize_proto_timestamp(self.timestamp, "timestamp", "JointStateMessage")
        )
        if hasattr(pb, "source"):
            pb.source = self.source

        if self.observation:
            pb.observation.names.extend(self.observation.names)
            pb.observation.status.extend(self.observation.status)
            pb.observation.position.extend(self.observation.position)
            pb.observation.velocity.extend(self.observation.velocity)
            pb.observation.acceleration.extend(self.observation.acceleration)
            pb.observation.temperature.extend(self.observation.temperature)
            pb.observation.effort.extend(self.observation.effort)
            pb.observation.motor_current.extend(self.observation.motor_current)

        if self.instruction:
            pb.instruction.target_position.extend(self.instruction.target_position)
            pb.instruction.target_velocity.extend(self.instruction.target_velocity)
            pb.instruction.target_effort.extend(self.instruction.target_effort)

        return pb

    def serialize(self) -> bytes:
        return self.to_protobuf().SerializeToString()
