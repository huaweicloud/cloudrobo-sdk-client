"""Moz1 robot hardware adapter for R2C SDK.

Camera keys (3 cameras, RGB uint8 HWC):
  - ``cam_high``       — overhead / high-angle camera
  - ``cam_left_wrist`` — left wrist camera
  - ``cam_right_wrist`` — right wrist camera
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np

from cloudrobo_r2c.core.interfaces import IRobotHardwareAdapter

logger = logging.getLogger(__name__)


def create_moz1_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> IRobotHardwareAdapter:
    """Entry_point factory for Moz1HardwareAdapter."""
    return Moz1HardwareAdapter(config=dict(config))


# ---------------------------------------------------------------------------
# SDK 导入保护 —— 只在有 SDK 的环境下可用
# ---------------------------------------------------------------------------
try:
    from mozrobot import MOZ1Robot, MOZ1RobotConfig as _MOZ1RobotConfig
except ImportError as _e:
    MOZ1Robot = None  # type: ignore[misc, assignment]
    _MOZ1RobotConfig = None
    _MOZ_IMPORT_ERROR = _e
else:
    _MOZ_IMPORT_ERROR = None


# ---------------------------------------------------------------------------
# HardwareAdapter
# ---------------------------------------------------------------------------


@dataclass
class Moz1HardwareAdapter(IRobotHardwareAdapter):
    """Moz1 机器人硬件适配器（Custom HardwareAdapter 方式）。

    YAML 配置示例见 ``config/robot_moz1_config.yaml``。

    观测返回的原始字段（由 ``ConfigurableDeviceTranslator`` 映射到 R2C 标准格式）：

    **状态字段：**
      - ``leftarm_state_joint_pos``   — 左臂 7 维关节位置 (rad)
      - ``rightarm_state_joint_pos``  — 右臂 7 维关节位置 (rad)
      - ``leftarm_state_cart_pos``    — 左臂笛卡尔位置 6D
      - ``rightarm_state_cart_pos``   — 右臂笛卡尔位置 6D
      - ``leftarm_gripper_state_pos`` — 左夹爪位置
      - ``rightarm_gripper_state_pos``— 右夹爪位置
      - ``torso_state_joint_pos``     — 躯干关节位置（wholebody 系列配置下存在）
      - ``torso_state_cart_pos``      — 躯干笛卡尔位置
      - ``base_state_speed``          — 底盘速度（wholebody 配置下存在）

    **图像字段：**
      - ``cam_high``           — uint8 HWC RGB
      - ``cam_left_wrist``     — uint8 HWC RGB
      - ``cam_right_wrist``    — uint8 HWC RGB
    """

    config: Mapping[str, Any]

    _driver: Any = field(default=None, init=False, repr=False)
    _connected: bool = field(default=False, init=False, repr=False)
    _last_observation: Dict[str, Any] = field(default_factory=dict, init=False, repr=False)


    CAMERA_KEYS: List[str] = field(
        default_factory=lambda: ["cam_high", "cam_left_wrist", "cam_right_wrist"],
        init=False,
    )

    def __post_init__(self) -> None:
        """验证配置并构建 SDK 配置对象。"""
        if MOZ1Robot is None:
            raise RuntimeError(
                "mozrobot 未安装，无法创建 Moz1HardwareAdapter"
            ) from _MOZ_IMPORT_ERROR

        dry_run = self.config.get("dry_run", False)
        if dry_run:
            logger.info("[DRY_RUN] DRY_RUN MODE")

        # 从 config 中提取 MozDriver 参数
        self._structure = str(self.config.get("structure", "wholebody"))
        self._robot_control_hz = int(self.config.get("robot_control_hz", 120))
        self._camera_mode = str(self.config.get("camera_mode", "off")).lower()
        self._realsense_serials = str(
            self.config.get("realsense_serials"))
        self._camera_resolutions = str(
            self.config.get("camera_resolutions", "320*240, 320*240, 320*240")
        )
        self._enable_soft_realtime = bool(self.config.get("enable_soft_realtime", False))
        self._bind_cpu_idxs: Optional[List[int]] = self.config.get("bind_cpu_idxs")
        self._disabled_cameras: Optional[List[str]] = self.config.get("disabled_cameras")

    def _build_sdk_config(self) -> _MOZ1RobotConfig:
        """由 adapter 配置字段构建 MOZ1RobotConfig。"""
        no_camera = self._camera_mode == "off"
        return _MOZ1RobotConfig(
            realsense_serials=self._realsense_serials,
            camera_resolutions=self._camera_resolutions,
            no_camera=no_camera,
            structure=self._structure,
            robot_control_hz=self._robot_control_hz,
            enable_soft_realtime=self._enable_soft_realtime,
            bind_cpu_idxs=self._bind_cpu_idxs,
            disabled_cameras=self._disabled_cameras,
        )

    # ------------------------------------------------------------------
    # IRobotHardwareAdapter 接口实现
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """初始化 MOZ1Robot 并建立连接。

        流程：
          1. 构建 SDK 配置
          2. 创建 MOZ1Robot 实例
          3. 调用 robot.connect()
          4. 调用 robot.enable_external_following_mode() 以准备接收指令
        """
        if self._connected:
            logger.debug("Already connected, skipping connect()")
            return

        if MOZ1Robot is None:
            raise RuntimeError("mozrobot SDK 不可用") from _MOZ_IMPORT_ERROR

        sdk_cfg = self._build_sdk_config()
        robot = MOZ1Robot(sdk_cfg)
        robot.connect()

        if not robot.is_robot_connected:
            raise RuntimeError("MOZ1Robot.connect() 后仍未连接")

        # 启用外部跟随模式（必须，否则 send_action 指令不生效）
        robot.enable_external_following_mode()

        self._driver = robot
        self._connected = True
        logger.info(
            "Moz1 robot connected: structure=%s, hz=%s, camera=%s",
            self._structure,
            self._robot_control_hz,
            self._camera_mode,
        )

    def disconnect(self) -> None:
        """断开机器人连接。"""
        if not self._connected:
            logger.debug("Already disconnected, skipping disconnect()")
            return

        if self._driver is not None:
            try:
                self._driver.disconnect()
            except Exception as e:
                logger.warning("Error during robot disconnect: %s", e)
            self._driver = None

        self._connected = False
        logger.info("Moz1 robot disconnected")

    def get_observation(self) -> Mapping[str, Any]:
        """捕获一帧观测数据。

        Returns:
            包含 raw 设备字段的字典，后续由 ConfigurableDeviceTranslator 映射到 R2C 格式。

            关键字段：
              - 双臂关节 (14 维): ``leftarm_state_joint_pos``, ``rightarm_state_joint_pos``
              - 双臂笛卡尔 (6D): ``leftarm_state_cart_pos``, ``rightarm_state_cart_pos``
              - 夹爪: ``leftarm_gripper_state_pos``, ``rightarm_gripper_state_pos``
              - 躯干（如有）: ``torso_state_joint_pos``, ``torso_state_cart_pos``
              - 底盘速度（如有）: ``base_state_speed``
              - 三路相机（如启用）: ``cam_high``, ``cam_left_wrist``, ``cam_right_wrist``
        """
        self._ensure_connected()

        try:
            obs = self._driver.capture_observation()
        except Exception as e:
            logger.warning("capture_observation failed, returning last observation: %s", e)
            return dict(self._last_observation)

        if obs is None:
            logger.warning("capture_observation returned None, returning last observation")
            return dict(self._last_observation)

        # --- 过滤掉全黑图像（相机未就绪时 SDK 返回全黑占位） ---
        for cam_key in self.CAMERA_KEYS:
            if cam_key in obs:
                img = obs[cam_key]
                arr = np.asarray(img) if img is not None else np.array([], dtype=np.uint8)
                if arr.size == 0 or np.all(arr == 0):
                    logger.debug("Camera %s returned blank frame, excluding", cam_key)
                    del obs[cam_key]

        # --- 预拼接字段供 Translator 映射使用 ---
        # 左右臂 7+7=14 维关节列表
        left_joint = np.asarray(obs.get("leftarm_state_joint_pos", np.zeros(7)), dtype=np.float64).reshape(-1)
        right_joint = np.asarray(obs.get("rightarm_state_joint_pos", np.zeros(7)), dtype=np.float64).reshape(-1)
        obs["joint_positions"] = np.concatenate([left_joint, right_joint])

        # 左右夹爪 2 维列表
        left_grip = np.asarray(obs.get("leftarm_gripper_state_pos", [0.0]), dtype=np.float64).reshape(-1)
        right_grip = np.asarray(obs.get("rightarm_gripper_state_pos", [0.0]), dtype=np.float64).reshape(-1)
        obs["gripper_positions"] = np.concatenate([left_grip, right_grip])

        self._last_observation = obs
        return obs

    def send_action(self, command: Mapping[str, Any]) -> None:
        """发送动作指令给 Moz1 机器人。

        支持的动作格式（由 Translator 映射后的字段）：
          - ``leftarm_cmd_joint_pos``  — 左臂 7 维关节目标 (rad)
          - ``rightarm_cmd_joint_pos`` — 右臂 7 维关节目标 (rad)
          - ``leftarm_cmd_cart_pos``   — 左臂笛卡尔目标 6D
          - ``rightarm_cmd_cart_pos``  — 右臂笛卡尔目标 6D
          - ``torso_cmd_joint_pos``    — 躯干关节目标 (rad)
          - ``leftarm_gripper_cmd_pos``— 左夹爪目标开度 (m)
          - ``rightarm_gripper_cmd_pos``— 右夹爪目标开度 (m)
          - ``base_cmd_speed``         — 底盘速度 [vx, vy, wz]（wholebody 配置）
        """
        self._ensure_connected()

        # 调试模式：只打印 action，不执行
        dry_run = self.config.get("dry_run", False)
        if dry_run:
            obs_without_images = {
                k: v for k, v in self._last_observation.items()
                if not isinstance(v, np.ndarray)
            }
            logger.info("[DRY_RUN] observation: %s", obs_without_images)
            logger.info("[DRY_RUN] action: %s", command)
            return

        # 构造动作字典（只包含 Moz robot SDK 能识别的字段）
        action: Dict[str, Any] = {}

        # 关节控制
        if "leftarm_cmd_joint_pos" in command:
            action["leftarm_cmd_joint_pos"] = self._to_python_list(
                command["leftarm_cmd_joint_pos"]
            )
        if "rightarm_cmd_joint_pos" in command:
            action["rightarm_cmd_joint_pos"] = self._to_python_list(
                command["rightarm_cmd_joint_pos"]
            )

        # 笛卡尔控制
        if "leftarm_cmd_cart_pos" in command:
            action["leftarm_cmd_cart_pos"] = self._to_python_list(
                command["leftarm_cmd_cart_pos"]
            )
        if "rightarm_cmd_cart_pos" in command:
            action["rightarm_cmd_cart_pos"] = self._to_python_list(
                command["rightarm_cmd_cart_pos"]
            )

        # 躯干控制
        if "torso_cmd_joint_pos" in command:
            action["torso_cmd_joint_pos"] = self._to_python_list(
                command["torso_cmd_joint_pos"]
            )

        # 夹爪控制
        if "leftarm_gripper_cmd_pos" in command:
            action["leftarm_gripper_cmd_pos"] = self._to_python_list(
                command["leftarm_gripper_cmd_pos"]
            )
        if "rightarm_gripper_cmd_pos" in command:
            action["rightarm_gripper_cmd_pos"] = self._to_python_list(
                command["rightarm_gripper_cmd_pos"]
            )

        # 底盘速度控制
        if "base_cmd_speed" in command:
            action["base_cmd_speed"] = self._to_python_list(
                command["base_cmd_speed"]
            )

        if not action:
            logger.warning("send_action received empty command (no Moz SDK-recognized fields)")
            return

        # 确保外部跟随模式已启用
        self._driver.enable_external_following_mode()
        self._driver.send_action(action)

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _to_python_list(value: Any) -> list:
        """将 numpy 数组或序列转为 Python list（避免 ROS message 校验失败）。"""
        if isinstance(value, np.ndarray):
            return value.reshape(-1).tolist()
        if isinstance(value, (list, tuple)):
            return list(value)
        return [float(value)]

    def _ensure_connected(self) -> None:
        if not self._connected or self._driver is None:
            raise RuntimeError("Moz1 adapter is not connected. Call connect() first.")
