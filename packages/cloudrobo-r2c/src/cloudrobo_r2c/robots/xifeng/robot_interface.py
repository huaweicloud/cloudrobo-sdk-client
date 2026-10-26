"""隙锋 Robot 接口约定。

R2C SDK 内置的 ``VendorSDKHardwareAdapter`` 期望我们的 Robot 实现 4 个方法
（参考 cloudrobo_r2c/robots/vendor_sdk_hardware_adapter.py）：

    connect() / disconnect() / get_observation() -> Mapping / send_action(command)

其中 ``get_observation()`` 返回的 Mapping 会被 ``ConfigurableDeviceTranslator``
按 robot config 的 ``device_to_r2c.mappings`` 规则转换为 R2C ``Observations``。

数据契约：
- ``get_observation()`` 返回扁平 dict，key 用 R2C 通用语义名（见 robot config 的 source_paths）：
    - 关节：``left_arm_1`` .. ``left_arm_7``, ``right_arm_1`` .. ``right_arm_7``（单位：弧度）
    - 末端：``left_gripper``, ``right_gripper``（0-100 的开合度）
    - （目前不支持 RGB 数据推流，observation 不含图像）
- ``send_action(command)`` 接收 dict，key 同上；value 是该关节目标位置（弧度）或目标开合度。

为什么对外用弧度：
- R2C 协议里关节单位是弧度
- 在 robot 内部统一弧度可以避免 mapper 里到处写 deg_to_rad 自定义 transform
- pypilot 内部用度，由 ``robot_real.py`` 在调用 ``armMoveJoints / armGetJoints`` 时一次性换算
"""

from __future__ import annotations

from typing import List, Mapping, Protocol


# R2C 语义名常量（与 robot_xifeng_config.yaml 的 source_paths 必须一致）
LEFT_ARM_JOINT_NAMES: List[str] = [f"left_arm_{i}" for i in range(1, 8)]
RIGHT_ARM_JOINT_NAMES: List[str] = [f"right_arm_{i}" for i in range(1, 8)]
ALL_ARM_JOINT_NAMES: List[str] = LEFT_ARM_JOINT_NAMES + RIGHT_ARM_JOINT_NAMES
GRIPPER_NAMES: List[str] = ["left_gripper", "right_gripper"]


class XifengRobotProtocol(Protocol):
    """隙锋 Robot 的 duck-typing 契约，与 R2C SDK 的 VendorSDKHardwareAdapter 对齐。"""

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def get_observation(self) -> Mapping[str, object]: ...

    def send_action(self, command: Mapping[str, object]) -> None: ...
