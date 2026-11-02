"""隙锋人形机器人 R2C 适配子包（自包含）。

通过 entry_point 注册（``hardware.type: xifeng``），由 R2C SDK 的
``RobotFactory`` 自动发现并加载。

对外入口：``XifengHardwareAdapter``（通过 ``create_xifeng_adapter`` 工厂函数注册
到 ``cloudrobo_r2c.adapters`` entry_point 组）。
"""

from cloudrobo_r2c.robots.xifeng.xifeng_adapter import XifengHardwareAdapter
from cloudrobo_r2c.robots.xifeng.robot_interface import (
    ALL_ARM_JOINT_NAMES,
    GRIPPER_NAMES,
    LEFT_ARM_JOINT_NAMES,
    RIGHT_ARM_JOINT_NAMES,
    XifengRobotProtocol,
)
from cloudrobo_r2c.robots.xifeng.robot_mock import MockXifengRobot

__all__ = [
    "XifengHardwareAdapter",
    "MockXifengRobot",
    "XifengRobotProtocol",
    "ALL_ARM_JOINT_NAMES",
    "LEFT_ARM_JOINT_NAMES",
    "RIGHT_ARM_JOINT_NAMES",
    "GRIPPER_NAMES",
]
