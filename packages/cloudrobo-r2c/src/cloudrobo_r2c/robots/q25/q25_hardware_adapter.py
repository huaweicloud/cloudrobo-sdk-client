"""Q25 Ultra 四足机器人硬件适配器.

该模块实现 IRobotHardwareAdapter 接口，提供与 R2C SDK 的集成。
支持通过 UDP 协议控制 Q25 机器人并获取其状态数据。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

import numpy as np

from cloudrobo_r2c.core.interfaces import IRobotHardwareAdapter
from .q25_client import Q25UDPClient, RobotStatus, DEFAULT_ROBOT_IP, DEFAULT_ROBOT_PORT, get_camera_rtsp_url, DEFAULT_RTSP_PORT

logger = logging.getLogger(__name__)


def create_q25_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> IRobotHardwareAdapter:
    """Entry_point factory for Q25HardwareAdapter (UDP)."""
    return Q25HardwareAdapter(config=config)


# Q25 关节名称
Q25_JOINT_NAMES = [
    "FL_hip_joint", "FL_thigh_joint", "FL_calf_joint",  # 前左腿
    "FR_hip_joint", "FR_thigh_joint", "FR_calf_joint",  # 前右腿
    "BL_hip_joint", "BL_thigh_joint", "BL_calf_joint",  # 后左腿
    "BR_hip_joint", "BR_thigh_joint", "BR_calf_joint",  # 后右腿
]


# 支持的摄像头列表
Q25_CAMERA_LOCATIONS = ["main_cam", "front_cam", "back_cam"]

# 轴值死区阈值 (与 Q25 机器人协议一致)
AXIS_DEADZONE_LEFT_Y = 100   # 左摇杆Y轴死区 (左右/前后)
AXIS_DEADZONE_LEFT_X = 100   # 左摇杆X轴死区 (左右平移)
AXIS_DEADZONE_RIGHT_X = 100  # 右摇杆X轴死区 (旋转)
AXIS_DEADZONE_RIGHT_Y = 100  # 右摇杆Y轴死区


def _get_action_name_from_axis(left_x: int, left_y: int, right_x: int, right_y: int) -> str:
    """根据轴值判断当前动作名称.

    参考 cloud adapter ACTION_SEQUENCE 中的定义：
    - 前进: left_y > 死区
    - 后退: left_y < -死区
    - 左移: left_x < -死区
    - 右移: left_x > 死区
    - 左转: right_x < -死区
    - 右转: right_x > 死区

    Args:
        left_x: 左摇杆 X 轴值
        left_y: 左摇杆 Y 轴值
        right_x: 右摇杆 X 轴值
        right_y: 右摇杆 Y 轴值

    Returns:
        动作名称字符串
    """
    # 检查所有轴向，判断主要动作
    if left_y > AXIS_DEADZONE_LEFT_Y:
        return "前进"
    elif left_y < -AXIS_DEADZONE_LEFT_Y:
        return "后退"
    elif left_x > AXIS_DEADZONE_LEFT_X:
        return "右移"
    elif left_x < -AXIS_DEADZONE_LEFT_X:
        return "左移"
    elif right_x > AXIS_DEADZONE_RIGHT_X:
        return "右转"
    elif right_x < -AXIS_DEADZONE_RIGHT_X:
        return "左转"
    elif right_y > AXIS_DEADZONE_RIGHT_Y:
        return "后退(右摇杆)"
    elif right_y < -AXIS_DEADZONE_RIGHT_Y:
        return "前进(右摇杆)"
    else:
        return "停止"


@dataclass
class Q25HardwareConfig:
    """Q25 硬件适配器配置."""
    robot_ip: str = DEFAULT_ROBOT_IP
    camera_ip: str = DEFAULT_ROBOT_IP  # 摄像头 RTSP 流 IP，默认与 robot_ip 相同
    robot_port: int = DEFAULT_ROBOT_PORT
    listen_port: int = DEFAULT_ROBOT_PORT
    observation_rate_hz: float = 10.0
    camera_enabled: bool = True
    camera_locations: list = field(default_factory=lambda: Q25_CAMERA_LOCATIONS)  # 启用的摄像头列表
    rtsp_port: int = DEFAULT_RTSP_PORT  # RTSP 流端口
    # 速度限制因子 (0.0 - 1.0)，降低此值可减少机器人移动速度
    # 参考 Q25SDKDemo 中的轴值 (20000-30000)，满幅为 32767
    # 默认 0.6 表示使用 60% 的最大速度
    speed_limit: float = 0.6
    # 调试模式：只接收数据和打印日志，不发送指令到机器人
    dry_run: bool = False


class Q25HardwareAdapter(IRobotHardwareAdapter):
    """Q25 Ultra 四足机器人硬件适配器.

    用法示例:
        config = Q25HardwareConfig(robot_ip="192.168.3.20")
        adapter = Q25HardwareAdapter(config)
        adapter.connect()

        # 获取观察数据
        observation = adapter.get_observation()

        # 发送动作命令
        adapter.send_action({"joint_positions": [...]})

        adapter.disconnect()
    """

    def __init__(self, config: Mapping[str, Any]):
        """初始化适配器.

        Args:
            config: 配置字典，支持以下键:
                - robot_ip: 机器人 IP 地址
                - robot_port: 机器人端口
                - listen_port: 本机监听端口
                - observation_rate_hz: 观察数据发布频率
                - camera_enabled: 是否启用摄像头
                - camera_locations: 启用的摄像头列表 (main, front, back)
                - rtsp_port: RTSP 流端口
        """
        # 解析配置
        if isinstance(config, Q25HardwareConfig):
            self._config = config
        else:
            self._config = Q25HardwareConfig(
                robot_ip=config.get("robot_ip", DEFAULT_ROBOT_IP),
                camera_ip=config.get("camera_ip", DEFAULT_ROBOT_IP),  # 摄像头 IP，默认与 robot_ip 相同
                robot_port=config.get("robot_port", DEFAULT_ROBOT_PORT),
                listen_port=config.get("listen_port", DEFAULT_ROBOT_PORT),
                observation_rate_hz=config.get("observation_rate_hz", 10.0),
                camera_enabled=config.get("camera_enabled", True),
                camera_locations=config.get("camera_locations", Q25_CAMERA_LOCATIONS),
                rtsp_port=config.get("rtsp_port", DEFAULT_RTSP_PORT),
                speed_limit=config.get("speed_limit", 0.6),
                dry_run=config.get("dry_run", False),
            )

        # 检查 dry_run 模式
        self._dry_run = getattr(self._config, 'dry_run', False)
        if self._dry_run:
            logger.info("[Q25] DRY_RUN MODE: receive data and log only, no commands sent")

        self._client: Optional[Q25UDPClient] = None
        self._connected = False
        self._observation_thread: Optional[threading.Thread] = None
        self._running = False
        self._sequence = 0

        # 摄像头相关 - 支持多摄像头
        self._cameras: Dict[str, Any] = {}  # camera_location -> cv2.VideoCapture
        self._latest_images: Dict[str, np.ndarray] = {}  # camera_location -> image

        # 状态缓存
        self._latest_status: Optional[RobotStatus] = None
        self._status_lock = threading.Lock()

        # 动作状态跟踪 - 用于检测状态变化
        self._last_stand: bool = False
        self._last_lie: bool = False
        self._last_gait: str = "unknown"
        self._last_height: int = 1
        self._last_left_x: int = 0
        self._last_left_y: int = 0
        self._last_right_x: int = 0
        self._last_right_y: int = 0
        # 当前站立状态: "standing", "lying", "unknown"
        self._current_posture: str = "unknown"
        # 当前站立高度 (0=低, 1=中, 2=高)
        self._current_height: int = 1

        # 轴控制后台线程 - 持续发送轴控制命令
        self._axis_control_thread: Optional[threading.Thread] = None
        self._axis_control_running = False
        self._axis_lock = threading.Lock()  # 保护共享轴值
        # 当前有效的轴值（供后台线程读取并发送）
        self._current_left_x: int = 0
        self._current_left_y: int = 0
        self._current_right_x: int = 0
        self._current_right_y: int = 0
        # 轴控制发送频率 (Hz) - 与 C++ demo 一致 (100Hz)
        self._axis_control_rate_hz: float = 100.0

    def connect(self) -> None:
        """连接到机器人."""
        if self._connected:
            logger.warning("Adapter already connected")
            return

        # 创建 UDP 客户端
        self._client = Q25UDPClient(
            robot_ip=self._config.robot_ip,
            robot_port=self._config.robot_port,
            listen_ip="0.0.0.0",
            listen_port=self._config.listen_port,
        )

        try:
            self._client.connect()

            # 启动心跳
            self._client.start_heartbeat(interval=0.5)

            # 启动状态接收
            self._client.start_status_receiver()
            self._client.set_status_callback(self._on_status_update)

            # 初始化摄像头
            if self._config.camera_enabled:
                self._init_camera()

            # 启动轴控制后台线程 - 持续发送轴控制命令
            self._start_axis_control_loop()

            self._connected = True
            logger.info("Q25 hardware adapter connected: %s:%s", self._config.robot_ip, self._config.robot_port)
        except Exception:
            logger.exception(
                "Q25 connection failed (%s:%s), cleaning up allocated resources",
                self._config.robot_ip,
                self._config.robot_port,
            )
            self._cleanup_partial_connection()
            raise

    def _cleanup_partial_connection(self) -> None:
        """清理连接过程中已分配的资源（连接失败时回滚）. """
        self._running = False
        self._axis_control_running = False
        if self._observation_thread and self._observation_thread.is_alive():
            self._observation_thread.join(timeout=2.0)
        if self._client:
            try:
                self._client.stop_heartbeat()
            except Exception as e:
                logger.debug("Cleanup heartbeat failed (ignorable): %s", e)
            try:
                self._client.disconnect()
            except Exception as e:
                logger.debug("Cleanup disconnect failed (ignorable): %s", e)
            self._client = None
        self._connected = False

    def disconnect(self) -> None:
        """断开与机器人的连接."""
        self._running = False

        # 停止轴控制后台线程
        self._stop_axis_control_loop()

        if self._observation_thread and self._observation_thread.is_alive():
            self._observation_thread.join(timeout=2.0)

        if self._client:
            self._client.stop_heartbeat()
            self._client.disconnect()
            self._client = None

        self._connected = False
        logger.info("Q25 hardware adapter disconnected")

    def _fill_joint_data(self, obs: Dict[str, Any], values: list, suffix: str) -> None:
        """将关节数据列表扁平化填充到观察字典中."""
        for i, joint_name in enumerate(Q25_JOINT_NAMES):
            key = joint_name if not suffix else f"{joint_name}_{suffix}"
            obs[key] = values[i] if i < len(values) else 0.0

    def _fill_imu_data(self, obs: Dict[str, Any], status: RobotStatus) -> None:
        """填充 IMU 数据."""
        obs["imu_roll"] = status.imu_roll
        obs["imu_pitch"] = status.imu_pitch
        obs["imu_yaw"] = status.imu_yaw
        obs["imu_angular_velocity_x"] = status.imu_angular_velocity_x
        obs["imu_angular_velocity_y"] = status.imu_angular_velocity_y
        obs["imu_angular_velocity_z"] = status.imu_angular_velocity_z
        obs["imu_acceleration_x"] = status.imu_acceleration_x
        obs["imu_acceleration_y"] = status.imu_acceleration_y
        obs["imu_acceleration_z"] = status.imu_acceleration_z

    def _fill_motion_state(self, obs: Dict[str, Any], status: RobotStatus) -> None:
        """填充运动状态并更新内部姿态跟踪."""
        obs["gait_mode"] = status.gait_mode
        obs["motion_gait"] = status.gait_mode
        gait_code = {"unknown": 0, "walk": 1, "trot": 2}.get(status.gait_mode, 0)
        obs["gait_code"] = gait_code

        if status.basic_state != "unknown":
            obs["basic_state"] = status.basic_state
            if status.basic_state == "standing":
                self._current_posture = "standing"
            elif status.basic_state == "lying":
                self._current_posture = "lying"

        basic_code = {"unknown": 0, "lying": 1, "standing": 2,
                      "lying_down": 3, "emergency_stop": 4, "creeping": 5}.get(status.basic_state, 0)
        obs["basic_code"] = basic_code

    def _fill_joystick_data(self, obs: Dict[str, Any], status: RobotStatus) -> None:
        """填充摇杆数据."""
        obs["joystick_lx"] = status.joystick_lx
        obs["joystick_ly"] = status.joystick_ly
        obs["joystick_rx"] = status.joystick_rx
        obs["joystick_ry"] = status.joystick_ry

    def _set_default_observation_values(self, obs: Dict[str, Any]) -> None:
        """设置无状态数据时的默认值."""
        for joint_name in Q25_JOINT_NAMES:
            obs[joint_name] = 0.0
            obs[f"{joint_name}_vel"] = 0.0
            obs[f"{joint_name}_torque"] = 0.0
            obs[f"{joint_name}_temp"] = 0.0
            obs[f"{joint_name}_motor_temp"] = 0.0
            obs[f"{joint_name}_driver_temp"] = 0.0
        defaults = {
            "imu_roll": 0.0, "imu_pitch": 0.0, "imu_yaw": 0.0,
            "imu_angular_velocity_x": 0.0, "imu_angular_velocity_y": 0.0, "imu_angular_velocity_z": 0.0,
            "imu_acceleration_x": 0.0, "imu_acceleration_y": 0.0, "imu_acceleration_z": 0.0,
            "gait_mode": "unknown", "motion_gait": "unknown", "gait_code": 0, "basic_code": 0,
            "joystick_lx": 0.0, "joystick_ly": 0.0, "joystick_rx": 0.0, "joystick_ry": 0.0,
            "cpu_temperature": 0.0, "cpu_frequency": 0.0,
        }
        obs.update(defaults)

    def _fill_camera_images(self, obs: Dict[str, Any]) -> None:
        """填充摄像头图像数据."""
        if self._config.camera_enabled and self._latest_images:
            for camera_location, image in self._latest_images.items():
                if image is not None:
                    obs[f"camera_{camera_location}"] = image

    def get_observation(self) -> Mapping[str, Any]:
        """获取观察数据.

        将 Q25UDPClient 从机器人获取到的所有状态数据完整地转换为扁平化字段结构，
        供 ConfigurableDeviceTranslator 映射到 R2C 标准格式。

        Returns:
            扁平化的字段字典，包含所有从机器人获取的数据:
                - 12个关节位置/速度/力矩/温度/电机温度/驱动器温度
                - CPU: cpu_temperature, cpu_frequency
                - IMU: imu_roll/pitch/yaw, 角速度, 加速度
                - 运动状态: basic_state, gait_mode (motion_gait)
                - 摇杆数据: joystick_lx/ly/rx/ry
                - 摄像头图像: camera_main_cam, camera_front_cam, camera_back_cam
        """
        if not self._connected:
            raise RuntimeError("适配器未连接")

        self._sequence += 1
        observation: Dict[str, Any] = {
            "timestamp": int(time.time() * 1000),
            "id": self._sequence,
        }

        self._fill_camera_images(observation)

        status = self._client.get_status()
        if not status:
            self._set_default_observation_values(observation)
            return observation

        # 填充传感器数据
        self._fill_joint_data(observation, status.joint_positions or [0.0] * 12, "")
        self._fill_joint_data(observation, status.joint_velocities or [0.0] * 12, "vel")
        self._fill_joint_data(observation, status.joint_torques or [0.0] * 12, "torque")
        self._fill_joint_data(observation, status.joint_temperatures or [0.0] * 12, "temp")
        self._fill_joint_data(observation, status.motor_temperatures or [0.0] * 12, "motor_temp")
        self._fill_joint_data(observation, status.driver_temperatures or [0.0] * 12, "driver_temp")

        observation["cpu_temperature"] = status.cpu_temperature
        observation["cpu_frequency"] = status.cpu_frequency

        self._fill_imu_data(observation, status)
        self._fill_motion_state(observation, status)
        self._fill_joystick_data(observation, status)

        return observation

    def _handle_dry_run(self, command: Mapping[str, Any]) -> bool:
        """调试模式处理：只打印不发送. 返回 True 表示已处理."""
        if not self._dry_run:
            return False
        left_x = int(command.get("left_x", 0))
        left_y = int(command.get("left_y", 0))
        right_x = int(command.get("right_x", 0))
        right_y = int(command.get("right_y", 0))
        action_name = _get_action_name_from_axis(left_x, left_y, right_x, right_y)
        logger.info(
            "[DRY_RUN] action: %s, stand=%s, lie=%s, emergency_stop=%s, gait=%s, height=%s, "
            "left=(%d, %d), right=(%d, %d)",
            action_name,
            command.get("stand", False), command.get("lie", False),
            command.get("emergency_stop", False), command.get("gait"), command.get("height"),
            left_x, left_y, right_x, right_y,
        )
        return True

    def _extract_axis_values(self, command: Mapping[str, Any]) -> tuple:
        """从命令中提取轴值."""
        return (
            int(command.get("left_x", 0)),
            int(command.get("left_y", 0)),
            int(command.get("right_x", 0)),
            int(command.get("right_y", 0)),
        )

    def _log_axis_change(self, left_x: int, left_y: int, right_x: int, right_y: int) -> None:
        """当轴值有显著变化时打印日志."""
        axis_changed = (
            abs(left_x - self._last_left_x) > AXIS_DEADZONE_LEFT_X or
            abs(left_y - self._last_left_y) > AXIS_DEADZONE_LEFT_Y or
            abs(right_x - self._last_right_x) > AXIS_DEADZONE_RIGHT_X or
            abs(right_y - self._last_right_y) > AXIS_DEADZONE_RIGHT_Y
        )
        if axis_changed:
            action_name = _get_action_name_from_axis(left_x, left_y, right_x, right_y)
            logger.info(
                "[Q25] Action: %s, left=(%d, %d), right=(%d, %d)",
                action_name, left_x, left_y, right_x, right_y,
            )

    def _handle_emergency_stop(self, command: Mapping[str, Any]) -> bool:
        """处理急停命令. 返回 True 表示已触发急停."""
        emergency_stop = command.get("emergency_stop")
        if not emergency_stop or not self._is_truthy(emergency_stop):
            return False
        self._client.send_emergency_stop()
        self._current_posture = "unknown"
        self._last_stand = False
        self._last_lie = False
        logger.info("[Q25] Emergency stop")
        return True

    def _resolve_posture_command(self, command: Mapping[str, Any]) -> dict:
        """解析站立/趴下命令，返回需执行的命令标志."""
        current_stand = self._is_truthy(command.get("stand"))
        current_lie = self._is_truthy(command.get("lie"))
        current_height = int(command.get("height", 1))
        left_x, left_y, right_x, right_y = self._extract_axis_values(command)
        has_axis_motion = any(v != 0 for v in (left_x, left_y, right_x, right_y))

        result = {"stand": False, "lie": False, "height": False, "height_value": current_height}

        if current_stand and not current_lie:
            if self._current_posture != "standing":
                result["stand"] = True
                logger.info("[Q25] Stand command (current state: %s, target height: %d)", self._current_posture, current_height)
            elif current_height != self._current_height:
                result["height"] = True
                logger.info("[Q25] Adjust height: %d -> %d", self._current_height, current_height)
        elif current_lie and not current_stand:
            result["lie"] = True
            logger.info("[Q25] Lie command (current state: %s)", self._current_posture)
        elif not current_stand and not current_lie and has_axis_motion:
            if self._current_posture not in ("standing", "unknown"):
                result["stand"] = True
                logger.info("[Q25] Stand before axis control (current state: %s)", self._current_posture)

        return result

    def _execute_posture_commands(self, posture: dict) -> None:
        """执行站立/趴下/高度命令."""
        if posture["stand"]:
            self._client.send_stand()
            self._current_posture = "standing"
            self._current_height = posture["height_value"]
            logger.info("[Q25] Stand command sent")
        elif posture["lie"]:
            self._client.send_lie()
            self._current_posture = "lying"
            self._current_height = 0
            logger.info("[Q25] Lie command sent")

        if posture["height"]:
            self._client.send_height_control(posture["height_value"])
            self._current_height = posture["height_value"]

    def _handle_gait_change(self, command: Mapping[str, Any]) -> None:
        """处理步态切换."""
        gait = command.get("gait")
        if gait is None:
            return
        gait_str = self._to_string(gait).lower()
        new_gait = "walk" if gait_str in ("walk", "0") else ("run" if gait_str in ("run", "trot", "1") else "unknown")
        if new_gait != self._last_gait and new_gait != "unknown":
            logger.info("[Q25] Gait: %s", new_gait)
            if new_gait == "walk":
                self._client.send_gait_walk()
            elif new_gait == "run":
                self._client.send_gait_run()
            self._last_gait = new_gait

    def _handle_height_change(self, command: Mapping[str, Any]) -> None:
        """处理高度调节（非站立命令中的高度变化）."""
        height = command.get("height")
        if height is None or self._current_posture != "standing":
            return
        try:
            height_int = int(float(height))
            if height_int in (0, 1, 2) and height_int != self._current_height:
                logger.info("[Q25] Height adjust: %d", height_int)
                self._client.send_height_control(height_int)
                self._current_height = height_int
        except (ValueError, TypeError):
            logger.debug("Invalid height value: %s", height)

    def _update_axis_control(self, left_x: int, left_y: int, right_x: int, right_y: int) -> None:
        """更新轴控制值并通过后台线程持续发送."""
        with self._axis_lock:
            has_motion = any(v != 0 for v in (left_x, left_y, right_x, right_y))
            if has_motion and self._current_posture not in ("standing", "unknown"):
                logger.info("[Q25] Axis control -> stand")
                self._client.send_stand()
                self._current_posture = "standing"

            self._current_left_x = left_x
            self._current_left_y = left_y
            self._current_right_x = right_x
            self._current_right_y = right_y

        self._last_left_x = left_x
        self._last_left_y = left_y
        self._last_right_x = right_x
        self._last_right_y = right_y

    def send_action(self, command: Mapping[str, Any]) -> None:
        """发送动作命令到机器人.

        Args:
            command: 命令字典，支持以下键:
                - joint_positions: 关节位置列表 (12 个元素)
                - left_y: 左摇杆 Y 轴 (-1000 ~ 1000), 用于前进/后退
                - right_x: 右摇杆 X 轴 (-1000 ~ 1000), 用于左转/右转
                - left_x: 左摇杆 X 轴 (-1000 ~ 1000), 用于左移/右移
                - stand: 站立命令 (True/False)
                - lie: 趴下命令 (True/False)
                - emergency_stop: 急停命令 (True/False)
                - gait: 步态 ("walk" / "run")
                - height: 高度 (0/1/2)

        注意: 根据 Q25SDKDemo，站立/趴下命令只需要发送一次，机器人会保持该状态。
        轴控制需要持续发送，且每次值变化时都需要发送。
        """
        if not self._connected or not self._client:
            raise RuntimeError("适配器未连接")

        if self._handle_dry_run(command):
            return

        left_x, left_y, right_x, right_y = self._extract_axis_values(command)
        self._log_axis_change(left_x, left_y, right_x, right_y)

        # 对互斥枚举状态做 argmax 解析。
        # 云端可能下发 softmax 概率值 (float)，取最大值的索引作为最终互斥命令。
        # gait 和 height 是数值型枚举，需要保留其原始索引值。
        _enum_keys = ["stand", "lie", "emergency_stop", "gait", "height"]
        _enum_vals = []
        for k in _enum_keys:
            v = command.get(k)
            if isinstance(v, (int, float)):
                _enum_vals.append(float(v))
            elif isinstance(v, bool):
                _enum_vals.append(1.0 if v else 0.0)
            else:
                _enum_vals.append(0.0)
        max_idx = max(range(len(_enum_vals)), key=lambda i: _enum_vals[i])
        # 构造 argmax 后的命令值：bool 类别用 True，数值类别用索引值
        cmd_args = {k: False for k in _enum_keys}
        if _enum_vals[max_idx] > 0.0:
            max_key = _enum_keys[max_idx]
            if max_key in ("gait", "height"):
                # gait: 0→walk, 1→run; height: 0/1/2
                cmd_args[max_key] = max_idx
            else:
                cmd_args[max_key] = True
        # 将 argmax 结果写回 command，供后续方法消费
        command = dict(command)
        command.update(cmd_args)

        if self._handle_emergency_stop(command):
            return

        current_stand = cmd_args["stand"]
        current_lie = cmd_args["lie"]
        current_height = int(command.get("height", 1))

        logger.debug(
            "[Q25] Received command: stand=%s, lie=%s, height=%s, left=(%d, %d), right=(%d, %d), current_posture=%s",
            current_stand, current_lie, current_height,
            left_x, left_y, right_x, right_y,
            self._current_posture,
        )

        posture = self._resolve_posture_command(command)
        self._execute_posture_commands(posture)

        self._last_stand = current_stand
        self._last_lie = current_lie

        # 处理步态和高度
        self._handle_gait_change(command)
        self._handle_height_change(command)

        # 更新轴控制
        self._update_axis_control(left_x, left_y, right_x, right_y)

    def _is_truthy(self, value: Any) -> bool:
        """判断值是否为真，支持布尔值、数值和字符串."""
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
        return bool(value)

    def _to_string(self, value: Any) -> str:
        """将值转换为字符串."""
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float)):
            return str(int(value)) if value == int(value) else str(value)
        return str(value)

    def _init_camera(self) -> None:
        """初始化摄像头，连接所有启用的 RTSP 流."""
        try:
            import cv2
            # 获取启用的摄像头列表
            camera_locations = self._config.camera_locations if self._config.camera_locations else []

            if not camera_locations:
                logger.info("No cameras configured")
                return

            # 使用 camera_ip 构建 RTSP URL（如果未配置，则使用 robot_ip）
            camera_ip = self._config.camera_ip if self._config.camera_ip else self._config.robot_ip

            # 初始化每个摄像头
            for camera_location in camera_locations:
                rtsp_url = get_camera_rtsp_url(
                    robot_ip=camera_ip,
                    camera_location=camera_location,
                    rtsp_port=self._config.rtsp_port
                )
                logger.info("Connecting RTSP camera %s: %s", camera_location, rtsp_url)
                cap = cv2.VideoCapture(rtsp_url)
                if cap.isOpened():
                    self._cameras[camera_location] = cap
                    self._latest_images[camera_location] = None
                    logger.info("Camera initialized: %s", camera_location)
                else:
                    logger.warning("Cannot open RTSP camera %s: %s", camera_location, rtsp_url)

            # 启动图像采集线程
            if self._cameras:
                self._running = True
                self._observation_thread = threading.Thread(
                    target=self._camera_capture_loop,
                    daemon=True,
                    name="Q25CameraCapture"
                )
                self._observation_thread.start()
        except ImportError:
            logger.warning("OpenCV not installed, camera unavailable")
        except Exception as e:
            logger.warning("Camera init failed: %s", e)

    def _camera_capture_loop(self) -> None:
        """摄像头图像采集循环."""
        import cv2
        while self._running:
            has_valid_frame = False
            for camera_location, camera in self._cameras.items():
                ret, frame = camera.read()
                if ret:
                    self._latest_images[camera_location] = frame
                    has_valid_frame = True
            if has_valid_frame:
                time.sleep(0.03)  # ~30fps

    def _on_status_update(self, status: RobotStatus) -> None:
        """状态数据回调."""
        with self._status_lock:
            self._latest_status = status

    def _start_axis_control_loop(self) -> None:
        """启动轴控制后台线程，持续发送轴控制命令."""
        if self._axis_control_thread and self._axis_control_thread.is_alive():
            logger.warning("Axis control thread already running")
            return

        self._axis_control_running = True
        self._axis_control_thread = threading.Thread(
            target=self._axis_control_loop,
            daemon=True,
            name="Q25AxisControl"
        )
        self._axis_control_thread.start()
        logger.info("Axis control thread started (%sHz)", self._axis_control_rate_hz)

    def _stop_axis_control_loop(self) -> None:
        """停止轴控制后台线程."""
        self._axis_control_running = False
        if self._axis_control_thread and self._axis_control_thread.is_alive():
            self._axis_control_thread.join(timeout=2.0)
        logger.info("Axis control thread stopped")

    def _axis_control_loop(self) -> None:
        """轴控制后台循环 - 持续发送轴控制命令.

        根据 Q25SDKDemo，轴控制需要持续发送（50-100Hz）才能保持运动状态。
        即使轴值不变，也需要持续发送命令。

        速度限制：通过 speed_limit 配置因子，可以降低机器人的最大移动速度。
        参考 Q25SDKDemo：
        - 前进/后退轴值: 20000
        - 左移/右移/左转/右转轴值: 30000
        - Q25 最大轴值: 32767
        """
        interval = 1.0 / self._axis_control_rate_hz
        R2C_AXIS_MAX = 1000
        # Q25 支持的最大轴值，实际使用时参考 Q25SDKDemo 中的 20000-30000
        Q25_AXIS_MAX = 32767
        # 使用配置的速度限制因子，降低最大速度
        # 0.6 表示使用约 60% 的最大速度 (约 20000/32767)
        speed_limit = getattr(self._config, 'speed_limit', 0.6)
        # 用于跟踪上次是否打印过日志（避免刷屏）
        last_logged_action = None

        def convert_axis(value: int) -> int:
            # 应用速度限制因子
            return int(value * Q25_AXIS_MAX * speed_limit / R2C_AXIS_MAX)

        while self._axis_control_running:
            try:
                # 获取当前轴值
                with self._axis_lock:
                    left_x = self._current_left_x
                    left_y = self._current_left_y
                    right_x = self._current_right_x
                    right_y = self._current_right_y

                # 判断当前动作名称
                current_action = _get_action_name_from_axis(left_x, left_y, right_x, right_y)

                # 只有当动作变化时才打印日志（避免刷屏）
                if current_action != last_logged_action:
                    logger.info("[Q25] Axis control: %s", current_action)
                    last_logged_action = current_action

                # 始终发送轴控制命令（包括停止状态）
                self._client.send_axis_control_extended(
                    left_x=convert_axis(left_x),
                    left_y=convert_axis(left_y),
                    right_x=convert_axis(right_x),
                    right_y=convert_axis(right_y),
                )

            except Exception as e:
                logger.error("Axis control send error: %s", e)

            time.sleep(interval)

    @property
    def is_connected(self) -> bool:
        """检查是否已连接."""
        return self._connected


def create_q25_adapter(
    robot_ip: str = DEFAULT_ROBOT_IP,
    robot_port: int = DEFAULT_ROBOT_PORT,
    camera_enabled: bool = True,
    camera_locations: list = None,
    rtsp_port: int = DEFAULT_RTSP_PORT,
    speed_limit: float = 0.6,
) -> Q25HardwareAdapter:
    """创建 Q25 硬件适配器的便捷函数.

    Args:
        robot_ip: 机器人 IP 地址
        robot_port: 机器人端口
        camera_enabled: 是否启用摄像头
        camera_locations: 启用的摄像头列表 (main, front, back)
        rtsp_port: RTSP 流端口
        speed_limit: 速度限制因子 (0.0 - 1.0)，降低此值可减少机器人移动速度

    Returns:
        Q25HardwareAdapter 实例
    """
    if camera_locations is None:
        camera_locations = Q25_CAMERA_LOCATIONS
    config = Q25HardwareConfig(
        robot_ip=robot_ip,
        robot_port=robot_port,
        camera_enabled=camera_enabled,
        camera_locations=camera_locations,
        rtsp_port=rtsp_port,
        speed_limit=speed_limit,
    )
    adapter = Q25HardwareAdapter(config)
    adapter.connect()
    return adapter
