"""Mock 版 AgiBot G01（智元精灵 G01）硬件适配器（无真机 / 无 GDK 环境）。

在 :class:`~cloudrobo_r2c.robots.agibot_g01.agibot_g01_hardware_adapter.AgiBotG01HardwareAdapter`
基础上只替换底层 client 为
:class:`~cloudrobo_r2c.robots.agibot_g01.agibot_g01_mock_client.AgiBotG01MockClient`：

- 观测组装（``get_observation``）、``state/current`` 采集（``get_runtime``）
- 动作按 ``joint_states.names`` 的第 7 / 8 章路由、互斥与完整性校验
- 扩展字段（头/腰/手/轮）解码、``go_home`` 复位命令
- ``ee_pose_channel`` / ``gripper_channel`` / ``ee_pose_repr`` 单选配置

全部复用真机适配器实现，因此具备与真机相同的全量功能，仅物理行为是仿真：
动作立即生效、关节与末端位姿状态独立维护（不做正运动学联动）。

初始关节与末端位姿默认取自云端示例
``examples/agibot_g01_cloud_adapter.py`` 的 ``GROUP_A``（joint_a）与
``GROUP_POSE_A``（pose_a）；可在 ``hardware.config`` 中覆盖：

- ``initial_joints``      双臂 14 关节（弧度）
- ``initial_gripper``     夹爪 2 通道（``[0, 1]`` 归一化）
- ``initial_ee_poses``    ``{left: [x,y,z,qx,qy,qz,qw], right: [...]}``
- ``initial_hand``        灵巧手 12 关节（弧度）
- ``initial_head``        头部 ``[yaw, pitch]``（弧度）
- ``initial_waist``       腰部 ``[pitch, height_cm]``
- ``initial_body_pose``   整体姿态关节状态（默认空数组，维度随整机配置）
- ``camera_width`` / ``camera_height``  合成相机帧分辨率（默认 64 / 48）

用法（``config/robot_agibot_g01_mock_config.yaml``，``hardware.type:
"agibot_g01_mock"``）：

    cloudrobo r2c client --robot-config config/robot_agibot_g01_mock_config.yaml

与真机适配器一致，``runtime.dry_run`` / ``hardware.config.dry_run`` 为 true 时
只打印动作、不更新 mock 状态。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional

from cloudrobo_r2c.core.interfaces import IRobotHardwareAdapter
from cloudrobo_r2c.robots.agibot_g01.agibot_g01_hardware_adapter import (
    AgiBotG01HardwareAdapter,
)
from cloudrobo_r2c.robots.agibot_g01.agibot_g01_mock_client import (
    ARM_DOF,
    DEFAULT_CAMERA_HEIGHT,
    DEFAULT_CAMERA_WIDTH,
    DEFAULT_INITIAL_EE_POSES,
    DEFAULT_INITIAL_GRIPPER,
    DEFAULT_INITIAL_HAND,
    DEFAULT_INITIAL_HEAD,
    DEFAULT_INITIAL_JOINTS,
    DEFAULT_INITIAL_WAIST,
    GRIPPER_DOF,
    HAND_DOF,
    HEAD_DOF,
    POSE_DOF,
    WAIST_DOF,
    AgiBotG01MockClient,
)

logger = logging.getLogger(__name__)


def _configured_vector(
    config: Mapping[str, Any],
    key: str,
    default: List[float],
    size: int,
) -> List[float]:
    """读取定长向量配置（缺失时用默认值），长度不符抛出 ``ValueError``。"""
    value = config.get(key, default)
    try:
        values = [float(v) for v in value]
    except TypeError as exc:
        raise ValueError(f"hardware.config.{key} must be a sequence of numbers") from exc
    if len(values) != size:
        raise ValueError(f"hardware.config.{key} expects {size} values, got {len(values)}")
    return values


def _configured_ee_poses(config: Mapping[str, Any]) -> Dict[str, List[float]]:
    """读取 ``initial_ee_poses``（左右各 7 维），缺失侧用 pose_a 默认值。"""
    raw = config.get("initial_ee_poses") or {}
    if not isinstance(raw, Mapping):
        raise ValueError("hardware.config.initial_ee_poses must be a mapping of side poses")
    result: Dict[str, List[float]] = {}
    for side in ("left", "right"):
        value = raw.get(side, DEFAULT_INITIAL_EE_POSES[side])
        result[side] = _configured_vector(
            {"initial_ee_poses": value},
            "initial_ee_poses",
            DEFAULT_INITIAL_EE_POSES[side],
            POSE_DOF,
        )
    return result


def create_agibot_g01_mock_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> IRobotHardwareAdapter:
    """Entry_point factory for :class:`AgiBotG01MockAdapter`."""
    del extra_kwargs  # 与真机 factory 签名一致；mock 不需要额外注入
    return AgiBotG01MockAdapter(config=dict(config))


class AgiBotG01MockAdapter(AgiBotG01HardwareAdapter):
    """AgiBot G01 的全功能 mock 适配器（接口与真机适配器一致，物理行为为仿真）。

    配置项除真机适配器支持的全部键（``dry_run`` / ``camera_enabled`` /
    ``ee_pose_channel`` / ``gripper_channel`` / ``ee_pose_repr`` /
    ``commands`` 等）外，增加初始状态与相机分辨率键，见模块 docstring。
    """

    def __init__(self, config: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(config)
        self._initial_joints = _configured_vector(
            self._config, "initial_joints", DEFAULT_INITIAL_JOINTS, ARM_DOF
        )
        self._initial_gripper = _configured_vector(
            self._config, "initial_gripper", DEFAULT_INITIAL_GRIPPER, GRIPPER_DOF
        )
        self._initial_ee_poses = _configured_ee_poses(self._config)
        self._initial_hand = _configured_vector(
            self._config, "initial_hand", DEFAULT_INITIAL_HAND, HAND_DOF
        )
        self._initial_head = _configured_vector(
            self._config, "initial_head", DEFAULT_INITIAL_HEAD, HEAD_DOF
        )
        self._initial_waist = _configured_vector(
            self._config, "initial_waist", DEFAULT_INITIAL_WAIST, WAIST_DOF
        )
        body_pose = self._config.get("initial_body_pose")
        self._initial_body_pose = [float(v) for v in body_pose] if body_pose is not None else []
        self._mock_camera_width = int(self._config.get("camera_width", DEFAULT_CAMERA_WIDTH))
        self._mock_camera_height = int(self._config.get("camera_height", DEFAULT_CAMERA_HEIGHT))

    def connect(self) -> None:
        """连接 mock 后端：创建 :class:`AgiBotG01MockClient`（无 GDK 依赖）。"""
        if self._connected:
            logger.debug("[AgiBotG01Mock] already connected, skipping connect()")
            return
        client = AgiBotG01MockClient(
            camera_group=self._camera_group,
            camera_key_map=self._camera_key_map,
            camera_enabled=self._camera_enabled,
            init_wait_s=self._init_wait_s,
            camera_poll_interval_s=self._camera_poll_interval_s,
            enable_motion_controller=self._enable_motion_controller,
            initial_joints=self._initial_joints,
            initial_gripper=self._initial_gripper,
            initial_ee_poses=self._initial_ee_poses,
            initial_hand=self._initial_hand,
            initial_head=self._initial_head,
            initial_waist=self._initial_waist,
            initial_body_pose=self._initial_body_pose,
            gripper_raw_range=self._gripper_raw_range,
            camera_width=self._mock_camera_width,
            camera_height=self._mock_camera_height,
        )
        client.connect()
        self._client = client
        self._connected = True
        logger.info(
            "[AgiBotG01Mock] adapter connected (dry_run=%s, cameras=%s, "
            "initial_joints=joint_a, initial_ee_poses=pose_a)",
            self._dry_run,
            self._camera_group if self._camera_enabled else [],
        )
