"""Q25 Ultra 四足机器人 UDP 客户端.

该模块提供与天狼 Q25 Ultra 四足机器人通信的 UDP 客户端实现。
支持发送控制命令和接收机器人状态数据。

命令码参考 Q25SDKDemo:
- 心跳: 0x21040001
- 站立: 0x21010202
- 趴下: 0x21010222
- 急停: 0x21010C0E
- 遥控模式: 0x21010C02
- 导航模式: 0x21010C03
- Walk步态: 0x21010300
- Run步态: 0x21010423
- 高度调节: 0x21010406
- 扩展轴控: 0x21010140
"""

from __future__ import annotations

import socket
import struct
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Callable

logger = logging.getLogger(__name__)

# 命令码定义 (参考 Q25SDKDemo)
CMD_HEARTBEAT = 0x21040001
CMD_STAND = 0x21010202
CMD_LIE = 0x21010222
CMD_EMERGENCY_STOP = 0x21010C0E
CMD_MODE_REMOTE = 0x21010C02
CMD_MODE_NAVIGATION = 0x21010C03
CMD_GAIT_WALK = 0x21010300
CMD_GAIT_RUN = 0x21010423
CMD_HEIGHT_CONTROL = 0x21010406

# 轴控制命令码 (参考 axis_control_demo.cpp)
CMD_LEFT_YAXIS = 0x21010130   # 左摇杆Y轴（前后）- 前进/后退
CMD_LEFT_XAXIS = 0x21010131   # 左摇杆X轴（左右）- 左移/右移
CMD_RIGHT_XAXIS = 0x21010135  # 右摇杆X轴（旋转）- 左转/右转

# 扩展轴控制 (同时控制四个轴)
CMD_AXIS_CONTROL_EXTENDED = 0x21010140

# 机器人默认地址
DEFAULT_ROBOT_IP = "192.168.3.20"
DEFAULT_ROBOT_PORT = 43893
DEFAULT_LISTEN_IP = "0.0.0.0"
DEFAULT_LISTEN_PORT = 43893


@dataclass
class RobotStatus:
    """机器人状态数据."""
    # IMU 数据
    imu_roll: float = 0.0  # 横滚角 (°)
    imu_pitch: float = 0.0  # 俯仰角 (°)
    imu_yaw: float = 0.0  # 偏航角 (°)
    imu_angular_velocity_x: float = 0.0  # 角速度 (rad/s)
    imu_angular_velocity_y: float = 0.0
    imu_angular_velocity_z: float = 0.0
    imu_acceleration_x: float = 0.0  # 加速度 (m/s^2)
    imu_acceleration_y: float = 0.0
    imu_acceleration_z: float = 0.0

    # 关节数据 (12个关节)
    joint_names: list = field(default_factory=list)
    joint_positions: list = field(default_factory=list)  # 位置 (rad)
    joint_velocities: list = field(default_factory=list)  # 速度 (rad/s)
    joint_torques: list = field(default_factory=list)  # 力矩 (Nm)
    joint_temperatures: list = field(default_factory=list)  # 温度 (℃)

    # 运动状态
    gait_mode: str = "unknown"  # walk/trot/unknown
    basic_state: str = "unknown"  # 机器人基本状态: lying/standing/emergency_stop/creeping

    # 摇杆数据 (来自 0x1008)
    joystick_lx: float = 0.0
    joystick_ly: float = 0.0
    joystick_rx: float = 0.0
    joystick_ry: float = 0.0

    # 电机温度 (来自 0x100B ControllerSafeData)
    motor_temperatures: list = field(default_factory=list)  # 12个关节电机温度 (℃), float[12]
    # 驱动器温度 (来自 0x100B ControllerSafeData)
    driver_temperatures: list = field(default_factory=list)  # 12个关节驱动器温度 (℃), uint8_t[12]
    # CPU 状态 (来自 0x100B ControllerSafeData)
    cpu_temperature: float = 0.0  # CPU 温度 (℃)
    cpu_frequency: float = 0.0  # CPU 主频 (MHz)


class Q25UDPClient:
    """Q25 Ultra 四足机器人 UDP 客户端.

    用法示例:
        client = Q25UDPClient()
        client.connect()

        # Sent stand command
        client.send_stand()

        # 发送移动命令 (左摇杆Y轴: 前进/后退, 右摇杆X轴: 左转/右转)
        client.send_axis_control(left_y=500, right_x=300)

        # 接收状态数据
        status = client.get_status()
        print(f"基本状态: {status.basic_state}, 步态: {status.gait_mode}")

        # 关闭连接
        client.disconnect()
    """

    def __init__(
        self,
        robot_ip: str = DEFAULT_ROBOT_IP,
        robot_port: int = DEFAULT_ROBOT_PORT,
        listen_ip: str = DEFAULT_LISTEN_IP,
        listen_port: int = DEFAULT_LISTEN_PORT,
        timeout: float = 5.0,
    ):
        """初始化 UDP 客户端.

        Args:
            robot_ip: 机器人 IP 地址
            robot_port: 机器人端口
            listen_ip: 本机监听 IP (用于接收状态数据)
            listen_port: 本机监听端口
            timeout: socket 超时时间 (秒)
        """
        self._robot_ip = robot_ip
        self._robot_port = robot_port
        self._listen_ip = listen_ip
        self._listen_port = listen_port
        self._timeout = timeout

        self._send_socket: Optional[socket.socket] = None
        self._recv_socket: Optional[socket.socket] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._status_thread: Optional[threading.Thread] = None
        self._running = False
        self._latest_status: Optional[RobotStatus] = None
        self._status_callback: Optional[Callable[[RobotStatus], None]] = None
        self._status_lock = threading.Lock()

    def connect(self) -> None:
        """建立与机器人的连接."""
        # 创建发送 socket
        self._send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._send_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # 创建接收 socket
        self._recv_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._recv_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._recv_socket.bind((self._listen_ip, self._listen_port))
        self._recv_socket.settimeout(1.0)

        logger.info("Q25 UDP client connected: %s:%d", self._robot_ip, self._robot_port)
        logger.info("Listening for status data: %s:%d", self._listen_ip, self._listen_port)

    def disconnect(self) -> None:
        """断开与机器人的连接."""
        self._running = False

        # 停止心跳线程
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=2.0)

        # 停止状态接收线程
        if self._status_thread and self._status_thread.is_alive():
            self._status_thread.join(timeout=2.0)

        # 关闭 socket
        if self._send_socket:
            self._send_socket.close()
            self._send_socket = None

        if self._recv_socket:
            self._recv_socket.close()
            self._recv_socket = None

        logger.info("Q25 UDP client disconnected")

    def start_heartbeat(self, interval: float = 0.5) -> None:
        """启动心跳发送 (2Hz).

        Args:
            interval: 心跳间隔 (秒), 默认 0.5s (2Hz)
        """
        if self._running:
            logger.warning("Heartbeat thread already running")
            return

        self._running = True
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(interval,),
            daemon=True,
            name="Q25Heartbeat"
        )
        self._heartbeat_thread.start()
        logger.info("Heartbeat thread started (%.1fHz)", 1.0 / interval)

    def stop_heartbeat(self) -> None:
        """停止心跳发送."""
        self._running = False
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=2.0)
        logger.info("Heartbeat thread stopped")

    def start_status_receiver(self) -> None:
        """启动状态数据接收线程."""
        if self._status_thread and self._status_thread.is_alive():
            logger.warning("Status receiver thread already running")
            return

        self._running = True
        self._status_thread = threading.Thread(
            target=self._status_receive_loop,
            daemon=True,
            name="Q25StatusReceiver"
        )
        self._status_thread.start()
        logger.info("Status receiver thread started")

    def set_status_callback(self, callback: Callable[[RobotStatus], None]) -> None:
        """设置状态数据回调函数.

        Args:
            callback: 回调函数，接收 RobotStatus 对象
        """
        self._status_callback = callback

    def get_status(self) -> Optional[RobotStatus]:
        """获取最新的机器人状态."""
        with self._status_lock:
            return self._latest_status

    def send_command(self, command_id: int, param: int = 0) -> None:
        """发送通用命令.

        根据 Q25SDKDemo 的协议格式:
        - UDPCommand: command_id(4) + parameters_size(4) + type(4)
        - 参数直接存储在 parameters_size 字段中

        Args:
            command_id: 命令码
            param: 命令参数 (存储在 parameters_size 字段)
        """
        if not self._send_socket:
            raise RuntimeError("未连接机器人")

        data = struct.pack("<III", command_id, param, 0)  # 小端序，type=0

        self._send_socket.sendto(data, (self._robot_ip, self._robot_port))
        logger.debug("Sent command: 0x%08X, param: %d", command_id, param)

    def send_stand(self) -> None:
        """Sent stand command."""
        self.send_command(CMD_STAND)
        logger.info("Sent stand command")

    def send_lie(self) -> None:
        """Sent lie command."""
        self.send_command(CMD_LIE)
        logger.info("Sent lie command")

    def send_emergency_stop(self) -> None:
        """Sent emergency stop command."""
        self.send_command(CMD_EMERGENCY_STOP)
        logger.info("Sent emergency stop command")

    def send_mode_remote(self) -> None:
        """Switched to remote control mode."""
        self.send_command(CMD_MODE_REMOTE)
        logger.info("Switched to remote control mode")

    def send_mode_navigation(self) -> None:
        """Switched to navigation mode."""
        self.send_command(CMD_MODE_NAVIGATION)
        logger.info("Switched to navigation mode")

    def send_gait_walk(self) -> None:
        """Switched to Walk gait."""
        self.send_command(CMD_GAIT_WALK)
        logger.info("Switched to Walk gait")

    def send_gait_run(self) -> None:
        """切换到 Trot/Run 步态."""
        self.send_command(CMD_GAIT_RUN)
        logger.info("Switched to Run gait")

    def send_height_control(self, height: int) -> None:
        """调节机器人高度.

        Args:
            height: 高度级别 (0=低/匍匐, 1=中, 2=高)
        """
        self.send_command(CMD_HEIGHT_CONTROL, height)
        logger.info("Adjust height: %d", height)

    # ============ 轴控制方法 (参考 axis_control_demo.cpp) ============

    def send_left_y_axis(self, value: int) -> None:
        """发送左摇杆Y轴控制 (前后运动).

        死区范围: -6553 ~ 6553
        正值: 前进, 负值: 后退

        Args:
            value: 轴值 (-32767 ~ 32767)
        """
        self.send_command(CMD_LEFT_YAXIS, value)
        logger.debug("Left stick Y axis: %d", value)

    def send_left_x_axis(self, value: int) -> None:
        """发送左摇杆X轴控制 (左右平移).

        死区范围: -24576 ~ 24576
        负值: 左移, 正值: 右移

        Args:
            value: 轴值 (-32767 ~ 32767)
        """
        self.send_command(CMD_LEFT_XAXIS, value)
        logger.debug("Left stick X axis: %d", value)

    def send_right_x_axis(self, value: int) -> None:
        """发送右摇杆X轴控制 (旋转).

        死区范围: -28212 ~ 28212
        负值: 左转, 正值: 右转

        Args:
            value: 轴值 (-32767 ~ 32767)
        """
        self.send_command(CMD_RIGHT_XAXIS, value)
        logger.debug("Right stick X axis: %d", value)

    def stop_all_axes(self) -> None:
        """Stopped all axes (发送停止命令)."""
        self.send_command(CMD_LEFT_YAXIS, 0)
        self.send_command(CMD_LEFT_XAXIS, 0)
        self.send_command(CMD_RIGHT_XAXIS, 0)
        logger.info("Stopped all axes")

    def move_forward(self, duration_ms: int = 100) -> None:
        """前进.

        Args:
            duration_ms: 持续时间 (毫秒)
        """
        AXIS_FORWARD = 20000  # 超过死区 6553
        for _ in range(duration_ms // 10):
            self.send_left_y_axis(AXIS_FORWARD)
            time.sleep(0.01)
        self.send_left_y_axis(0)

    def move_backward(self, duration_ms: int = 100) -> None:
        """后退.

        Args:
            duration_ms: 持续时间 (毫秒)
        """
        AXIS_BACKWARD = -20000
        for _ in range(duration_ms // 10):
            self.send_left_y_axis(AXIS_BACKWARD)
            time.sleep(0.01)
        self.send_left_y_axis(0)

    def turn_left(self, duration_ms: int = 100) -> None:
        """左转.

        Args:
            duration_ms: 持续时间 (毫秒)
        """
        AXIS_TURN_LEFT = -30000  # 超过死区 28212
        for _ in range(duration_ms // 10):
            self.send_right_x_axis(AXIS_TURN_LEFT)
            time.sleep(0.01)
        self.send_right_x_axis(0)

    def turn_right(self, duration_ms: int = 100) -> None:
        """右转.

        Args:
            duration_ms: 持续时间 (毫秒)
        """
        AXIS_TURN_RIGHT = 30000
        for _ in range(duration_ms // 10):
            self.send_right_x_axis(AXIS_TURN_RIGHT)
            time.sleep(0.01)
        self.send_right_x_axis(0)

    def send_axis_control_extended(
        self,
        left_x: int = 0,
        left_y: int = 0,
        right_x: int = 0,
        right_y: int = 0,
    ) -> None:
        """发送扩展轴控制命令 (单命令控制四个轴).

        Args:
            left_x: 左摇杆 X 轴 (-1000 ~ 1000)
            left_y: 左摇杆 Y 轴 (-1000 ~ 1000)
            right_x: 右摇杆 X 轴 (-1000 ~ 1000)
            right_y: 右摇杆 Y 轴 (-1000 ~ 1000)
        """
        if not self._send_socket:
            raise RuntimeError("未连接机器人")

        # 构建命令头
        command_id = CMD_AXIS_CONTROL_EXTENDED
        command_type = 1  # 扩展指令

        axis_data = struct.pack(
            "<iiii",
            self._clamp_axis(left_x),
            self._clamp_axis(left_y),
            self._clamp_axis(right_x),
            self._clamp_axis(right_y),
        )

        # 完整数据包
        data = struct.pack("<III", command_id, len(axis_data), command_type)
        data += axis_data

        self._send_socket.sendto(data, (self._robot_ip, self._robot_port))
        logger.debug(
            "Sent extended axis control: left=(%d, %d), right=(%d, %d)",
            left_x, left_y, right_x, right_y,
        )

    def _clamp_axis(self, value: int) -> int:
        """限制轴值在有效范围内."""
        return max(-1000, min(1000, value))

    def _heartbeat_loop(self, interval: float) -> None:
        """心跳发送循环."""
        while self._running:
            try:
                self.send_command(CMD_HEARTBEAT)
            except Exception as e:
                logger.error("Heartbeat send failed: %s", e)
            time.sleep(interval)

    def _status_receive_loop(self) -> None:
        """状态数据接收循环."""
        while self._running:
            try:
                if self._recv_socket:
                    data, addr = self._recv_socket.recvfrom(4096)
                    if data:
                        # 校验原始数据完整性
                        logger.debug("Received UDP data: size=%d, from=%s", len(data), addr)
                        status = self._parse_status(data)
                        with self._status_lock:
                            self._latest_status = status
                        # Status sync: print key state info at debug level to avoid log spam
                        if status:
                            logger.debug(
                                "Status sync: basic_state=%s, gait=%s, "
                                "IMU(rpy)=(%.1f, %.1f, %.1f), "
                                "joints(pos=%d vel=%d tau=%d)",
                                status.basic_state, status.gait_mode,
                                status.imu_roll, status.imu_pitch, status.imu_yaw,
                                len(status.joint_positions), len(status.joint_velocities), len(status.joint_torques),
                            )
                        # 调用回调函数
                        if self._status_callback:
                            self._status_callback(status)
            except socket.timeout:
                continue
            except Exception as e:
                logger.error("Status receive error: %s", e)

    def _parse_status(self, data: bytes) -> RobotStatus:
        """解析状态数据."""
        status = RobotStatus()
        status.joint_names = self._JOINT_NAMES

        try:
            command_id, payload = self._unpack_header(data)
            if payload is None:
                return status

            parser = {
                0x1008: self._parse_rcs_data,
                0x1009: self._parse_motion_state,
                0x100A: self._parse_controller_sensor,
                0x100B: self._parse_safe_data,
            }.get(command_id)

            if parser:
                parser(status, payload)
            else:
                logger.debug(
                    "Unknown command: 0x%04X, payload_len=%d",
                    command_id, len(payload),
                )
        except Exception as e:
            logger.warning("Status data parse error: %s", e, exc_info=True)

        return status

    _JOINT_NAMES = [
        "FL_hip_joint", "FL_thigh_joint", "FL_calf_joint",
        "FR_hip_joint", "FR_thigh_joint", "FR_calf_joint",
        "BL_hip_joint", "BL_thigh_joint", "BL_calf_joint",
        "BR_hip_joint", "BR_thigh_joint", "BR_calf_joint",
    ]

    def _unpack_header(self, data: bytes):
        """解析扩展指令包头，返回 (command_id, payload) 或 (None, None)。"""
        if len(data) < 12:
            logger.warning("Packet too short: %d < 12", len(data))
            return None, None

        command_id = struct.unpack("<I", data[0:4])[0]
        data_length = struct.unpack("<I", data[4:8])[0]
        command_type = struct.unpack("<I", data[8:12])[0]

        if command_type != 1:
            logger.warning("Non-extended command: type=%d", command_type)
            return None, None

        return command_id, data[12:12 + data_length]

    def _parse_rcs_data(self, status: RobotStatus, payload: bytes) -> None:
        """解析 0x1008 运行状态指令 (RcsData / 摇杆数据)。"""
        if len(payload) < 76:
            return
        off = 55  # 15 + 4*4 + 8*4
        status.joystick_lx = struct.unpack("<f", payload[off:off+4])[0]
        status.joystick_ly = struct.unpack("<f", payload[off+4:off+8])[0]
        status.joystick_rx = struct.unpack("<f", payload[off+8:off+12])[0]
        status.joystick_ry = struct.unpack("<f", payload[off+12:off+16])[0]
        logger.debug(
            "RcsData: joystick=(%.2f, %.2f, %.2f, %.2f)",
            status.joystick_lx, status.joystick_ly, status.joystick_rx, status.joystick_ry,
        )

    _BASIC_STATE_MAP = {
        0x00: "lying",
        0x02: "standing",
        0x03: "standing",
        0x05: "lying_down",
        0x06: "emergency_stop",
        0x09: "creeping",
    }

    def _parse_motion_state(self, status: RobotStatus, payload: bytes) -> None:
        """解析 0x1009 运动状态指令 (MotionStateData)。"""
        if len(payload) < 60:
            logger.warning("Motion status data too short: %d < 60", len(payload))
            return

        basic_state = payload[0]
        gait_state = payload[1]
        status.gait_mode = "walk" if gait_state == 0 else "trot" if gait_state == 1 else "unknown"
        status.basic_state = self._BASIC_STATE_MAP.get(basic_state, "unknown")
        logger.debug("Motion status: basic=0x%02X, gait=%d", basic_state, gait_state)

    def _parse_joints(self, payload: bytes, offset: int, count: int = 12) -> list:
        """从 payload 指定偏移解析 count 个 float 关节数据。"""
        result = []
        for i in range(count):
            off = offset + i * 4
            result.append(struct.unpack("<f", payload[off:off+4])[0])
        return result

    def _parse_controller_sensor(self, status: RobotStatus, payload: bytes) -> None:
        """解析 0x100A 传感器状态指令 (ControllerSensorData)。"""
        logger.debug("Sensor status data: length=%d", len(payload))

        if len(payload) >= 40:
            status.imu_roll = struct.unpack("<f", payload[4:8])[0]
            status.imu_pitch = struct.unpack("<f", payload[8:12])[0]
            status.imu_yaw = struct.unpack("<f", payload[12:16])[0]
            status.imu_angular_velocity_x = struct.unpack("<f", payload[16:20])[0]
            status.imu_angular_velocity_y = struct.unpack("<f", payload[20:24])[0]
            status.imu_angular_velocity_z = struct.unpack("<f", payload[24:28])[0]
            status.imu_acceleration_x = struct.unpack("<f", payload[28:32])[0]
            status.imu_acceleration_y = struct.unpack("<f", payload[32:36])[0]
            status.imu_acceleration_z = struct.unpack("<f", payload[36:40])[0]
            logger.debug(
                "IMU: roll=%.2f, pitch=%.2f, yaw=%.2f",
                status.imu_roll, status.imu_pitch, status.imu_yaw,
            )

        if len(payload) >= 88:
            status.joint_positions = self._parse_joints(payload, 40)
        if len(payload) >= 136:
            status.joint_velocities = self._parse_joints(payload, 88)
        if len(payload) >= 184:
            status.joint_torques = self._parse_joints(payload, 136)

    def _parse_safe_data(self, status: RobotStatus, payload: bytes) -> None:
        """解析 0x100B 安全状态指令 (ControllerSafeData)。"""
        logger.debug("Control safety status data: length=%d", len(payload))

        if len(payload) >= 48:
            status.motor_temperatures = self._parse_joints(payload, 0)
            logger.info(
                "Motor temperatures: min=%.1f, max=%.1f",
                min(status.motor_temperatures), max(status.motor_temperatures),
            )
        if len(payload) >= 60:
            status.driver_temperatures = [float(payload[48 + i]) for i in range(12)]
        if len(payload) >= 68:
            status.cpu_temperature = struct.unpack("<f", payload[60:64])[0]
            status.cpu_frequency = struct.unpack("<f", payload[64:68])[0]
            logger.info("CPU: temp=%.1f, freq=%.0fMHz", status.cpu_temperature, status.cpu_frequency)


# 便捷函数
def create_q25_client(
    robot_ip: str = DEFAULT_ROBOT_IP,
    robot_port: int = DEFAULT_ROBOT_PORT,
) -> Q25UDPClient:
    """创建 Q25 UDP 客户端的便捷函数."""
    client = Q25UDPClient(robot_ip=robot_ip, robot_port=robot_port)
    client.connect()
    return client


# ============ 摄像头相关 ============

# 默认 RTSP 端口
DEFAULT_RTSP_PORT = 8554


def get_camera_rtsp_url(robot_ip: str, camera_location: str = "main_cam", rtsp_port: int = 8554) -> str:
    """获取机器人摄像头的 RTSP 流地址.

    根据机器人 IP 地址生成 RTSP URL。

    Args:
        robot_ip: 机器人 IP 地址
        camera_location: 摄像头位置 ("main_cam", "front_cam", "back_cam")
        rtsp_port: RTSP 流端口 (默认 8554)

    Returns:
        RTSP URL 字符串

    Examples:
        >>> get_camera_rtsp_url("192.168.1.102")
        'rtsp://192.168.1.102:8554/main_cam'
        >>> get_camera_rtsp_url("192.168.1.102", "front_cam", 8554)
        'rtsp://192.168.1.102:8554/front_cam'
    """
    # 直接使用 camera_location 作为 RTSP 路径
    stream_path = camera_location if camera_location else "main_cam"
    return f"rtsp://{robot_ip}:{rtsp_port}/{stream_path}"
