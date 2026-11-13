"""Fake ``a2d_sdk`` modules shared by the AgiBot G01 adapter tests.

``cloudrobo_r2c.robots.agibot_g01.agibot_g01_client`` binds the GDK SDK classes
at import time. ``tests/conftest.py`` installs the fakes *before* any test
module is imported, so the binding is deterministic regardless of test
collection order — no GDK environment or robot required.
"""

from __future__ import annotations

import sys
import types
from typing import Any, Dict, List

import numpy as np


class FakeRobotDds:
    """记录所有 GDK 调用；返回确定性的关节状态。"""

    instances: List["FakeRobotDds"] = []

    def __init__(self) -> None:
        type(self).instances.append(self)
        self.calls: List[tuple] = []
        self.arm = [float(i + 1) / 10 for i in range(14)]
        self.gripper = [56.25, 98.75]  # GDK 原始量程 [35, 120]，归一化后 ≈ [0.25, 0.75]
        self.hand = [0.0] * 12
        self.head = [0.1, -0.2]
        self.waist = [0.05, 30.0]
        self.body_pose = [0.0, 0.1, 0.2, 0.3]
        self.fail_next_state_query = False
        self.shutdown_count = 0

    # 状态查询（《指南》7.1：返回 (数据, 时间戳)）
    def arm_joint_states(self):
        if self.fail_next_state_query:
            self.fail_next_state_query = False
            raise RuntimeError("dds hiccup")
        return list(self.arm), 1_000_000_000

    def gripper_states(self):
        return list(self.gripper), 1_000_000_000

    def hand_joint_states(self):
        return list(self.hand), 1_000_000_000

    def head_joint_states(self):
        return list(self.head), 1_000_000_000

    def waist_joint_states(self):
        return list(self.waist), 1_000_000_000

    def body_pose_joint_states(self):
        return list(self.body_pose)

    # 运动控制（《指南》7.2）
    def move_arm(self, positions):
        self.calls.append(("move_arm", list(positions)))

    def move_gripper(self, positions):
        self.calls.append(("move_gripper", list(positions)))

    def move_hand(self, positions):
        self.calls.append(("move_hand", list(positions)))

    def move_head(self, positions):
        self.calls.append(("move_head", list(positions)))

    def move_waist(self, positions):
        self.calls.append(("move_waist", list(positions)))

    def move_wheel(self, linear, angular):
        self.calls.append(("move_wheel", float(linear), float(angular)))

    def reset(self, **targets):
        self.calls.append(("reset", dict(targets)))

    def shutdown(self):
        self.shutdown_count += 1
        self.calls.append(("shutdown",))


class FakeCosineCamera:
    instances: List["FakeCosineCamera"] = []

    def __init__(self, camera_group):
        self.camera_group = list(camera_group)
        self.frames = {name: np.full((4, 4, 3), 7, dtype=np.uint8) for name in self.camera_group}
        self.closed = False
        type(self).instances.append(self)

    def get_latest_image(self, camera_name):
        # 真实 a2d_sdk 返回 (图像, 时间戳)（见《指南》9.5 使用示例）
        return self.frames.get(camera_name), 1_000_000_000

    def close(self):
        self.closed = True


class FakeRobotController:
    """记录第 8 章 set_end_effector_pose_control / set_joint_position_control 调用。

    ``get_motion_status`` 返回固定 frames（含左右末端位姿），用于观测侧测试。
    """

    instances: List["FakeRobotController"] = []

    def __init__(self):
        type(self).instances.append(self)
        self.pose_calls: List[tuple] = []
        self.joint_calls: List[tuple] = []
        self.motion_mode_calls: List[int] = []
        self.motion_status: Any = fake_motion_status()

    def get_motion_status(self):
        return self.motion_status

    def set_motion_control_mode(self, mode):
        self.motion_mode_calls.append(int(mode))
        self.motion_status["mode"] = {
            "value": int(mode),
            "name": {1: "SERVO", 2: "PLANNING"}.get(int(mode), "?"),
        }

    def set_end_effector_pose_control(
        self, lifetime, control_group, left_pose=None, right_pose=None
    ):
        self.pose_calls.append((float(lifetime), list(control_group), left_pose, right_pose))

    def set_joint_position_control(self, lifetime, joint_group):
        self.joint_calls.append((float(lifetime), dict(joint_group)))


def fake_motion_status() -> Dict[str, Any]:
    """《指南》8.2.3 get_motion_status 返回结构示例（含左右末端 frames 位姿）。"""
    return {
        "timestamp": 1,
        "mode": {"value": 1, "name": "SERVO"},
        "error": {"code": 0, "message": "", "has_error": False},
        "frames": {
            "arm_left_link7": {
                "position": {"x": 0.13, "y": 0.89, "z": 0.85},
                "orientation": {"quaternion": {"x": -0.7071, "y": 0.0, "z": 0.0, "w": 0.7071}},
            },
            "arm_right_link7": {
                "position": {"x": 0.13, "y": -0.89, "z": 0.85},
                "orientation": {"quaternion": {"x": 0.0, "y": -0.7071, "z": 0.7071, "w": 0.0}},
            },
        },
        "collisions": [],
    }


def install_fake_a2d_sdk() -> None:
    """真实 a2d_sdk 未安装时注入假模块（``agibot_g01_client`` 导入前调用）。"""
    try:
        import a2d_sdk  # noqa: F401
    except ImportError:
        pass
    else:
        return  # 真实 SDK 已安装，不注入假模块
    if "a2d_sdk" in sys.modules:
        return  # 已注入（幂等）
    a2d = types.ModuleType("a2d_sdk")
    robot_mod = types.ModuleType("a2d_sdk.robot")
    robot_mod.RobotDds = FakeRobotDds
    robot_mod.CosineCamera = FakeCosineCamera
    robot_mod.RobotController = FakeRobotController
    a2d.robot = robot_mod
    sys.modules.setdefault("a2d_sdk", a2d)
    sys.modules.setdefault("a2d_sdk.robot", robot_mod)


def reset_fake_instances() -> None:
    """清空三类 fake 的实例缓存（autouse fixture 调用，保证测试隔离）。"""
    FakeRobotDds.instances.clear()
    FakeCosineCamera.instances.clear()
    FakeRobotController.instances.clear()
