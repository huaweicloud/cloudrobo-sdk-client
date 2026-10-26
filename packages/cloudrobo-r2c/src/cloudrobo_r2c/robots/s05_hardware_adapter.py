"""S05 (华沿/Huayan) robot hardware adapter for R2C SDK.

基于华沿 S05 机器人官方 CPS SDK 和汉森夹爪 SDK 的适配器实现。
遵循 :class:`IRobotHardwareAdapter` 接口规范。

CPS SDK 和 gripper_control 的文件路径通过配置中 ``sdk_paths`` 字段指定，
适配器会动态将其所在目录加入 ``sys.path`` 后导入。
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Mapping

import numpy as np

from cloudrobo_r2c.core.interfaces import IRobotHardwareAdapter

logger = logging.getLogger(__name__)


def _import_from_file(module_name: str, file_path: str) -> Any:
    """从指定文件路径动态导入模块（不依赖包名解析）。

    使用 ``importlib.util.spec_from_file_location`` 直接从文件路径加载，
    避免 ``importlib.import_module`` 需要包名可解析的问题。

    Args:
        module_name: 导入后的模块名（仅用于缓存键，可任意指定）
        file_path:   文件的绝对或相对路径

    Returns:
        导入的模块对象
    """
    abs_path = Path(file_path).resolve()
    spec = importlib.util.spec_from_file_location(module_name, str(abs_path))
    if spec is None:
        raise ImportError(f"Could not load module from {abs_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def create_s05_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> IRobotHardwareAdapter:
    """Entry_point factory for S05HardwareAdapter."""
    return S05HardwareAdapter(config=dict(config))


@dataclass
class S05HardwareAdapter(IRobotHardwareAdapter):
    """华沿 S05 机器人硬件适配器。

    通过 CPS SDK 控制机械臂，RealSense 相机采集图像，Modbus 夹爪控制。
    通过 entry_point 注册（``hardware.type: s05``）。

    Config keys (``config`` 部分):

        **s05_config**
            robot_ip (str): 机器人 IP 地址，默认 ``192.168.0.10``
            robot_port (int): 机器人端口，默认 ``10003``
            gripper_slave_id (int): 夹爪 Modbus slave ID，默认 ``3``

        **sdk_paths** (可选)
            cps_sdk (str): CPS.py 文件路径（相对于配置文件或绝对路径）
            gripper_control (str): gripper_control.py 文件路径

        **cameras**
            width (int): 相机宽度，默认 ``640``
            height (int): 相机高度，默认 ``480``
            fps (int): 相机帧率，默认 ``30``

        dry_run (bool): 空运行模式（不操作硬件），默认 ``False``
    """

    config: Mapping[str, Any]

    # CPS 机械臂控制句柄
    _rbt: Any = field(default=None, init=False, repr=False)
    # RealSense 相机 pipeline
    _pipeline: Any = field(default=None, init=False, repr=False)
    # 夹爪控制句柄
    _gripper: Any = field(default=None, init=False, repr=False)
    _connected: bool = field(default=False, init=False, repr=False)
    _cmd_id: int = field(default=1, init=False, repr=False)
    _gripper_closed: bool = field(default=False, init=False, repr=False)

    _robot_ip: str = field(default="192.168.0.10", init=False, repr=False)
    _robot_port: int = field(default=10003, init=False, repr=False)
    _slave_id: int = field(default=3, init=False, repr=False)
    _dry_run: bool = field(default=False, init=False, repr=False)

    _camera_width: int = field(default=640, init=False, repr=False)
    _camera_height: int = field(default=480, init=False, repr=False)
    _camera_fps: int = field(default=30, init=False, repr=False)

    # SDK 文件路径缓存
    _cps_sdk_path: str = field(default="", init=False, repr=False)
    _gripper_ctrl_path: str = field(default="", init=False, repr=False)

    def __post_init__(self) -> None:
        """加载配置参数。"""
        s05_cfg = self.config.get("s05_config", {})
        if not isinstance(s05_cfg, Mapping):
            s05_cfg = {}

        self._robot_ip = str(s05_cfg.get("robot_ip", "192.168.0.10"))
        self._robot_port = int(s05_cfg.get("robot_port", 10003))
        self._slave_id = int(s05_cfg.get("gripper_slave_id", 3))

        self._dry_run = bool(self.config.get("dry_run", False))

        camera_cfg = self.config.get("cameras", {})
        if isinstance(camera_cfg, Mapping):
            self._camera_width = int(camera_cfg.get("width", 640))
            self._camera_height = int(camera_cfg.get("height", 480))
            self._camera_fps = int(camera_cfg.get("fps", 30))

        # 读取 SDK 文件路径配置
        sdk_cfg = self.config.get("sdk_paths", {})
        if isinstance(sdk_cfg, Mapping):
            self._cps_sdk_path = str(sdk_cfg.get("cps_sdk", ""))
            self._gripper_ctrl_path = str(sdk_cfg.get("gripper_control", ""))

        if self._dry_run:
            logger.info("[S05] DRY_RUN mode enabled, no physical hardware operations.")

    def connect(self) -> None:
        """连接机械臂、拉起相机流、初始化夹爪。"""
        if self._connected:
            logger.debug("[S05] Already connected, skipping connect().")
            return

        logger.info(
            "[S05] Connecting to robot (dry_run=%s, ip=%s, port=%d)...",
            self._dry_run,
            self._robot_ip,
            self._robot_port,
        )

        if self._dry_run:
            logger.info("[S05] Dry-run mode — skipping physical hardware handshake.")
            self._connected = True
            return

        # ---- 动态导入 SDK ----
        if not self._cps_sdk_path:
            raise RuntimeError(
                "[S05] 'sdk_paths.cps_sdk' is not configured in robot config"
            )
        if not self._gripper_ctrl_path:
            raise RuntimeError(
                "[S05] 'sdk_paths.gripper_control' is not configured in robot config"
            )

        cps_mod = _import_from_file("hardware.CPS", self._cps_sdk_path)
        gripper_mod = _import_from_file(
            "hardware.gripper_control", self._gripper_ctrl_path
        )

        # 1. 机械臂网络使能
        CPSClient = cps_mod.CPSClient
        self._rbt = CPSClient()
        ret = self._rbt.HRIF_Connect(0, self._robot_ip, self._robot_port)
        if ret != 0:
            raise RuntimeError(f"[S05] 机械臂连接失败 (ret={ret})")
        if hasattr(self._rbt, "HRIF_GrpEnable"):
            self._rbt.HRIF_GrpEnable(0, 0)

        # 2. 相机流拉起 (RealSense)
        import pyrealsense2 as rs

        self._pipeline = rs.pipeline()
        cam_config = rs.config()
        cam_config.enable_stream(
            rs.stream.color,
            self._camera_width,
            self._camera_height,
            rs.format.bgr8,
            self._camera_fps,
        )
        self._pipeline.start(cam_config)

        # 3. 夹爪初始化
        HansGripper = gripper_mod.HansGripper
        self._gripper = HansGripper(
            robot_ip=self._robot_ip,
            gripper_id=1,
            gripper_type="DHPGI",
            connect_type="ModbusRTU",
            uart_port="End",
            slave_id=self._slave_id,
            verbose=False,
            register_gripper=False,
        )
        self._gripper.setup()

        self._connected = True
        logger.info("[S05] 华沿 S05 物理硬件全机群 Ready!")

    def disconnect(self) -> None:
        """断开连接，释放资源。"""
        if not self._connected:
            logger.debug("[S05] Already disconnected, skipping disconnect().")
            return

        if self._dry_run:
            self._connected = False
            return

        # 释放相机
        if self._pipeline is not None:
            try:
                self._pipeline.stop()
            except Exception:
                logger.debug("[S05] Error stopping camera pipeline.", exc_info=True)
            self._pipeline = None

        # 断开夹爪
        if self._gripper is not None:
            try:
                self._gripper.disconnect_gripper()
            except Exception:
                logger.debug("[S05] Error disconnecting gripper.", exc_info=True)
            self._gripper = None

        # 断开机械臂
        if self._rbt is not None:
            try:
                self._rbt.HRIF_DisConnect(0)
            except Exception:
                logger.debug("[S05] Error disconnecting robot.", exc_info=True)
            self._rbt = None

        self._connected = False
        logger.info("[S05] Robot disconnected.")

    def get_observation(self) -> Mapping[str, Any]:
        """获取观测数据。

        Returns:
            dict 包含:
                - ``joint_positions``: List[float] 6 关节弧度值
                - ``gripper_position``: List[float] [1.0 if closed else 0.0]
                - ``camera_image``: np.ndarray (H, W, 3) BGR 图像
        """
        self._ensure_connected()

        if self._dry_run:
            joint_radians = [0.0] * 6
            gripper_state = [0.0]
            frame_bgr = np.zeros(
                (self._camera_height, self._camera_width, 3), dtype=np.uint8
            )
        else:
            # 物理读取关节角度（度 -> 弧度）
            res_data: List[float] = []
            ret = self._rbt.HRIF_ReadActPos(0, 0, res_data)
            if ret == 0 and len(res_data) >= 6:
                joint_radians = [math.radians(float(x)) for x in res_data[0:6]]
            else:
                logger.warning("[S05] Failed to read joint positions, using zeros.")
                joint_radians = [0.0] * 6

            gripper_state = [1.0 if self._gripper_closed else 0.0]

            # 物理捕获图像
            try:
                frames = self._pipeline.wait_for_frames()
                color_frame = frames.get_color_frame()
                if color_frame is not None:
                    frame_bgr = np.asanyarray(color_frame.get_data())
                else:
                    frame_bgr = np.zeros(
                        (self._camera_height, self._camera_width, 3), dtype=np.uint8
                    )
            except Exception:
                logger.warning("[S05] Failed to capture camera frame, using dummy.")
                frame_bgr = np.zeros(
                    (self._camera_height, self._camera_width, 3), dtype=np.uint8
                )

        # 估算 JPEG 编码后大小（用于日志）
        try:
            import cv2
            _, jpeg_buf = cv2.imencode(".jpg", frame_bgr)
            image_bytes = len(jpeg_buf)
        except Exception:
            image_bytes = frame_bgr.nbytes

        obs = {
            "joint_positions": joint_radians,
            "gripper_position": gripper_state,
            "camera_image": frame_bgr,
        }
        logger.info(
            "[S05] >> send to cloud adapter: joint_positions=%s gripper_position=%s camera_image=%d bytes",
            [round(v, 4) for v in joint_radians],
            gripper_state,
            image_bytes,
        )
        return obs

    def send_action(self, command: Mapping[str, Any]) -> None:
        """执行动作命令。

        ``command`` 字典预期包含:
            - ``arm_action``: List[float] 6 关节弧度目标值
            - ``gripper_action``: List[float] 夹爪开关值 ([0.0]=开, [1.0]=关)
        """
        self._ensure_connected()

        target_joints = command.get("arm_action")
        gripper_action = command.get("gripper_action")

        pred_gripper_val = float(gripper_action[0]) if gripper_action else 0.0

        logger.info(
            "[S05] << recv from cloud adapter: arm_action=%s gripper_action=%.3f",
            [round(v, 4) for v in target_joints] if target_joints is not None else None,
            pred_gripper_val,
        )

        if self._dry_run:
            return

        if target_joints is None or not isinstance(target_joints, (list, np.ndarray)):
            logger.warning("[S05] No valid arm_action in command, skipping.")
            return

        if len(target_joints) != 6:
            logger.warning(
                "[S05] Invalid target_joints length: expected 6, got %d, skipping.",
                len(target_joints),
            )
            return

        # 读取当前位姿作为 MoveJ 的参考
        result_data: List[float] = []
        self._rbt.HRIF_ReadActPos(0, 0, result_data)
        current_pose = [float(result_data[i]) for i in range(6, 12)]

        # 发送 MoveJ 指令（CPS SDK 使用弧度制）
        self._rbt.HRIF_MoveJ(
            0, 0,
            current_pose,
            target_joints,
            "TCP",
            "Base",
            20.0, 30.0, 0.0,
            1, 0, 0, 0,
            str(self._cmd_id),
        )

        # 轮询等待运动完成（最多 5 秒）
        start_time = time.time()
        while time.time() - start_time < 5.0:
            done_res: List[str] = []
            if hasattr(self._rbt, "HRIF_IsMotionDone"):
                self._rbt.HRIF_IsMotionDone(0, 0, done_res)
                if (
                    len(done_res) > 0
                    and str(done_res[0]).strip().lower() in ("1", "true")
                ):
                    break
            time.sleep(0.02)
        self._cmd_id += 1

        # 夹爪控制
        if pred_gripper_val >= 0.5 and not self._gripper_closed:
            if self._gripper.close_and_wait():
                self._gripper_closed = True
                logger.debug("[S05] Gripper closed.")
        elif pred_gripper_val < 0.5 and self._gripper_closed:
            if self._gripper.open_and_wait():
                self._gripper_closed = False
                logger.debug("[S05] Gripper opened.")

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("[S05] Adapter is not connected. Call connect() first.")
