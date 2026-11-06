"""RunTime data model (robot state upload).

Dict organization mirrors the observation-style structures:
    joint_states:      {names, position, velocity, torque}
    end_effector_poses: {names, pose: [[x,y,z,qw,qx,qy,qz], ...]}
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .validators import (
    ValidationError,
    validate_required_fields,
    validate_type,
)

logger = logging.getLogger(__name__)

try:
    from ..generated import current_state_pb2 as runtime_pb2
except Exception:
    runtime_pb2 = None

_JOINT_KEYS = ("names", "position", "velocity", "torque")

# 内部标识默认值（type 为 repeated，序列化时自动填充）
TYPE_JOINT_STATES = ["joint_states"]


def _ee_types(names: List[str]) -> List[str]:
    """EE 编号：与 names 等长（EE1, EE2, ...）。"""
    return [f"EE{i + 1}" for i in range(len(names))]


@dataclass
class RuntimeJointStatesMessage:
    """Vector-style joint states (mirror of observation JointStates + 扩展)."""

    type: List[str] = field(default_factory=lambda: list(TYPE_JOINT_STATES))
    names: List[str] = field(default_factory=list)
    position: List[float] = field(default_factory=list)
    velocity: List[float] = field(default_factory=list)
    torque: List[float] = field(default_factory=list)
    acceleration: List[float] = field(default_factory=list)
    temperature: List[float] = field(default_factory=list)
    current: List[float] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuntimeJointStatesMessage":
        validate_type(data, dict, "data", "RuntimeJointStatesMessage")
        return cls(
            type=[str(v) for v in (data.get("type") or TYPE_JOINT_STATES)],
            names=[str(v) for v in data.get("names", [])],
            position=[float(v) for v in data.get("position", [])],
            velocity=[float(v) for v in data.get("velocity", [])],
            torque=[float(v) for v in data.get("torque", [])],
            acceleration=[float(v) for v in data.get("acceleration", [])],
            temperature=[float(v) for v in data.get("temperature", [])],
            current=[float(v) for v in data.get("current", [])],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": list(self.type or TYPE_JOINT_STATES),
            "names": list(self.names),
            "position": list(self.position),
            "velocity": list(self.velocity),
            "torque": list(self.torque),
            "acceleration": list(self.acceleration),
            "temperature": list(self.temperature),
            "current": list(self.current),
        }


@dataclass
class RuntimeEndEffectorStatesMessage:
    """Vector-style end-effector states (mirror of observation EndEffectorStates)."""

    type: List[str] = field(default_factory=list)
    names: List[str] = field(default_factory=list)
    position: List[float] = field(default_factory=list)
    force: List[float] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuntimeEndEffectorStatesMessage":
        validate_type(data, dict, "data", "RuntimeEndEffectorStatesMessage")
        return cls(
            type=[str(v) for v in data.get("type") or []],
            names=[str(v) for v in data.get("names", [])],
            position=[float(v) for v in data.get("position", [])],
            force=[float(v) for v in data.get("force", [])],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": list(self.type or _ee_types(self.names)),
            "names": list(self.names),
            "position": list(self.position),
            "force": list(self.force),
        }


@dataclass
class RuntimeEndEffectorPosesMessage:
    """Vector-style end-effector poses (mirror of observation EndEffectorPoses + 扩展)."""

    type: List[str] = field(default_factory=list)
    names: List[str] = field(default_factory=list)
    pose: List[List[float]] = field(default_factory=list)
    velocity: List[List[float]] = field(default_factory=list)      # RuntimePose 列表（维度不限）
    acceleration: List[List[float]] = field(default_factory=list)  # 同上
    effort: List[List[float]] = field(default_factory=list)        # 同上

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuntimeEndEffectorPosesMessage":
        validate_type(data, dict, "data", "RuntimeEndEffectorPosesMessage")
        poses = [[float(v) for v in p] for p in data.get("pose", [])]
        return cls(
            type=[str(v) for v in data.get("type") or []],
            names=[str(v) for v in data.get("names", [])],
            pose=poses,
            velocity=[[float(v) for v in p] for p in data.get("velocity", [])],
            acceleration=[[float(v) for v in p] for p in data.get("acceleration", [])],
            effort=[[float(v) for v in p] for p in data.get("effort", [])],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": list(self.type or _ee_types(self.names)),
            "names": list(self.names),
            "pose": [list(p) for p in self.pose],
            "velocity": list(self.velocity),
            "acceleration": list(self.acceleration),
            "effort": list(self.effort),
        }


@dataclass
class RunTimeMessage:
    timestamp: int
    location: str = ""
    # 数据来源（发布时由 session._ensure_state_source 填充）
    source: str = ""
    joint_states: RuntimeJointStatesMessage = field(
        default_factory=RuntimeJointStatesMessage
    )
    end_effector_states: RuntimeEndEffectorStatesMessage = field(
        default_factory=RuntimeEndEffectorStatesMessage
    )
    end_effector_poses: RuntimeEndEffectorPosesMessage = field(
        default_factory=RuntimeEndEffectorPosesMessage
    )

    def __post_init__(self) -> None:
        # 允许直接传 dict 构造（自动转子消息）
        if isinstance(self.joint_states, dict):
            self.joint_states = RuntimeJointStatesMessage.from_dict(self.joint_states)
        if isinstance(self.end_effector_states, dict):
            self.end_effector_states = RuntimeEndEffectorStatesMessage.from_dict(
                self.end_effector_states
            )
        if isinstance(self.end_effector_poses, dict):
            self.end_effector_poses = RuntimeEndEffectorPosesMessage.from_dict(
                self.end_effector_poses
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RunTimeMessage":
        validate_type(data, dict, "data", "RunTimeMessage")
        validate_required_fields(data, ["timestamp"], "RunTimeMessage")
        if not isinstance(data["timestamp"], int):
            raise ValidationError(
                "RunTimeMessage: Field 'timestamp' must be int (epoch ms), "
                f"got {type(data['timestamp']).__name__}"
            )

        joint_raw = data.get("joint_states", {}) or {}
        ee_states_raw = data.get("end_effector_states", {}) or {}
        poses_raw = data.get("end_effector_poses", {}) or {}
        if not isinstance(joint_raw, dict) or not isinstance(poses_raw, dict) \
                or not isinstance(ee_states_raw, dict):
            raise ValidationError(
                "RunTimeMessage: 'joint_states'/'end_effector_states'/"
                "'end_effector_poses' must be dicts"
            )

        return cls(
            timestamp=data["timestamp"],
            location=data.get("location", ""),
            source=data.get("source", ""),
            joint_states=RuntimeJointStatesMessage.from_dict(joint_raw),
            end_effector_states=RuntimeEndEffectorStatesMessage.from_dict(ee_states_raw),
            end_effector_poses=RuntimeEndEffectorPosesMessage.from_dict(poses_raw),
        )

    @classmethod
    def from_protobuf(cls, payload: bytes) -> "RunTimeMessage":
        if not runtime_pb2:
            raise ImportError("Protobuf code not generated")

        pb = runtime_pb2.CurrentState()
        pb.ParseFromString(payload)

        return cls(
            timestamp=pb.timestamp,
            location=pb.location,
            source=pb.source,
            joint_states=RuntimeJointStatesMessage(
                type=list(pb.joint_states.type) or list(TYPE_JOINT_STATES),
                names=list(pb.joint_states.names),
                position=list(pb.joint_states.position),
                velocity=list(pb.joint_states.velocity),
                torque=list(pb.joint_states.torque),
                acceleration=list(pb.joint_states.acceleration),
                temperature=list(pb.joint_states.temperature),
                current=list(pb.joint_states.current),
            ),
            end_effector_states=RuntimeEndEffectorStatesMessage(
                type=list(pb.end_effector_states.type) or _ee_types(list(pb.end_effector_states.names)),
                names=list(pb.end_effector_states.names),
                position=list(pb.end_effector_states.position),
                force=list(pb.end_effector_states.force),
            ),
            end_effector_poses=RuntimeEndEffectorPosesMessage(
                type=list(pb.end_effector_poses.type) or _ee_types(list(pb.end_effector_poses.names)),
                names=list(pb.end_effector_poses.names),
                pose=[list(p.data) for p in pb.end_effector_poses.pose],
                velocity=[list(p.data) for p in pb.end_effector_poses.velocity],
                acceleration=[list(p.data) for p in pb.end_effector_poses.acceleration],
                effort=[list(p.data) for p in pb.end_effector_poses.effort],
            ),
        )

    def to_protobuf(self) -> Any:
        if not runtime_pb2:
            raise ImportError("Protobuf code not generated")

        pb = runtime_pb2.CurrentState()
        pb.source = self.source
        pb.timestamp = int(self.timestamp)
        pb.location = self.location
        pb.joint_states.type.extend(self.joint_states.type or TYPE_JOINT_STATES)
        pb.joint_states.names.extend(self.joint_states.names)
        pb.joint_states.position.extend(self.joint_states.position)
        pb.joint_states.velocity.extend(self.joint_states.velocity)
        pb.joint_states.torque.extend(self.joint_states.torque)
        pb.joint_states.acceleration.extend(self.joint_states.acceleration)
        pb.joint_states.temperature.extend(self.joint_states.temperature)
        pb.joint_states.current.extend(self.joint_states.current)
        pb.end_effector_states.type.extend(
            self.end_effector_states.type or _ee_types(self.end_effector_states.names)
        )
        pb.end_effector_states.names.extend(self.end_effector_states.names)
        pb.end_effector_states.position.extend(self.end_effector_states.position)
        pb.end_effector_states.force.extend(self.end_effector_states.force)
        pb.end_effector_poses.type.extend(
            self.end_effector_poses.type or _ee_types(self.end_effector_poses.names)
        )
        pb.end_effector_poses.names.extend(self.end_effector_poses.names)
        for pose in self.end_effector_poses.pose:
            pb_pose = pb.end_effector_poses.pose.add()
            pb_pose.data.extend(float(v) for v in pose)
        for vector in (
            self.end_effector_poses.velocity,
            self.end_effector_poses.acceleration,
            self.end_effector_poses.effort,
        ):
            for item in vector:
                pb_vector = getattr(
                    pb.end_effector_poses,
                    "velocity"
                    if vector is self.end_effector_poses.velocity
                    else "acceleration"
                    if vector is self.end_effector_poses.acceleration
                    else "effort",
                )
                pb_vector.add().data.extend(float(v) for v in item)
        return pb

    def serialize(self) -> bytes:
        return self.to_protobuf().SerializeToString()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "timestamp": self.timestamp,
            "location": self.location,
            "joint_states": self.joint_states.to_dict(),
            "end_effector_states": self.end_effector_states.to_dict(),
            "end_effector_poses": self.end_effector_poses.to_dict(),
        }
