"""Runtime frame collector: StateCache -> RunTimeMessage dict.

Frame organization mirrors observation.proto structures (vector style):

    joint_states:       {names, position, velocity, torque}
                        15 维：左臂7 + 右臂7 + 腰部可控关节（最后一维）
    end_effector_poses: {names, pose: [[x,y,z,qw,qx,qy,qz], ...]}

location = "当前朝向;当前地理位置"（朝向由腰部末关节推算，度 [0,360)；
地理位置为静态配置）。

Granularity schedule: ``position`` is always attempted; each other field
group is only fetched on ticks where ``tick % factor == 0``. Groups not
scheduled (or not fresh in cache) are omitted from the frame entirely.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from .duco import DucoArm
from .state_cache import StateCache

logger = logging.getLogger(__name__)

_GROUP_METHODS = {
    "velocity": "get_velocities",
    "torque": "get_torques",
    "acceleration": "get_accelerations",
    "temperature": "get_temperatures",
    "current": "get_currents",
}

def expand_to_14(seg: List[float]) -> List[float]:
    """双臂 14 关节向量（左臂7 + 右臂7）；夹爪不占用关节数（走 end_effector_states）。"""
    return list(seg[:14])


# runtime 帧 15 关节名：左臂7 + 右臂7 + 腰部可控关节（夹爪关节已去除）。
# 腰部前 3 关节写死不采集，最后一维为腰部第 4 关节（可控关节）
JOINT_NAMES = [
    "左肩俯仰", "左肩横滚", "左肩偏航", "左肘俯仰", "左腕横滚", "左腕偏航", "左腕俯仰",
    "右肩俯仰", "右肩横滚", "右肩偏航", "右肘俯仰", "右腕横滚", "右腕偏航", "右腕俯仰",
    "腰部旋转关节",
]

# 夹爪 end_effector_states 名称（observation 与 current_state 共用）
EE_STATE_NAMES = ["左夹爪", "右夹爪"]


class RuntimeCollector:
    def __init__(
        self,
        cache: StateCache,
        arms_provider: Callable[[], Dict[str, DucoArm]],
        granularity: Optional[Dict[str, int]] = None,
        location: str = "",
        position_source: Optional[Callable[[], List[float]]] = None,
        ee_states_source: Optional[Callable[[], Dict[str, Any]]] = None,
        ee_poses_source: Optional[Callable[[], Dict[str, Any]]] = None,
        waist_source: Optional[Callable[[], float]] = None,
        heading_source: Optional[Callable[[], float]] = None,
    ) -> None:
        self._cache = cache
        self._arms_provider = arms_provider
        self._granularity = {
            name: max(int(factor), 1)
            for name, factor in (granularity or {}).items()
        }
        self._location = location
        # Playback mode: positions come from the recorded dataset instead
        # of the robot arms; velocity/torque stay absent.
        self._position_source = position_source
        # 夹爪状态源（回放：录制文件 ee 值；实时：从 DucoArm 最后指令推导）
        self._ee_states_source = ee_states_source
        # 末端位姿源（回放：录制 actions xyzrpy；实时：get_tcp_pose 读取）
        self._ee_poses_source = ee_poses_source
        # 腰部可控关节（第 4 关节）读数源（节流，弧度）；未注入时补 0.0
        self._waist_source = waist_source
        # 当前朝向源（度，[0,360)，由腰部末关节推算）；location 组成见 _compose_location
        self._heading_source = heading_source

    def _waist_position(self) -> float:
        if self._waist_source is None:
            return 0.0
        try:
            return float(self._waist_source())
        except Exception:
            logger.warning("waist position read failed; fallback 0.0", exc_info=True)
            return 0.0

    def _compose_location(self) -> str:
        """location = "当前朝向;当前地理位置"（如 "270;上海世博展览馆"）。

        朝向（度，[0,360)）由腰部最后一个关节读数推算（反向/偏移标定
        在 heading_source 内完成）；地理位置为静态配置（devices.location）。
        无朝向源（回放/未配置）时朝向为 0。
        """
        heading = 0.0
        if self._heading_source is not None:
            try:
                heading = float(self._heading_source())
            except Exception:
                logger.warning("heading read failed; fallback 0", exc_info=True)
                heading = 0.0
        heading_i = int(round(heading)) % 360
        return f"{heading_i};{self._location}" if self._location else str(heading_i)

    def _fetchers(self, tick: int) -> Dict[str, Callable[[], List[float]]]:
        if self._position_source is not None:
            return {"position": self._position_source}
        arms = list(self._arms_provider().values())
        if not arms:
            return {}

        def _merged(method: str) -> Callable[[], List[float]]:
            def _read() -> List[float]:
                merged: List[float] = []
                for arm in arms:
                    merged.extend(getattr(arm, method)())
                return merged

            return _read

        fetchers: Dict[str, Callable[[], List[float]]] = {"position": _merged("get_positions")}
        for group in _GROUP_METHODS:
            factor = self._granularity.get(group, 1)
            if tick % factor == 0:
                fetchers[group] = _merged(_GROUP_METHODS[group])
        return fetchers

    def collect(self, tick: int = 0) -> Optional[Dict[str, Any]]:
        if self._position_source is not None:
            # Playback: read the current frame directly (no cache) so the
            # frame index is exact at publish time; no velocity/torque.
            position = self._position_source()
            if not position:
                return None
            groups: Dict[str, List[float]] = {}
            left_grip, right_grip = 0.0, 0.0
        else:
            state = self._cache.get(self._fetchers(tick))
            position = state.get("position")
            if not position:
                return None
            groups = {
                group: values
                for group in _GROUP_METHODS
                if (values := state.get(group)) and len(values) >= len(position)
            }
            left_grip, right_grip = self._grip_values()

        # 15 关节：双臂 14（左臂7 + 右臂7）+ 腰部可控关节（最后一维）；
        # 夹爪不占关节。其余组只有臂读数，末位补 0.0 对齐维度
        waist = self._waist_position()
        position = expand_to_14(position) + [waist]
        groups = {
            group: expand_to_14(values) + [0.0]
            for group, values in groups.items()
        }

        joint_states = {
            "type": [f"J{i}" for i in range(1, 16)],
            "names": list(JOINT_NAMES),
            "position": [float(v) for v in position],
            "velocity": [float(v) for v in groups.get("velocity", [])],
            "torque": [float(v) for v in groups.get("torque", [])],
            "acceleration": [float(v) for v in groups.get("acceleration", [])],
            # 温度/电流：get_robot_status（actual_joint_temp/actual_joint_current），
            # 一次 RPC 节流共享；读取失败/未调度时为空向量
            "temperature": [float(v) for v in groups.get("temperature", [])],
            "current": [float(v) for v in groups.get("current", [])],
               }
        ee_states = (
            self._ee_states_source() if self._ee_states_source is not None
            else self._derive_ee_states()
        )
        ee_states.setdefault(
            "type",
            [f"EE{i + 1}" for i in range(len(ee_states.get("names", [])))],
        )
        ee_poses = (
            self._ee_poses_source() if self._ee_poses_source is not None
            else self._tcp_poses()
        )
        ee_poses.setdefault(
            "type",
            [f"EE{i + 1}" for i in range(len(ee_poses.get("names", [])))],
        )
        frame: Dict[str, Any] = {
            "timestamp": int(time.time() * 1000),
            "location": self._compose_location(),
            "joint_states": joint_states,
            "end_effector_states": ee_states,
            "end_effector_poses": ee_poses,
        }
        return frame

    def _grip_values(self) -> "tuple[float, float]":
        """Left/right gripper values 0-10（真实反馈优先，节流读取）。"""
        arms = self._arms_provider()

        def _gv(arm_name: str) -> float:
            arm = arms.get(arm_name)
            if arm is None or not getattr(arm, "gripper_enabled", False):
                return 0.0
            return arm.gripper_state_0_10()

        return _gv("left"), _gv("right")

    def _tcp_poses(self) -> Dict[str, Any]:
        """双臂末端位姿 + 速度/加速度（应传尽传，米/弧度）。

        pose 6 维 xyzrpy；velocity/acceleration 6 维 TCP 速度/加速度
        （get_robot_status 单 RPC 共享提取，单臂失败跳过该臂该组）。
        """
        arms = self._arms_provider()
        names: List[str] = []
        poses: List[List[float]] = []
        velocities: List[List[float]] = []
        accelerations: List[List[float]] = []
        for ee_name, arm_name in (("左末端", "left"), ("右末端", "right")):
            arm = arms.get(arm_name)
            if arm is None or not arm.connected:
                continue
            try:
                pose = arm.get_tcp_pose()
            except Exception:
                logger.debug("arm %r get_tcp_pose failed", arm_name, exc_info=True)
                continue
            if pose and len(pose) >= 6:
                names.append(ee_name)
                poses.append([float(v) for v in pose[:6]])
            try:
                vel = arm.get_tcp_velocity()
            except Exception:
                logger.debug(
                    "arm %r get_tcp_velocity failed", arm_name, exc_info=True
                )
                vel = []
            if vel and len(vel) >= 6:
                velocities.append([float(v) for v in vel[:6]])
            try:
                acc = arm.get_tcp_acceleration()
            except Exception:
                logger.debug(
                    "arm %r get_tcp_acceleration failed", arm_name, exc_info=True
                )
                acc = []
            if acc and len(acc) >= 6:
                accelerations.append([float(v) for v in acc[:6]])
        out: Dict[str, Any] = {"names": names, "pose": poses}
        # 应传尽传：有数据的组才带（回放源无 velocity/acceleration）
        if velocities:
            out["velocity"] = velocities
        if accelerations:
            out["acceleration"] = accelerations
        return out

    def _derive_ee_states(self) -> Dict[str, Any]:
        """Gripper state 0-10（真实反馈优先、节流读取）。"""
        left_grip, right_grip = self._grip_values()
        return {
            "names": list(EE_STATE_NAMES),
            "position": [left_grip, right_grip],
            "force": [],
        }
