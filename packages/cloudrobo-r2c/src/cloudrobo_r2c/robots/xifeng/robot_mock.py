"""Mock 隙锋 Robot：合成数据，便于无真机/无 pypilot wheel 的开发调试。

模型类似 cloudrobo_r2c 内置的 DummyRobotHardwareAdapter：每次 get_observation 让关节
缓慢向 _target 推进。observation 只含关节 + 夹爪（目前不支持 RGB 推流，无图像）。
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, Mapping

from .robot_interface import (
    ALL_ARM_JOINT_NAMES,
    GRIPPER_NAMES,
)

logger = logging.getLogger(__name__)


class MockXifengRobot:
    """合成数据的隙锋机器人实现（关节 + 夹爪，无图像）。

    Parameters
    ----------
    max_joint_speed_rad_s:
        每秒最大关节移动速度，模拟真机响应延迟
    """

    def __init__(
        self,
        max_joint_speed_rad_s: float = 1.5,
    ) -> None:
        self._max_joint_speed = float(max_joint_speed_rad_s)

        self._connected = False
        self._state: Dict[str, float] = {}
        self._target: Dict[str, float] = {}
        self._gripper_state: Dict[str, float] = {}
        self._gripper_target: Dict[str, float] = {}
        self._t0 = time.monotonic()
        self._last_step = self._t0
        # 用于 send_action 日志限频与统计
        self._action_recv_count = 0
        self._last_action_log_ts = 0.0

    def connect(self) -> None:
        if self._connected:
            return
        # 初始 pose：双臂略微张开，gripper 全开
        for i, name in enumerate(ALL_ARM_JOINT_NAMES):
            self._state[name] = 0.05 * (i + 1) * (-1 if "right" in name else 1)
            self._target[name] = self._state[name]
        for name in GRIPPER_NAMES:
            self._gripper_state[name] = 100.0
            self._gripper_target[name] = 100.0
        self._connected = True
        self._t0 = time.monotonic()
        self._last_step = self._t0
        logger.info(
            "MockXifengRobot connected: %d joints, %d grippers (no images)",
            len(self._state),
            len(self._gripper_state),
        )

    def disconnect(self) -> None:
        if not self._connected:
            return
        self._connected = False
        logger.info("MockXifengRobot disconnected")

    def get_observation(self) -> Mapping[str, Any]:
        self._ensure_connected()
        self._step_simulation()

        obs: Dict[str, Any] = {}
        obs.update(self._state)          # 关节
        obs.update(self._gripper_state)  # 末端
        # 目前不支持 RGB 推流：observation 不含图像。
        return obs

    def send_action(self, command: Mapping[str, Any]) -> None:
        """接收 robot_xifeng_config.yaml r2c_to_device 透传过来的命令。

        预期 schema::

            {
                "arm_positions":    [float, ...],   # 前 N 个对应 ALL_ARM_JOINT_NAMES
                "gripper_positions":[float, ...],   # 前 N 个对应 GRIPPER_NAMES
            }

        长度容错：list 比关节数短就只更新前 N 个；多余的丢弃。
        """
        self._ensure_connected()

        arm_positions = command.get("arm_positions") or []
        if isinstance(arm_positions, (list, tuple)):
            for i, value in enumerate(arm_positions):
                if i >= len(ALL_ARM_JOINT_NAMES):
                    break
                try:
                    self._target[ALL_ARM_JOINT_NAMES[i]] = float(value)
                except (TypeError, ValueError) as e:
                    logger.debug("Failed to convert arm_positions[%s] value %s: %s", i, value, e)
                    continue

        gripper_positions = command.get("gripper_positions") or []
        if isinstance(gripper_positions, (list, tuple)):
            for i, value in enumerate(gripper_positions):
                if i >= len(GRIPPER_NAMES):
                    break
                try:
                    self._gripper_target[GRIPPER_NAMES[i]] = float(value)
                except (TypeError, ValueError) as e:
                    logger.debug("Failed to convert gripper_positions[%s] value %s: %s", i, value, e)
                    continue

        self._action_recv_count += 1

        # 限频 1Hz 的 INFO 摘要，便于云端调试时观察 action 是否到达 robot 层
        now = time.monotonic()
        if now - self._last_action_log_ts >= 1.0:
            self._last_action_log_ts = now
            logger.info(
                "[mock-action] total=%d left_arm_1=%.3f right_arm_1=%.3f "
                "left_gripper=%.0f right_gripper=%.0f",
                self._action_recv_count,
                self._target.get("left_arm_1", 0.0),
                self._target.get("right_arm_1", 0.0),
                self._gripper_target.get("left_gripper", 0.0),
                self._gripper_target.get("right_gripper", 0.0),
            )

        # 详细日志仍走 DEBUG，平时不刷屏
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "Mock action received: arm=%s grippers=%s",
                {k: round(self._target[k], 3) for k in ALL_ARM_JOINT_NAMES},
                {k: round(self._gripper_target[k], 1) for k in GRIPPER_NAMES},
            )

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("MockXifengRobot is not connected; call connect() first")

    def _step_simulation(self) -> None:
        now = time.monotonic()
        dt = max(now - self._last_step, 0.0)
        self._last_step = now
        max_step = self._max_joint_speed * dt
        if max_step <= 0:
            return

        for name in ALL_ARM_JOINT_NAMES:
            cur, tgt = self._state[name], self._target[name]
            delta = tgt - cur
            if abs(delta) <= max_step:
                self._state[name] = tgt
            else:
                self._state[name] = cur + math.copysign(max_step, delta)

        # 末端假设瞬时响应（实际 hex 命令也是离散的）
        for name in GRIPPER_NAMES:
            self._gripper_state[name] = self._gripper_target[name]
