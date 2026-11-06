"""Mock SONGYI robot: pure-software twin of :mod:`duco` (no Thrift / 485).

接口与 ``duco.py`` 的 ``DucoArm`` / ``DucoAuxAxis`` 逐一同名同参（lifecycle、
状态组读取、servoj/movej/servoj_pose、夹爪、腰部/头部），不实现 Thrift
RPC 与工具 485 Modbus-RTU——全部状态由内置仿真线程合成：

- **观测**：get_positions / get_velocities / get_accelerations /
  get_torques / get_temperatures / get_currents / get_tcp_pose /
  get_tcp_velocity / get_tcp_acceleration / get_robot_status 全组可用，
  结构与真机一致（get_robot_status 一次提供全部状态组并按
  status_min_interval_s 节流）。
- **动作**：servoj/movej 下发关节目标、servoj_pose 下发 TCP 目标，
  仿真线程按「一阶低通 + 最大速度限幅」平滑逼近——观测读数渐变
  （非跳变），movej(blocking=True) 等待到位后返回。
- **夹爪**：gripper_command 目标 0-1000，仿真线程按固定速度渐进；
  gripper_read_position / gripper_state_0_10 返回仿真实时反馈
  （0=夹紧, 1000=松开），dedup 语义与真机一致。
- **腰部/头部**：fixed_joints 拼回、enforce_fixed_joints、
  wait_power_on 立即视为已外部上电使能（SR_Enable）。
- **事件源**：MockEventSource 每 interval_s（默认 60s）一个完整告警
  周期——构造 mock Event（默认「机器人电量低」，type=alarm/
  severity=Major，ends_at=0 进行中），clear_after_s（默认 30s）后
  **复用原告警 event_id 自动解除**（severity=Info，配对语义）；
  首周期立即发。事件**逐条经 EventManager 单例推送**（D158，原
  MockEventPublisher 直捅 transport 废止；测试可注入 publish_fn），
  由 SongyiMockAdapter.connect/disconnect 驱动启停。

接入方式：系统/机器人配置 ``custom_config.devices.mock: true``（腰部/
头部经 ``custom_config.waist.mock`` / ``head.mock`` 或 devices.mock
继承），``app.connect_arms`` / ``connect_aux_axes`` 自动切换本模块工厂。

自演示：``python -m songyi_robot.mock_duco``
"""

from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .duco import DucoArmError, SR_ENABLE, SR_STATE_NAMES

logger = logging.getLogger(__name__)

# 仿真步进频率（Hz）：观测读数刷新与运动平滑的基准
_SIM_HZ = 100.0
_SIM_DT = 1.0 / _SIM_HZ

# movej 阻塞等待到位的超时（与 duco.BLOCKING_RPC_TIMEOUT_MS 同量级）
_MOVEJ_TIMEOUT_S = 60.0
# 关节到位判据（弧度）
_JOINT_EPS = 1e-3
# servoj 一阶低通时间常数（秒）：流式目标平滑逼近
_JOINT_TAU_S = 0.04

# 夹爪全行程速度（寄存器单位/s）：0-1000 约 0.5s 全行程
_GRIPPER_SPEED = 2000.0
# TCP 插值限速（米/s 与 弧度/s）
_TCP_LINEAR_SPEED = 0.5
_TCP_ANGULAR_SPEED = 2.0

# 双臂默认初始关节位（与现场 reset_pose 同风格：左臂/右臂对称）
DEFAULT_LEFT_JOINTS: List[float] = [
    -1.0320814488493082, -1.518377327058867, 1.5707963267948966,
    2.328474977986255, -0.36408075262473183, 0.1747299991200988,
    0.1772946232498458,
]
DEFAULT_RIGHT_JOINTS: List[float] = [
    1.0320814488493082, -1.518377327058867, -1.5707963267948966,
    2.328474977986255, 0.36408075262473183, 0.1747299991200988,
    -0.1772946232498458,
]

# mock 事件源默认值（MockEventSource，custom_config.mock_events 可覆盖）
DEFAULT_EVENT_ABSTRACT = "机器人电量低"
DEFAULT_EVENT_CONTENT = "机器人电量低，请及时充电"


class MockEventSource(threading.Thread):
    """mock 事件源：定期构造 mock 告警 Event，经 EventManager 单例推送。

    与 MockDucoArm/MockDucoAuxAxis 配套（devices.mock=true 场景），按
    **完整告警周期**运行（对齐联调工具语义）：每 *interval_s* 秒一个
    周期——① 构造告警（默认「机器人电量低」，type=alarm /
    severity=Major，ends_at=0 表示进行中，新 uuid4 event_id，**首周期
    启动后立即推送**）；② *clear_after_s* 秒后（默认 30s）推送**解除**：
    **复用原告警 event_id**（平台侧配对还原完整告警区间）、
    severity=Info、abstract「…解除」、starts_at 保持告警发起时刻、
    ends_at=当前时刻；随后进入下一周期（新 event_id）。
    *clear_after_s*=0 关闭自动解除（仅上报告警，旧行为）。

    **发布通道（D158）**：逐条经 EventManager 单例（``publish(event_dict)``，
    source 由其按 session 身份填充、限频 2Hz、未接线时入缓存待补发）；
    *publish_fn* 可注入（测试捕获 dict）。单条发布失败仅记日志不中断
    循环。由 SongyiMockAdapter.connect/disconnect 驱动启停
    （``custom_config.mock_events`` 节：enabled/interval_s/clear_after_s/
    abstract/content/type/severity）。
    """

    def __init__(
        self,
        cfg: Optional[Mapping[str, Any]] = None,
        *,
        publish_fn: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> None:
        super().__init__(name="songyi-mock-events", daemon=True)
        cfg = dict(cfg or {})
        self._interval_s = max(float(cfg.get("interval_s", 60.0)), 0.05)
        self._clear_after_s = max(float(cfg.get("clear_after_s", 30.0)), 0.0)
        self._abstract = str(cfg.get("abstract", DEFAULT_EVENT_ABSTRACT))
        self._content = str(cfg.get("content", DEFAULT_EVENT_CONTENT))
        self._event_type = str(cfg.get("type", "alarm"))
        self._severity = str(cfg.get("severity", "Major"))
        self._publish_fn = publish_fn
        self._stop_evt = threading.Event()
        self.published = 0

    def stop(self) -> None:
        self._stop_evt.set()

    def _build_event(
        self, event_id: str, starts_at: int, clear: bool
    ) -> Dict[str, Any]:
        """构造 EventMessage 形态 dict（source 留空——EventManager 按
        session 身份填充；告警 ends_at=0 进行中，解除 ends_at=当前）。"""
        now_ms = int(time.time() * 1000)
        if clear:
            # 解除：复用原告警 event_id，starts_at 保持告警发起时刻，
            # ends_at=当前时刻（告警/解除 id 关联语义）
            abstract = f"{self._abstract}解除"
            severity = "Info"
            content = f"{self._content}已恢复"
            ends_at: int = now_ms
        else:
            abstract = self._abstract
            severity = self._severity
            content = self._content
            ends_at = 0  # 0 = 告警进行中
        return {
            "timestamp": now_ms,
            "event_id": event_id,
            "metadata": [{
                "abstract": abstract,
                "type": self._event_type,
                "severity": severity,
                "content": content,
                "starts_at": starts_at,
                "ends_at": ends_at,
            }],
        }

    def _publish(self, event: Dict[str, Any], clear: bool) -> None:
        publish_fn = self._publish_fn
        if publish_fn is None:
            # 缺省接 EventManager 单例（D158；lazy import 避免环）
            from cloudrobo_r2c.client.event_manager import event_manager

            publish_fn = event_manager.publish
        publish_fn(event)
        self.published += 1
        meta = event["metadata"][0]
        logger.info(
            "mock event published (#%d): %s event_id=%s abstract=%s "
            "severity=%s ends_at=%s",
            self.published, "clear" if clear else "raise",
            event["event_id"],
            meta["abstract"],
            meta["severity"],
            meta["ends_at"],
        )

    def run(self) -> None:
        logger.info(
            "mock event source started: interval=%.0fs clear_after=%.0fs "
            "abstract=%r severity=%s (via EventManager)",
            self._interval_s, self._clear_after_s,
            self._abstract, self._severity,
        )
        while not self._stop_evt.is_set():
            event_id = str(uuid.uuid4())
            starts_at = int(time.time() * 1000)
            try:
                self._publish(self._build_event(event_id, starts_at, False),
                              clear=False)
            except Exception:
                logger.exception("mock event publish failed")
            if self._clear_after_s > 0:
                # 告警发布 clear_after_s 秒后自动解除（复用同一 event_id）
                if self._stop_evt.wait(self._clear_after_s):
                    break
                try:
                    self._publish(
                        self._build_event(event_id, starts_at, True),
                        clear=True,
                    )
                except Exception:
                    logger.exception("mock event clear publish failed")
                remain = self._interval_s - self._clear_after_s
            else:
                remain = self._interval_s
            # 下一周期（周期从告警发布起算，减去已消耗的告警/解除时长）
            if self._stop_evt.wait(max(remain, 0.05)):
                break
        logger.info("mock event source stopped (published=%d)", self.published)


class _JointSim:
    """关节组仿真：目标低通 + 速度限幅逼近，输出位置/速度。"""

    def __init__(
        self,
        joints: Sequence[float],
        max_velocity: float,
        *,
        tau: float = _JOINT_TAU_S,
        eps: float = _JOINT_EPS,
    ) -> None:
        self._lock = threading.Lock()
        self._pos = [float(v) for v in joints]
        self._target = [float(v) for v in joints]
        self._vel = [0.0] * len(self._pos)
        self._max_velocity = max(float(max_velocity), 1e-3)
        self._tau = max(float(tau), 1e-3)
        self._eps = float(eps)

    # ── 状态读 ────────────────────────────────────────────────────────

    def positions(self) -> List[float]:
        with self._lock:
            return list(self._pos)

    def velocities(self) -> List[float]:
        with self._lock:
            return list(self._vel)

    def target(self) -> List[float]:
        with self._lock:
            return list(self._target)

    def reached(self, eps: Optional[float] = None) -> bool:
        limit = self._eps if eps is None else float(eps)
        with self._lock:
            return all(
                abs(t - p) <= limit for t, p in zip(self._target, self._pos)
            )

    # ── 控制 / 步进 ──────────────────────────────────────────────────

    def set_target(self, target: Sequence[float]) -> List[float]:
        """更新关节目标（按当前维度截断/右补当前位），返回生效目标。"""
        with self._lock:
            values = [float(v) for v in target[: len(self._pos)]]
            while len(values) < len(self._pos):
                values.append(self._pos[len(values)])
            self._target = values
            return list(self._target)

    def snap(self, joints: Sequence[float]) -> None:
        """瞬移（测试/初始化用）：位置与目标同时置位，速度清零。"""
        with self._lock:
            self._pos = [float(v) for v in joints]
            self._target = [float(v) for v in joints]
            self._vel = [0.0] * len(self._pos)

    def set_max_velocity(self, value: float) -> None:
        """调整关节最大速度（弧度/s，movej velocity 覆盖用）。"""
        self._max_velocity = max(float(value), 1e-3)

    def step(self, dt: float) -> None:
        alpha = 1.0 - math.exp(-dt / self._tau)
        max_step = self._max_velocity * dt
        with self._lock:
            for i, (pos, tgt) in enumerate(zip(self._pos, self._target)):
                delta = tgt - pos
                if abs(delta) <= self._eps:
                    self._pos[i] = tgt
                    self._vel[i] = 0.0
                    continue
                step = max(-max_step, min(max_step, alpha * delta))
                self._pos[i] = pos + step
                self._vel[i] = step / dt


class _TcpSim:
    """TCP 位姿仿真（servoj_pose 目标）：平移/旋转限速低通逼近。"""

    def __init__(self, pose: Sequence[float]) -> None:
        self._lock = threading.Lock()
        self._pose = [float(v) for v in pose[:6]]
        self._target = list(self._pose)
        self._vel = [0.0] * 6

    def pose(self) -> List[float]:
        with self._lock:
            return list(self._pose)

    def velocity(self) -> List[float]:
        with self._lock:
            return list(self._vel)

    def set_target(self, pose: Sequence[float]) -> None:
        with self._lock:
            values = [float(v) for v in pose[:6]]
            while len(values) < 6:
                values.append(self._pose[len(values)])
            self._target = values

    def reached(self, eps: float = 1e-3) -> bool:
        with self._lock:
            return all(
                abs(t - p) <= eps for t, p in zip(self._target, self._pose)
            )

    def step(self, dt: float) -> None:
        alpha = 1.0 - math.exp(-dt / _JOINT_TAU_S)
        with self._lock:
            for i, (pos, tgt) in enumerate(zip(self._pose, self._target)):
                delta = tgt - pos
                if abs(delta) <= 1e-4:
                    self._pose[i] = tgt
                    self._vel[i] = 0.0
                    continue
                limit = (
                    _TCP_LINEAR_SPEED if i < 3 else _TCP_ANGULAR_SPEED
                ) * dt
                step = max(-limit, min(limit, alpha * delta))
                self._pose[i] = pos + step
                self._vel[i] = step / dt


class _GripperSim:
    """夹爪仿真：位置指令 0-1000，按固定速度渐进（0=夹紧, 1000=松开）。"""

    def __init__(self, speed: float = _GRIPPER_SPEED) -> None:
        self._lock = threading.Lock()
        self._pos = 0.0
        self._target = 0
        self._speed = float(speed)

    def step(self, dt: float) -> None:
        with self._lock:
            delta = self._target - self._pos
            limit = self._speed * dt
            if abs(delta) <= limit:
                self._pos = float(self._target)
            else:
                self._pos += limit if delta > 0 else -limit

    def set_target(self, value: int) -> None:
        with self._lock:
            self._target = max(0, min(1000, int(value)))

    def position(self) -> int:
        with self._lock:
            return int(round(self._pos))


def _pseudo_fk(
    joints: Sequence[float], base_xyz: Sequence[float], mirror: bool
) -> List[float]:
    """伪正运动学：关节位 -> 确定性 TCP [x,y,z,Rx,Ry,Rz]（米/弧度）。

    无真实连杆参数，仅保证 TCP 随关节**连续、单调相关**地变化（观测
    联动真实感）；*mirror* 用于左右臂区分（基座 y 偏移与姿态取反）。
    """
    j = [float(v) for v in joints[:7]]
    while len(j) < 7:
        j.append(0.0)
    sign = -1.0 if mirror else 1.0
    x = float(base_xyz[0]) + sign * (
        0.30 + 0.05 * math.cos(j[0]) + 0.04 * math.sin(j[1])
        + 0.03 * math.cos(j[3]) + 0.02 * math.sin(j[5])
    )
    y = float(base_xyz[1]) + sign * (
        0.05 * math.sin(j[0]) + 0.04 * math.sin(j[2]) + 0.02 * math.cos(j[4])
    )
    z = float(base_xyz[2]) + (
        0.35 + 0.05 * math.sin(j[1]) + 0.04 * math.cos(j[2])
        + 0.02 * math.sin(j[4])
    )
    rx = 0.1 * math.sin(j[3] + j[4])
    ry = 0.1 * math.sin(j[4] + j[5])
    rz = 0.15 * sign * math.sin(j[0] + j[5] + j[6])
    return [x, y, z, rx, ry, rz]


def _deterministic_noise(t: float, index: int, amplitude: float) -> float:
    """确定性伪噪声（无随机源，测试可复现）：多频正弦叠加。"""
    return amplitude * (
        0.6 * math.sin(1.7 * t + 2.1 * index)
        + 0.4 * math.sin(0.9 * t + 0.7 * index)
    )


class _FkTracker:
    """关节模式下把 TCP 目标持续刷新为伪 FK(当前关节)——TCP 随关节联动。

    servoj/movej（关节模式）后 TCP 由伪正运动学驱动；servoj_pose 后
    ``arm._tcp_mode=True``，TCP 脱离伪 FK、按自身目标独立插值。
    """

    def __init__(self, arm: "MockDucoArm") -> None:
        self._arm = arm

    def step(self, dt: float) -> None:
        arm = self._arm
        if not arm._tcp_mode:
            arm._tcp.set_target(
                _pseudo_fk(
                    arm._joints.positions(), arm._tcp_fk_base, arm._mirror
                )
            )


class _SimLoop(threading.Thread):
    """设备仿真线程：固定频率驱动全部 steppers 的 step(dt)。"""

    def __init__(self, steppers: Sequence[Any]) -> None:
        super().__init__(name="songyi-mock-sim", daemon=True)
        self._steppers = list(steppers)
        self._stop_evt = threading.Event()

    def run(self) -> None:
        while not self._stop_evt.wait(_SIM_DT):
            for stepper in self._steppers:
                stepper.step(_SIM_DT)

    def stop(self) -> None:
        self._stop_evt.set()


class MockDucoArm:
    """Mock 双臂单臂连接（与 :class:`duco.DucoArm` 接口逐一同名同参）。

    真实构造参数全部保留（ip/port/socket_timeout_ms 等仅记录不使用），
    mock 专属参数：*initial_joints*（初始关节位，7 维）、
    *max_joint_velocity*（关节最大速度，弧度/s）、*noise*（读数伪噪声
    幅度，默认 0 保证确定性）、*base_xyz*/*mirror*（伪正运动学基座）。
    """

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
        initial_joints: Optional[Sequence[float]] = None,
        max_joint_velocity: float = 1.5,
        noise: float = 0.0,
        base_xyz: Sequence[float] = (0.0, 0.25, 0.0),
        mirror: bool = False,
        tcp_velocity: Optional[float] = None,
        q_near: Optional[Sequence[float]] = None,
        tool: str = "tcp_1",
        wobj: str = "",
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
        self.gripper_hand_id = gripper_hand_id
        self._status_min_interval_s = float(status_min_interval_s)
        # 笛卡尔通道配置面（与 DucoArm 对齐，movel/servol 用）
        self.tcp_velocity = (
            float(tcp_velocity) if tcp_velocity is not None else None
        )
        self.q_near = [float(v) for v in q_near] if q_near else None
        self.tool = str(tool)
        self.wobj = str(wobj) or ("arm_2_base" if mirror else "arm_1_base")
        joints = [
            float(v) for v in (initial_joints or DEFAULT_LEFT_JOINTS)
        ][:7]
        while len(joints) < 7:
            joints.append(0.0)
        self._joints = _JointSim(joints, max_joint_velocity)
        self._tcp_fk_base = [float(v) for v in base_xyz[:3]]
        self._mirror = bool(mirror)
        self._tcp = _TcpSim(_pseudo_fk(joints, self._tcp_fk_base, self._mirror))
        self._tcp_mode = False  # servoj_pose 后 TCP 脱离伪 FK 独立插值
        self._fk_tracker: Optional[_FkTracker] = None
        self._gripper = _GripperSim()
        self._gripper_last: Dict[int, int] = {}
        self._gripper_feedback_raw: Optional[int] = None
        self._gripper_last_read_ts: float = 0.0
        self._status: Any = None
        self._status_ts: float = 0.0
        self._loop: Optional[_SimLoop] = None
        self._connected = False

    # ── lifecycle ────────────────────────────────────────────────────

    def connect(self) -> None:
        if self._connected:
            return
        self._fk_tracker = _FkTracker(self)
        self._loop = _SimLoop(
            [self._joints, self._fk_tracker, self._tcp, self._gripper]
        )
        self._loop.start()
        self._connected = True
        logger.info(
            "mock arm %r connected (%s:%s) [software simulation]",
            self.name, self.ip, self.port,
        )

    def disconnect(self) -> None:
        if not self._connected:
            return
        if self._loop is not None:
            self._loop.stop()
            self._loop = None
        self._connected = False
        logger.info("mock arm %r disconnected", self.name)

    @property
    def connected(self) -> bool:
        return self._connected

    # ── mock 便利方法 ────────────────────────────────────────────────

    def set_joints(self, joints: Sequence[float]) -> None:
        """瞬移到指定位姿（测试/摆位用，不经运动过程）。"""
        values = [float(v) for v in joints][:7]
        self._joints.snap(values)
        self._tcp.set_target(_pseudo_fk(values, self._tcp_fk_base, self._mirror))
        self._tcp_mode = False

    @property
    def joint_target(self) -> List[float]:
        """最近一次 servoj/movej 的关节目标（调试用）。"""
        return self._joints.target()

    # ── state reads（与真机同构：全组可读）──────────────────────────

    def get_positions(self) -> List[float]:
        return self._joints.positions()

    def get_velocities(self) -> List[float]:
        return self._joints.velocities()

    def get_accelerations(self) -> List[float]:
        """关节加速度：运动时给非零合成值，静止为 0（量级仿真实测）。"""
        t = time.monotonic()
        return [
            (abs(v) * 0.5 + _deterministic_noise(t, i, 0.05))
            if abs(v) > 1e-3 else 0.0
            for i, v in enumerate(self._joints.velocities())
        ]

    def get_torques(self) -> List[float]:
        """关节扭矩：与 |速度| 相关的合成值（运动阻力 + 伪噪声）。"""
        t = time.monotonic()
        return [
            0.15 + abs(v) * 2.0 + _deterministic_noise(t, i, 0.05)
            for i, v in enumerate(self._joints.velocities())
        ]

    def get_temperatures(self) -> List[float]:
        """关节温度（°C）：40°C 基线缓慢漂移（无热模型）。"""
        t = time.monotonic()
        return [
            40.0 + 0.5 * math.sin(0.05 * t + 0.3 * i)
            + _deterministic_noise(t, i, 0.05)
            for i in range(7)
        ]

    def get_currents(self) -> List[float]:
        """关节电流（A）：0.2 空载基线 + |速度| 分量。"""
        t = time.monotonic()
        return [
            0.2 + abs(v) * 0.8 + _deterministic_noise(t, i, 0.03)
            for i, v in enumerate(self._joints.velocities())
        ]

    def get_tcp_pose(self) -> List[float]:
        """末端位姿 [x, y, z, Rx, Ry, Rz]：伪 FK 或 servoj_pose 插值状态。"""
        return self._tcp.pose()

    def get_tcp_velocity(self) -> List[float]:
        return self._tcp.velocity()

    def get_tcp_acceleration(self) -> List[float]:
        return [0.0] * 6

    def get_robot_state(self) -> List[float]:
        """``get_robot_state``：[机器人状态, 程序状态, 任务状态, 安全状态]。

        mock 恒返回正常四元组（SR_Enable/SP_Stopped/ST_Idle/SS_RUN，
        告警检测 D133 全维正常——模拟健康机器人）。
        """
        return [float(SR_ENABLE), 0.0, 0.0, 5.0]

    def get_servo_errids(self) -> List[float]:
        """各关节伺服错误码：mock 恒全 0（无故障）。"""
        return [0.0] * 7

    def get_robot_status(self, min_interval_s: Optional[float] = None) -> Any:
        """RobotStatus 同构对象（一次提供全部状态组，节流语义同真机）。"""
        now = time.monotonic()
        interval = (
            self._status_min_interval_s
            if min_interval_s is None
            else float(min_interval_s)
        )
        if (now - self._status_ts) >= interval:
            import types as _types

            self._status = _types.SimpleNamespace(
                actual_joint_pos=self._joints.positions(),
                actual_joint_vel=self._joints.velocities(),
                actual_joint_acc=self.get_accelerations(),
                actual_joint_torq=self.get_torques(),
                actual_joint_temp=self.get_temperatures(),
                actual_joint_current=self.get_currents(),
                actual_tcp_pose=self._tcp.pose(),
                servo_errid=[0.0] * 7,
            )
            self._status_ts = now
        return self._status

    # ── motion ───────────────────────────────────────────────────────

    def servoj(self, joints: Sequence[float]) -> None:
        """Non-blocking real-time joint servo target (7 joints per arm)."""
        target = self._joints.set_target([float(v) for v in joints])
        self._tcp_mode = False
        logger.debug("mock arm %r servoj -> %s", self.name, target)

    def movej(
        self,
        joints: Sequence[float],
        *,
        velocity: Optional[float] = None,
        acceleration: Optional[float] = None,
        blocking: bool = True,
    ) -> None:
        target = self._joints.set_target([float(v) for v in joints])
        self._tcp_mode = False
        if blocking:
            # velocity 覆盖仅作用于本次运动（等待到位后恢复默认速度）
            saved = self._joints._max_velocity
            if velocity is not None:
                self._joints.set_max_velocity(velocity)
            try:
                self._wait_joints_reached(velocity)
            finally:
                self._joints.set_max_velocity(saved)
        logger.debug("mock arm %r movej -> %s (blocking=%s)",
                     self.name, target, blocking)

    def servoj_pose(self, pose: Sequence[float]) -> None:
        """Non-blocking cartesian servo target [x, y, z, Rx, Ry, Rz]。"""
        values = [float(v) for v in pose]
        if len(values) != 6:
            raise DucoArmError(
                f"arm {self.name!r}: servoj_pose 需要 6 维 xyzrpy，"
                f"收到 {len(values)} 维"
            )
        self._tcp.set_target(values)
        self._tcp_mode = True
        logger.debug("mock arm %r servoj_pose -> %s", self.name, values)

    def _cartesian(self, pose: Sequence[float], label: str) -> List[float]:
        """笛卡尔目标公共路径：6 维校验 + TCP 插值目标（movel/servol 用）。"""
        values = [float(v) for v in pose]
        if len(values) != 6:
            raise DucoArmError(
                f"arm {self.name!r}: {label} 需要 6 维 xyzrpy，"
                f"收到 {len(values)} 维"
            )
        self._tcp.set_target(values)
        self._tcp_mode = True
        logger.debug("mock arm %r %s -> %s", self.name, label, values)
        return values

    def movel(
        self,
        pose: Sequence[float],
        *,
        velocity: Optional[float] = None,
        acceleration: Optional[float] = None,
        blocking: bool = False,
    ) -> None:
        """末端直线运动到目标位姿（mock：复用 TCP 插值逼近；blocking 等到位）。"""
        self._cartesian(pose, "movel")
        if blocking:
            self._wait_tcp_reached()

    def servol(
        self,
        pose: Sequence[float],
        *,
        velocity: Optional[float] = None,
        acceleration: Optional[float] = None,
        blocking: bool = False,
    ) -> None:
        """servol（movel 备选通道；mock 行为同 movel——TCP 插值逼近）。"""
        self._cartesian(pose, "servol")
        if blocking:
            self._wait_tcp_reached()

    def check_ik(self, pose: Sequence[float]) -> bool:
        """``cal_ikine`` 试解：mock 恒有解（6 维合法即 True）。"""
        return len(pose) == 6

    def _wait_tcp_reached(self, timeout_s: float = _MOVEJ_TIMEOUT_S) -> None:
        """阻塞等待 TCP 到位（movel/servol blocking 语义）。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._tcp.reached():
                return
            time.sleep(0.002)
        raise DucoArmError(
            f"mock arm {self.name!r}: 笛卡尔运动到位等待超时"
        )

    def _wait_joints_reached(self, velocity: Optional[float]) -> None:
        """阻塞等待关节到位（movej blocking 语义；超时抛 DucoArmError）。"""
        effective = (
            self._joints._max_velocity
            if velocity is None
            else max(float(velocity), 1e-3)
        )
        distance = max(
            abs(t - p)
            for t, p in zip(self._joints.target(), self._joints.positions())
        )
        budget = min(distance / effective * 3.0 + 1.0, _MOVEJ_TIMEOUT_S)
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            if self._joints.reached():
                return
            time.sleep(0.002)
        raise DucoArmError(
            f"mock arm {self.name!r}: movej 到位等待超时 "
            f"({budget:.1f}s, target={self._joints.target()})"
        )

    # ── gripper（仿真反馈，无 485 Modbus）───────────────────────────

    @property
    def gripper_enabled(self) -> bool:
        return self.gripper_hand_id is not None

    def gripper_init(self, full_calibration: bool = False) -> None:
        """初始化夹爪（模拟 Reg 256 流程：短延时后视为完成、回夹紧位）。"""
        if not self.gripper_enabled:
            raise DucoArmError(
                f"arm {self.name!r}: gripper_hand_id not configured"
            )
        logger.info(
            "mock arm %r: gripper init (hand_id=%s, full_calibration=%s) ...",
            self.name, self.gripper_hand_id, full_calibration,
        )
        time.sleep(0.2)  # 模拟真机约 3s 初始化的时序（缩短版）
        self._gripper.set_target(0)
        self._gripper_last.clear()
        self._gripper_feedback_raw = 0
        logger.info("mock arm %r: gripper init done", self.name)

    def gripper_command(self, position_0_1000: int) -> None:
        """Command gripper position 0-1000 (0=close, 1000=open); dedup repeats."""
        if not self.gripper_enabled:
            return
        value = max(0, min(1000, int(position_0_1000)))
        if self._gripper_last.get(self.gripper_hand_id) == value:
            return
        self._gripper.set_target(value)
        self._gripper_last[self.gripper_hand_id] = value
        logger.debug("mock arm %r gripper -> %d", self.name, value)

    def gripper_last_position(self) -> Optional[int]:
        """Last commanded position 0-1000; None when never commanded/disabled."""
        if not self.gripper_enabled:
            return None
        return self._gripper_last.get(self.gripper_hand_id)

    def gripper_read_position(self) -> Optional[int]:
        """仿真实时位置反馈 0-1000（等效真机 Reg 514 读取）。"""
        if not self.gripper_enabled:
            return None
        return self._gripper.position()

    def gripper_state_0_10(self, min_interval_s: float = 0.2) -> float:
        """夹爪状态 0-10（节流读取仿真反馈；语义与真机一致）。"""
        now = time.monotonic()
        if (now - self._gripper_last_read_ts) >= min_interval_s:
            self._gripper_last_read_ts = now
            raw = self.gripper_read_position()
            if raw is not None:
                self._gripper_feedback_raw = raw
        if self._gripper_feedback_raw is not None:
            clamped = max(0, min(1000, self._gripper_feedback_raw))
            return round(clamped * 0.01, 2)
        return 0.0


class MockDucoAuxAxis:
    """Mock 腰部/头部模块（与 :class:`duco.DucoAuxAxis` 接口同名同参）。

    fixed_joints 拼回/enforce 语义与真机一致；连接即视为外部已上电
    使能（wait_power_on 立即通过 SR_Enable）。*joint_count* 为全关节
    数（腰部 4：前 3 固定 + 第 4 可控；头部默认与 fixed_joints 等长，
    即全固定无可控段）。
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
        joint_count: int = 4,
        max_joint_velocity: float = 0.5,
        noise: float = 0.0,
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
        self._joint_count = max(int(joint_count), len(self._fixed_joints))
        initial = list(self._fixed_joints) + [0.0] * (
            self._joint_count - len(self._fixed_joints)
        )
        self._joints = _JointSim(initial, max_joint_velocity)
        self._loop: Optional[_SimLoop] = None
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

    def connect(self) -> None:
        if self._connected:
            return
        self._loop = _SimLoop([self._joints])
        self._loop.start()
        self._connected = True
        logger.info(
            "mock aux %s %r connected (%s:%s) [software simulation]",
            self.kind, self.name, self.ip, self.port,
        )

    def disconnect(self) -> None:
        if not self._connected:
            return
        if self._loop is not None:
            self._loop.stop()
            self._loop = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    # ── power-on state（mock：连接即视为已外部使能）─────────────────

    def get_robot_state(self) -> List[float]:
        """[机器人状态, 程序状态, 任务状态, 安全状态]：恒正常四元组。

        mock 模拟健康设备（SR_Enable/SP_Stopped/ST_Idle/SS_RUN——
        告警检测 D133 四维全正常，不触发状态告警）。
        """
        return [float(SR_ENABLE), 0.0, 0.0, 5.0]

    def power_state(self) -> "tuple[int, str]":
        code = int(self.get_robot_state()[0])
        return code, SR_STATE_NAMES.get(code, f"Unknown({code})")

    def wait_power_on(self) -> None:
        """mock 无需外部使能：连接即 SR_Enable，立即返回。"""
        code, name = self.power_state()
        logger.info(
            "mock aux %s %r: 已上电使能 (%s) [simulated]", self.kind, self.name, name
        )

    # ── state / motion ───────────────────────────────────────────────

    def get_positions(self) -> List[float]:
        return self._joints.positions()

    def get_servo_errids(self) -> List[float]:
        """各关节伺服错误码：mock 恒全 0（无故障，告警检测静默）。"""
        return [0.0] * self._joint_count

    def servoj(self, joints: Sequence[float]) -> None:
        """Non-blocking real-time servo（*joints* 为可控关节值，自动拼回固定前缀）。"""
        target = self._full_target(joints)
        self._joints.set_target(target)
        logger.debug("mock aux %s %r servoj -> %s", self.kind, self.name, target)

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
        self._joints.set_target(target)
        if blocking:
            # velocity 覆盖仅作用于本次运动（等待到位后恢复默认速度）
            saved = self._joints._max_velocity
            if velocity is not None:
                self._joints.set_max_velocity(velocity)
            try:
                deadline = time.monotonic() + _MOVEJ_TIMEOUT_S
                while time.monotonic() < deadline:
                    if self._joints.reached():
                        break
                    time.sleep(0.002)
                else:
                    raise DucoArmError(
                        f"mock aux {self.name!r}: movej 到位等待超时"
                    )
            finally:
                self._joints.set_max_velocity(saved)
        logger.debug(
            "mock aux %s %r movej -> %s (blocking=%s)",
            self.kind, self.name, target, blocking,
        )

    def enforce_fixed_joints(self) -> bool:
        """初始化就绪后调用**一次**：固定前缀锁定 + 可控段保持当前位。"""
        if not self._fixed_joints:
            return False
        actual = self.get_positions()
        if len(actual) < len(self._fixed_joints):
            logger.warning(
                "mock aux %s %r: 关节读取不完整 (%d)，跳过固定关节下发",
                self.kind, self.name, len(actual),
            )
            return False
        tail = actual[len(self._fixed_joints):]
        logger.info(
            "mock aux %s %r: 固定关节下发一次：%s + 可控段 %s",
            self.kind, self.name, self._fixed_joints, tail,
        )
        self.movej(tail, blocking=True)
        return True


def open_arms(devices_cfg: Mapping[str, Any]) -> Dict[str, MockDucoArm]:
    """Build mock arms from a system-config ``devices`` mapping（结构同
    ``duco.open_arms``：common 节 + 顶层标量作默认值）。

    mock 专属配置键（common 或各臂节）：``initial_joints``（7 维初始
    关节位，左臂缺省 DEFAULT_LEFT_JOINTS、右臂缺省 DEFAULT_RIGHT_JOINTS）、
    ``max_joint_velocity``（关节最大速度，弧度/s）、``noise``（读数
    伪噪声幅度）。ip/port 仅记录不使用。
    """
    common: Dict[str, Any] = dict(devices_cfg.get("common", {}) or {})
    for key in (
        "sdk_path",
        "socket_timeout_ms",
        "velocity",
        "acceleration",
        "smooth_vel",
        "smooth_acc",
        "status_min_interval_s",
        "initial_joints",
        "max_joint_velocity",
        "noise",
    ):
        if key in devices_cfg and key not in common:
            common[key] = devices_cfg[key]

    arms: Dict[str, MockDucoArm] = {}
    for name, cfg in devices_cfg.items():
        if name == "common" or not isinstance(cfg, dict):
            continue
        hand_id = cfg.get("gripper_hand_id", common.get("gripper_hand_id"))
        default_joints = (
            DEFAULT_RIGHT_JOINTS if name == "right" else DEFAULT_LEFT_JOINTS
        )
        arm = MockDucoArm(
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
            status_min_interval_s=float(
                cfg.get(
                    "status_min_interval_s",
                    common.get("status_min_interval_s", 0.1),
                )
            ),
            gripper_hand_id=(int(hand_id) if hand_id is not None else None),
            initial_joints=cfg.get(
                "initial_joints", common.get("initial_joints", default_joints)
            ),
            max_joint_velocity=float(
                cfg.get(
                    "max_joint_velocity",
                    common.get("max_joint_velocity", 1.5),
                )
            ),
            noise=float(cfg.get("noise", common.get("noise", 0.0))),
            tcp_velocity=cfg.get("tcp_velocity", common.get("tcp_velocity")),
            q_near=cfg.get("q_near", common.get("q_near")),
            tool=str(cfg.get("tool", common.get("tool", "tcp_1"))),
            wobj=str(cfg.get("wobj", common.get("wobj", ""))),
        )
        arm.connect()
        arms[name] = arm
    return arms


def open_aux_axes(cfg: Mapping[str, Any]) -> Dict[str, MockDucoAuxAxis]:
    """Build mock waist/head modules（结构同 ``duco.open_aux_axes``）。

    仅 ``enabled: true`` 的节构建；mock 关节数：腰部固定 4（前 3 固定
    + 第 4 可控），头部与 fixed_joints 等长（全固定）。``joint_count``
    可覆盖。ip/port 仅记录不使用。
    """
    common: Dict[str, Any] = {}
    for key in ("sdk_path", "socket_timeout_ms"):
        if key in cfg:
            common[key] = cfg[key]
    axes: Dict[str, MockDucoAuxAxis] = {}
    for kind in ("waist", "head"):
        section = cfg.get(kind)
        if not isinstance(section, dict) or not section.get("enabled"):
            continue
        raw_fixed = section.get(
            "fixed_joints",
            [-0.8419940400200225, 1.561184229406744, -0.7249411193131796]
            if kind == "waist"
            else None,
        )
        fixed = [float(v) for v in (raw_fixed or [])]
        default_count = 4 if kind == "waist" else max(len(fixed), 1)
        axes[kind] = MockDucoAuxAxis(
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
            joint_count=int(section.get("joint_count", default_count)),
            max_joint_velocity=float(section.get("max_joint_velocity", 0.5)),
            noise=float(section.get("noise", 0.0)),
        )
        axes[kind].connect()
    return axes


# ── 自演示：python -m songyi_robot.mock_duco ────────────────

def _demo() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logger.info("=== 新松 Mock 机器人自演示（无硬件）===")
    arms = open_arms({
        "mock": True,
        "left": {"ip": "mock", "port": 7003, "gripper_hand_id": 1},
        "right": {"ip": "mock", "port": 7004, "gripper_hand_id": 1},
    })
    axes = open_aux_axes({
        "waist": {"enabled": True, "ip": "mock", "port": 7020,
                  "fixed_joints": [-0.842, 1.561, -0.725]},
    })

    def _show(tag: str) -> None:
        left, right = arms["left"], arms["right"]
        waist = axes["waist"]
        print(f"\n[{tag}]")
        print(f"  左臂关节 : {[round(v, 3) for v in left.get_positions()]}")
        print(f"  右臂关节 : {[round(v, 3) for v in right.get_positions()]}")
        print(f"  左臂 TCP : {[round(v, 3) for v in left.get_tcp_pose()]}")
        print(f"  右臂 TCP : {[round(v, 3) for v in right.get_tcp_pose()]}")
        print(f"  左臂速度 : {[round(v, 3) for v in left.get_velocities()]}")
        print(
            f"  夹爪 0-10: 左={left.gripper_state_0_10(0)} "
            f"右={right.gripper_state_0_10(0)}"
        )
        print(f"  腰部关节 : {[round(v, 3) for v in waist.get_positions()]}")
        status = left.get_robot_status(0)
        print(
            f"  status   : temp={[round(v, 1) for v in status.actual_joint_temp]}"
            f" current={[round(v, 2) for v in status.actual_joint_current]}"
        )

    _show("初始观测")

    logger.info("servoj 双臂到新目标（非阻塞，观测渐变）...")
    arms["left"].servoj([-0.6, -1.2, 1.2, 1.8, -0.3, 0.2, 0.2])
    arms["right"].servoj([0.6, -1.2, -1.2, 1.8, 0.3, 0.2, -0.2])
    axes["waist"].servoj([0.4])
    arms["left"].gripper_command(1000)
    time.sleep(1.0)
    _show("servoj 1s 后")

    logger.info("movej 阻塞复位（等待到位）...")
    arms["left"].movej(DEFAULT_LEFT_JOINTS, velocity=1.0)
    arms["right"].movej(DEFAULT_RIGHT_JOINTS, velocity=1.0)
    axes["waist"].movej([0.0])
    _show("movej 复位后（已到位）")

    logger.info("servoj_pose 笛卡尔目标 ...")
    pose = arms["left"].get_tcp_pose()
    arms["left"].servoj_pose([pose[0] + 0.05, pose[1], pose[2] + 0.03,
                              pose[3], pose[4], pose[5]])
    time.sleep(0.5)
    print(
        f"\n[servoj_pose 0.5s 后] 左臂 TCP: "
        f"{[round(v, 3) for v in arms['left'].get_tcp_pose()]}"
    )

    for arm in arms.values():
        arm.disconnect()
    for axis in axes.values():
        axis.disconnect()
    logger.info("=== 演示结束 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
