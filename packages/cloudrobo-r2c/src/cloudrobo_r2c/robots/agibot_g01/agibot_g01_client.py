"""AgiBot G01（智元精灵 G01）GDK 客户端封装。

将智元 GDK 的 ``a2d_sdk``（``RobotDds`` / ``CosineCamera``）隔离在一个窄接口后面，
使硬件适配器在没有机器人或 GDK 环境的机器上仍然可测试。

接口依据《智元精灵G01+GDK v1.5.0使用指南》：
- ``python 函数 RobotDds 接口使用``（手臂/手部/头部/腰部/轮子）
- ``python 函数 CosineCamera 接口使用``（相机图像）

注意事项（来自《指南》）：
- 状态查询函数返回 ``(数据, 时间戳)``，时间戳单位为纳秒
- 初始化后需等待资源就绪（示例使用 ``time.sleep(0.5)``）
- ``CosineCamera.get_latest_image`` 的第一帧返回 ``None``，需跳过
- 使用完毕后必须调用 ``close()`` 释放相机资源，``shutdown()`` 关闭 DDS 连接
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

try:
    from a2d_sdk.robot import CosineCamera as _CosineCamera
    from a2d_sdk.robot import RobotController as _RobotController
    from a2d_sdk.robot import RobotDds as _RobotDds
except ImportError as _exc:  # 无 GDK 环境的机器上触发（适配器会给出明确报错）
    _RobotDds = None  # type: ignore[assignment, misc]
    _CosineCamera = None  # type: ignore[assignment, misc]
    _RobotController = None  # type: ignore[assignment, misc]
    _A2D_IMPORT_ERROR: Optional[ImportError] = _exc
else:
    _A2D_IMPORT_ERROR = None

# G01 相机通道名（《指南》9.1 节 camera_group 取值）
G01_CAMERA_CHANNELS: List[str] = [
    "head",
    "hand_left",
    "hand_right",
    "head_depth",
    "hand_left_fisheye",
    "hand_right_fisheye",
    "back_left_fisheye",
    "back_right_fisheye",
    "head_center_fisheye",
    "head_left_fisheye",
    "head_right_fisheye",
]

# 默认启用的相机通道与 R2C 图像键（与 moz1 等适配器的 cam_high/cam_*_wrist 约定一致）
DEFAULT_CAMERA_GROUP: List[str] = ["head", "hand_left", "hand_right"]
DEFAULT_CAMERA_KEY_MAP: Dict[str, str] = {
    "head": "cam_high",
    "hand_left": "cam_left_wrist",
    "hand_right": "cam_right_wrist",
}


class AgiBotG01Client:
    """``a2d_sdk`` ``RobotDds`` + ``CosineCamera`` + ``RobotController`` 的线程安全轻量封装。

    接口范围（《指南》）：

    - ``RobotDds``：状态查询原样转发 ``(数据, 时间戳)``；关节空间运动控制
      ``move_arm`` / ``move_gripper`` / ``move_hand`` / ``move_head`` / ``move_waist``
      / ``move_wheel`` / ``reset``
    - ``RobotController``：末端绝对位姿 ``set_end_effector_pose_control`` 与
      关节绝对位置控制 ``set_joint_position_control``（由适配器按 ``joint_states.names``
      路由调用）
    - ``CosineCamera``：后台取流线程，``get_camera_images()`` 返回最近一帧
      非空图像缓存（G01 通道名 → ndarray）

    ``a2d_sdk`` 未安装时 ``connect()`` 抛出带部署指引的 ``RuntimeError``；
    ``RobotController`` 创建失败仅停用RobotController 能力，不影响RobotDds 关节控制。
    """

    def __init__(
        self,
        camera_group: Optional[Sequence[str]] = None,
        camera_key_map: Optional[Dict[str, str]] = None,
        camera_enabled: bool = True,
        init_wait_s: float = 0.5,
        camera_poll_interval_s: float = 0.03,
        enable_motion_controller: bool = True,
        camera_color_order: str = "rgb",
    ) -> None:
        self._lock = threading.RLock()
        self._camera_group: List[str] = list(
            camera_group if camera_group is not None else DEFAULT_CAMERA_GROUP
        )
        self._camera_key_map: Dict[str, str] = dict(
            camera_key_map if camera_key_map is not None else DEFAULT_CAMERA_KEY_MAP
        )
        self._camera_enabled = camera_enabled
        self._init_wait_s = float(init_wait_s)
        self._camera_poll_interval_s = float(camera_poll_interval_s)
        self._enable_motion_controller = enable_motion_controller
        # GDK CosineCamera 输出 RGB 通道序；OpenCV 的 JPEG 编码（ndarray_to_jpeg）
        # 期望 BGR，因此入缓存前统一做一次通道反转（可配关闭）。
        self._camera_color_order = str(camera_color_order).strip().lower()
        if self._camera_color_order not in ("rgb", "bgr"):
            raise ValueError(
                "camera_color_order must be 'rgb' or 'bgr', "
                f"got {camera_color_order!r}"
            )

        self._robot: Any = None
        self._camera: Any = None
        self._motion_controller: Any = None
        self._capture_thread: Optional[threading.Thread] = None
        self._running = False
        self._latest_images: Dict[str, np.ndarray] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """创建 ``RobotDds``，等待初始化；按需创建 ``CosineCamera`` 并启动取流。"""
        with self._lock:
            if self._robot is not None:
                return
            if _RobotDds is None:
                raise RuntimeError(
                    "a2d_sdk 未安装：请按《智元精灵G01 GDK 使用指南》在可与机器人通信的 "
                    "PC 上部署 GDK 环境（cd a2d_sdk && source env.sh）后再运行"
                ) from _A2D_IMPORT_ERROR

            self._robot = _RobotDds()
            if self._init_wait_s > 0:
                # 《指南》7.4 使用示例：等待资源初始化，收到消息
                time.sleep(self._init_wait_s)

            if self._camera_enabled and self._camera_group:
                try:
                    if _CosineCamera is None:
                        raise RuntimeError(
                            "a2d_sdk 未安装，无法初始化 CosineCamera"
                        ) from _A2D_IMPORT_ERROR
                    self._camera = _CosineCamera(list(self._camera_group))
                    self._start_camera_capture()
                except Exception:
                    # 相机初始化失败时回滚已创建的机器人连接，避免资源泄漏
                    self._shutdown_robot_safe()
                    self._robot = None
                    raise

            if self._enable_motion_controller:
                try:
                    if _RobotController is None:
                        raise RuntimeError(
                            "a2d_sdk 未安装，无法初始化 RobotController"
                        ) from _A2D_IMPORT_ERROR
                    # 《指南》8.2.2：RobotController() 提供（异步）运动控制接口
                    self._motion_controller = _RobotController()
                except Exception as exc:
                    # 运控器创建失败不阻断RobotDds 关节控制链路，只停用RobotController 能力
                    self._motion_controller = None
                    logger.warning(
                        "RobotController init failed; chapter-8 pose/joint control " "disabled: %s",
                        exc,
                    )

            logger.info(
                "AgiBot G01 client connected (camera_enabled=%s, camera_group=%s)",
                self._camera_enabled,
                self._camera_group,
            )

    def close(self) -> None:
        """停止取流线程，关闭相机并关闭 DDS 连接。"""
        with self._lock:
            self._running = False
            thread = self._capture_thread
            self._capture_thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

        with self._lock:
            if self._camera is not None:
                # 《指南》9.6：使用完毕后必须调用 close() 释放资源
                try:
                    self._camera.close()
                except Exception as exc:
                    logger.debug("CosineCamera.close() failed (ignorable): %s", exc)
                self._camera = None
            self._shutdown_robot_safe()
            self._latest_images.clear()
            logger.info("AgiBot G01 client closed")

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._robot is not None

    def _shutdown_robot_safe(self) -> None:
        # 《指南》7.3：shutdown() 关闭 DDS 连接
        if self._robot is not None:
            try:
                self._robot.shutdown()
            except Exception as exc:
                logger.debug("RobotDds.shutdown() failed (ignorable): %s", exc)
            self._robot = None

    def _require_robot(self) -> Any:
        if self._robot is None:
            raise RuntimeError("AgiBot G01 client is not connected. Call connect() first.")
        return self._robot

    # ------------------------------------------------------------------
    # 状态查询（《指南》7.1，返回 (数据, 时间戳) 原样转发）
    # ------------------------------------------------------------------

    def arm_joint_states(self) -> Any:
        with self._lock:
            return self._require_robot().arm_joint_states()

    def gripper_states(self) -> Any:
        with self._lock:
            return self._require_robot().gripper_states()

    def hand_joint_states(self) -> Any:
        with self._lock:
            return self._require_robot().hand_joint_states()

    def hand_force_states(self) -> Any:
        with self._lock:
            return self._require_robot().hand_force_states()

    def head_joint_states(self) -> Any:
        with self._lock:
            return self._require_robot().head_joint_states()

    def waist_joint_states(self) -> Any:
        with self._lock:
            return self._require_robot().waist_joint_states()

    def body_pose_joint_states(self) -> Any:
        with self._lock:
            return self._require_robot().body_pose_joint_states()

    # ------------------------------------------------------------------
    # 运动控制（《指南》7.2）
    # ------------------------------------------------------------------

    def move_arm(self, positions: Sequence[float]) -> None:
        with self._lock:
            self._require_robot().move_arm(self._as_float_list(positions))

    def move_gripper(self, positions: Sequence[float]) -> None:
        with self._lock:
            self._require_robot().move_gripper(self._as_float_list(positions))

    def move_hand(self, positions: Sequence[float]) -> None:
        with self._lock:
            self._require_robot().move_hand(self._as_float_list(positions))

    def move_head(self, positions: Sequence[float]) -> None:
        with self._lock:
            self._require_robot().move_head(self._as_float_list(positions))

    def move_waist(self, positions: Sequence[float]) -> None:
        with self._lock:
            self._require_robot().move_waist(self._as_float_list(positions))

    def move_wheel(self, linear: float, angular: float) -> None:
        with self._lock:
            self._require_robot().move_wheel(float(linear), float(angular))

    def reset(self, **targets: Any) -> None:
        with self._lock:
            self._require_robot().reset(**targets)

    # ------------------------------------------------------------------
    # RobotController（末端绝对位姿 / 关节绝对位置控制）
    # ------------------------------------------------------------------

    def _require_motion_controller(self) -> Any:
        if self._motion_controller is None:
            raise RuntimeError(
                "RobotController 不可用（初始化失败或被禁用），"
                "RobotController 末端位姿/关节控制不可用"
            )
        return self._motion_controller

    def get_motion_status(self) -> Any:
        """《指南》8.2.3：返回运控状态 dict（``frames`` 含左右末端位姿），无状态返回 None。

        适配器用它产出 ``left_ee_pose`` / ``right_ee_pose`` / ``ee_poses`` 观测
        （映射到 device_to_r2c 的 ``end_effector_poses``）。
        """
        with self._lock:
            return self._require_motion_controller().get_motion_status()

    def set_motion_control_mode(self, mode: int) -> None:
        """《指南》8.1：设置运动控制模式（0=stop，1=servo，2=planning）。"""
        with self._lock:
            self._require_motion_controller().set_motion_control_mode(int(mode))

    def set_end_effector_pose(
        self,
        lifetime: float,
        control_group: Sequence[str],
        left_pose: Optional[Dict[str, float]] = None,
        right_pose: Optional[Dict[str, float]] = None,
    ) -> None:
        """《指南》8.1：末端绝对位姿控制接口 ``set_end_effector_pose_control``。

        Args:
            lifetime: 指令有效时间（秒），超时未发送下一帧则停止运动
            control_group: ``['left_arm','right_arm','dual_arm',...]`` 的子集
            left_pose / right_pose: ``{x, y, z, qx, qy, qz, qw}`` 位姿 dict，可为 None
        """
        with self._lock:
            self._require_motion_controller().set_end_effector_pose_control(
                float(lifetime),
                list(control_group),
                left_pose=dict(left_pose) if left_pose else None,
                right_pose=dict(right_pose) if right_pose else None,
            )

    def set_joint_position_control(self, lifetime: float, joint_group: Dict[str, Any]) -> None:
        """《指南》8.1：关节绝对位置控制接口 ``set_joint_position_control``。

        Args:
            lifetime: 指令有效时间（秒）
            joint_group: 形如 ``{"left_arm": [7 维], "right_arm": [7 维], ...}`` 的
                部位 → 关节目标 dict（key 可为 GROUP_IDS 任意子集）
        """
        with self._lock:
            self._require_motion_controller().set_joint_position_control(
                float(lifetime), dict(joint_group)
            )

    # ------------------------------------------------------------------
    # 相机（《指南》9 章）
    # ------------------------------------------------------------------

    @property
    def camera_key_map(self) -> Dict[str, str]:
        """G01 相机通道名 → R2C 图像键（如 ``head → cam_high``）。"""
        return dict(self._camera_key_map)

    def get_camera_images(self) -> Dict[str, np.ndarray]:
        """返回最近一帧非空图像（G01 通道名 → HWC ndarray），无帧通道不出现。"""
        with self._lock:
            return dict(self._latest_images)

    def _normalize_camera_frame(self, arr: Any) -> Any:
        """按 camera_color_order 归一化通道序（RGB 源 → BGR，供 OpenCV 编码）。"""
        if self._camera_color_order == "rgb" and arr.ndim == 3 and arr.shape[2] == 3:
            return np.ascontiguousarray(arr[:, :, ::-1])
        return arr

    def _start_camera_capture(self) -> None:
        # 先同步采一轮，保证 connect() 返回后首次观测即可拿到图像
        self._poll_camera_once()
        if self._capture_thread is not None and self._capture_thread.is_alive():
            return
        self._running = True
        self._capture_thread = threading.Thread(
            target=self._camera_capture_loop,
            daemon=True,
            name="AgiBotG01CameraCapture",
        )
        self._capture_thread.start()
        logger.info(
            "AgiBot G01 camera capture started (channels=%s, %.0f Hz)",
            self._camera_group,
            1.0 / max(self._camera_poll_interval_s, 1e-3),
        )

    def _poll_camera_once(self) -> None:
        """同步轮询一轮相机通道，刷新非空帧缓存。"""
        with self._lock:
            camera = self._camera
        if camera is None:
            return
        for name in self._camera_group:
            try:
                # 真实 a2d_sdk 返回 (图像, 时间戳)（见《指南》9.5 使用示例）
                image, _camera_ts = camera.get_latest_image(name)
            except Exception as exc:
                logger.debug("AgiBot G01 camera %s poll failed: %s", name, exc)
                continue
            if image is None:
                continue
            arr = np.asarray(image)
            if arr.size == 0 or np.all(arr == 0):
                continue  # 相机未就绪时可能返回全零占位帧
            arr = self._normalize_camera_frame(arr)
            with self._lock:
                self._latest_images[name] = arr

    def _camera_capture_loop(self) -> None:
        # 《指南》9.6：get_latest_image 第一帧为 None，跳过空帧
        while self._running:
            try:
                with self._lock:
                    camera = self._camera
                if camera is None:
                    break
                for name in self._camera_group:
                    image, camera_ts = camera.get_latest_image(name)
                    if image is None:
                        continue
                    arr = np.asarray(image)
                    if arr.size == 0 or np.all(arr == 0):
                        continue  # 相机未就绪时可能返回全零占位帧
                    arr = self._normalize_camera_frame(arr)
                    with self._lock:
                        self._latest_images[name] = arr
            except Exception as exc:
                logger.warning("AgiBot G01 camera capture error: %s", exc)
            time.sleep(self._camera_poll_interval_s)

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _as_float_list(value: Any) -> List[float]:
        """将序列 / ndarray 转为 float 列表（GDK 接口要求 Python list）。"""
        if isinstance(value, np.ndarray):
            return value.reshape(-1).astype(float).tolist()
        return [float(v) for v in value]
