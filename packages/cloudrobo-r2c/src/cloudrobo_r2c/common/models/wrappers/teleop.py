"""TeleopFrame wrapper: teleop.proto 的操作端帧模型。

teleop.proto 是 teleop topic 的唯一 proto（复用 observation.proto 的
共享类型，不修改它们）。命令帧（oneof command）走 RELIABLE，动作帧
（末端位姿 + 夹爪/触发值）走 BEST_EFFORT；未握持的手其位姿与触发值
不进消息——数据缺席即安全信号。

供 tools/custom_topic_listener、custom_topic_subscriber、
custom_topic_publisher 通过 ``cloudrobo_r2c.common.models:TeleopFrame``
引用（wrapper 四方法协议）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from google.protobuf.json_format import MessageToDict

from .observations import EndEffectorPoses, EndEffectorStates

logger = logging.getLogger(__name__)

try:
    from ..generated import teleop_pb2
except Exception:
    teleop_pb2: Any = None  # type: ignore[no-redef]


@dataclass
class RawInputs:
    """原始手柄输入（A/X 按钮、摇杆按键与轴值），仅透传。"""

    primary_left: bool = False
    primary_right: bool = False
    thumbstick_pressed_left: bool = False
    thumbstick_pressed_right: bool = False
    thumbstick_x_left: float = 0.0
    thumbstick_y_left: float = 0.0
    thumbstick_x_right: float = 0.0
    thumbstick_y_right: float = 0.0


# 会话命令命令名 ↔ oneof 字段（proto: TeleopStart/Pause/Resume/Finish/Quit）
_COMMAND_FIELDS = ("start", "pause", "resume", "finish", "quit")


def _parse_command(data: Dict[str, Any]) -> Optional[str]:
    """从 dict 中识别会话命令（{"start": {...}} → "start"）。"""
    for name in _COMMAND_FIELDS:
        if name in data and data[name] is not None:
            payload = data[name]
            if payload is False or isinstance(payload, dict):
                return name
            raise ValueError(
                f"TeleopFrame command {name!r} must be an object ({{}}) "
                f"or null, got {type(payload).__name__}"
            )
    return None


@dataclass
class TeleopFrame:
    """Teleop 操作端帧：会话命令 + 末端位姿/触发值 + 原始手柄输入。"""

    timestamp: int = 0
    operator_id: str = ""
    session_id: str = ""
    # 会话命令名："start"/"pause"/"resume"/"finish"/"quit"，动作帧为 None
    command: Optional[str] = None
    # start 命令的 input_mode："vr"（当前）/"isomorphic"（预留）
    input_mode: str = "unspecified"
    joint_states: EndEffectorStates = field(default_factory=EndEffectorStates)
    end_effector_poses: EndEffectorPoses = field(default_factory=EndEffectorPoses)
    end_effector_states: EndEffectorStates = field(default_factory=EndEffectorStates)
    raw_inputs: RawInputs = field(default_factory=RawInputs)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TeleopFrame":
        if not isinstance(data, dict):
            raise ValueError(f"TeleopFrame data must be a dict, got {type(data).__name__}")
        command = _parse_command(data)
        # input_mode 挂在 TeleopStart 载荷内（proto: start.input_mode）
        start_payload = data.get("start") or {}
        input_mode = str(
            start_payload.get("input_mode", "unspecified") if isinstance(start_payload, dict) else "unspecified"
        ) or "unspecified"
        joint_states_data = data.get("joint_states") or {}
        joint_states = EndEffectorStates(
            names=list(joint_states_data.get("names", [])),
            position=list(joint_states_data.get("position", [])),
            force=list(joint_states_data.get("torque", [])),
        )
        ee_poses_data = data.get("end_effector_poses") or {}
        ee_poses = EndEffectorPoses(
            names=list(ee_poses_data.get("names", [])),
            pose=[list(p) for p in ee_poses_data.get("pose", [])],
        )
        ee_states_data = data.get("end_effector_states") or {}
        ee_states = EndEffectorStates(
            names=list(ee_states_data.get("names", [])),
            position=list(ee_states_data.get("position", [])),
            force=list(ee_states_data.get("force", [])),
        )
        raw_data = data.get("raw_inputs") or {}
        raw_inputs = RawInputs(
            primary_left=bool(raw_data.get("primary_left", False)),
            primary_right=bool(raw_data.get("primary_right", False)),
            thumbstick_pressed_left=bool(raw_data.get("thumbstick_pressed_left", False)),
            thumbstick_pressed_right=bool(raw_data.get("thumbstick_pressed_right", False)),
            thumbstick_x_left=float(raw_data.get("thumbstick_x_left", 0.0)),
            thumbstick_y_left=float(raw_data.get("thumbstick_y_left", 0.0)),
            thumbstick_x_right=float(raw_data.get("thumbstick_x_right", 0.0)),
            thumbstick_y_right=float(raw_data.get("thumbstick_y_right", 0.0)),
        )
        return cls(
            timestamp=data.get("timestamp", 0),
            operator_id=data.get("operator_id", ""),
            session_id=data.get("session_id", ""),
            command=command,
            input_mode=input_mode,
            joint_states=joint_states,
            end_effector_poses=ee_poses,
            end_effector_states=ee_states,
            raw_inputs=raw_inputs,
        )

    @classmethod
    def from_protobuf(cls, payload: bytes) -> "TeleopFrame":
        if teleop_pb2 is None:
            raise ImportError("Protobuf code not generated")
        pb = teleop_pb2.TeleopFrame()
        pb.ParseFromString(payload)
        which = pb.WhichOneof("command")
        raw = pb.raw_inputs
        if which == "start":
            # proto 枚举名 INPUT_MODE_VR → 简名 vr（与 to_protobuf 约定互逆）
            enum_name = teleop_pb2.InputMode.Name(pb.start.input_mode)
            input_mode = enum_name[len("INPUT_MODE_"):].lower()
        else:
            input_mode = "unspecified"
        return cls(
            timestamp=pb.timestamp,
            operator_id=pb.operator_id,
            session_id=pb.session_id,
            command=which,
            input_mode=input_mode,
            joint_states=EndEffectorStates(
                names=list(pb.joint_states.names),
                position=list(pb.joint_states.position),
                force=list(pb.joint_states.torque),
            ),
            end_effector_poses=EndEffectorPoses(
                names=list(pb.end_effector_poses.names),
                pose=[list(pose.data) for pose in pb.end_effector_poses.pose],
            ),
            end_effector_states=EndEffectorStates(
                names=list(pb.end_effector_states.names),
                position=list(pb.end_effector_states.position),
                force=list(pb.end_effector_states.force),
            ),
            raw_inputs=RawInputs(
                primary_left=raw.primary_left,
                primary_right=raw.primary_right,
                thumbstick_pressed_left=raw.thumbstick_pressed_left,
                thumbstick_pressed_right=raw.thumbstick_pressed_right,
                thumbstick_x_left=raw.thumbstick_x_left,
                thumbstick_y_left=raw.thumbstick_y_left,
                thumbstick_x_right=raw.thumbstick_x_right,
                thumbstick_y_right=raw.thumbstick_y_right,
            ),
        )

    def to_protobuf(self) -> Any:
        if teleop_pb2 is None:
            raise ImportError("Protobuf code not generated")
        pb = teleop_pb2.TeleopFrame()
        pb.timestamp = self.timestamp
        pb.operator_id = self.operator_id
        pb.session_id = self.session_id
        if self.command is not None:
            getattr(pb, self.command).SetInParent()
            if self.command == "start":
                try:
                    pb.start.input_mode = teleop_pb2.InputMode.Value(
                        "INPUT_MODE_" + str(self.input_mode).upper()
                    )
                except ValueError as exc:
                    raise ValueError(
                        f"TeleopFrame input_mode {self.input_mode!r} is invalid; "
                        f"expected one of: unspecified/vr/isomorphic"
                    ) from exc
        pb.joint_states.names.extend(self.joint_states.names)
        pb.joint_states.position.extend(self.joint_states.position)
        pb.joint_states.torque.extend(self.joint_states.force)
        pb.end_effector_poses.names.extend(self.end_effector_poses.names)
        for pose in self.end_effector_poses.pose:
            pb.end_effector_poses.pose.add().data.extend(
                pose.data if hasattr(pose, "data") else pose
            )
        pb.end_effector_states.names.extend(self.end_effector_states.names)
        pb.end_effector_states.position.extend(self.end_effector_states.position)
        pb.end_effector_states.force.extend(self.end_effector_states.force)
        raw = self.raw_inputs
        pb.raw_inputs.primary_left = raw.primary_left
        pb.raw_inputs.primary_right = raw.primary_right
        pb.raw_inputs.thumbstick_pressed_left = raw.thumbstick_pressed_left
        pb.raw_inputs.thumbstick_pressed_right = raw.thumbstick_pressed_right
        pb.raw_inputs.thumbstick_x_left = raw.thumbstick_x_left
        pb.raw_inputs.thumbstick_y_left = raw.thumbstick_y_left
        pb.raw_inputs.thumbstick_x_right = raw.thumbstick_x_right
        pb.raw_inputs.thumbstick_y_right = raw.thumbstick_y_right
        return pb

    def serialize(self) -> bytes:
        return self.to_protobuf().SerializeToString()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "operator_id": self.operator_id,
            "session_id": self.session_id,
            "command": self.command,
            "input_mode": self.input_mode,
            "joint_states": {
                "names": list(self.joint_states.names),
                "position": list(self.joint_states.position),
                "torque": list(self.joint_states.force),
            },
            "end_effector_poses": {
                "names": list(self.end_effector_poses.names),
                "pose": [list(p) for p in self.end_effector_poses.pose],
            },
            "end_effector_states": {
                "names": list(self.end_effector_states.names),
                "position": list(self.end_effector_states.position),
                "force": list(self.end_effector_states.force),
            },
            "raw_inputs": {
                "primary_left": self.raw_inputs.primary_left,
                "primary_right": self.raw_inputs.primary_right,
                "thumbstick_pressed_left": self.raw_inputs.thumbstick_pressed_left,
                "thumbstick_pressed_right": self.raw_inputs.thumbstick_pressed_right,
                "thumbstick_x_left": self.raw_inputs.thumbstick_x_left,
                "thumbstick_y_left": self.raw_inputs.thumbstick_y_left,
                "thumbstick_x_right": self.raw_inputs.thumbstick_x_right,
                "thumbstick_y_right": self.raw_inputs.thumbstick_y_right,
            },
        }
