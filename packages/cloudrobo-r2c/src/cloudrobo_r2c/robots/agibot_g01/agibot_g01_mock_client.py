"""Mock 版 AgiBot G01（智元精灵 G01）客户端：内存仿真后端。

实现与 :class:`~cloudrobo_r2c.robots.agibot_g01.agibot_g01_client.AgiBotG01Client`
相同的窄接口（RobotDds 状态查询/关节空间运动控制、RobotController 末端位姿与
关节绝对位置控制、CosineCamera 相机图像），但状态保存在内存中、动作直接更新
状态，不依赖 ``a2d_sdk``，可在无真机、无 GDK 环境的机器上跑通完整数据链路。

默认初始状态与云端示例 ``examples/agibot_g01_cloud_adapter.py`` 一致：

- 双臂 14 关节：``GROUP_A``（joint_a）的目标关节角（弧度）
- 末端位姿：``GROUP_POSE_A``（pose_a）的 left/right 7 维位姿
  （``[x, y, z, qx, qy, qz, qw]``）
- 夹爪：joint_a 的 ``[0.15, 0.15]``（``[0, 1]`` 归一化）
- 灵巧手 / 头部 / 腰部：零位

与真机的差异（仅物理行为，接口保持全量）：

- 动作立即生效（无运动时间与角速度限制）；关节与末端位姿状态独立维护，
  不做正运动学联动（真机末端位姿由运控器按机械臂模型计算）
- 夹爪内部保存 ``[0, 1]`` 归一化，``gripper_states`` 返回 ``[35, 120]`` 原始
  量程（与 GDK 一致）；``move_gripper`` / ``reset`` 同时接受 ``[0, 1]`` 与
  ``[35, 120]`` 输入
- 相机图像为合成渐变帧（各通道底色不同），分辨率默认 64x48，可在适配器配置中
  通过 ``camera_width`` / ``camera_height`` 修改；``init_wait_s`` 不实际等待
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# G01 关节维度（《指南》7.1）
ARM_DOF = 14
GRIPPER_DOF = 2
HAND_DOF = 12
HEAD_DOF = 2
WAIST_DOF = 2
POSE_DOF = 7

# ---------------------------------------------------------------------------
# 初始状态默认值：与 examples/agibot_g01_cloud_adapter.py 的
# GROUP_A（joint_a）与 GROUP_POSE_A（pose_a）一致（真机调试采集的目标值）。
# ---------------------------------------------------------------------------
DEFAULT_INITIAL_JOINTS: List[float] = [
    -1.125699520111084,
    0.5228543281555176,
    0.10490940511226654,
    -1.283866286277771,
    0.7302126884460449,
    1.476741075515747,
    -0.16992810368537903,
    1.1257692575454712,
    -0.522889256477356,
    -0.10499665141105652,
    1.2836743593215942,
    -0.7302126884460449,
    -1.495011329650879,
    0.16991065442562103,
]

DEFAULT_INITIAL_GRIPPER: List[float] = [0.15, 0.15]

DEFAULT_INITIAL_EE_POSES: Dict[str, List[float]] = {
    "left": [
        0.45006241859210033,
        0.34431358357149866,
        1.0250712982705688,
        0.11851912989635048,
        0.6617943563342805,
        -0.6437205892683082,
        0.3655205175154617,
    ],
    "right": [
        0.4481309242140103,
        -0.3455449250377566,
        1.0226385863602234,
        -0.6659388881998952,
        -0.12370331875302967,
        -0.3598052659951305,
        0.6416876628662489,
    ],
}

DEFAULT_INITIAL_HAND: List[float] = [0.0] * HAND_DOF
DEFAULT_INITIAL_HEAD: List[float] = [0.0] * HEAD_DOF
DEFAULT_INITIAL_WAIST: List[float] = [0.0] * WAIST_DOF
# 整体姿态维度随整机配置变化，mock 默认不伪造维度（空数组）
DEFAULT_INITIAL_BODY_POSE: List[float] = []

DEFAULT_CAMERA_WIDTH = 64
DEFAULT_CAMERA_HEIGHT = 48

# GDK 夹爪原始量程（《指南》7.2.1：[35, 120]）
DEFAULT_GRIPPER_RAW_RANGE = [35.0, 120.0]

# RobotController 位姿 dict 的键（《指南》8.1）
_POSE_DICT_KEYS = ("x", "y", "z", "qx", "qy", "qz", "qw")

# 运动控制模式（《指南》8.1：0=stop，1=servo，2=planning）
_MOTION_MODE_NAMES = {0: "STOP", 1: "SERVO", 2: "PLANNING"}

# RobotController.get_motion_status() frames 中左右末端的 link 键（《指南》8.2.3）
_LEFT_EE_FRAME = "arm_left_link7"
_RIGHT_EE_FRAME = "arm_right_link7"


def _as_vector(value: Any, size: int, name: str) -> List[float]:
    """把序列规整为指定长度的 float 列表，长度不符时抛出 ``ValueError``。"""
    try:
        values = [float(v) for v in value]
    except TypeError as exc:
        raise ValueError(f"{name} must be a sequence of numbers") from exc
    if len(values) != size:
        raise ValueError(f"{name} expects {size} values, got {len(values)}")
    return values


def _resize(values: Any, size: int, name: str) -> List[float]:
    """按期望维度规整（超长截断、不足补零），用于宽松处理组名关节输入。"""
    values = [float(v) for v in values]
    if len(values) == size:
        return values
    logger.debug("[AgiBotG01Mock] %s expects %d values, got %d; resizing", name, size, len(values))
    result = [0.0] * size
    for index, value in enumerate(values[:size]):
        result[index] = value
    return result


def _pose_dict_to_list(pose: Any, name: str) -> List[float]:
    """把 GDK 位姿 dict ``{x, y, z, qx, qy, qz, qw}`` 转为 7 维列表。"""
    try:
        return [float(pose[key]) for key in _POSE_DICT_KEYS]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain {list(_POSE_DICT_KEYS)}") from exc


class AgiBotG01MockClient:
    """``AgiBotG01Client`` 的内存仿真实现（线程安全）。

    状态查询方法返回 ``(数据, 时间戳)``（时间戳为 ``time.time_ns()``），与 GDK
    接口约定一致；运动控制方法直接更新内部状态。``get_motion_status`` 按
    《指南》8.2.3 结构返回 ``frames``（含左右末端位姿），供适配器观测/状态发布。
    """

    def __init__(
        self,
        camera_group: Optional[Sequence[str]] = None,
        camera_key_map: Optional[Dict[str, str]] = None,
        camera_enabled: bool = True,
        init_wait_s: float = 0.0,
        camera_poll_interval_s: float = 0.03,
        enable_motion_controller: bool = True,
        initial_joints: Optional[Sequence[float]] = None,
        initial_gripper: Optional[Sequence[float]] = None,
        initial_ee_poses: Optional[Dict[str, Sequence[float]]] = None,
        initial_hand: Optional[Sequence[float]] = None,
        initial_head: Optional[Sequence[float]] = None,
        initial_waist: Optional[Sequence[float]] = None,
        initial_body_pose: Optional[Sequence[float]] = None,
        gripper_raw_range: Optional[Sequence[float]] = None,
        camera_width: int = DEFAULT_CAMERA_WIDTH,
        camera_height: int = DEFAULT_CAMERA_HEIGHT,
    ) -> None:
        self._lock = threading.RLock()
        self._camera_group: List[str] = list(
            camera_group if camera_group is not None else ["head", "hand_left", "hand_right"]
        )
        self._camera_key_map: Dict[str, str] = dict(camera_key_map or {})
        self._camera_enabled = bool(camera_enabled)
        self._camera_poll_interval_s = float(camera_poll_interval_s)
        self._init_wait_s = float(init_wait_s)
        self._enable_motion_controller = bool(enable_motion_controller)

        raw_range = (
            gripper_raw_range if gripper_raw_range is not None else DEFAULT_GRIPPER_RAW_RANGE
        )
        self._gripper_raw_range = [float(raw_range[0]), float(raw_range[1])]
        if self._gripper_raw_range[1] <= self._gripper_raw_range[0]:
            raise ValueError("gripper_raw_range must be [low, high] with high > low")

        self._camera_width = int(camera_width)
        self._camera_height = int(camera_height)
        if self._camera_width <= 0 or self._camera_height <= 0:
            raise ValueError("camera_width / camera_height must be positive")

        self._arm = _as_vector(
            DEFAULT_INITIAL_JOINTS if initial_joints is None else initial_joints,
            ARM_DOF,
            "initial_joints",
        )
        gripper = _as_vector(
            DEFAULT_INITIAL_GRIPPER if initial_gripper is None else initial_gripper,
            GRIPPER_DOF,
            "initial_gripper",
        )
        self._gripper = [self._normalize_gripper(value) for value in gripper]
        self._hand = _as_vector(
            DEFAULT_INITIAL_HAND if initial_hand is None else initial_hand,
            HAND_DOF,
            "initial_hand",
        )
        self._head = _as_vector(
            DEFAULT_INITIAL_HEAD if initial_head is None else initial_head,
            HEAD_DOF,
            "initial_head",
        )
        self._waist = _as_vector(
            DEFAULT_INITIAL_WAIST if initial_waist is None else initial_waist,
            WAIST_DOF,
            "initial_waist",
        )
        self._body_pose = [float(v) for v in (initial_body_pose or DEFAULT_INITIAL_BODY_POSE)]

        poses = initial_ee_poses or {}
        self._ee_poses: Dict[str, List[float]] = {}
        for side in ("left", "right"):
            default = DEFAULT_INITIAL_EE_POSES[side]
            value = poses.get(side, default)
            self._ee_poses[side] = _as_vector(value, POSE_DOF, f"initial_ee_poses.{side}")

        self._motion_mode = 1
        self._wheel_speed = [0.0, 0.0]
        self._connected = False
        self._latest_images: Dict[str, np.ndarray] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """标记连接并生成相机帧（幂等；``init_wait_s`` 不实际等待）。"""
        with self._lock:
            if self._connected:
                return
            self._connected = True
            if self._camera_enabled:
                self._latest_images = self._build_camera_images()
        logger.info(
            "[AgiBotG01Mock] client connected (camera_enabled=%s, camera_group=%s, "
            "motion_controller=%s)",
            self._camera_enabled,
            self._camera_group,
            self._enable_motion_controller,
        )

    def close(self) -> None:
        """清空相机帧并标记断开（幂等）。"""
        with self._lock:
            self._connected = False
            self._latest_images.clear()
        logger.info("[AgiBotG01Mock] client closed")

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("AgiBot G01 mock client is not connected. Call connect() first.")

    def _require_motion_controller(self) -> None:
        self._require_connected()
        if not self._enable_motion_controller:
            raise RuntimeError(
                "RobotController 不可用（mock 已通过 enable_motion_controller: false 禁用），"
                "末端位姿/关节绝对位置控制不可用"
            )

    # ------------------------------------------------------------------
    # 状态查询（《指南》7.1，返回 (数据, 时间戳)）
    # ------------------------------------------------------------------

    def _state(self, values: List[float]) -> Any:
        with self._lock:
            return list(values), time.time_ns()

    def arm_joint_states(self) -> Any:
        return self._state(self._arm)

    def gripper_states(self) -> Any:
        # 与 GDK 一致返回原始量程 [35, 120]；适配器观测侧再归一化到 [0, 1]
        with self._lock:
            raw = [self._raw_gripper(value) for value in self._gripper]
            return raw, time.time_ns()

    def hand_joint_states(self) -> Any:
        return self._state(self._hand)

    def hand_force_states(self) -> Any:
        with self._lock:
            return [0.0] * HAND_DOF, time.time_ns()

    def head_joint_states(self) -> Any:
        return self._state(self._head)

    def waist_joint_states(self) -> Any:
        return self._state(self._waist)

    def body_pose_joint_states(self) -> Any:
        # 《指南》7.1.1：body_pose 返回 list（无时间戳）
        with self._lock:
            return list(self._body_pose)

    # ------------------------------------------------------------------
    # 运动控制（《指南》7.2）：立即更新状态
    # ------------------------------------------------------------------

    def move_arm(self, positions: Sequence[float]) -> None:
        with self._lock:
            self._require_connected()
            self._arm = _as_vector(positions, ARM_DOF, "move_arm.positions")

    def move_gripper(self, positions: Sequence[float]) -> None:
        with self._lock:
            self._require_connected()
            values = [float(v) for v in positions]
            if len(values) != GRIPPER_DOF:
                raise ValueError(
                    f"move_gripper.positions expects {GRIPPER_DOF} values, got {len(values)}"
                )
            self._gripper = [self._normalize_gripper(value) for value in values]

    def move_hand(self, positions: Sequence[float]) -> None:
        with self._lock:
            self._require_connected()
            self._hand = _as_vector(positions, HAND_DOF, "move_hand.positions")

    def move_head(self, positions: Sequence[float]) -> None:
        with self._lock:
            self._require_connected()
            self._head = _as_vector(positions, HEAD_DOF, "move_head.positions")

    def move_waist(self, positions: Sequence[float]) -> None:
        with self._lock:
            self._require_connected()
            self._waist = _as_vector(positions, WAIST_DOF, "move_waist.positions")

    def move_wheel(self, linear: float, angular: float) -> None:
        with self._lock:
            self._require_connected()
            self._wheel_speed = [float(linear), float(angular)]

    def reset(self, **targets: Any) -> None:
        """立即复位到目标部位位姿（GDK ``reset()`` 的 mock 等价实现）。"""
        with self._lock:
            self._require_connected()
            if "arm_positions" in targets:
                self._arm = _as_vector(targets["arm_positions"], ARM_DOF, "reset.arm_positions")
            if "gripper_positions" in targets:
                values = _as_vector(
                    targets["gripper_positions"], GRIPPER_DOF, "reset.gripper_positions"
                )
                self._gripper = [self._normalize_gripper(value) for value in values]
            if "hand_positions" in targets:
                self._hand = _as_vector(targets["hand_positions"], HAND_DOF, "reset.hand_positions")
            if "head_positions" in targets:
                self._head = _as_vector(targets["head_positions"], HEAD_DOF, "reset.head_positions")
            if "waist_positions" in targets:
                self._waist = _as_vector(
                    targets["waist_positions"], WAIST_DOF, "reset.waist_positions"
                )
            ignored = sorted(
                set(targets)
                - {
                    "arm_positions",
                    "gripper_positions",
                    "hand_positions",
                    "head_positions",
                    "waist_positions",
                }
            )
            if ignored:
                logger.debug("[AgiBotG01Mock] reset: ignored targets %s", ignored)

    # ------------------------------------------------------------------
    # RobotController（末端绝对位姿 / 关节绝对位置控制）
    # ------------------------------------------------------------------

    def get_motion_status(self) -> Any:
        """《指南》8.2.3：返回运控状态 dict（``frames`` 含左右末端位姿）。"""
        with self._lock:
            self._require_motion_controller()
            frames = {
                _LEFT_EE_FRAME: self._pose_to_frame(self._ee_poses["left"]),
                _RIGHT_EE_FRAME: self._pose_to_frame(self._ee_poses["right"]),
            }
            return {
                "timestamp": time.time_ns(),
                "mode": {
                    "value": self._motion_mode,
                    "name": _MOTION_MODE_NAMES.get(self._motion_mode, "?"),
                },
                "error": {"code": 0, "message": "", "has_error": False},
                "frames": frames,
                "collisions": [],
            }

    def set_motion_control_mode(self, mode: int) -> None:
        """《指南》8.1：设置运动控制模式（0=stop，1=servo，2=planning）。"""
        with self._lock:
            self._require_motion_controller()
            self._motion_mode = int(mode)

    def set_end_effector_pose(
        self,
        lifetime: float,
        control_group: Sequence[str],
        left_pose: Optional[Dict[str, float]] = None,
        right_pose: Optional[Dict[str, float]] = None,
    ) -> None:
        """《指南》8.1：末端绝对位姿控制（mock 立即更新位姿状态）。"""
        with self._lock:
            self._require_motion_controller()
            groups = [str(group) for group in control_group]
            if left_pose is not None and self._pose_group_enabled("left", groups):
                self._ee_poses["left"] = _pose_dict_to_list(left_pose, "left_pose")
            if right_pose is not None and self._pose_group_enabled("right", groups):
                self._ee_poses["right"] = _pose_dict_to_list(right_pose, "right_pose")

    def set_joint_position_control(self, lifetime: float, joint_group: Dict[str, Any]) -> None:
        """《指南》8.1：关节绝对位置控制（mock 立即更新对应部位状态）。

        支持的组：``left_arm`` / ``right_arm`` / ``dual_arm`` / ``head`` /
        ``waist`` / ``gripper`` / ``hand``；其余组记录 debug 日志后忽略。
        """
        with self._lock:
            self._require_motion_controller()
            for group, values in dict(joint_group).items():
                self._apply_joint_group(str(group), values)

    def _apply_joint_group(self, group: str, values: Any) -> None:
        if group == "left_arm":
            self._arm[0:7] = _resize(values, 7, "joint_group.left_arm")
        elif group == "right_arm":
            self._arm[7:14] = _resize(values, 7, "joint_group.right_arm")
        elif group == "dual_arm":
            self._arm = _as_vector(values, ARM_DOF, "joint_group.dual_arm")
        elif group == "head":
            self._head = _resize(values, HEAD_DOF, "joint_group.head")
        elif group == "waist":
            self._waist = _resize(values, WAIST_DOF, "joint_group.waist")
        elif group == "gripper":
            self._gripper = [
                self._normalize_gripper(value)
                for value in _resize(values, GRIPPER_DOF, "joint_group.gripper")
            ]
        elif group == "hand":
            self._hand = _resize(values, HAND_DOF, "joint_group.hand")
        else:
            logger.debug("[AgiBotG01Mock] joint_group %r ignored by mock", group)

    # ------------------------------------------------------------------
    # 相机（《指南》9 章）：合成渐变帧
    # ------------------------------------------------------------------

    @property
    def camera_key_map(self) -> Dict[str, str]:
        """G01 相机通道名 → R2C 图像键。"""
        return dict(self._camera_key_map)

    def get_camera_images(self) -> Dict[str, np.ndarray]:
        """返回各通道最近一帧合成图像（G01 通道名 → HWC ndarray）。"""
        with self._lock:
            return dict(self._latest_images)

    def _build_camera_images(self) -> Dict[str, np.ndarray]:
        height, width = self._camera_height, self._camera_width
        xs = np.linspace(0, 255, width, dtype=np.uint8)
        ys = np.linspace(40, 200, height, dtype=np.uint8)
        frames: Dict[str, np.ndarray] = {}
        for index, name in enumerate(self._camera_group):
            image = np.empty((height, width, 3), dtype=np.uint8)
            image[:, :, 0] = xs[None, :]
            image[:, :, 1] = ys[:, None]
            image[:, :, 2] = (index * 60 + 40) % 256
            frames[name] = image
        return frames

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def _normalize_gripper(self, value: float) -> float:
        """夹爪输入 → ``[0, 1]`` 归一化（同时接受 ``[0, 1]`` 与原始量程值）。"""
        value = float(value)
        lo, hi = self._gripper_raw_range
        span = hi - lo
        if 0.0 <= value <= 1.0:
            return min(max(value, 0.0), 1.0)
        return min(max((value - lo) / span, 0.0), 1.0)

    def _raw_gripper(self, normalized: float) -> float:
        lo, hi = self._gripper_raw_range
        return lo + float(normalized) * (hi - lo)

    @staticmethod
    def _pose_to_frame(pose: Sequence[float]) -> Dict[str, Any]:
        """7 维位姿 → ``get_motion_status().frames`` 的单帧结构。"""
        return {
            "position": {"x": float(pose[0]), "y": float(pose[1]), "z": float(pose[2])},
            "orientation": {
                "quaternion": {
                    "x": float(pose[3]),
                    "y": float(pose[4]),
                    "z": float(pose[5]),
                    "w": float(pose[6]),
                }
            },
        }

    @staticmethod
    def _pose_group_enabled(side: str, groups: List[str]) -> bool:
        """``set_end_effector_pose_control`` 的 control_group 是否覆盖该侧。"""
        if not groups:
            return True
        allow = "left_arm" if side == "left" else "right_arm"
        return allow in groups or "dual_arm" in groups
