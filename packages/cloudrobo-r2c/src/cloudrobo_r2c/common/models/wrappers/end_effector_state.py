"""End-effector state model definitions."""

from __future__ import annotations

import logging
from google.protobuf.timestamp_pb2 import Timestamp
from dataclasses import dataclass, field
from typing import List, Any, Dict

from .common import Pose7D
from .validators import (
    ValidationError,
    validate_list_items_type,
    validate_optional_list_field,
    validate_pose7d,
    validate_required_fields,
    validate_string,
    normalize_proto_timestamp,
    validate_type,
)

logger = logging.getLogger(__name__)

try:
    from ..generated import end_effector_state_pb2
except Exception:
    end_effector_state_pb2 = None


@dataclass
class EndEffectorState:
    timestamp: Timestamp
    name: str
    source: str = ""
    # Pose7D (wrapper around list)
    pose: Pose7D = field(default_factory=Pose7D)
    state: List[float] = field(default_factory=list)
    velocity: List[float] = field(default_factory=list)
    acceleration: List[float] = field(default_factory=list)
    force: List[float] = field(default_factory=list)
    tactile: List[float] = field(default_factory=list)
    category: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EndEffectorState:
        """Construct EndEffectorState object from dict.

        Args:
            data: Dictionary containing end-effector state data with required fields:
                - timestamp (int, required): Timestamp in milliseconds
                - name (str, required): End-effector name
                - pose (list, optional): Pose7D [x,y,z,qx,qy,qz,qw]
                - state (list, optional): State values
                - velocity (list, optional): Velocity values
                - acceleration (list, optional): Acceleration values
                - force (list, optional): Force values
                - tactile (list, optional): Tactile sensor values
                - category (str, optional): Category string

        Raises:
            ValidationError: If input data is invalid
        """
        validate_type(data, dict, "data", "EndEffectorState")
        validate_required_fields(data, ["timestamp", "name"], "EndEffectorState")

        timestamp = data["timestamp"]
        timestamp = normalize_proto_timestamp(
            timestamp, "timestamp", "EndEffectorState"
        )

        name = data["name"]
        validate_string(name, "name", "EndEffectorState")

        # Validate pose (Pose7D - 7 elements)
        pose = data.get("pose", [])
        if pose:
            pose = validate_pose7d(pose, "pose", "EndEffectorState")

        # Validate list fields if present
        for f in ["state", "velocity", "acceleration", "force", "tactile"]:
            values = validate_optional_list_field(data, f, "EndEffectorState")
            validate_list_items_type(values, (int, float), f, "EndEffectorState")

        # Validate category
        category = data.get("category", "")
        if category:
            validate_string(category, "category", "EndEffectorState")

        source = data.get("source", "")
        if source:
            validate_string(source, "source", "EndEffectorState")

        return cls(
            timestamp=timestamp,
            source=source,
            name=name,
            pose=Pose7D(pose),
            state=data.get("state", []),
            velocity=data.get("velocity", []),
            acceleration=data.get("acceleration", []),
            force=data.get("force", []),
            tactile=data.get("tactile", []),
            category=category,
        )

    @classmethod
    def from_protobuf(cls, payload: bytes) -> EndEffectorState:
        if not end_effector_state_pb2:
            raise ImportError("Protobuf code not generated")

        pb = end_effector_state_pb2.EndEffectorState()
        pb.ParseFromString(payload)

        pose_obj = Pose7D(list(pb.pose.data))

        return cls(
            timestamp=normalize_proto_timestamp(
                pb.timestamp, "timestamp", "EndEffectorState"
            ),
            source=getattr(pb, "source", ""),
            name=pb.name,
            pose=pose_obj,
            state=list(pb.state),
            velocity=list(pb.velocity),
            acceleration=list(pb.acceleration),
            force=list(pb.force),
            tactile=list(pb.tactile),
            category=pb.category,
        )

    def to_protobuf(self) -> Any:
        if not end_effector_state_pb2:
            raise ImportError("Protobuf code not generated")

        pb = end_effector_state_pb2.EndEffectorState()
        pb.timestamp.CopyFrom(
            normalize_proto_timestamp(self.timestamp, "timestamp", "EndEffectorState")
        )
        if hasattr(pb, "source"):
            pb.source = self.source
        pb.name = self.name

        if self.pose:
            data = self.pose.data if hasattr(self.pose, "data") else self.pose
            pb.pose.data.extend(data)

        pb.state.extend(self.state)
        pb.velocity.extend(self.velocity)
        pb.acceleration.extend(self.acceleration)
        pb.force.extend(self.force)
        pb.tactile.extend(self.tactile)
        pb.category = self.category

        return pb

    def serialize(self) -> bytes:
        return self.to_protobuf().SerializeToString()
