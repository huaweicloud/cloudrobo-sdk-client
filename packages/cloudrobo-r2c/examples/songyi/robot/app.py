"""Module-level wiring for config-referenced functions.

Robot config ``hardware.topics`` 三绑定引用 bare function names
（``collector: get_runtime``、``on_message: on_command`` /
``on_waist_command``）resolved against this module. The functions
operate on module singletons initialized by :func:`configure_app`.

告警（event）自 D158 起不走 topic 绑定：AlarmManager 检测线程状态
转移时逐条经 EventManager 单例推送（:func:`start_alarms` /
:func:`stop_alarms` 由 adapter 生命周期驱动）。
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Dict, List, Mapping, Optional

from .alarms import AlarmManager
from .collector import RuntimeCollector
from .duco import (
    DucoArm,
    DucoArmError,
    DucoAuxAxis,
    _interruptible_sleep,
    is_stop_requested,
    open_aux_axes,
    open_arms,
)
from .playback import PlaybackSource
from .state_cache import StateCache
from .teleop import TeleopController

logger = logging.getLogger(__name__)

_cache = StateCache()
_arms: Dict[str, DucoArm] = {}
_aux: Dict[str, DucoAuxAxis] = {}
_collector: Optional[RuntimeCollector] = None
_teleop: Optional[TeleopController] = None
_playback: Optional[PlaybackSource] = None
_config_lock = threading.Lock()
# 腰部控制 topic 回调（adapter.connect 注入，见 on_waist_command）
_waist_command_cb: Optional[Callable[[List[float]], None]] = None
# 腰部关节读数节流（ Thrift RPC 降频；观测 30Hz 下 ~10Hz 实读）
_WAIST_MIN_INTERVAL_S = 0.1
_waist_last_value: float = 0.0
_waist_last_ts: float = 0.0
# 朝向标定（waist 配置节：orientation_invert 反向 / orientation_offset_deg 偏移）
_heading_invert: bool = False
_heading_offset_deg: float = 0.0
# 告警管理（空闲 + 硬件状态；adapter.connect 注入配置）
_alarm_manager: Optional[AlarmManager] = None


def configure_app(
    devices_cfg: Mapping[str, Any],
    teleop_cfg: Optional[Mapping[str, Any]] = None,
    granularity: Optional[Mapping[str, Any]] = None,
    location: str = "",
    reset_pose: Optional[List[float]] = None,
    playback_cfg: Optional[Mapping[str, Any]] = None,
    action_clear_cb: Optional[Callable[[], None]] = None,
    waist_command_cb: Optional[Callable[[List[float]], None]] = None,
    heading_cfg: Optional[Mapping[str, Any]] = None,
    teleop_hold_fn: Optional[Callable[[], None]] = None,
) -> None:
    """Initialize module singletons from the robot config's custom_config.

    When *playback_cfg* is given (``record_observation``), the robot SDK is
    bypassed: runtime/observation are sourced from the recorded JSON file.
    """
    global _collector, _teleop, _playback, _waist_command_cb
    global _heading_invert, _heading_offset_deg

    with _config_lock:
        granularity_dict: Dict[str, int] = {}
        for name, spec in (granularity or {}).items():
            factor = spec.get("factor", 1) if isinstance(spec, dict) else spec
            granularity_dict[name] = max(int(factor), 1)

        _waist_command_cb = waist_command_cb
        heading = dict(heading_cfg or {})
        _heading_invert = bool(heading.get("orientation_invert", False))
        _heading_offset_deg = float(heading.get("orientation_offset_deg", 0.0))

        if playback_cfg:
            cfg = dict(playback_cfg)
            path = cfg.get("path", "") if isinstance(cfg, dict) else str(cfg)
            if not path:
                raise ValueError("record_observation.path is required for playback")
            _playback = PlaybackSource(
                path,
                loop=bool(cfg.get("loop", True)) if isinstance(cfg, dict) else True,
                fps=float(cfg.get("fps")) if isinstance(cfg, dict) and cfg.get("fps") else None,
            )
            _collector = RuntimeCollector(
                _cache,
                lambda: {},
                granularity=granularity_dict,
                location=location,
                position_source=_playback.states_runtime,
                ee_states_source=_playback.ee_states,
                ee_poses_source=_playback.ee_poses,
                # 回放无腰部模块：joint_states 末位补 0.0（录制文件不含腰部）
            )
        else:
            _playback = None
            _collector = RuntimeCollector(
                _cache,
                lambda: dict(_arms),
                granularity=granularity_dict,
                location=location,
                waist_source=get_waist_position,
                heading_source=get_waist_heading,
            )

        reset_pose_list = [float(v) for v in (reset_pose or [])]
        teleop_cfg = teleop_cfg or {}
        _teleop = TeleopController(
            reset_on=dict(teleop_cfg.get("reset_on", {}) or {}),
            reset_fn=(
                (lambda: _do_reset(reset_pose_list)) if reset_pose_list else None
            ),
            # reset_on=false 的进/出场景：按当前实际位伺服一次就地停止
            # （否则控制器继续朝上一流式目标运动，危险）
            hold_fn=teleop_hold_fn,
            action_clear_cb=action_clear_cb,
        )


def get_playback() -> Optional[PlaybackSource]:
    return _playback


def configure_alarms(
    alarms_cfg: Mapping[str, Any],
    cameras_fn: Optional[Callable[[], Any]] = None,
) -> None:
    """初始化告警管理器（adapter.connect 调用，custom_config.alarms 节）。

    空闲告警（idle）与硬件状态告警（status）配置见 alarms.AlarmManager；
    *cameras_fn* 返回相机管理器（相机读取失败告警），未配置时
    不检测相机。未配置时按缺省（两者均开启）。事件经 EventManager
    单例推送（D158），随后的 :func:`start_alarms` 启动检测线程。
    """
    global _alarm_manager
    with _config_lock:
        _alarm_manager = AlarmManager(
            alarms_cfg, arms_fn=get_arms, aux_fn=get_aux,
            cameras_fn=cameras_fn,
        )


def start_alarms() -> None:
    """启动告警检测守护线程（adapter.connect 调用；D158 推式发布）。

    检出的事件逐条经 EventManager 单例推送（未接线时自动入缓存，
    主 session 建立后按限频补发）。
    """
    manager = _alarm_manager
    if manager is not None:
        manager.start()


def stop_alarms() -> None:
    """停止告警检测线程（adapter.disconnect 调用；幂等）。"""
    manager = _alarm_manager
    if manager is not None:
        manager.stop()


def get_alarm_manager() -> Optional[AlarmManager]:
    """当前告警管理器（未配置为 None；测试/查询用）。"""
    return _alarm_manager


def note_action_activity() -> None:
    """动作活动打点（adapter.send_action 调用，空闲告警计时刷新）。"""
    manager = _alarm_manager
    if manager is not None:
        manager.note_action()


def get_collector_tcp_poses() -> Dict[str, Any]:
    """双臂末端位姿 6 维 xyzrpy（实时读取，单臂失败跳过）。"""
    return _collector._tcp_poses() if _collector is not None else {
        "names": [], "pose": []
    }


def get_collector_grip_values() -> "tuple[float, float]":
    """双臂夹爪 0-10（真实反馈优先，节流读取；失败回退最后指令值），供观测组装。"""
    arms = get_arms()

    def _gv(arm_name: str) -> float:
        arm = arms.get(arm_name)
        if arm is None or not getattr(arm, "gripper_enabled", False):
            return 0.0
        return arm.gripper_state_0_10()

    ret = _gv("left"), _gv("right")
    logger.debug("gripper_values: %s", ret)
    return ret


def _do_reset(reset_pose: List[float], velocity: Optional[float] = None) -> None:
    """Reset both arms to the configured pose (left = first half, right = second).

    双臂 **servoj** 到 reset_pose。目标即终点位，控制器自行平滑到位。
    """
    half = len(reset_pose) // 2
    order = ["left", "right"]
    for index, arm_name in enumerate(order):
        arm = _arms.get(arm_name)
        if arm is None or not arm.connected:
            logger.warning("reset: arm %r not connected; skipped", arm_name)
            continue
        segment = reset_pose[index * half : (index + 1) * half] if half else []
        if len(segment) != 7:
            logger.warning(
                "reset: pose segment for %r has %d joints (need 7); skipped",
                arm_name,
                len(segment),
            )
            continue
        arm.servoj(segment, velocity=velocity)


def reset_to_pose(
    reset_pose: List[float], velocity: Optional[float] = None
) -> None:
    """双臂复位到 reset_pose（左7 + 右7，阻塞）。

    teleop 复位与键盘 go_home（按 H）共用；未连接的臂跳过并告警。
    *velocity* 为复位最大速度（弧度/s，custom_config.reset_velocity）；
    None 时用各臂配置速度。
    """
    _do_reset(list(reset_pose), velocity=velocity)


def connect_arms(devices_cfg: Mapping[str, Any]) -> Dict[str, DucoArm]:
    """Open connections for all configured arms（真机 Thrift 或 mock 仿真）。

    ``devices.mock: true`` 时走软件仿真机器人（``mock_duco.open_arms``，
    接口与真机逐一同名同参，无 Thrift/485），用于无硬件联调与演示。
    仅 ``SongyiMockAdapter`` 会以 ``mock: true`` 调用本函数；真机
    adapter 构造时剥除 mock 键，运行时不触达仿真分支（lazy import，
    真机进程不加载 mock_duco）。
    """
    global _arms
    with _config_lock:
        if bool(devices_cfg.get("mock", False)):
            from .mock_duco import open_arms as open_mock_arms

            logger.warning(
                "devices.mock=true: 使用软件仿真机器人 "
                "(MockDucoArm)——不会连接任何真实硬件"
            )
            _arms = open_mock_arms(dict(devices_cfg))
        else:
            _arms = open_arms(dict(devices_cfg))
        return dict(_arms)


def connect_aux_axes(custom_cfg: Mapping[str, Any]) -> Dict[str, DucoAuxAxis]:
    """Open optional waist/head modules (custom_config 的 waist/head 节)。

    仅 ``enabled: true`` 的模块会连接；mock 开关（``custom_cfg.mock``，
    由 SongyiMockAdapter 注入）时走软件仿真——mock 工厂返回的轴已连接，
    ``wait_power_on`` 立即视为外部已上电使能。真机路径：连接失败（模块
    未上电时 Thrift 连不上）或状态未到 SR_Enable 时打印提示并循环等待
    外部上电使能，就绪前不返回（见 ``DucoAuxAxis.wait_power_on``）。
    ``request_stop()`` 后等待立刻中止并抛错退出（Ctrl+C 可停止进程）。
    """
    global _aux
    mock_axes = bool(custom_cfg.get("mock", False))
    with _config_lock:
        if mock_axes:
            from .mock_duco import open_aux_axes as open_mock_aux

            logger.warning("mock=true: 腰部/头部走软件仿真 (MockDucoAuxAxis)")
            _aux = open_mock_aux(dict(custom_cfg))
        else:
            _aux = open_aux_axes(dict(custom_cfg))
    # 连接建立后，等待与固定关节锁定移到锁外（轮询可阻塞数秒到数十秒，
    # 避免长时间占用 _config_lock），不改变等待/锁定行为与顺序。
    for kind, axis in _aux.items():
        _wait_aux_ready(axis, kind)
        if axis.enforce_fixed_pose and axis.fixed_count:
            # 就绪后把腰部固定关节锁到写死值（可控关节保持当前位）
            try:
                axis.enforce_fixed_joints()
            except Exception:
                logger.exception(
                    "aux %s: 固定关节锁定失败（下发仍会拼回写死前缀）", kind
                )
    return dict(_aux)


def _wait_aux_ready(axis: DucoAuxAxis, kind: str) -> None:
    """等待单轴就绪：先尝试连接，再 wait_power_on 直到外部使能。"""
    if getattr(axis, "connected", False):
        # mock 工厂已连接；wait_power_on 立即视为已使能
        axis.wait_power_on()
        return
    while True:
        try:
            if not axis.connected:
                axis.connect()
            axis.wait_power_on()
            break
        except DucoArmError as exc:
            if is_stop_requested():
                logger.warning(
                    "aux %s: 等待中止（收到停止请求），进程退出",
                    kind,
                )
                raise
            logger.warning(
                "aux %s: %s；%.0fs 后重试（请检查模块供电/外部使能）...",
                kind,
                exc,
                axis.poll_interval_s,
            )
            _interruptible_sleep(axis.poll_interval_s)


def disconnect_arms() -> None:
    with _config_lock:
        for arm in _arms.values():
            arm.disconnect()
        _arms.clear()
        for axis in _aux.values():
            axis.disconnect()
        _aux.clear()


def get_arms() -> Dict[str, DucoArm]:
    return dict(_arms)


def get_aux() -> Dict[str, DucoAuxAxis]:
    return dict(_aux)


def get_state_cache() -> StateCache:
    return _cache


def get_waist_position() -> float:
    """腰部可控关节（第 4 关节）当前读数（弧度，节流 ~10Hz）。

    joint_states.position 最后一维数据源（observation 与 current_state
    共用）。腰部未使能/未连接/读数不完整时回退 0.0（不阻塞观测链路）。
    """
    global _waist_last_value, _waist_last_ts
    axis = _aux.get("waist")
    if axis is None or not axis.connected:
        return 0.0
    now = time.monotonic()
    with _config_lock:
        if now - _waist_last_ts < _WAIST_MIN_INTERVAL_S:
            return _waist_last_value
        _waist_last_ts = now
    try:
        positions = axis.get_positions()
    except Exception:
        logger.debug("waist get_positions failed; keep last value", exc_info=True)
        _waist_last_ts = 0.0  # 读取失败：下次调用立即重试
        return _waist_last_value
    control = positions[axis.fixed_count:]
    if not control:
        return _waist_last_value
    _waist_last_value = float(control[-1])
    return _waist_last_value


def get_waist_heading() -> float:
    """当前朝向（度，[0,360)）：腰部最后一个关节（第 4 关节）读数推算。

    runtime 帧 location 第一段数据源（第二段为静态地理位置）：
    ``heading = offset + (invert ? -deg(j4) : deg(j4))``，归一化到
    [0,360)。反向/偏移由 waist 配置节 ``orientation_invert`` /
    ``orientation_offset_deg`` 标定（现场按腰 j4=0 的实际朝向标定）。
    腰部未使能时 j4 读 0.0，朝向即偏移基值。
    """
    deg = math.degrees(get_waist_position())
    if _heading_invert:
        deg = -deg
    return (deg + _heading_offset_deg) % 360.0


def get_teleop() -> Optional[TeleopController]:
    return _teleop


def is_teleop_active() -> bool:
    return bool(_teleop is not None and _teleop.active)


def heartbeat_mode_source() -> str:
    """当前运行模式（AUTO/CONTROLLED/ASSISTED）——心跳 mode 的权威读数。

    模式状态由 ModeControl（sync_client 公共能力）持有；本函数读
    teleop 状态机的执行状态供 ModeControl/心跳拉取。
    """
    if _teleop is None:
        return "AUTO"
    if _teleop.assist_active:
        return "ASSISTED"
    if _teleop.teleop_active:
        return "CONTROLLED"
    return "AUTO"


# ── config-referenced functions ──────────────────────────────────────


def get_runtime(tick: int = 0) -> Optional[Dict[str, Any]]:
    """Collector for the runtime publish topic (StateCache -> frame dict)."""
    if _collector is None:
        logger.warning("get_runtime called before configure_app(); frame skipped")
        return None
    return _collector.collect(tick)


def teleop_enter() -> None:
    """ModeControl 动作回调：进入遥操（清 action + hold/复位 + 进脚本）。"""
    if _teleop is None:
        logger.warning("teleop_enter called before configure_app(); dropped")
        return
    _teleop.enter_teleop()


def teleop_exit() -> None:
    """ModeControl 动作回调：退出遥操（hold/复位 + 恢复动作执行）。"""
    if _teleop is None:
        logger.warning("teleop_exit called before configure_app(); dropped")
        return
    _teleop.exit_teleop()


def assist_enter() -> None:
    """ModeControl 动作回调：进入辅助接管。"""
    if _teleop is None:
        logger.warning("assist_enter called before configure_app(); dropped")
        return
    _teleop.enter_assist()


def assist_exit() -> None:
    """ModeControl 动作回调：退出辅助接管。"""
    if _teleop is None:
        logger.warning("assist_exit called before configure_app(); dropped")
        return
    _teleop.exit_assist()


def on_waist_command(message: Any) -> None:
    """on_message handler for the waist_command topic (WaistCommand pb2 直引用).

    腰部控制唯一入口（action 不再携带腰部，extensions 通道已删除）：
    收到本 topic 才控制腰部。position 对齐可控段（当前腰部仅第 4 关节
    1 个值，弧度），下发经 adapter 的步长夹紧与固定前缀拼回。
    """
    if _waist_command_cb is None:
        logger.warning("on_waist_command: no callback wired; command dropped")
        return
    positions = [float(v) for v in (getattr(message, "position", None) or [])]
    if not positions:
        logger.warning("on_waist_command: empty position; command dropped")
        return
    logger.info("waist command received: %s", positions)
    _waist_command_cb(positions)
