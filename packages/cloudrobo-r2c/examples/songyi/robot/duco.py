"""Thin wrapper around the SONGYI robot SDK (DucoCobot Thrift RPC, 3.8.5).

All RPC calls are serialized through a process-wide lock because the
underlying Thrift socket is not thread-safe. A socket timeout is forced
so a hung RPC can never stall the whole process.

Gripper: driven through the arm's tool-485 port with raw Modbus-RTU
frames (mechanism from ``demo/hand_test.py``): register 256 = init,
259 = position command 0-1000 (0=close, 1000=open).

Waist/Head (optional): ``DucoCobotWaist`` / ``DucoCobotHead`` on separate
Thrift ports. They are NEVER power-on/enabled by this SDK — enabling must
be done externally (teach pendant); :meth:`DucoAuxAxis.wait_power_on`
polls the power state and blocks (with prompts) until SR_Enable.
"""

from __future__ import annotations

import importlib
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_THRIFT_LOCK = threading.RLock()

# aux 等待循环（连接重试/外部使能等待）的全局停止事件：
# 进程信号处理器调用 request_stop() 后，所有等待立即唤醒并中止
_STOP_EVENT = threading.Event()


def request_stop() -> None:
    """请求中止所有 aux 等待循环（供 SIGINT/SIGTERM 处理器调用）。

    典型场景：入口脚本用自定义信号处理器只置标志不抛 KeyboardInterrupt，
    主线程阻塞在腰部/头部等待循环时 Ctrl+C 无法生效——调用本函数后
    等待循环立刻醒来并以 DucoArmError 中止，交由外层 finally 清理退出。
    """
    _STOP_EVENT.set()


def is_stop_requested() -> bool:
    return _STOP_EVENT.is_set()


def _interruptible_sleep(seconds: float) -> None:
    """可中断睡眠：request_stop() 后立即返回。"""
    _STOP_EVENT.wait(seconds)


def _set_transport_timeout(robot: Any, timeout_ms: int) -> None:
    """把超时设置到 vendor 传输栈内部真正的 TSocket 上。

    vendor ``DucoCobot*`` 把 TSocket 包进 TFramedTransport（内部属性
    name-mangled 为 ``_TFramedTransport__trans``，且无 ``__getattr__``
    转发）——直接对 ``robot.transport`` 调 ``setTimeout`` 会被 ``hasattr``
    拦下而**静默失效**：无超时的 connect 在模块未上电/IP 无响应时按
    OS 默认阻塞约 130s，期间等待循环与信号均无法打断。
    """
    transport = getattr(robot, "transport", None)
    seen: set[int] = set()
    while transport is not None and id(transport) not in seen:
        seen.add(id(transport))
        if hasattr(transport, "setTimeout"):
            transport.setTimeout(timeout_ms)
            return
        transport = getattr(
            transport, "_TFramedTransport__trans", None
        ) or getattr(transport, "trans", None)
    logger.debug(
        "no TSocket with setTimeout found; timeout %sms not applied", timeout_ms
    )


class DucoArmError(RuntimeError):
    pass


# 阻塞 RPC（movej blocking：响应等到运动完成才返回）专用超时——默认
# 100ms socket 超时必然早于运动完成触发，超时后迟到的响应污染字节流，
# 后续所有 RPC 报 invalid TType / Bad version（协议失步）
BLOCKING_RPC_TIMEOUT_MS = 60_000

# 连接重试次数（open/power_on/enable 任一失败即重建 socket 重试）
_CONNECT_ATTEMPTS = 3
_CONNECT_RETRY_INTERVAL_S = 1.0


def _recover_transport(robot: Any, name: str, socket_timeout_ms: int) -> None:
    """close+open 重建 thrift 传输层（不动 power/enable 状态）。

    socket 超时或半包错误后字节流错位（后续 RPC 全部报 invalid TType /
    Bad version），必须重建 socket 才能恢复对齐。
    """
    logger.warning("%s: thrift 传输层异常，重建 socket...", name)
    robot.close()
    if robot.open() != 0:
        raise DucoArmError(f"{name}: 传输层重建失败（open() != 0）")
    _set_transport_timeout(robot, socket_timeout_ms)


def _rpc_with_recovery(
    name: str,
    robot: Any,
    socket_timeout_ms: int,
    fn: "Callable[[], Any]",
    timeout_ms: Optional[int] = None,
) -> Any:
    """锁内执行单次 RPC；传输/协议错误重建 socket 后重试一次。

    *timeout_ms* 非空时本调用临时放宽 socket 超时（阻塞 movej 等待
    运动完成的响应远超默认 100ms），调用结束（含重试）恢复默认。
    重试仍失败则抛出，由调用方按语义处理（状态组省略/告警等）。
    """
    with _THRIFT_LOCK:
        try:
            if timeout_ms is not None:
                _set_transport_timeout(robot, timeout_ms)
            return fn()
        except Exception as first:
            logger.warning(
                "%s: RPC 异常（%s: %s），重建传输层后重试一次",
                name,
                type(first).__name__,
                first,
            )
            try:
                _recover_transport(robot, name, socket_timeout_ms)
                return fn()
            except Exception:
                logger.exception("%s: RPC 重试仍失败", name)
                raise
        finally:
            if timeout_ms is not None:
                _set_transport_timeout(robot, socket_timeout_ms)


# 夹爪 Modbus 寄存器（工具 485 总线）
GRIPPER_REG_INIT = 256        # 1=初始化, 165=全程标定
GRIPPER_REG_SAVE = 768        # 标定保存
GRIPPER_REG_POSITION = 259    # 位置指令 0-1000（0=夹紧, 1000=松开）
GRIPPER_REG_INIT_STATE = 512  # 初始化状态（轮询至 1 = 初始化完成）
GRIPPER_REG_STATE = 514       # 实时位置反馈 0-1000（读取寄存器，新协议）

# gen_py.robot.ttypes.StateRobot（腰部/头部共用同一状态机）
SR_ENABLE = 6  # SR_Enable：已上电且使能，可接受运动指令
SR_STATE_NAMES = {
    0: "SR_Start",
    1: "SR_Initialize",
    2: "SR_Logout",
    3: "SR_Login",
    4: "SR_PowerOff",
    5: "SR_Disable",
    6: "SR_Enable",
}

# 腰部前 3 关节固定值（现场标定值）：
# 写死不允许通过 action 控制，servoj/movej 只接受第 4 关节起的可控值，
# 下发时自动拼回该前缀
WAIST_FIXED_JOINTS: List[float] = [
    -0.8419940400200225,
    1.561184229406744,
    -0.7249411193131796,
]


def _u8_to_s8(byte_val: int) -> int:
    """Unsigned byte (0-255) -> signed byte (-128..127), thrift i8 wire type."""
    return byte_val if byte_val <= 127 else byte_val - 256


def modbus_crc16(data_bytes: Sequence[int]) -> "tuple[int, int]":
    """Modbus-RTU CRC-16; returns (crc_low, crc_high) — low byte first."""
    crc = 0xFFFF
    for byte in data_bytes:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1
    return crc & 0xFF, (crc >> 8) & 0xFF


def parse_gripper_position(resp: Sequence[int]) -> Optional[int]:
    """从 485 串口原始缓冲中解析夹爪实时位置（新版 hand_test.py 协议）。

    *resp* 为 thrift 返回的**有符号**字节序列。协议：在缓冲中扫描帧头
    ``01 03 02``（从站 1 / 功能码 3 / 字节数 2——缓冲可能含前导杂散
    字节，不做定位假设），其后 2 字节为大端位置值（0-1000‰）。响应帧
    不带 CRC 校验（与官方 hand_test.py 一致）。解析失败返回 None。
    """
    if not resp:
        return None
    data_u8 = [b + 256 if b < 0 else b for b in resp]
    frame_header = [0x01, 0x03, 0x02]
    header_len = len(frame_header)
    header_index = -1
    for i in range(len(data_u8) - header_len + 1):
        if data_u8[i : i + header_len] == frame_header:
            header_index = i
            break
    if header_index == -1:
        return None
    pos_start = header_index + header_len
    if pos_start + 2 > len(data_u8):
        return None
    return (data_u8[pos_start] << 8) | data_u8[pos_start + 1]


def build_gripper_frame(
    slave_addr: int, func_code: int, reg_addr: int, reg_data: int
) -> List[int]:
    """Build a Modbus-RTU write frame as signed bytes for the thrift API."""
    frame = [
        slave_addr,
        func_code,
        (reg_addr >> 8) & 0xFF,
        reg_addr & 0xFF,
        (reg_data >> 8) & 0xFF,
        reg_data & 0xFF,
    ]
    crc_low, crc_high = modbus_crc16(frame)
    return [_u8_to_s8(b) for b in frame + [crc_low, crc_high]]


class DucoArm:
    """One robot arm connection (left: 7003 / right: 7004 in the demo)."""

    def __init__(
        self,
        name: str,
        ip: str,
        port: int,
        *,
        sdk_path: str = "",
        socket_timeout_ms: int = 100,
        velocity: float = 1.0,
        acceleration: float = 10.0,
        smooth_vel: int = 10,
        smooth_acc: int = 1,
        gripper_hand_id: Optional[int] = None,
        status_min_interval_s: float = 0.1,
        tool: str = "tcp_1",
        wobj: Optional[str] = None,
        tcp_velocity: Optional[float] = None,
        q_near: Optional[Sequence[float]] = None,
    ) -> None:
        self.name = name
        self.ip = ip
        self.port = port
        self.sdk_path = sdk_path
        self.socket_timeout_ms = socket_timeout_ms
        self.velocity = velocity
        self.acceleration = acceleration
        self.smooth_vel = smooth_vel
        self.smooth_acc = smooth_acc
        # 末端速度（m/s，movel/servol 笛卡尔通道；独立于关节速度
        # velocity）；未配置回退关节速度
        self.tcp_velocity = (
            float(tcp_velocity) if tcp_velocity is not None else None
        )
        # 末端移动 q_near：7 关节弧度；None=按末端移动默认行为
        # （vendor 参数为空；"home" 关键字由 adapter 解析为 reset_pose
        # 对应臂段后再传入）
        self.q_near = (
            [float(v) for v in q_near] if q_near else None
        )
        self.gripper_hand_id = gripper_hand_id
        # servoj_pose 的 tool/wobj：wobj 缺省按臂名（左 arm_1_base /
        # 右 arm_2_base），可经 devices 配置键 tool/wobj 覆盖
        self.tool = tool
        self.wobj = wobj or {
            "left": "arm_1_base",
            "right": "arm_2_base",
        }.get(name, "")
        # 状态统一读取（get_robot_status）节流窗口：位置/速度/扭矩/温度/
        # 电流/TCP 全部由这一条 RPC 提供，窗口可配（devices 配置键
        # status_min_interval_s，弧度无关、单位秒）
        self._status_min_interval_s = float(status_min_interval_s)
        self._gripper_last: Dict[int, int] = {}
        self._gripper_feedback_raw: Optional[int] = None
        self._gripper_last_read_ts: float = 0.0
        self._gripper_read_failed: bool = False
        self._status: Any = None
        self._status_ts: float = 0.0
        self._robot: Any = None
        self._connected = False

    # ── lifecycle ────────────────────────────────────────────────────

    def _load_sdk(self) -> Any:
        base = Path(self.sdk_path) if self.sdk_path else Path("demo")
        lib_path = base / "lib"
        for path in (str(base.resolve()), str(lib_path.resolve())):
            if path not in sys.path:
                sys.path.insert(0, path)
        try:
            import DucoCobot  # type: ignore
        except ImportError as exc:
            raise DucoArmError(
                f"Cannot import SONGYI demo SDK (DucoCobot) from {base}: {exc}. "
                "Set devices.<name>.sdk_path to the folder containing "
                "DucoCobot.py / gen_py / lib."
            ) from exc
        return DucoCobot

    def connect(self) -> None:
        with _THRIFT_LOCK:
            if self._connected:
                return
            module = self._load_sdk()
            last_exc: Optional[Exception] = None
            for attempt in range(1, _CONNECT_ATTEMPTS + 1):
                robot = module.DucoCobot(self.ip, self.port)
                _set_transport_timeout(robot, self.socket_timeout_ms)
                try:
                    if robot.open() != 0:
                        raise DucoArmError(
                            f"arm {self.name!r}: open() failed "
                            f"({self.ip}:{self.port})"
                        )
                    # power_on/enable 阻塞等控制器完成（秒级）：临时放宽
                    # socket 超时，否则 100ms 必超时且传输层失步
                    _set_transport_timeout(robot, BLOCKING_RPC_TIMEOUT_MS)
                    robot.power_on(True)
                    robot.enable(True)
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "arm %r: connect attempt %d/%d failed (%s: %s)%s",
                        self.name,
                        attempt,
                        _CONNECT_ATTEMPTS,
                        type(exc).__name__,
                        exc,
                        "；重建 socket 重试..."
                        if attempt < _CONNECT_ATTEMPTS
                        else "；放弃",
                    )
                    try:
                        robot.close()
                    except Exception:
                        logger.debug("arm %r close error", self.name)
                    if attempt < _CONNECT_ATTEMPTS:
                        time.sleep(_CONNECT_RETRY_INTERVAL_S)
                    continue
                # 成功：恢复常规 100ms 超时（状态/运动 RPC 由调用点按需放宽）
                _set_transport_timeout(robot, self.socket_timeout_ms)
                self._robot = robot
                self._connected = True
                logger.info(
                    "arm %r connected (%s:%s)", self.name, self.ip, self.port
                )
                return
            raise DucoArmError(
                f"arm {self.name!r}: connect failed after "
                f"{_CONNECT_ATTEMPTS} attempts"
            ) from last_exc

    def disconnect(self) -> None:
        with _THRIFT_LOCK:
            if not self._connected:
                return
            try:
                if self._robot is not None:
                    self._robot.stop(True)
                    self._robot.close()
            except Exception as exc:
                logger.debug(
                    "arm %r disconnect error: %s", self.name, type(exc).__name__
                )
            finally:
                self._robot = None
                self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def _require(self) -> Any:
        if not self._connected or self._robot is None:
            raise DucoArmError(f"arm {self.name!r} is not connected")
        return self._robot

    # ── state reads ──────────────────────────────────────────────────

    def get_positions(self) -> List[float]:
        """关节位置（弧度）：get_robot_status 单 RPC 提取（节流共享）。"""
        return self._status_group(
            "actual_joint_pos", ("get_actual_joints_position",)
        )

    def get_tcp_pose(self) -> List[float]:
        """末端位姿 [x, y, z, Rx, Ry, Rz]（米/弧度），同上单 RPC 提取。"""
        return self._status_group("actual_tcp_pose", ("get_tcp_pose",))[:6]

    def get_tcp_velocity(self) -> List[float]:
        """末端速度 [vx,vy,vz,vRx,vRy,vRz]：get_robot_status 单 RPC 提取。"""
        return self._status_group(
            "actual_tcp_vel", ("get_tcp_speed", "get_tcp_velocity")
        )[:6]

    def get_tcp_acceleration(self) -> List[float]:
        """末端加速度（6 维）：get_robot_status 单 RPC 提取。"""
        return self._status_group(
            "actual_tcp_acc", ("get_tcp_acceleration", "get_actual_tcp_acc")
        )[:6]

    def get_velocities(self) -> List[float]:
        """关节速度：get_robot_status 单 RPC 提取（节流共享）。"""
        return self._status_group("actual_joint_vel", ("get_actual_joints_speed",))

    def get_accelerations(self) -> List[float]:
        """关节加速度：get_robot_status 单 RPC 提取（节流共享）。"""
        return self._status_group(
            "actual_joint_acc", ("get_actual_joints_acceleration",)
        )

    def get_torques(self) -> List[float]:
        """关节扭矩：get_robot_status 单 RPC 提取（节流共享）。"""
        return self._status_group("actual_joint_torq", ("get_actual_joints_torque",))

    def get_temperatures(self) -> List[float]:
        """Joint temperatures (°C)：get_robot_status 单 RPC 提取。"""
        return self._status_group(
            "actual_joint_temp",
            (
                "get_actual_joints_temperature",
                "get_joints_temperature",
                "get_joints_temperatures",
            ),
        )

    def get_currents(self) -> List[float]:
        """Joint currents (A)：get_robot_status 单 RPC 提取。"""
        return self._status_group("actual_joint_current", ())

    def get_robot_state(self) -> List[float]:
        """``get_robot_state``：[机器人状态, 程序状态, 安全状态, 操作模式]。

        告警检测用：机器人状态非 ``SR_Enable`` 视为异常。
        """
        robot = self._require()
        values = _rpc_with_recovery(
            f"arm {self.name!r}", robot, self.socket_timeout_ms,
            robot.get_robot_state,
        )
        return [float(v) for v in (values or [])]

    def get_servo_errids(self) -> List[float]:
        """各关节伺服错误码（``get_robot_status().servo_errid``）。

        告警检测用：任一非 0 即异常；读取失败返回空列表。
        """
        status = self.get_robot_status()
        return list(getattr(status, "servo_errid", None) or [])

    def get_robot_status(self, min_interval_s: Optional[float] = None) -> Any:
        """RobotStatus（一次 RPC 覆盖 pos/vel/acc/torq/temp/current/tcp）。

        全部状态组读取共用这一条 RPC，按 *min_interval_s*（缺省取
        ``status_min_interval_s`` 配置，默认 0.1s）节流——此前位置/速度/
        扭矩等各自独立 getter 高频请求会打满控制器导致报错。读取失败
        也计入同一节流窗口（退避，窗口内不重试）并返回 None，调用方
        （``_status_group``）按需回退旧版独立 getter。
        """
        now = time.monotonic()
        interval = (
            self._status_min_interval_s
            if min_interval_s is None
            else float(min_interval_s)
        )
        if (now - self._status_ts) >= interval:
            robot = self._require()
            try:
                self._status = _rpc_with_recovery(
                    f"arm {self.name!r}",
                    robot,
                    self.socket_timeout_ms,
                    robot.get_robot_status,
                )
            except Exception:
                self._status = None
            finally:
                self._status_ts = now
        return self._status

    def _status_group(
        self, attr: str, legacy_getters: Sequence[str] = ()
    ) -> List[float]:
        """从 get_robot_status 提取一组状态数据（全部组共享单次 RPC）。

        status 读取失败（None）或字段为空时回退旧版独立 getter RPC
        （旧 SDK 兼容）；新旧都拿不到时返回空列表。
        """
        status = self.get_robot_status()
        if status is not None:
            values = getattr(status, attr, None) or []
            if values:
                return [float(v) for v in values]
        robot = self._require()
        for method_name in legacy_getters:
            getter = getattr(robot, method_name, None)
            if getter is None:
                continue
            values = _rpc_with_recovery(
                f"arm {self.name!r}", robot, self.socket_timeout_ms, getter
            )
            if values:
                return [float(v) for v in values]
        return []

    # ── motion ───────────────────────────────────────────────────────

    def servoj(
        self,
        joints: Sequence[float],
        *,
        velocity: Optional[float] = None,
        acceleration: Optional[float] = None,
    ) -> None:
        """Non-blocking real-time joint servo target (7 joints per arm).

        *velocity*/*acceleration* 缺省 None → 用臂配置值（self.velocity）。
        """
        target = [float(v) for v in joints]
        robot = self._require()
        _rpc_with_recovery(
            f"arm {self.name!r}",
            robot,
            self.socket_timeout_ms,
            lambda: robot.servoj(
                target,
                velocity if velocity is not None else self.velocity,
                acceleration if acceleration is not None else self.acceleration,
                False,
                200,
                25,
                self.smooth_vel,
                self.smooth_acc,
            ),
        )

    def movej(
        self,
        joints: Sequence[float],
        *,
        velocity: Optional[float] = None,
        acceleration: Optional[float] = None,
        blocking: bool = True,
    ) -> None:
        robot = self._require()
        _rpc_with_recovery(
            f"arm {self.name!r}",
            robot,
            self.socket_timeout_ms,
            lambda: robot.movej2(
                [float(v) for v in joints],
                velocity if velocity is not None else self.velocity,
                acceleration if acceleration is not None else self.acceleration,
                0,
                blocking,
            ),
            # 阻塞等待运动完成的响应远超默认 100ms：临时放宽，防超时失步
            timeout_ms=BLOCKING_RPC_TIMEOUT_MS if blocking else None,
        )

    def _tcp_speed(self, velocity: Optional[float]) -> float:
        """末端速度解析：显式值 > 臂 tcp_velocity 配置 > 关节速度。"""
        if velocity is not None:
            return velocity
        if self.tcp_velocity is not None:
            return self.tcp_velocity
        return self.velocity

    def movel(
        self,
        pose: Sequence[float],
        *,
        velocity: Optional[float] = None,
        acceleration: Optional[float] = None,
        blocking: bool = False,
    ) -> None:
        """末端直线运动到目标位姿 [x, y, z, Rx, Ry, Rz]（m/rad）——笛卡尔主通道。

        v/a 为**末端**速度（m/s）/加速度（m/s²），缺省 None → 用臂配置
        tcp_velocity（未配回退 velocity）/acceleration；r（融合半径）=0；
        q_near 按臂配置（缺省 None）；tool/wobj 按臂配置。
        """
        target = [float(v) for v in pose]
        robot = self._require()
        _rpc_with_recovery(
            f"arm {self.name!r}",
            robot,
            self.socket_timeout_ms,
            lambda: robot.movel(
                target,
                self._tcp_speed(velocity),
                acceleration if acceleration is not None else self.acceleration,
                0,
                list(self.q_near) if self.q_near else None,
                self.tool,
                self.wobj,
                blocking,
            ),
        )

    def servol(
        self,
        pose: Sequence[float],
        *,
        velocity: Optional[float] = None,
        acceleration: Optional[float] = None,
        blocking: bool = False,
    ) -> None:
        """servol：直线伺服到目标位姿（**movel 的备选通道**，参数布局多
        kp/kd/平滑项；绝对位姿，非偏移）。速度解析同 movel（tcp_velocity
        优先）；q_near 按臂配置（缺省 None）。"""
        target = [float(v) for v in pose]
        robot = self._require()
        _rpc_with_recovery(
            f"arm {self.name!r}",
            robot,
            self.socket_timeout_ms,
            lambda: robot.servol(
                target,
                self._tcp_speed(velocity),
                acceleration if acceleration is not None else self.acceleration,
                list(self.q_near) if self.q_near else None,
                self.tool,
                self.wobj,
                blocking,
                200,
                25,
                self.smooth_vel,
                self.smooth_acc,
            ),
        )

    def check_ik(self, pose: Sequence[float]) -> bool:
        """``cal_ikine`` 试解（ik_check 开关用）：解不存在返回 False。

        调用 vendor ``cal_ikine(p, q_near, tool, wobj)``（与 servol 同
        q_near/tool/wobj 配置）；异常或空解均视为无解。
        """
        robot = self._require()
        result = _rpc_with_recovery(
            f"arm {self.name!r}",
            robot,
            self.socket_timeout_ms,
            lambda: robot.cal_ikine(
                [float(v) for v in pose],
                list(self.q_near) if self.q_near else None,
                self.tool,
                self.wobj,
            ),
        )
        return bool(result)

    def servoj_pose(self, pose: Sequence[float]) -> None:
        """Non-blocking real-time cartesian servo target [x, y, z, Rx, Ry, Rz].

        tool/wobj 按臂配置（左 arm_1_base / 右 arm_2_base，tool=tcp_1）。
        """
        target = [float(v) for v in pose]
        robot = self._require()
        logger.debug('ee_pose send: %s', target)
        _rpc_with_recovery(
            f"arm {self.name!r}",
            robot,
            self.socket_timeout_ms,
            lambda: robot.servoj_pose(
                target,
                self.velocity,
                self.acceleration,
                None,
                self.tool,
                self.wobj,
                False,
                200,
                25,
                self.smooth_vel,
                self.smooth_acc,
            ),
        )

    # ── gripper (tool-485 Modbus-RTU, demo/hand_test.py mechanism) ───

    @property
    def gripper_enabled(self) -> bool:
        return self.gripper_hand_id is not None

    def _gripper_write(self, reg_addr: int, reg_data: int) -> None:
        robot = self._require()
        frame = build_gripper_frame(self.gripper_hand_id, 6, reg_addr, reg_data)

        def _call() -> None:
            robot.tool_read_raw_data_485(4096)
            robot.tool_write_raw_data_485(frame)

        _rpc_with_recovery(
            f"arm {self.name!r}", robot, self.socket_timeout_ms, _call
        )

    def gripper_init(self, full_calibration: bool = False) -> None:
        """Initialize the gripper（写 256 后轮询 512 状态至 1；165=全程标定）.

        交互顺序与新版 hand_test.py 一致：初始化帧 → 轮询 Reg 512 直到
        状态 = 1（初始化完成）→ 全程标定时写 768 保存。轮询超时（30s）
        抛 DucoArmError。
        """
        if not self.gripper_enabled:
            raise DucoArmError(
                f"arm {self.name!r}: gripper_hand_id not configured"
            )
        reg_data = 165 if full_calibration else 1
        logger.info(
            "arm %r: gripper init (hand_id=%s, full_calibration=%s) ...",
            self.name,
            self.gripper_hand_id,
            full_calibration,
        )
        self._gripper_write(GRIPPER_REG_INIT, reg_data)
        self._gripper_wait_init_done()
        if full_calibration:
            self._gripper_write(GRIPPER_REG_SAVE, 1)
        self._gripper_last.clear()
        logger.info("arm %r: gripper init done", self.name)

    def _gripper_wait_init_done(self, timeout_s: float = 30.0) -> None:
        """轮询 Reg 512 直到初始化状态 = 1（新版 hand_test.py 流程）。

        轮询帧为 func-3 **读请求**（写读请求 → sleep 20ms → 读响应）。
        """
        robot = self._require()
        state_req = build_gripper_frame(
            self.gripper_hand_id, 3, GRIPPER_REG_INIT_STATE, 1
        )

        def _poll() -> Any:
            robot.tool_write_raw_data_485(state_req)
            time.sleep(0.02)
            return robot.tool_read_raw_data_485(4096)

        deadline = time.monotonic() + timeout_s
        first = True
        while time.monotonic() < deadline:
            try:
                if first:
                    # 清 485 接收缓冲
                    robot.tool_read_raw_data_485(4096)
                    first = False
                reply = _rpc_with_recovery(
                    f"arm {self.name!r}",
                    robot,
                    self.socket_timeout_ms,
                    _poll,
                )
            except Exception:
                reply = None
            state = parse_gripper_position(reply)
            if state == 1:
                logger.info("arm %r: gripper init state = 1 (完成)", self.name)
                return
            time.sleep(0.02)
        raise DucoArmError(
            f"arm {self.name!r}: gripper init timeout ({timeout_s:.0f}s, "
            f"Reg {GRIPPER_REG_INIT_STATE} 状态未到 1)"
        )

    def gripper_command(self, position_0_1000: int) -> None:
        """Command gripper position 0-1000 (0=close, 1000=open); dedup repeats."""
        if not self.gripper_enabled:
            return
        value = max(0, min(1000, int(position_0_1000)))
        if self._gripper_last.get(self.gripper_hand_id) == value:
            return
        self._gripper_write(GRIPPER_REG_POSITION, value)
        self._gripper_last[self.gripper_hand_id] = value
        logger.debug(
            "arm %r gripper -> %d", self.name, value
        )

    def gripper_last_position(self) -> Optional[int]:
        """Last commanded position 0-1000; None when never commanded/disabled."""
        if not self.gripper_enabled:
            return None
        return self._gripper_last.get(self.gripper_hand_id)

    def gripper_read_position(self) -> Optional[int]:
        """Read gripper real-time position (Modbus func 3, **Reg 514**) 0-1000.

        交互顺序与新版 hand_test.py 一致：先清 485 接收缓冲，写读请求，
        立即读取响应；响应缓冲按帧头 ``01 03 02`` 扫描解析（容忍前导
        杂散字节，无响应 CRC）。解析失败返回 None。
        """
        if not self.gripper_enabled:
            return None
        robot = self._require()
        req = build_gripper_frame(
            self.gripper_hand_id, 3, GRIPPER_REG_STATE, 1
        )

        def _call() -> Any:
            robot.tool_read_raw_data_485(4096)
            robot.tool_write_raw_data_485(req)
            time.sleep(0.02)
            return robot.tool_read_raw_data_485(4096)

        resp = _rpc_with_recovery(
            f"arm {self.name!r}", robot, self.socket_timeout_ms, _call
        )
        return parse_gripper_position(resp)

    def gripper_state_0_10(self, min_interval_s: float = 0.2) -> float:
        """夹爪状态 0-10（SDK Modbus Reg 259 真实反馈，节流读取）。

        **只上送真实反馈**（观测/current_state 应反映实际位置而非目标）：读取失败沿用上一次成功反馈；从未成功过则 0.0，**不再
        回退指令目标值**。失败以限频 WARNING 提示（5s 一次）。
        """
        now = time.monotonic()
        if (now - self._gripper_last_read_ts) >= min_interval_s:
            self._gripper_last_read_ts = now
            try:
                raw = self.gripper_read_position()
            except Exception:
                raw = None
            if raw is not None:
                self._gripper_feedback_raw = raw
                self._gripper_read_failed = False
            else:
                if not self._gripper_read_failed:
                    logger.error(
                        "[GRIPPER-DIAG] arm %r: 夹爪反馈读取失败（Modbus "
                        "Reg 259）——上送值停留于上次成功反馈，不再回退"
                        "目标值；请检查夹爪 485 总线/gripper_hand_id",
                        self.name,
                    )
                    self._gripper_read_failed = True
        if self._gripper_feedback_raw is not None:
            clamped = max(0, min(1000, self._gripper_feedback_raw))
            return round(clamped * 0.01, 2)
        logger.warning(
            "arm %r: 夹爪从未读到反馈，上送 0.0（不回退目标值）",
            self.name,
        )
        return 0.0


class DucoAuxAxis:
    """Optional waist/head module (DucoCobotWaist / DucoCobotHead, Thrift).

    使能策略：**永远不由本 SDK 上电/使能**（无 power_on 接口，使能须由
    外部如示教器完成）。connect 后 ``wait_power_on`` 循环读取
    ``get_robot_state()[0]``，未到 ``SR_Enable`` 则打印提示并循环等待。

    固定关节约定：``fixed_joints`` 非空时（腰部 = 前 3 关节写死 demo
    值），``servoj``/``movej`` 入参为**可控关节值**（固定前缀之后的
    段），下发前自动拼回前缀——action 永远无法触碰固定关节。
    ``enforce_fixed_joints`` 在就绪后把实际关节锁到固定前缀。
    """

    def __init__(
        self,
        name: str,
        kind: str,
        ip: str,
        port: int,
        *,
        sdk_path: str = "",
        socket_timeout_ms: int = 100,
        velocity: float = 0.3,
        acceleration: float = 5.0,
        smooth_vel: int = 10,
        smooth_acc: int = 1,
        poll_interval_s: float = 2.0,
        fixed_joints: Optional[Sequence[float]] = None,
        enforce_fixed_pose: bool = True,
    ) -> None:
        self.name = name
        self.kind = kind  # "waist" | "head"
        self.ip = ip
        self.port = port
        self.sdk_path = sdk_path
        self.socket_timeout_ms = socket_timeout_ms
        self.velocity = velocity
        self.acceleration = acceleration
        self.smooth_vel = smooth_vel
        self.smooth_acc = smooth_acc
        self.poll_interval_s = poll_interval_s
        self._fixed_joints = [float(v) for v in (fixed_joints or [])]
        self.enforce_fixed_pose = enforce_fixed_pose
        self._robot: Any = None
        self._connected = False

    @property
    def fixed_joints(self) -> List[float]:
        """固定关节前缀（写死、不经 action 控制）；无可控限制时为空。"""
        return list(self._fixed_joints)

    @property
    def fixed_count(self) -> int:
        return len(self._fixed_joints)

    def _full_target(self, control_joints: Sequence[float]) -> List[float]:
        """可控关节值 -> 完整关节目标（拼回固定前缀）。"""
        return self._fixed_joints + [float(v) for v in control_joints]

    # ── lifecycle ────────────────────────────────────────────────────

    def _load_sdk(self) -> Any:
        base = Path(self.sdk_path) if self.sdk_path else Path("demo")
        lib_path = base / "lib"
        for path in (str(base.resolve()), str(lib_path.resolve())):
            if path not in sys.path:
                sys.path.insert(0, path)
        module_name = "DucoCobotWaist" if self.kind == "waist" else "DucoCobotHead"
        try:
            module = importlib.import_module(module_name)  # type: ignore
        except ImportError as exc:
            raise DucoArmError(
                f"Cannot import {module_name} from {base}: {exc}. "
                "Set devices.sdk_path to the folder containing "
                "DucoCobotWaist.py / DucoCobotHead.py / gen_py / lib."
            ) from exc
        return module

    def connect(self) -> None:
        with _THRIFT_LOCK:
            if self._connected:
                return
            module = self._load_sdk()
            robot = module.DucoCobotWaist(self.ip, self.port) if self.kind == "waist" else module.DucoCobotHead(self.ip, self.port)
            _set_transport_timeout(robot, self.socket_timeout_ms)
            if robot.open() != 0:
                raise DucoArmError(
                    f"aux {self.name!r}: open() failed ({self.ip}:{self.port})"
                )
            # 外部使能约定：不调用 power_on/enable，仅等待外部上电使能
            self._robot = robot
            self._connected = True
            logger.info(
                "aux %s %r connected (%s:%s)", self.kind, self.name, self.ip, self.port
            )

    def disconnect(self) -> None:
        with _THRIFT_LOCK:
            if not self._connected:
                return
            try:
                if self._robot is not None:
                    self._robot.stop(True)
                    self._robot.close()
            except Exception as exc:
                logger.debug(
                    "aux %r disconnect error: %s", self.name, type(exc).__name__
                )
            finally:
                self._robot = None
                self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def _require(self) -> Any:
        if not self._connected or self._robot is None:
            raise DucoArmError(f"aux {self.name!r} is not connected")
        return self._robot

    # ── power-on state (external enable) ─────────────────────────────

    def get_robot_state(self) -> List[float]:
        """``get_robot_state``：[机器人状态, 程序状态, 安全状态, 操作模式]。"""
        robot = self._require()
        values = _rpc_with_recovery(
            f"aux {self.name!r}", robot, self.socket_timeout_ms,
            robot.get_robot_state,
        )
        return [float(v) for v in (values or [])]

    def get_servo_errids(self) -> List[float]:
        """各关节伺服错误码（``get_robot_status().servo_errid``）。

        告警检测用：任一非 0 即异常；读取失败返回空列表。
        """
        robot = self._require()
        status = _rpc_with_recovery(
            f"aux {self.name!r}", robot, self.socket_timeout_ms,
            robot.get_robot_status,
        )
        return list(getattr(status, "servo_errid", None) or [])

    def power_state(self) -> "tuple[int, str]":
        """(状态码, 状态名)，取 ``get_robot_state()[0]``。"""
        state = self.get_robot_state()
        code = int(state[0]) if state else -1
        return code, SR_STATE_NAMES.get(code, f"Unknown({code})")

    def wait_power_on(self) -> None:
        """循环等待外部上电使能：未到 SR_Enable 则提示并循环等待。

        ``request_stop()`` 后立刻中止（DucoArmError），不再睡眠轮询。
        """
        while True:
            if is_stop_requested():
                raise DucoArmError(
                    f"aux {self.name!r}: 等待外部上电使能被中止（停止请求）"
                )
            try:
                code, name = self.power_state()
            except Exception:
                if is_stop_requested():
                    raise DucoArmError(
                        f"aux {self.name!r}: 等待外部上电使能被中止（停止请求）"
                    )
                logger.warning(
                    "aux %s %r: 状态读取失败，%.0fs 后重试（外部使能等待中）...",
                    self.kind,
                    self.name,
                    self.poll_interval_s,
                )
                _interruptible_sleep(self.poll_interval_s)
                continue
            if code == SR_ENABLE:
                logger.info("aux %s %r: 已上电使能 (%s)", self.kind, self.name, name)
                return
            logger.warning(
                "aux %s %r: 未上电使能（当前状态 %s）。请在示教器/外部完成"
                "上电并使能，等待中（每 %.0fs 轮询）...",
                self.kind,
                self.name,
                name,
                self.poll_interval_s,
            )
            _interruptible_sleep(self.poll_interval_s)

    # ── state / motion ───────────────────────────────────────────────

    def get_positions(self) -> List[float]:
        robot = self._require()
        values = _rpc_with_recovery(
            f"aux {self.name!r}", robot, self.socket_timeout_ms,
            robot.get_actual_joints_position,
        )
        return [float(v) for v in (values or [])]

    def servoj(self, joints: Sequence[float]) -> None:
        """Non-blocking real-time servo（*joints* 为可控关节值，自动拼回固定前缀）。"""
        target = self._full_target(joints)
        robot = self._require()
        _rpc_with_recovery(
            f"aux {self.name!r}",
            robot,
            self.socket_timeout_ms,
            lambda: robot.servoj(
                target,
                self.velocity,
                self.acceleration,
                False,
                200,
                25,
                self.smooth_vel,
                self.smooth_acc,
            ),
        )

    def movej(
        self,
        joints: Sequence[float],
        *,
        velocity: Optional[float] = None,
        acceleration: Optional[float] = None,
        blocking: bool = True,
    ) -> None:
        """Blocking joint move（*joints* 为可控关节值，自动拼回固定前缀）。"""
        target = self._full_target(joints)
        robot = self._require()
        _rpc_with_recovery(
            f"aux {self.name!r}",
            robot,
            self.socket_timeout_ms,
            lambda: robot.movej2(
                target,
                velocity if velocity is not None else self.velocity,
                acceleration if acceleration is not None else self.acceleration,
                0,
                blocking,
            ),
            # 阻塞等待运动完成的响应远超默认 100ms：临时放宽，防超时失步
            timeout_ms=BLOCKING_RPC_TIMEOUT_MS if blocking else None,
        )

    def enforce_fixed_joints(self) -> bool:
        """初始化就绪后调用**一次**：无论偏差大小都发送一次 movej。

        发送前先读取实际关节位：目标 = 固定关节 + 当前可控段实际值
        （腰部第 4 关节保持当前位不跳点；头部全关节固定、无可控段 →
        即固定位姿）。读取不到完整关节位时不发送（仅告警），避免盲发。
        返回是否执行了发送。
        """
        if not self._fixed_joints:
            return False
        actual = self.get_positions()
        if len(actual) < len(self._fixed_joints):
            logger.warning(
                "aux %s %r: 关节读取不完整 (%d)，跳过固定关节下发",
                self.kind,
                self.name,
                len(actual),
            )
            return False
        tail = actual[len(self._fixed_joints):]
        logger.info(
            "aux %s %r: 固定关节下发一次：%s + 可控段 %s",
            self.kind,
            self.name,
            self._fixed_joints,
            tail,
        )
        self.movej(tail, blocking=True)
        return True


def open_aux_axes(cfg: Dict[str, Any]) -> Dict[str, DucoAuxAxis]:
    """Build waist/head modules from optional ``waist``/``head`` config sections.

    每节形如 ``{enabled: true, ip, port, velocity, ...}``；未配置或
    ``enabled: false``（默认）时不构建，完全不影响原双臂链路。
    固定关节均来自配置键 ``fixed_joints``（弧度数组）：腰部缺省回退
    代码常量（现场标定值，前 3 关节），头部默认空（不固定）。
    仅构建不连接——连接与外部使能等待由调用方控制（可重试）。
    """
    common: Dict[str, Any] = {}
    for key in ("sdk_path", "socket_timeout_ms"):
        if key in cfg:
            common[key] = cfg[key]
    axes: Dict[str, DucoAuxAxis] = {}
    for kind in ("waist", "head"):
        section = cfg.get(kind)
        if not isinstance(section, dict) or not section.get("enabled"):
            continue
        # 固定关节均来自配置键 fixed_joints（弧度数组）；腰部缺省回退
        # 代码常量（现场标定值），头部默认空（不固定）。均为全关节固定
        # 语义：初始化就绪后 enforce 一次性下发
        raw_fixed = section.get(
            "fixed_joints", WAIST_FIXED_JOINTS if kind == "waist" else None
        )
        fixed = [float(v) for v in (raw_fixed or [])]
        axes[kind] = DucoAuxAxis(
            name=kind,
            kind=kind,
            ip=str(section.get("ip", "")),
            port=int(section.get("port", 0)),
            sdk_path=str(section.get("sdk_path", common.get("sdk_path", "demo"))),
            socket_timeout_ms=int(
                section.get("socket_timeout_ms", common.get("socket_timeout_ms", 100))
            ),
            velocity=float(section.get("velocity", 0.3)),
            acceleration=float(section.get("acceleration", 5.0)),
            smooth_vel=int(section.get("smooth_vel", 10)),
            smooth_acc=int(section.get("smooth_acc", 1)),
            poll_interval_s=float(section.get("poll_interval_s", 2.0)),
            fixed_joints=fixed,
            enforce_fixed_pose=bool(section.get("enforce_fixed_pose", True)),
        )
    return axes


def _build_arm(name: str, cfg: Dict[str, Any], common: Dict[str, Any]) -> DucoArm:
    """Construct a DucoArm from one ``devices`` entry, merging defaults."""
    hand_id = cfg.get("gripper_hand_id", common.get("gripper_hand_id"))
    return DucoArm(
        name=name,
        ip=str(cfg.get("ip", "")),
        port=int(cfg.get("port", 0)),
        sdk_path=str(cfg.get("sdk_path", common.get("sdk_path", "demo"))),
        socket_timeout_ms=int(
            cfg.get("socket_timeout_ms", common.get("socket_timeout_ms", 100))
        ),
        velocity=float(cfg.get("velocity", common.get("velocity", 1.0))),
        acceleration=float(
            cfg.get("acceleration", common.get("acceleration", 10.0))
        ),
        smooth_vel=int(cfg.get("smooth_vel", common.get("smooth_vel", 10))),
        smooth_acc=int(cfg.get("smooth_acc", common.get("smooth_acc", 1))),
        tool=str(cfg.get("tool", common.get("tool", "tcp_1"))),
        wobj=(
            str(cfg["wobj"])
            if "wobj" in cfg
            else (str(common["wobj"]) if "wobj" in common else None)
        ),
        status_min_interval_s=float(
            cfg.get(
                "status_min_interval_s",
                common.get("status_min_interval_s", 0.1),
            )
        ),
        gripper_hand_id=(int(hand_id) if hand_id is not None else None),
        tcp_velocity=(
            float(cfg["tcp_velocity"])
            if cfg.get("tcp_velocity") is not None
            else (
                float(common["tcp_velocity"])
                if common.get("tcp_velocity") is not None
                else None
            )
        ),
        q_near=(
            cfg["q_near"]
            if cfg.get("q_near") is not None
            else common.get("q_near")
        ),
    )



def open_arms(devices_cfg: Dict[str, Any]) -> Dict[str, DucoArm]:
    """Build and connect arms from a system-config ``devices`` mapping."""
    common = dict(devices_cfg.get("common", {}) or {})
    # top-level scalars act as defaults for every arm (sdk_path, timeouts, ...)
    for key in (
        "sdk_path",
        "socket_timeout_ms",
        "velocity",
        "acceleration",
        "smooth_vel",
        "smooth_acc",
        "status_min_interval_s",
        "tool",
        "wobj",
        "tcp_velocity",
        "q_near",
    ):
        if key in devices_cfg and key not in common:
            common[key] = devices_cfg[key]

    arms: Dict[str, DucoArm] = {}
    try:
        for name, cfg in devices_cfg.items():
            if name == "common" or not isinstance(cfg, dict):
                continue
            arm = _build_arm(name, cfg, common)
            arm.connect()
            arms[name] = arm
    except Exception:
        # 连接中途失败：回滚释放此前已成功连接的臂（TCP/Thrift socket +
        # 底层资源），避免部分连接泄漏，随后原样抛出。
        for arm in arms.values():
            try:
                arm.disconnect()
            except Exception as exc:
                logger.debug(
                    "arm %r cleanup error: %s", arm.name, type(exc).__name__
                )
        raise
    return arms
