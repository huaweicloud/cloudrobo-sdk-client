"""Robot action/command data model definitions."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List

from cloudrobo_r2c.common.utils import BytesEncoder
from .common import Pose7D, ExtensionValue
from .validators import (
    ValidationError, validate_required_fields, validate_type, validate_timestamp,
    validate_list_items_type, validate_pose7d, validate_dict_field
)

logger = logging.getLogger(__name__)

# Try to import generated Protobuf code
try:
    from ..generated import action_pb2
except Exception:
    action_pb2 = None


# Note: JointStateStep has been hidden, no longer exposed to user API
# But conversion logic still needs it internally (corresponds to message JointStateStep in proto)

@dataclass
class JointAction:
    names: List[str] = field(default_factory=list)
    # Changed to 2D list List[List[float]]
    position: List[List[float]] = field(default_factory=list)
    velocity: List[List[float]] = field(default_factory=list)
    torque: List[List[float]] = field(default_factory=list)


@dataclass
class EndEffectorPoseChunk:
    # Changed to 2D list List[List[float]]
    pose: List[Pose7D] = field(default_factory=list)


@dataclass
class EndEffectorStateAction:
    names: List[str] = field(default_factory=list)
    # 2D list
    position: List[List[float]] = field(default_factory=list)
    velocity: List[List[float]] = field(default_factory=list)
    torque: List[List[float]] = field(default_factory=list)


@dataclass
class LocalizationAction:
    odom_pose: List[Pose7D] = field(default_factory=list)
    map_pose: List[Pose7D] = field(default_factory=list)


def _parse_action_extensions(data: Dict[str, Any]) -> Dict[str, ExtensionValue]:
    """Parse extensions dict for Actions (reuses observations._parse_extensions logic)."""
    from .observations import _parse_extensions
    return _parse_extensions(data, _context="Actions")


@dataclass
class Actions:
    """Batch action commands from cloud (Actions)."""
    timestamp: int
    id: int = 0
    chunk_size: int = 1
    joint_states: JointAction = field(default_factory=lambda: JointAction([], [], [], []))
    end_effector_poses: Dict[str, EndEffectorPoseChunk] = field(default_factory=dict)
    end_effector_states: EndEffectorStateAction = field(default_factory=lambda: EndEffectorStateAction([], [], [], []))
    localization: LocalizationAction = field(default_factory=lambda: LocalizationAction([], []))
    extensions: Dict[str, "ExtensionValue"] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Actions:
        """Construct Actions object from dict."""
        validate_type(data, dict, 'data', 'Actions')
        validate_required_fields(data, ['timestamp'], 'Actions')
        validate_timestamp(data['timestamp'], 'timestamp', 'Actions')
        if 'id' in data:
            validate_type(data['id'], int, 'id', 'Actions')
        if 'chunk_size' in data:
            validate_type(data['chunk_size'], int, 'chunk_size', 'Actions')

        joint = data.get('joint_states', {}) or {}
        validate_type(joint, dict, 'joint_states', 'Actions')
        validate_list_items_type(joint.get('names', []), str, 'joint_states.names', 'Actions')

        ee_pose_data = data.get('end_effector_poses', {}) or {}
        ee_state_data = data.get('end_effector_states', {}) or {}
        loc_data = data.get('localization', {}) or {}

        joint_states = JointAction(
            names=joint.get('names', []),
            position=joint.get('position', []),
            velocity=joint.get('velocity', []),
            torque=joint.get('torque', []),
        )
        end_effector_poses = {
            name: EndEffectorPoseChunk(pose=[validate_pose7d(p, f'end_effector_poses.{name}.pose', 'Actions') for p in payload.get('pose', [])])
            for name, payload in ee_pose_data.items()
        }
        end_effector_states = EndEffectorStateAction(
            names=ee_state_data.get('names', []),
            position=ee_state_data.get('position', []),
            velocity=ee_state_data.get('velocity', []),
            torque=ee_state_data.get('torque', []),
        )
        localization = LocalizationAction(
            odom_pose=[validate_pose7d(p, 'localization.odom_pose', 'Actions') for p in loc_data.get('odom_pose', [])],
            map_pose=[validate_pose7d(p, 'localization.map_pose', 'Actions') for p in loc_data.get('map_pose', [])],
        )
        extensions = _parse_action_extensions(data.get('extensions', {}))
        return cls(
            timestamp=data['timestamp'],
            id=data.get('id', 0),
            chunk_size=data.get('chunk_size', 1),
            joint_states=joint_states,
            end_effector_poses=end_effector_poses,
            end_effector_states=end_effector_states,
            localization=localization,
            extensions=extensions,
        )

    @classmethod
    def from_protobuf(cls, payload: bytes) -> Actions:
        """Parse binary Protobuf data into Actions object."""
        if not action_pb2:
            raise ImportError("Protobuf code not generated, please run protoc to compile action.proto")
        
        pb = action_pb2.Actions()
        pb.ParseFromString(payload)
        
        # Convert JointAction (flatten)
        # Proto update: JointStateStep.data (originally values)
        def _extract_steps(pb_steps):
            # Directly return list of float lists
            return [list(step.data) for step in pb_steps]

        ja = JointAction(
            names=list(pb.joint_states.names),
            position=_extract_steps(pb.joint_states.position),
            velocity=_extract_steps(pb.joint_states.velocity),
            torque=_extract_steps(pb.joint_states.torque),
        )

        # Convert EndEffectorPoses
        ee_poses = {}
        for k, v in pb.end_effector_poses.items():
            # Still use Pose7D, because it has been enhanced
            pose_list = [Pose7D(list(p.data)) for p in v.pose]
            ee_poses[k] = EndEffectorPoseChunk(pose=pose_list)

        # Convert EndEffectorStates
        ees = EndEffectorStateAction(
            names=list(pb.end_effector_states.names),
            position=_extract_steps(pb.end_effector_states.position),
            velocity=_extract_steps(pb.end_effector_states.velocity),
            torque=_extract_steps(pb.end_effector_states.torque),
        )

        # Convert Localization
        loc = LocalizationAction(
            odom_pose=[Pose7D(list(p.data)) for p in pb.localization.odom_pose],
            map_pose=[Pose7D(list(p.data)) for p in pb.localization.map_pose],
        )

        return cls(
            timestamp=pb.timestamp,
            id=pb.id,
            chunk_size=pb.chunk_size,
            joint_states=ja,
            end_effector_poses=ee_poses,
            end_effector_states=ees,
            localization=loc,
            extensions=ExtensionValue.from_proto_extensions(pb.extensions),
        )

    def to_protobuf(self) -> Any:
        """Convert to Protobuf message object (for simulated sending)."""
        if not action_pb2:
            raise ImportError("Protobuf code not generated")

        pb = action_pb2.Actions()
        pb.timestamp = self.timestamp
        pb.id = self.id
        pb.chunk_size = self.chunk_size

        pb.joint_states.names.extend(self.joint_states.names)
        for field_name in ('position', 'velocity', 'torque'):
            container = getattr(pb.joint_states, field_name)
            for step_vals in getattr(self.joint_states, field_name):
                container.add().data.extend(step_vals)

        for key, value in self.end_effector_poses.items():
            for pose in value.pose:
                pb.end_effector_poses[key].pose.add().data.extend(pose.data if hasattr(pose, 'data') else pose)

        pb.end_effector_states.names.extend(self.end_effector_states.names)
        for field_name in ('position', 'velocity', 'torque'):
            container = getattr(pb.end_effector_states, field_name)
            for step_vals in getattr(self.end_effector_states, field_name):
                container.add().data.extend(step_vals)

        for field_name in ('odom_pose', 'map_pose'):
            container = getattr(pb.localization, field_name)
            for pose in getattr(self.localization, field_name):
                container.add().data.extend(pose.data if hasattr(pose, 'data') else pose)
        # ── extensions ─────────────────────────────────────────
        for key, ev in self.extensions.items():
            ev_pb = pb.extensions[key]
            ev_pb.dtype = ev.to_proto_dtype()
            ev_pb.shape.extend(ev.shape)
            ev_pb.data = ev.data
            ev_pb.mime_type = ev.mime_type
        return pb

    def serialize(self) -> bytes:
        return self.to_protobuf().SerializeToString()
