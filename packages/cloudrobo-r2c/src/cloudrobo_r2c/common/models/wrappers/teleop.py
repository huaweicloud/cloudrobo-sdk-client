"""TeleopFrame wrapper: teleop.proto 的操作端帧模型。

teleop.proto 是 teleop topic 的唯一 proto（复用 observation.proto 的
共享类型，不修改它们）。命令帧（oneof command）走 RELIABLE，动作帧
（末端位姿 + 夹爪/触发值）走 BEST_EFFORT；未握持的手其位姿与触发值
不进消息——数据缺席即安全信号。

原始手柄/主从臂输入（primary 按钮、摇杆按键与轴值）经 extensions 透传，
键名按 ``<hand>.btn.primary`` / ``<hand>.btn.thumbstick`` /
``<hand>.stick.x`` / ``<hand>.stick.y`` 分组（``<hand>`` 为
``left``/``right``）；网关不据此做会话决策。

供 tools/custom_topic_listener、custom_topic_subscriber、
custom_topic_publisher 通过 ``cloudrobo_r2c.common.models:TeleopFrame``
引用（wrapper 四方法协议）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .common import ExtensionValue
from .observations import EndEffectorPoses, EndEffectorStates

logger = logging.getLogger(__name__)

try:
    from ..generated import teleop_pb2
except Exception:
    teleop_pb2: Any = None  # type: ignore[no-redef]


@dataclass
class RawInputs:
    """手柄/主从臂输入的便捷封装，与 extensions 键名互转。

    键名约定（teleop.proto TeleopFrame.extensions，分组透传）：
    ``<hand>.btn.primary`` bool、``<hand>.btn.thumbstick`` bool、
    ``<hand>.stick.x`` float、``<hand>.stick.y`` float，
    ``<hand>`` 为 ``left``/``right``。
    """

    primary_left: bool = False
    primary_right: bool = False
    thumbstick_pressed_left: bool = False
    thumbstick_pressed_right: bool = False
    thumbstick_x_left: float = 0.0
    thumbstick_y_left: float = 0.0
    thumbstick_x_right: float = 0.0
    thumbstick_y_right: float = 0.0

    _BOOL_KEYS = {
        "primary_left": "left.btn.primary",
        "primary_right": "right.btn.primary",
        "thumbstick_pressed_left": "left.btn.thumbstick",
        "thumbstick_pressed_right": "right.btn.thumbstick",
    }
    _FLOAT_KEYS = {
        "thumbstick_x_left": "left.stick.x",
        "thumbstick_y_left": "left.stick.y",
        "thumbstick_x_right": "right.stick.x",
        "thumbstick_y_right": "right.stick.y",
    }

    @classmethod
    def from_extensions(
        cls, extensions: Dict[str, ExtensionValue]
    ) -> "RawInputs":
        """从 extensions dict 还原（未出现的键取默认值）。"""
        raw = cls()
        for attr, key in cls._BOOL_KEYS.items():
            ev = extensions.get(key)
            if ev is not None:
                try:
                    setattr(raw, attr, bool(ev.ext_decode()))
                except Exception:
                    logger.debug("cannot decode teleop extension %r", key, exc_info=True)
        for attr, key in cls._FLOAT_KEYS.items():
            ev = extensions.get(key)
            if ev is not None:
                try:
                    setattr(raw, attr, float(ev.ext_decode()))
                except Exception:
                    logger.debug("cannot decode teleop extension %r", key, exc_info=True)
        return raw

    def to_extensions(self) -> Dict[str, ExtensionValue]:
        """转为 extensions dict（默认值不写入，保持帧精简）。"""
        extensions: Dict[str, ExtensionValue] = {}
        for attr, key in self._BOOL_KEYS.items():
            value = getattr(self, attr)
            if value:
                extensions[key] = ExtensionValue.from_scalar(value)
        for attr, key in self._FLOAT_KEYS.items():
            value = getattr(self, attr)
            if value != 0.0:
                extensions[key] = ExtensionValue.from_scalar(value)
        return extensions


# 会话命令名 ↔ oneof 字段（proto: TeleopStart/Pause/Resume/Finish；
# TeleopQuit 已于 2026-11-16 移除，field 8/"quit" 被 reserved）
_COMMAND_FIELDS = ("start", "pause", "resume", "finish")

# 2026-11-15/16 从 proto 移除的旧顶层键：输入中出现即 fail-fast，
# 提示按新协议构造（quit → 不再有；raw_inputs → extensions）
_REMOVED_KEYS = ("quit", "raw_inputs")


def _parse_command(data: Dict[str, Any]) -> Optional[str]:
    """从 dict 中识别会话命令（{"start": {}} → "start"）。

    JSON 里 ``"start": {}`` 表示命令存在，``null`` 表示无命令；
    其余类型（bool/str/数字）视为无效载荷并 fail-fast。
    """
    for removed in _REMOVED_KEYS:
        if removed in data:
            raise ValueError(
                f"TeleopFrame field {removed!r} was removed from teleop.proto "
                f"(reserved); use the current protocol instead"
            )
    for name in _COMMAND_FIELDS:
        if name in data and data[name] is not None:
            payload = data[name]
            if isinstance(payload, dict):
                return name
            raise ValueError(
                f"TeleopFrame command {name!r} must be an object ({{}}) "
                f"or null, got {type(payload).__name__}"
            )
    return None


@dataclass
class TeleopFrame:
    """Teleop 操作端帧：会话命令 + 末端位姿/触发值 + 透传输入。"""

    timestamp: int = 0
    operator_id: str = ""
    session_id: str = ""
    # 会话命令名："start"/"pause"/"resume"/"finish"，动作帧为 None
    command: Optional[str] = None
    # start 命令的 input_mode："vr"（当前）/"isomorphic"（预留）
    input_mode: str = "unspecified"
    joint_states: EndEffectorStates = field(default_factory=EndEffectorStates)
    end_effector_poses: EndEffectorPoses = field(default_factory=EndEffectorPoses)
    end_effector_states: EndEffectorStates = field(default_factory=EndEffectorStates)
    # 透传输入 extensions（键名约定见 RawInputs；可放任意 ExtensionValue）
    extensions: Dict[str, ExtensionValue] = field(default_factory=dict)

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
        extensions = _parse_extensions(data.get("extensions") or {})
        return cls(
            timestamp=data.get("timestamp", 0),
            operator_id=data.get("operator_id", ""),
            session_id=data.get("session_id", ""),
            command=command,
            input_mode=input_mode,
            joint_states=joint_states,
            end_effector_poses=ee_poses,
            end_effector_states=ee_states,
            extensions=extensions,
        )

    @classmethod
    def from_protobuf(cls, payload: bytes) -> "TeleopFrame":
        if teleop_pb2 is None:
            raise ImportError("Protobuf code not generated")
        pb = teleop_pb2.TeleopFrame()
        pb.ParseFromString(payload)
        which = pb.WhichOneof("command")
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
            extensions=ExtensionValue.from_proto_extensions(pb.extensions),
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
        # -- extensions（map<string, ExtensionValue>）--------------
        for key, ev in self.extensions.items():
            ev_pb = pb.extensions[key]
            ev_pb.dtype = ev.to_proto_dtype()
            ev_pb.shape.extend(ev.shape)
            ev_pb.data = ev.data
            ev_pb.mime_type = ev.mime_type
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
            "extensions": {
                key: {
                    "dtype": ev.dtype,
                    "shape": list(ev.shape),
                    "data": ev.data.hex(),
                    "mime_type": ev.mime_type,
                }
                for key, ev in self.extensions.items()
            },
        }


def _parse_extensions(
    data: Dict[str, Any],
) -> Dict[str, ExtensionValue]:
    """解析 TeleopFrame.extensions：ExtensionValue 或其字段 dict。

    标量便捷写法（bool/int/float/str/bytes）也接受，便于 publisher 的
    JSON 输入直接写 ``"left.btn.primary": true`` 风格的键值。dict 形式的
    ``data`` 支持十六进制字符串（如 to_dict 的输出）或 bytes。
    """
    result: Dict[str, ExtensionValue] = {}
    for key, val in data.items():
        if isinstance(val, ExtensionValue):
            result[key] = val
        elif isinstance(val, dict):
            raw_data = val.get("data", b"")
            if isinstance(raw_data, str):
                raw_data = bytes.fromhex(raw_data)
            result[key] = ExtensionValue(
                dtype=str(val.get("dtype", "")),
                shape=list(val.get("shape", [])),
                data=bytes(raw_data),
                mime_type=str(val.get("mime_type", "")),
            )
        elif isinstance(val, bool):
            result[key] = ExtensionValue.from_scalar(val)
        elif isinstance(val, int):
            result[key] = ExtensionValue.from_scalar(val)
        elif isinstance(val, float):
            result[key] = ExtensionValue.from_scalar(val)
        elif isinstance(val, str):
            result[key] = ExtensionValue.from_string(val)
        elif isinstance(val, (bytes, bytearray)):
            result[key] = ExtensionValue(dtype="BYTES", data=bytes(val))
        else:
            raise ValueError(
                f"TeleopFrame extensions[{key!r}] must be an ExtensionValue, "
                f"a dict, or a scalar, got {type(val).__name__}"
            )
    return result
