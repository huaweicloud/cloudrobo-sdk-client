"""Flexiv hardware adapter for R2C SDK."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from cloudrobo_r2c.core.interfaces import IRobotHardwareAdapter


def create_flexiv_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> IRobotHardwareAdapter:
    """Entry_point factory for FlexivHardwareAdapter."""
    return FlexivHardwareAdapter(config=dict(config))


logger = logging.getLogger(__name__)


# Freshness thresholds (see ADR-0006).
_STALE_THRESHOLD_MS = 200.0
_BROKEN_THRESHOLD_S = 1.0
_STALE_WARNING_DEBOUNCE_S = 5.0
_CAPTURE_THREAD_JOIN_TIMEOUT_S = 2.0


@dataclass
class _CameraSlot:
    """Thread-safe slot holding the freshest frame from one camera.

    Producer (capture thread) calls :meth:`put` on every successful
    ``cap.read()``. Consumer (``get_observation``) calls :meth:`get`
    to read the latest payload. A ``threading.Lock`` guards the
    payload tuple; the lock is uncontended in normal operation
    (1 producer, 1 consumer, ~30 Hz).

    Payload semantics:
    - ``(None, 0.0, None)`` — no frame ever received (initial state).
    - ``(frame, ts, None)`` — healthy; ``ts`` is ``time.monotonic()``
      when the producer wrote the frame.
    - ``(None, 0.0, error)`` — capture thread died; ``error`` is the
      exception string.
    """

    name: str
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )
    _payload: Tuple[Optional[np.ndarray], float, Optional[str]] = field(
        default=(None, 0.0, None), init=False, repr=False
    )
    _warned: bool = field(default=False, init=False, repr=False)
    _last_stale_warning: float = field(default=0.0, init=False, repr=False)

    def put(
        self,
        frame: Optional[np.ndarray],
        timestamp: float,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._payload = (frame, timestamp, error)

    def get(self) -> Tuple[Optional[np.ndarray], float, Optional[str]]:
        with self._lock:
            return self._payload

_BASE_OBSERVATION_NAMES: List[str] = [
    # 关节位置 6
    "joint_1", "joint_2", "joint_3", "joint_4", "joint_5", "joint_6",
    # 关节速度 6
    "dq_1", "dq_2", "dq_3", "dq_4", "dq_5", "dq_6",
    # 关节力矩 6
    "tau_1", "tau_2", "tau_3", "tau_4", "tau_5", "tau_6",
    # TCP 位置 3
    "tcp_x", "tcp_y", "tcp_z",
    # TCP 四元数姿态 4
    "tcp_qw", "tcp_qx", "tcp_qy", "tcp_qz",
    # TCP 欧拉角姿态 3 (roll, pitch, yaw)
    "tcp_roll", "tcp_pitch", "tcp_yaw",
    # 力传感器 raw 6（当前帧）
    "wrench_raw_0", "wrench_raw_1", "wrench_raw_2", "wrench_raw_3", "wrench_raw_4", "wrench_raw_5",
    # 力传感器 filtered 6
    "wrench_filtered_0", "wrench_filtered_1", "wrench_filtered_2",
    "wrench_filtered_3", "wrench_filtered_4", "wrench_filtered_5",
    # 夹爪 1
    "gripper",
]


def _build_observation_names(force_history_len: int) -> List[str]:
    """根据 force_history_len 构建完整的观测 names 列表。

    Args:
        force_history_len: 力矩历史帧数（0=无历史字段）

    Returns:
        包含基础 names + 历史力矩 names 的完整列表
    """
    names = list(_BASE_OBSERVATION_NAMES)
    for frame_idx in range(1, force_history_len + 1):
        for dim in range(6):
            names.append(f"wrench_raw-{frame_idx}_{dim}")
    return names


# 默认 OBSERVATION_NAMES（force_history_len=10 → 41+60=101维）
OBSERVATION_NAMES: List[str] = _build_observation_names(10)


@dataclass
class FlexivHardwareAdapter(IRobotHardwareAdapter):
    """Flexiv robot hardware adapter."""

    config: Mapping[str, Any]

    _robot: Any = field(default=None, init=False, repr=False)
    _gripper: Any = field(default=None, init=False, repr=False)
    _connected: bool = field(default=False, init=False, repr=False)
    _camera_captures: Dict[str, Any] = field(
        default_factory=dict, init=False, repr=False
    )
    # Per-camera capture thread plumbing (see ADR-0006).
    _camera_slots: Dict[str, _CameraSlot] = field(
        default_factory=dict, init=False, repr=False
    )
    _camera_threads: Dict[str, threading.Thread] = field(
        default_factory=dict, init=False, repr=False
    )
    _camera_stop_events: Dict[str, threading.Event] = field(
        default_factory=dict, init=False, repr=False
    )
    _connect_time: float = field(default=0.0, init=False, repr=False)
    _last_observation: Dict[str, Any] = field(
        default_factory=dict, init=False, repr=False
    )

    # 力矩历史采集
    _force_history_len: int = field(default=10, init=False, repr=False)
    _force_history_freq: float = field(default=30.0, init=False, repr=False)
    _wrench_history: deque = field(default=None, init=False, repr=False)
    _wrench_sampling_thread: threading.Thread = field(default=None, init=False, repr=False)
    _wrench_sampling_stop: threading.Event = field(default=None, init=False, repr=False)
    _cached_robot_state: Any = field(default=None, init=False, repr=False)
    _state_lock: threading.Lock = field(default=None, init=False, repr=False)
    _observation_names: List[str] = field(default=None, init=False, repr=False)
    def __post_init__(self) -> None:
        """Validate config and register commands."""
        required = ["robot_sn"]
        for key in required:
            if key not in self.config:
                raise ValueError(f"Missing required config: {key!r}")

        dry_run = self.config.get("dry_run", False)
        if dry_run:
            logger.info(f"[DRY_RUN] DRY_RUN MODE")

        # 力矩历史配置
        self._force_history_len = int(self.config.get("force_history_len", 10))
        self._force_history_freq = float(self.config.get("force_history_freq", 30.0))
        self._wrench_history = deque(maxlen=self._force_history_len)
        self._wrench_sampling_stop = threading.Event()
        self._cached_robot_state = None
        self._state_lock = threading.Lock()
        self._observation_names = _build_observation_names(self._force_history_len)
        
        from cloudrobo_r2c.robots.commands.flexiv import FlexivGoHomeCommand
        self.register_command_class("go_home", FlexivGoHomeCommand)

    def connect(self) -> None:
        """连接机器人、初始化夹爪、准备控制模式、打开摄像头"""
        if self._connected:
            logger.debug("Already connected, skipping connect()")
            return

        # 导入 flexivrdk
        import flexivrdk

        # 连接机器人
        robot_sn = self.config["robot_sn"]
        self._robot = flexivrdk.Robot(str(robot_sn))

        # 清除故障并使能
        if self._robot.fault():
            if not self._robot.ClearFault():
                raise RuntimeError("Failed to clear robot fault")
        self._robot.Enable()

        # 等待机器人就绪
        self._wait_until_operational()

        # 初始化夹爪
        gripper_name = self.config.get("gripper_name", "Flexiv-GN01")
        self._gripper = flexivrdk.Gripper(self._robot)
        tool = flexivrdk.Tool(self._robot)
        self._gripper.Enable(str(gripper_name))
        tool.Switch(str(gripper_name))

        # 准备笛卡尔控制模式
        self._prepare_cartesian_control()

        # 打开摄像头
        self._open_cameras()

        self._connect_time = time.monotonic()
        self._connected = True
        logger.info("Flexiv robot connected successfully")
        # 启动力矩历史采样线程（唯一调用 robot.states() 的线程）
        self._wrench_sampling_stop.clear()
        self._wrench_sampling_thread = threading.Thread(
            target=self._wrench_sampling_loop,
            name="r2c-flexiv-wrench-sampling",
            daemon=True,
        )
        self._wrench_sampling_thread.start()
        logger.info(
            "Wrench history sampling started: freq=%.1f Hz, len=%d frames, names=%d",
            self._force_history_freq, self._force_history_len, len(self._observation_names),
        )

    def _wait_until_operational(self, timeout_s: float = 15.0) -> None:
        """等待机器人进入操作状态"""
        deadline = time.time() + float(timeout_s)
        while not self._robot.operational():
            if time.time() > deadline:
                raise TimeoutError("Timed out waiting for robot to become operational")
            time.sleep(0.2)

    def _prepare_cartesian_control(self) -> None:
        """切换到笛卡尔运动-力控制模式"""
        import flexivrdk

        force_limit_n = self.config.get("force_limit_n", 20.0)
        zero_ft = self.config.get("zero_ft_on_start", False)

        if zero_ft:
            self._robot.SwitchMode(flexivrdk.Mode.NRT_PRIMITIVE_EXECUTION)
            self._robot.ExecutePrimitive("ZeroFTSensor", dict())
            while not self._robot.primitive_states()["terminated"]:
                time.sleep(0.1)

        self._robot.SwitchMode(flexivrdk.Mode.NRT_CARTESIAN_MOTION_FORCE)
        self._robot.SetForceControlFrame(flexivrdk.CoordType.WORLD)
        self._robot.SetForceControlAxis([False, False, False, False, False, False])
        self._robot.SetMaxContactWrench([force_limit_n] * 3 + [2.0, 2.0, 2.0])

    def _open_cameras(self) -> None:
        """打开配置的摄像头"""
        import cv2

        cameras = self.config.get("cameras", {})
        for name, cam_config in cameras.items():
            source = cam_config.get("source", 0)
            cap = cv2.VideoCapture(
                int(source) if isinstance(source, int) else str(source)
            )

            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

            if not cap.isOpened():
                logger.warning(
                    f"Failed to open camera [{name}] from source [{source}], skipping"
                )
                continue

            # 设置分辨率和帧率
            if "width" in cam_config:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(cam_config["width"]))
            if "height" in cam_config:
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(cam_config["height"]))
            if "fps" in cam_config:
                cap.set(cv2.CAP_PROP_FPS, float(cam_config["fps"]))

            self._camera_captures[name] = cap

            # Start a per-camera capture thread that drains the V4L2
            # buffer into a thread-safe slot. The thread is the freshness
            # guarantee: it overwrites the slot with each new frame, so a
            # slow consumer always reads the freshest one.
            slot = _CameraSlot(name=name)
            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._capture_loop,
                args=(name, cap, slot, stop_event),
                name=f"flexiv-cam-{name}",
                daemon=True,
            )
            self._camera_slots[name] = slot
            self._camera_stop_events[name] = stop_event
            self._camera_threads[name] = thread
            thread.start()
            logger.info(f"Camera [{name}] opened: {source} (capture thread started)")

    def _wrench_sampling_loop(self) -> None:
        """后台线程：按 force_history_freq 频率采样力矩数据到 deque。

        此线程是 robot.states() 的唯一调用者，避免并发调用导致的不安全问题。
        deque 只存历史帧，当前帧由 get_observation() 从缓存直接提取。
        """
        dt = 1.0 / self._force_history_freq
        while not self._wrench_sampling_stop.is_set():
            try:
                # 在锁外调用 robot.states()，避免慢速响应阻塞 get_observation()
                robot_state = self._robot.states()

                # 提取 wrench 并缓存完整状态
                raw_wrench = np.array(
                    getattr(robot_state, "ext_wrench_in_world_raw",
                            robot_state.ext_wrench_in_world),
                    dtype=np.float64,
                ).reshape(6)

                with self._state_lock:
                    self._cached_robot_state = robot_state
                    self._wrench_history.append(raw_wrench.copy())
            except Exception as e:
                logger.warning("Failed to sample wrench for history: %s", e)

            self._wrench_sampling_stop.wait(timeout=dt)
			
    def _capture_loop(
        self,
        name: str,
        cap: Any,
        slot: _CameraSlot,
        stop_event: threading.Event,
    ) -> None:
        """Capture loop: continuously drain the camera into the slot.

        Runs in a daemon thread. Exits cleanly when ``stop_event`` is
        set (via :meth:`disconnect`). On any exception, the error is
        recorded in the slot and the thread exits — the consumer's
        ``thread.is_alive()`` check will then mark the camera broken.
        """
        try:
            while not stop_event.is_set():
                ok, frame = cap.read()
                if ok and frame is not None:
                    slot.put(np.asarray(frame), time.monotonic())
        except Exception as exc:  # noqa: BLE001 — surface to slot
            logger.exception("capture thread %r died", name)
            slot.put(None, 0.0, str(exc))
            return

    def get_observation(self) -> Mapping[str, Any]:
        """获取观测数据（从采样线程缓存读取，不直接调用 robot.states()）"""
        self._ensure_connected()
        robot_state, history_snapshot = self._get_cached_state()
        result = self._build_base_observation(robot_state)
        self._append_wrench_history(result, history_snapshot)
        if self._camera_captures:
            self._append_camera_frames(result)
        self._last_observation = result
        return result

    def _get_cached_state(self) -> Tuple[Any, List[np.ndarray]]:
        """从采样线程缓存读取机器人状态和力矩历史快照。"""
        with self._state_lock:
            robot_state = self._cached_robot_state
            history_snapshot = list(self._wrench_history)

        if robot_state is None:
            raise RuntimeError("No cached robot state available yet")

        # 读取最新机器人状态
        robot_state = self._robot.states()
        return robot_state, history_snapshot

    def _build_base_observation(self, robot_state: Any) -> Dict[str, Any]:
        """从 robot_state 提取关节/TCP/力矩/夹爪数据，构建基础观测字典。"""
        joint_positions = np.array(robot_state.q, dtype=np.float64)
        joint_velocities = np.array(robot_state.dq, dtype=np.float64)
        joint_torques = np.array(robot_state.tau, dtype=np.float64)

        # TCP 姿态
        tcp_pose = np.array(robot_state.tcp_pose, dtype=np.float64).reshape(7)
        tcp_position = tcp_pose[:3]
        tcp_quat = self._normalize_quat(tcp_pose[3:7])
        tcp_euler = self._quat_to_euler(
            tcp_quat[0], tcp_quat[1], tcp_quat[2], tcp_quat[3]
        )

        # 力传感器
        raw_wrench = np.array(
            getattr(
                robot_state, "ext_wrench_in_world_raw", robot_state.ext_wrench_in_world
            ),
            dtype=np.float64,
        ).reshape(6)
        filtered_wrench = np.array(
            robot_state.ext_wrench_in_world, dtype=np.float64
        ).reshape(6)

        # 夹爪状态
        gripper_state = self._gripper.states()
        gripper_width = float(gripper_state.width)

        return {
            # 关节位置 (6个)
            "joint_1": joint_positions[0],
            "joint_2": joint_positions[1],
            "joint_3": joint_positions[2],
            "joint_4": joint_positions[3],
            "joint_5": joint_positions[4],
            "joint_6": joint_positions[5],
            # 关节速度 (6个)
            "dq_1": joint_velocities[0],
            "dq_2": joint_velocities[1],
            "dq_3": joint_velocities[2],
            "dq_4": joint_velocities[3],
            "dq_5": joint_velocities[4],
            "dq_6": joint_velocities[5],
            # 关节力矩 (6个)
            "tau_1": joint_torques[0],
            "tau_2": joint_torques[1],
            "tau_3": joint_torques[2],
            "tau_4": joint_torques[3],
            "tau_5": joint_torques[4],
            "tau_6": joint_torques[5],
            # TCP 位置 (3个)
            "tcp_x": tcp_position[0],
            "tcp_y": tcp_position[1],
            "tcp_z": tcp_position[2],
            # TCP 四元数 (4个)
            "tcp_qw": tcp_quat[0],
            "tcp_qx": tcp_quat[1],
            "tcp_qy": tcp_quat[2],
            "tcp_qz": tcp_quat[3],
            # TCP 欧拉角 (3个) - 同时输出四元数和欧拉角
            "tcp_roll": tcp_euler[0],
            "tcp_pitch": tcp_euler[1],
            "tcp_yaw": tcp_euler[2],
            # 力传感器 raw (6个)
            "wrench_raw_0": raw_wrench[0],
            "wrench_raw_1": raw_wrench[1],
            "wrench_raw_2": raw_wrench[2],
            "wrench_raw_3": raw_wrench[3],
            "wrench_raw_4": raw_wrench[4],
            "wrench_raw_5": raw_wrench[5],
            # 力传感器 filtered (6个)
            "wrench_filtered_0": filtered_wrench[0],
            "wrench_filtered_1": filtered_wrench[1],
            "wrench_filtered_2": filtered_wrench[2],
            "wrench_filtered_3": filtered_wrench[3],
            "wrench_filtered_4": filtered_wrench[4],
            "wrench_filtered_5": filtered_wrench[5],
            # 夹爪
            "gripper": gripper_width,
        }

    def _append_wrench_history(
        self, result: Dict[str, Any], history_snapshot: List[np.ndarray]
    ) -> None:
        """从快照构建力矩历史字段并追加到 result。

        wrench_raw-1 = 最近1个历史帧（快照末尾）
        wrench_raw-N = 第N近历史帧
        当前帧 wrench 通过 wrench_raw_0..5 单独输出，与历史帧语义不同
        """
        history_len = self._force_history_len
        filled = len(history_snapshot)
        for frame_idx in range(1, history_len + 1):
            deque_idx = filled - frame_idx
            if deque_idx >= 0:
                wrench = history_snapshot[deque_idx]
            else:
                wrench = np.zeros(6, dtype=np.float64)
            for dim in range(6):
                result[f"wrench_raw-{frame_idx}_{dim}"] = float(wrench[dim])

    def _append_camera_frames(self, result: Dict[str, Any]) -> None:
        """读取摄像头帧，处理 freshness/stale/error 逻辑并追加到 result。"""
        camera_meta: Dict[str, Dict[str, Any]] = {}
        now = time.monotonic()
        for name in self._camera_captures:
            slot = self._camera_slots.get(name)
            thread = self._camera_threads.get(name)

            # Read the slot first — a producer crash writes the
            # error to the slot before the thread exits, so the
            # error must be surfaced even when is_alive() is False.
            frame, timestamp, error = (
                slot.get() if slot is not None else (None, 0.0, None)
            )

            if error is not None:
                camera_meta[name] = {
                    "timestamp": 0.0,
                    "age_ms": 0.0,
                    "stale": False,
                    "not_ready": False,
                    "error": error,
                }
                continue

            if slot is None or thread is None or not thread.is_alive():
                camera_meta[name] = {
                    "timestamp": 0.0,
                    "age_ms": 0.0,
                    "stale": False,
                    "not_ready": True,
                    "error": "capture thread not running",
                }
                continue

            if timestamp == 0.0:
                # Producer never delivered a frame. Warn once per
                # connect cycle; escalate to "broken" after 1s.
                if not slot._warned:
                    elapsed = now - self._connect_time
                    if elapsed > _BROKEN_THRESHOLD_S:
                        logger.warning(
                            "Camera %r broken: no frame after %.1fs",
                            name,
                            elapsed,
                        )
                    else:
                        logger.warning("Camera %r not yet ready", name)
                    slot._warned = True
                camera_meta[name] = {
                    "timestamp": 0.0,
                    "age_ms": 0.0,
                    "stale": False,
                    "not_ready": True,
                    "error": None,
                }
                continue

            age_ms = (now - timestamp) * 1000.0
            stale = age_ms > _STALE_THRESHOLD_MS
            if (
                stale
                and (now - slot._last_stale_warning) > _STALE_WARNING_DEBOUNCE_S
            ):
                logger.warning(
                    "Camera %r frame is %.0f ms old (threshold %.0f ms)",
                    name,
                    age_ms,
                    _STALE_THRESHOLD_MS,
                )
                slot._last_stale_warning = now

            result[name] = frame
            camera_meta[name] = {
                "timestamp": timestamp,
                "age_ms": age_ms,
                "stale": stale,
                "not_ready": False,
                "error": None,
            }
        result["camera_meta"] = camera_meta

    def send_action(self, command: Mapping[str, Any]) -> None:
        """发送动作命令

        支持三种格式（通过 joint_states.names 动态决定）：
        1. 关节位置 + 夹爪
        2. TCP位置 + TCP姿态(欧拉角) + 夹爪
        3. TCP位置 + TCP姿态(四元数) + 夹爪
        """
        self._ensure_connected()

        # 调试模式：只打印 action，不执行实际动作
        dry_run = self.config.get("dry_run", False)
        if dry_run:
            # 排除图像字段，只保留数值数据
            obs_without_images = {
                k: v
                for k, v in self._last_observation.items()
                if not isinstance(v, np.ndarray)
            }
            logger.info(f"[DRY_RUN] observation: {obs_without_images}")
            logger.info(f"[DRY_RUN] action: {command}")
            return

        # 检查是否有 TCP 控制
        has_tcp = any(k in command for k in ["tcp_x", "tcp_y", "tcp_z"])

        if has_tcp:
            # 笛卡尔位置控制
            tcp_x = command.get("tcp_x", 0.0)
            tcp_y = command.get("tcp_y", 0.0)
            tcp_z = command.get("tcp_z", 0.0)

            # 检查是四元数还是欧拉角
            if "tcp_qw" in command:
                quat_wxyz = [
                    command.get("tcp_qw", 1.0),
                    command.get("tcp_qx", 0.0),
                    command.get("tcp_qy", 0.0),
                    command.get("tcp_qz", 0.0),
                ]

                self._send_cartesian_pose([tcp_x, tcp_y, tcp_z], quat_wxyz=quat_wxyz)
            elif "tcp_roll" in command:
                euler_rpy = [
                    command.get("tcp_roll", 0.0),
                    command.get("tcp_pitch", 0.0),
                    command.get("tcp_yaw", 0.0),
                ]

                # 启用欧拉角路径
                self._send_cartesian_pose([tcp_x, tcp_y, tcp_z], euler_rpy=euler_rpy)

        # 检查夹爪控制
        if "gripper" in command:
            gripper_width = command["gripper"]
            self._move_gripper(gripper_width)

    def move_to_pose(
        self,
        pose: List[float],
        gripper: Optional[float] = None,
    ) -> None:
        """移动到指定位姿（笛卡尔空间）。

        Args:
            pose: [x, y, z, roll, pitch, yaw] — 位置(m) + 欧拉角(rad)
            gripper: 可选，夹爪目标宽度(m)。为 None 时不控制夹爪。
        """
        self._ensure_connected()

        if len(pose) != 6:
            raise ValueError(
                f"pose must have 6 elements [x,y,z,roll,pitch,yaw], got {len(pose)}"
            )

        position_xyz = list(pose[:3])
        euler_rpy = list(pose[3:6])
        self._send_cartesian_pose(position_xyz, euler_rpy=euler_rpy)

        if gripper is not None:
            self._move_gripper(float(gripper))

    def _send_cartesian_pose(
        self,
        position_xyz: List[float],
        *,
        quat_wxyz: Optional[List[float]] = None,
        euler_rpy: Optional[List[float]] = None,
    ) -> None:
        """发送笛卡尔位置命令"""
        position = np.asarray(position_xyz, dtype=np.float64).reshape(3)

        if quat_wxyz is not None:
            quat = self._normalize_quat(np.asarray(quat_wxyz, dtype=np.float64))
        else:
            roll, pitch, yaw = np.asarray(euler_rpy, dtype=np.float64).reshape(3)
            quat = self._euler_to_quat(roll, pitch, yaw)

        target_pose = np.concatenate([position, quat], axis=0)
        command_wrench = np.zeros(6, dtype=np.float64)

        self._robot.SendCartesianMotionForce(
            target_pose.tolist(), command_wrench.tolist(), max_linear_vel=0.08
        )

    def _move_gripper(self, width: float) -> None:
        """移动夹爪到目标宽度"""
        velocity = self.config.get("gripper_velocity", 0.10)
        force_limit = self.config.get("gripper_force_limit", 20.0)
        self._gripper.Move(float(width), float(velocity), float(force_limit))

    # ------------------------------------------------------------------
    # go_home (typed interface)
    # ------------------------------------------------------------------

    def move_to(
        self,
        *,
        pose_euler: Optional[Sequence[float]] = None,
        pose_quat: Optional[Sequence[float]] = None,
        joints: Optional[Sequence[float]] = None,
    ) -> None:
        """Move to a home configuration (typed interface).

        Exactly one of ``pose_euler`` / ``pose_quat`` / ``joints`` must be
        supplied. Gripper motion is *not* performed here — use
        :meth:`set_gripper` after this call.
        """
        self._validate_move_to_inputs(pose_euler, pose_quat, joints)

        if joints is not None:
            self._move_to_joint_positions(list(joints))
            return

        if pose_euler is not None:
            if len(pose_euler) != 6:
                raise ValueError(
                    f"pose_euler must have 6 elements, got {len(pose_euler)}"
                )
            # Route through the public move_to_pose so existing test mocks
            # (and any user monkey-patching) still intercept the call.
            self.move_to_pose(list(pose_euler))
            return

        if len(pose_quat) != 7:
            raise ValueError(f"pose_quat must have 7 elements, got {len(pose_quat)}")
        position = list(pose_quat[:3])
        quat_wxyz = list(pose_quat[3:7])
        self._send_cartesian_pose(position, quat_wxyz=quat_wxyz)

    def set_gripper(
        self,
        *,
        width: Optional[float] = None,
        action: Optional[str] = None,
    ) -> None:
        """Move the gripper to a target width or perform an open/close action.

        Args:
            width: Target width in metres; passed straight to
                :meth:`_move_gripper`.
            action: ``"open"`` moves to ``config["gripper_open_width"]``;
                ``"close"`` moves to ``config["gripper_close_width"]``.

        Exactly one of ``width`` / ``action`` must be supplied.
        """
        if (width is None) == (action is None):
            raise ValueError(
                "set_gripper() requires exactly one of width or action to be "
                f"supplied, got width={width!r}, action={action!r}."
            )

        if action is not None:
            normalised = action.strip().lower()
            if normalised == "open":
                key = "gripper_open_width"
            elif normalised == "close":
                key = "gripper_close_width"
            else:
                raise ValueError(
                    f"set_gripper action must be 'open' or 'close', got {action!r}"
                )
            if key not in self.config:
                raise ValueError(
                    f"set_gripper(action={normalised!r}) requires "
                    f"hardware config key {key!r} to be set."
                )
            target = float(self.config[key])
            self._move_gripper(target)
            return

        self._move_gripper(float(width))

    def _move_to_joint_positions(self, joints: List[float]) -> None:
        """Move to joint positions via the flexivrdk ``JointMotion`` primitive.

        Switches to ``NRT_PRIMITIVE_EXECUTION`` mode, executes the primitive,
        blocks until termination, then restores ``NRT_CARTESIAN_MOTION_FORCE``
        so subsequent calls (e.g. ``send_action``) work as expected.

        Gripper motion is handled separately by :meth:`set_gripper` (the
        generic ``GoHomeCommand`` calls it after ``move_to``); this method
        is joint-only.
        """
        import flexivrdk

        self._ensure_connected()

        if len(joints) != 6:
            raise ValueError(
                f"Flexiv go_home(joints=...) expects 6 floats, got {len(joints)}"
            )

        # Switch to primitive execution mode
        self._robot.SwitchMode(flexivrdk.Mode.NRT_PRIMITIVE_EXECUTION)
        try:
            self._robot.ExecutePrimitive(
                "JointMotion",
                {"target": [float(v) for v in joints]},
            )
            # Block until the primitive terminates
            while not self._robot.primitive_states()["terminated"]:
                time.sleep(0.05)
        finally:
            # Always restore cartesian + force control
            self._prepare_cartesian_control()

    def disconnect(self) -> None:
        """断开机器人连接、释放摄像头资源"""
        if not self._connected:
            logger.debug("Already disconnected, skipping disconnect()")
            return

        # 停止力矩采样线程
        if self._wrench_sampling_thread is not None:
            self._wrench_sampling_stop.set()
            self._wrench_sampling_thread.join(timeout=2.0)
            self._wrench_sampling_thread = None
            logger.info("Wrench history sampling stopped")
        # Stop capture threads first, then release cameras. Order
        # matters: if we released the cap first, the in-flight
        # cap.read() inside the thread would raise on a freed handle.
        for name, stop_event in self._camera_stop_events.items():
            stop_event.set()

        for name, thread in self._camera_threads.items():
            thread.join(timeout=_CAPTURE_THREAD_JOIN_TIMEOUT_S)
            if thread.is_alive():
                logger.warning(
                    "Camera capture thread %r did not exit within %.1fs",
                    name,
                    _CAPTURE_THREAD_JOIN_TIMEOUT_S,
                )

        # 释放摄像头
        import cv2

        for name, cap in self._camera_captures.items():
            try:
                cap.release()
                logger.info(f"Camera [{name}] released")
            except Exception as e:
                logger.warning(f"Failed to release camera [{name}]: {e}")
        self._camera_captures.clear()
        self._camera_slots.clear()
        self._camera_threads.clear()
        self._camera_stop_events.clear()

        # 断开机器人
        if self._robot is not None:
            self._robot = None

        self._gripper = None
        self._connected = False
        logger.info("Flexiv robot disconnected")

    def _ensure_connected(self) -> None:
        """确保已连接"""
        if not self._connected:
            raise RuntimeError("Adapter is not connected. Call connect() first.")

    @staticmethod
    def _normalize_quat(quat: np.ndarray) -> np.ndarray:
        """归一化四元数"""
        norm = np.linalg.norm(quat)
        if norm < 1.0e-9:
            return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
        return quat / norm

    @staticmethod
    def _euler_to_quat(roll: float, pitch: float, yaw: float) -> np.ndarray:
        """欧拉角转四元数"""
        import math

        cr = math.cos(0.5 * roll)
        sr = math.sin(0.5 * roll)
        cp = math.cos(0.5 * pitch)
        sp = math.sin(0.5 * pitch)
        cy = math.cos(0.5 * yaw)
        sy = math.sin(0.5 * yaw)
        return np.array(
            [
                cr * cp * cy + sr * sp * sy,
                sr * cp * cy - cr * sp * sy,
                cr * sp * cy + sr * cp * sy,
                cr * cp * sy - sr * sp * cy,
            ],
            dtype=np.float64,
        )

    @staticmethod
    def _quat_to_euler(qw: float, qx: float, qy: float, qz: float) -> np.ndarray:
        """四元数转欧拉角 (roll, pitch, yaw)

        Returns:
            np.ndarray: [roll, pitch, yaw] in radians
        """
        import math

        # Roll (x-axis rotation)
        sinr_cosp = 2.0 * (qw * qx + qy * qz)
        cosr_cosp = 1.0 - 2.0 * (qx * qx + qy * qy)
        roll = math.atan2(sinr_cosp, cosr_cosp)

        # Pitch (y-axis rotation)
        sinp = 2.0 * (qw * qy - qz * qx)
        if abs(sinp) >= 1:
            pitch = math.copysign(math.pi / 2, sinp)  # use 90 degrees if out of range
        else:
            pitch = math.asin(sinp)

        # Yaw (z-axis rotation)
        siny_cosp = 2.0 * (qw * qz + qx * qy)
        cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
        yaw = math.atan2(siny_cosp, cosy_cosp)

        return np.array([roll, pitch, yaw], dtype=np.float64)
