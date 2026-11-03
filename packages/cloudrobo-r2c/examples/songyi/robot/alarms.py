"""机器人告警（event）管理：空闲告警 + 硬件状态告警。

两类告警均以 Event 消息经 EventManager 单例推送到
``{project_id}/{device_id}/event``（D158 推式发布；forwarder 转发
第三方本地上云）：

1. **空闲告警**（``alarms.idle``）：距最后一次 action 超过可配阈值
   （秒）上报 Minor 告警（"机器人空闲"），恢复动作后上报解除。
2. **硬件状态告警**（``alarms.status``）：定时检测左右臂/腰/头的
   ``get_robot_state``（机器人状态非 ``SR_Enable`` 即异常）与
   ``get_robot_status().servo_errid``（0x700B **32 位故障位图**，任
   一位非 0 即异常，按内置故障码表逐位解码）。
   状态码/errid 经映射表转实际错误信息（等级/abstract/详情 +
   解除侧三字段）；缺映射时按"内部错误"模板（可自定义）上报。
   同一异常只上报一次（不反复），恢复时按解除配置上报一次。

开关：``alarms.idle.enabled``、``alarms.status.enabled``（告警上传）、
``alarms.status.clear_enabled``（解除告警上传）。

**持久化**（``alarms.state_file``）：激活告警的 event_id 与事
件类型等保存到本地 JSON；重启后读取，若故障已消除则复用原 event_id
上传解除告警（ends_at=当前时间）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from .duco import SR_ENABLE

logger = logging.getLogger(__name__)

# 伺服故障码表（对象字典 0x700B，32 位位图逐位对应；来源：
# 《伺服软件接口说明书-v04-20260305》第 8 章"故障码"）
ERRID_FAULT_NAMES: Dict[int, str] = {
    0x0000_0001: "母线欠压故障",
    0x0000_0002: "母线过压故障",
    0x0000_0004: "软件过流（相电流）",
    0x0000_0008: "电机超速",
    0x0000_0010: "驱动板温度传感器故障",
    0x0000_0020: "母线硬件过流",
    0x0000_0040: "位置误差超限",
    0x0000_0080: "驱动板过温",
    0x0000_0100: "母线电压自检故障",
    0x0000_0200: "以太网通信超时",
    0x0000_0400: "扭矩传感器无连接",
    0x0000_0800: "ABS编码器超速（超过12万rpm）",
    0x0000_1000: "母线电压传感器故障",
    0x0000_2000: "ABS编码器磁场弱",
    0x0000_4000: "电流采样模块故障",
    0x0000_8000: "电流环饱和故障",
    0x0001_0000: "硬件版本不匹配",
    0x0002_0000: "CPU1程序卡死",
    0x0004_0000: "电机过载",
    0x0008_0000: "电流和不为0",
    0x0010_0000: "DSP过温故障",
    0x0020_0000: "编码器交叉校验故障",
    0x0040_0000: "ABS编码器CRC校验错误",
    0x0080_0000: "电机相位反向",
    0x0100_0000: "ABS编码器欠压",
    0x0200_0000: "INC编码器丢脉冲",
    0x0400_0000: "高频注入寻相抖动过小",
    0x0800_0000: "V相零电流采样故障",
    0x1000_0000: "W相零电流采样故障",
    0x2000_0000: "高频注入寻相失败",
    0x4000_0000: "INC编码器丢z",
    0x8000_0000: "扭矩传感器数据刷新超时",
}


def _errid_default_spec(fault_name: str) -> Dict[str, Any]:
    """内置故障码缺省文案：告警 Major / 解除 Info。"""
    return {
        "severity": "Major",
        "abstract": fault_name,
        "content": f"伺服故障: {fault_name}",
        "clear_severity": "Info",
        "clear_abstract": f"{fault_name}解除",
        "clear_content": f"伺服故障恢复: {fault_name}",
    }


def _state_default_spec(severity: str, text: str) -> Dict[str, Any]:
    """robot_state 内置状态文案：告警按表 / 解除 Info。"""
    return {
        "severity": severity,
        "abstract": text,
        "content": text,
        "clear_severity": "Info",
        "clear_abstract": f"{text}解除",
        "clear_content": f"{text}解除",
    }


# robot_state 四元组 [StateRobot, StateProgram, TaskState, SafetyState]
# 逐维检测表（枚举定义见 vendor gen_py/robot/ttypes.py 各类注释）。
# 每维给出**正常值集合**（不告警）与异常码 → (severity, 文案)——解除侧
# 自动为 Info + "…解除"。
_STATE_ENUM_NAMES: Dict[str, Dict[int, str]] = {
    "robot": {0: "SR_Start", 1: "SR_Initialize", 2: "SR_Logout",
              3: "SR_Login", 4: "SR_PowerOff", 5: "SR_Disable",
              6: "SR_Enable"},
    "program": {0: "SP_Stopped", 1: "SP_Stopping", 2: "SP_Running",
                3: "SP_Paused", 4: "SP_Pausing", 5: "SP_TaskRunning"},
    "task": {0: "ST_Idle", 1: "ST_Running", 2: "ST_Paused",
             3: "ST_Stopped", 4: "ST_Finished", 5: "ST_Interrupt",
             6: "ST_Error", 7: "ST_Illegal", 8: "ST_ParameterMismatch"},
    "safety": {0: "SS_INIT", 2: "SS_WAIT", 3: "SS_CONFIG",
               4: "SS_POWER_OFF", 5: "SS_RUN", 6: "SS_RECOVERY",
               7: "SS_STOP2", 8: "SS_STOP1", 9: "SS_STOP0",
               10: "SS_MODEL", 12: "SS_REDUCE", 13: "SS_BOOT",
               14: "SS_FAIL", 99: "SS_UPDATE"},
}

_STATE_FIELDS = {
    "robot": "robot_state",
    "program": "program_state",
    "task": "task_state",
    "safety": "safety_state",
}

# 各维正常值：robot=SR_Enable(6)；program=Stopped/Running/TaskRunning
# （外部任务运行属正常，现场反馈）；task=Idle/Running/Paused/
# Stopped/Finished（任务正常生命周期）；safety=SS_RUN(5)
ROBOT_STATE_NORMAL: Dict[str, set] = {
    "robot": {SR_ENABLE},
    "program": {0, 2, 5},
    "task": {0, 1, 2, 3, 4},
    "safety": {5, 0},  # SS_INIT(0) 初始化为正常瞬时态
}

ROBOT_STATE_SPECS: Dict[str, Dict[int, Tuple[str, str]]] = {
    # StateRobot：非使能均异常（瞬时态 Minor，失能类 Major）
    "robot": {
        0: ("Minor", "机器人启动中"),
        1: ("Minor", "机器人初始化中"),
        2: ("Major", "机器人已注销"),
        3: ("Major", "机器人未上电（已登录）"),
        4: ("Major", "伺服下电"),
        5: ("Major", "伺服未使能"),
    },
    # StateProgram：Stopped/Running/TaskRunning 正常；过渡态告警
    "program": {
        1: ("Minor", "程序停止中"),
        3: ("Minor", "程序已暂停"),
        4: ("Minor", "程序暂停中"),
    },
    # TaskState：中断/错误/非法/参数不匹配为异常
    "task": {
        5: ("Minor", "任务被中断"),
        6: ("Major", "任务错误"),
        7: ("Major", "非法任务状态"),
        8: ("Major", "任务参数不匹配"),
    },
    # SafetyState：SS_RUN 之外告警；急停/安全故障最高级
    # （SS_INIT(0) 初始化属正常瞬时态，现场反馈忽略）
    "safety": {
        2: ("Minor", "安全控制器等待"),
        3: ("Minor", "安全配置模式"),
        4: ("Major", "安全下电"),
        6: ("Minor", "安全恢复中"),
        7: ("Major", "安全停止(SStop2)"),
        8: ("Major", "安全停止(SStop1)"),
        9: ("Critical", "急停(SStop0)"),
        10: ("Minor", "安全模型切换"),
        12: ("Minor", "安全缩减模式"),
        13: ("Minor", "安全引导"),
        14: ("Critical", "安全故障"),
        99: ("Info", "安全固件更新中"),
    },
}


# 相机读取失败内置 spec：观测缺该路图像，恢复后解除
_CAMERA_SPEC: Dict[str, Any] = {
    "severity": "Minor",
    "abstract": "图像读取失败",
    "content": "图像读取失败",
    "clear_severity": "Info",
    "clear_abstract": "图像读取失败解除",
    "clear_content": "图像读取恢复",
}

# 相机中文名（可读文案）
_CAMERA_CN = {
    "head": "头部相机",
    "wrist_left": "左腕相机",
    "wrist_right": "右腕相机",
}


# 轴 RPC 读取失败（运行中断连）内置 spec：重建传输层仍未
# 恢复时按通信断连上报，恢复后解除
_RPC_SPEC: Dict[str, Any] = {
    "severity": "Major",
    "abstract": "通信断连",
    "content": "通信断连",
    "clear_severity": "Info",
    "clear_abstract": "通信断连解除",
    "clear_content": "通信恢复",
}


# 空闲告警缺省文案（可在 alarms.idle.* 覆盖）
_IDLE_DEFAULTS: Dict[str, Any] = {
    "severity": "Minor",
    "abstract": "机器人空闲",
    "content": "机器人空闲",
    "clear_severity": "Info",
    "clear_abstract": "机器人空闲解除",
    "clear_content": "机器人空闲解除",
}

# 缺映射时的"内部错误"模板（可在 alarms.status.internal_error.* 覆盖）
_INTERNAL_ERROR_DEFAULTS: Dict[str, Any] = {
    "severity": "Minor",
    "abstract": "内部错误",
    "content": "内部错误",
    "clear_severity": "Info",
    "clear_abstract": "内部错误解除",
    "clear_content": "内部错误解除",
}

_SPEC_FIELDS = (
    "severity", "abstract", "content",
    "clear_severity", "clear_abstract", "clear_content",
)


def _merge_spec(defaults: Mapping[str, Any], override: Any) -> Dict[str, Any]:
    spec = dict(defaults)
    if isinstance(override, Mapping):
        for key in _SPEC_FIELDS:
            if key in override and override[key] is not None:
                spec[key] = str(override[key])
    return spec


def _axis_label(axis: Any) -> str:
    """统一轴标签：臂用 left/right，辅助轴用 waist/head。"""
    kind = str(getattr(axis, "kind", "") or "")
    if kind in ("waist", "head"):
        return kind
    return str(getattr(axis, "name", "") or "unknown")


# 告警文案中文轴名
_AXIS_CN = {
    "left": "左臂",
    "right": "右臂",
    "waist": "腰部",
    "head": "头部",
}

# abstract 统一短名
_AXIS_SHORT = {
    "left": "左臂",
    "right": "右臂",
    "waist": "腰",
    "head": "头",
}
_CAMERA_SHORT = {
    "head": "头",
    "wrist_left": "左腕",
    "wrist_right": "右腕",
}


class AlarmManager:
    """空闲 + 硬件状态告警状态机（线程安全）。

    ``poll_and_drain()`` 节流执行检测并弹出一条待发布 Event dict（无
    则 None）；:meth:`start` 启动内部检测守护线程，状态转移产生的
    事件**逐条经 ``publish_fn`` 推送**（D158 推式发布，缺省接
    ``cloudrobo_r2c.client.event_manager.event_manager`` 单例；测试可注入）。
    ``poll_and_drain`` 保留供测试直接驱动。动作活动经
    :meth:`note_action` 打点。
    """

    def __init__(
        self,
        cfg: Mapping[str, Any],
        *,
        arms_fn: Callable[[], Mapping[str, Any]],
        aux_fn: Callable[[], Mapping[str, Any]],
        cameras_fn: Optional[Callable[[], Any]] = None,
        now_fn: Callable[[], float] = time.monotonic,
        publish_fn: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> None:
        cfg = dict(cfg or {})
        self._arms_fn = arms_fn
        self._aux_fn = aux_fn
        self._cameras_fn = cameras_fn
        self._now = now_fn
        self._publish_fn = publish_fn
        self._lock = threading.Lock()
        self._pending: deque = deque()
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None

        idle_cfg = cfg.get("idle", {}) or {}
        if not isinstance(idle_cfg, Mapping):
            idle_cfg = {}
        self._idle_enabled = bool(idle_cfg.get("enabled", True))
        self._idle_threshold_s = float(idle_cfg.get("threshold_s", 60.0))
        if self._idle_threshold_s <= 0:
            raise ValueError("alarms.idle.threshold_s must be > 0")
        self._idle_spec = _merge_spec(_IDLE_DEFAULTS, idle_cfg)

        status_cfg = cfg.get("status", {}) or {}
        if not isinstance(status_cfg, Mapping):
            status_cfg = {}
        self._status_enabled = bool(status_cfg.get("enabled", True))
        self._clear_enabled = bool(status_cfg.get("clear_enabled", True))
        self._check_interval_s = float(status_cfg.get("check_interval_s", 2.0))
        if self._check_interval_s <= 0:
            raise ValueError("alarms.status.check_interval_s must be > 0")
        self._internal_spec = _merge_spec(
            _INTERNAL_ERROR_DEFAULTS, status_cfg.get("internal_error")
        )
        self._errid_mappings = {
            str(k): _merge_spec(_INTERNAL_ERROR_DEFAULTS, v)
            for k, v in dict(status_cfg.get("errid_mappings", {}) or {}).items()
        }
        # 激活告警持久化：空=不持久化；配置路径则在告警状态
        # 变化时落盘，重启读取（故障已消除则复用原 id 上传解除）
        self._state_file = str(cfg.get("state_file", "") or "")

        # 运行态：启动视为刚有活动（不立即报空闲）
        self._last_action_ts = self._now()
        # 空闲告警激活态：None=未激活；激活后保存 {"event_id", "starts_at"}
        # （解除时复用同一 event_id，ends_at=当前时间）
        self._idle_active: Optional[Dict[str, Any]] = None
        # (类别, 轴, 细分键) -> {"event_id", "starts_at"}；
        # state 键: 状态码 int；errid 键: (关节序号, errid)
        self._active: Dict[Tuple[str, str, Any], Dict[str, Any]] = {}
        self._last_check_ts = 0.0
        # 重启恢复：读取上次运行遗留的激活告警（首次检测若故障已消除，
        # 复用原 event_id 上传解除）
        self._load_state()

    # ── 外部打点 ─────────────────────────────────────────────────────

    def note_action(self) -> None:
        """动作活动打点（adapter.send_action 调用）：刷新空闲计时。"""
        with self._lock:
            self._last_action_ts = self._now()
            if self._idle_active is not None:
                info = self._idle_active
                self._idle_active = None
                self._enqueue_spec(
                    self._idle_spec, clear=True,
                    event_id=info["event_id"], starts_at_ms=info["starts_at"],
                )
                self._save_state()

    # ── 检测线程（D158 推式发布）────────────────────────────────────

    def start(self) -> None:
        """启动检测守护线程（幂等）：每 0.5s 驱动一次
        ``poll_and_drain``，状态转移事件逐条经 ``publish_fn`` 推送。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_evt.clear()
            self._thread = threading.Thread(
                target=self._loop, name="songyi-alarms", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """停止检测线程（disconnect 调用；幂等）。"""
        self._stop_evt.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        self._thread = None

    def _loop(self) -> None:
        logger.info(
            "alarm loop started (push mode, check_interval_s=%.1fs)",
            self._check_interval_s,
        )
        while not self._stop_evt.wait(0.1):
            publish = self._publish_fn
            if publish is None:
                # 缺省接 EventManager 单例（D158；lazy import 避免环）
                from cloudrobo_r2c.client.event_manager import event_manager

                publish = event_manager.publish
            # 本轮检出的事件全部排出（每条独立容错，单条失败不中断）
            while not self._stop_evt.is_set():
                try:
                    event = self.poll_and_drain()
                except Exception:
                    logger.exception("alarm poll failed; tick skipped")
                    break
                if event is None:
                    break
                try:
                    publish(event)
                except Exception:
                    logger.exception(
                        "alarm event publish failed; dropped (event_id=%s)",
                        event.get("event_id", "?"),
                    )

    # ── collector 入口 ───────────────────────────────────────────────

    def poll_and_drain(self) -> Optional[Dict[str, Any]]:
        """检测（节流）并弹出一条待发布 Event dict；无则 None。"""
        with self._lock:
            now = self._now()
            if self._idle_enabled and self._idle_active is None:
                idle_s = now - self._last_action_ts
                if idle_s > self._idle_threshold_s:
                    spec = dict(self._idle_spec)
                    spec["content"] = (
                        f"距最后一次 action 已 {idle_s:.0f}s "
                        f"(阈值 {self._idle_threshold_s:.0f}s)，机器人空闲"
                    )
                    self._idle_active = self._enqueue_spec(spec, clear=False)
                    self._save_state()
            if now - self._last_check_ts >= self._check_interval_s:
                self._last_check_ts = now
                self._check_axes()
                self._check_cameras()
            if self._pending:
                return self._pending.popleft()
        return None

    # ── 硬件检测 ─────────────────────────────────────────────────────

    def _check_axes(self) -> None:
        """检测全部轴（锁内调用；单轴异常只告警不中断其余检测）。"""
        seen: set = set()
        for axis in self._connected_axes():
            label = _axis_label(axis)
            state_ok = self._read_axis_state(axis, label, seen)
            self._check_axis_servo(axis, label, state_ok, seen)

    def _connected_axes(self) -> List[Any]:
        """收集已连接的手臂与腰部/头部轴。"""
        axes: List[Any] = []
        for arm in dict(self._arms_fn() or {}).values():
            if arm is not None and getattr(arm, "connected", False):
                axes.append(arm)
        for aux in dict(self._aux_fn() or {}).values():
            if aux is not None and getattr(aux, "connected", False):
                axes.append(aux)
        return axes

    def _read_axis_state(self, axis: Any, label: str, seen: set) -> bool:
        """单轴 robot_state 四维检测；读取失败返回 False（跳过清除）。"""
        try:
            state = axis.get_robot_state()
        except Exception:
            logger.warning(
                "[ALARM] %s get_robot_state 读取失败", label, exc_info=True
            )
            return False
        for idx, dim in enumerate(("robot", "program", "task", "safety")):
            if len(state) <= idx:
                continue
            try:
                code = int(state[idx])
            except (TypeError, ValueError):
                continue
            if code in ROBOT_STATE_NORMAL[dim]:
                continue
            key = ("state", label, (dim, code))
            seen.add(key)
            name = _STATE_ENUM_NAMES[dim].get(code, f"Unknown({code})")
            entry = ROBOT_STATE_SPECS[dim].get(code)
            spec = (_state_default_spec(*entry) if entry else self._internal_spec)
            self._raise(
                key,
                spec,
                prefix=f"{label} {_STATE_FIELDS[dim]}={code}({name})",
            )
        # 恢复：清除该轴其它 state 告警。读取失败轮**跳过清除**
        # （激活态保留）——否则瞬时 RPC 抖动会把 伺服未使能 等
        # 告警误清除、下一轮恢复读取后再重复上报
        self._clear_category("state", label, keep=seen)
        return True

    def _check_axis_servo(self, axis: Any, label: str, state_ok: bool, seen: set) -> None:
        """单轴 servo_errid 位图解码与 RPC 断连告警。"""
        try:
            errids = axis.get_servo_errids()
            errids_ok = True
        except Exception:
            errids_ok = False
            logger.warning("[ALARM] %s servo_errid 读取失败", label, exc_info=True)
            errids = []
        # 任一读取失败（运行中断连）→ 上报通信断连告警，恢复后解除
        reads_ok = state_ok and errids_ok
        rpc_key = ("rpc", label, "conn")
        if not reads_ok:
            self._raise(rpc_key, dict(_RPC_SPEC), prefix="")
        self._clear_category("rpc", label, keep={rpc_key} if not reads_ok else set())
        if not errids_ok:
            return
        err_seen: set = set()
        for index, errid in enumerate(errids or []):
            try:
                errid_val = int(errid) & 0xFFFFFFFF  # 位图（thrift i32 符号兜底）
            except (TypeError, ValueError):
                continue
            if errid_val == 0:
                continue
            # 0x700B 为 32 位位图：逐位解码（内置故障码表）
            for bit, fault_name in ERRID_FAULT_NAMES.items():
                if not (errid_val & bit):
                    continue
                key = ("errid", label, (index, bit))
                err_seen.add(key)
                seen.add(key)
                self._raise(
                    key,
                    self._errid_mappings.get(str(bit))
                    or _errid_default_spec(fault_name),
                    prefix=f"{label} 关节{index + 1} servo_errid=0x{bit:X}",
                )
        # 恢复：仅清除已归零/消失的 errid 告警（读取失败轮跳过）
        self._clear_category("errid", label, keep=err_seen)

    def _check_cameras(self) -> None:
        """相机读取失败告警：failed_cameras() 上报/恢复解除。

        cameras_fn 返回相机管理器（TeleimagerCameraManager/
        CameraManager）或 None（未配置/回放）。
        """
        if self._cameras_fn is None:
            return
        try:
            cm = self._cameras_fn()
            failed = set(cm.failed_cameras()) if cm is not None else set()
        except Exception:
            logger.warning(
                "[ALARM] 相机失败状态读取失败；本轮跳过", exc_info=True
            )
            return
        labels = {k[1] for k in self._active if k[0] == "camera"} | failed
        for name in sorted(labels):
            key = ("camera", name, "conn")
            if name in failed:
                self._raise(key, dict(_CAMERA_SPEC), prefix="")
            self._clear_category(
                "camera", name, keep={key} if name in failed else set()
            )

    def _raise(
        self, key: Tuple[str, str, Any], spec: Mapping[str, Any], prefix: str
    ) -> None:
        if not self._status_enabled or key in self._active:
            return
        merged = dict(spec)
        # abstract 统一「轴 + 异常」短格式（≤7 字），content
        # 保留完整明细
        if key[0] == "errid":
            # errid：content 带故障码与关节明细（关节号 1-based）
            joint_no, bit = key[2][0] + 1, key[2][1]
            axis_cn = _AXIS_CN.get(key[1], key[1])
            axis_short = _AXIS_SHORT.get(key[1], key[1])
            fault = str(spec["abstract"])
            merged["abstract"] = f"{axis_short}异常"
            merged["content"] = (
                f"{axis_cn}关节{joint_no} 伺服故障：{fault}"
                f"（故障码 0x{bit:X}）"
            )
        elif key[0] == "rpc":
            axis_short = _AXIS_SHORT.get(key[1], key[1])
            axis_cn = _AXIS_CN.get(key[1], key[1])
            merged["abstract"] = f"{axis_short}异常"
            merged["content"] = (
                f"{axis_cn} 通信断连：轴 RPC 读取失败"
                f"（传输层重建未恢复，疑似运行中断连）"
            )
        elif key[0] == "camera":
            cam_short = _CAMERA_SHORT.get(key[1], key[1])
            cam_cn = _CAMERA_CN.get(key[1], key[1])
            merged["abstract"] = f"{cam_short}异常"
            merged["content"] = (
                f"{cam_cn}图像读取失败（camera_interface 读取异常，"
                f"观测缺该路图像）"
            )
        else:
            # state：prefix 含字段名/状态码明细，abstract 短格式
            axis_short = _AXIS_SHORT.get(key[1], key[1])
            merged["abstract"] = f"{axis_short}异常"
            merged["content"] = f"{prefix}: {spec['content']}"
        # 告警 id 在此生成并随激活态保存（解除时复用同一 id）
        info = self._enqueue_spec(merged, clear=False)
        self._active[key] = info
        self._save_state()

    def _clear_category(
        self, category: str, label: str, keep: set
    ) -> None:
        changed = False
        for key in [k for k in self._active if k[0] == category and k[1] == label]:
            if key not in keep:
                info = self._active.pop(key, None)
                changed = True
                if not self._clear_enabled or info is None:
                    # 开关关闭：不上报解除，但状态机仍要出栈（下次异常可再报）
                    continue
                spec = self._spec_for_key(key)
                detail = key[2]
                axis_short = _AXIS_SHORT.get(label, label)
                axis_cn = _AXIS_CN.get(label, label)
                if category == "state":
                    dim, code = detail
                    prefix = (
                        f"{label} 恢复 "
                        f"({_STATE_FIELDS[dim]}={code})"
                    )
                    merged = dict(spec)
                    merged["clear_abstract"] = f"{axis_short}异常解除"
                    merged["clear_content"] = (
                        f"{prefix}: {spec['clear_content']}"
                    )
                elif category == "errid":
                    # errid 解除：content 带故障码明细（关节号 1-based）
                    joint_no, bit = detail[0] + 1, detail[1]
                    axis_cn = _AXIS_CN.get(label, label)
                    fault = str(spec["abstract"])
                    merged = dict(spec)
                    merged["clear_abstract"] = f"{axis_short}异常解除"
                    merged["clear_content"] = (
                        f"{axis_cn}关节{joint_no} 伺服故障恢复：{fault}"
                        f"（故障码 0x{bit:X}）"
                    )
                elif category == "camera":
                    cam_cn = _CAMERA_CN.get(label, label)
                    cam_short = _CAMERA_SHORT.get(label, label)
                    merged = dict(spec)
                    merged["clear_abstract"] = f"{cam_short}异常解除"
                    merged["clear_content"] = f"{cam_cn}图像读取已恢复"
                else:
                    merged = dict(spec)
                    merged["clear_abstract"] = f"{axis_short}异常解除"
                    merged["clear_content"] = f"{axis_cn} 通信恢复"
                self._enqueue_spec(
                    merged, clear=True,
                    event_id=info["event_id"], starts_at_ms=info["starts_at"],
                )
        if changed:
            self._save_state()

    def _spec_for_key(self, key: Tuple[str, str, Any]) -> Mapping[str, Any]:
        detail = key[2]
        if key[0] == "state":
            dim, code = detail
            entry = ROBOT_STATE_SPECS.get(dim, {}).get(code)
            return (
                _state_default_spec(*entry) if entry else self._internal_spec
            )
        if key[0] == "rpc":
            return dict(_RPC_SPEC)
        if key[0] == "camera":
            return dict(_CAMERA_SPEC)
        fault = ERRID_FAULT_NAMES.get(int(detail[1]))
        base = _errid_default_spec(fault) if fault else self._internal_spec
        return self._errid_mappings.get(str(detail[1]), base)

    # ── 事件构造 ─────────────────────────────────────────────────────

    def _enqueue_spec(
        self,
        spec: Mapping[str, Any],
        *,
        clear: bool,
        event_id: Optional[str] = None,
        starts_at_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """构造一条 Event 入队并返回激活信息 ``{"event_id", "starts_at"}``。

        告警（clear=False）生成新 event_id；解除（clear=True）复用传入
        的 event_id（同一告警的关联标识），ends_at 置为当前时间、
        starts_at 保持告警发起时刻。
        """
        now_ms = int(time.time() * 1000)
        if event_id is None:
            event_id = str(uuid.uuid4())
        if starts_at_ms is None:
            starts_at_ms = now_ms
        info = {"event_id": event_id, "starts_at": starts_at_ms}
        self._pending.append({
            "timestamp": now_ms,
            "event_id": event_id,
            "metadata": [{
                "abstract": str(spec.get("clear_abstract" if clear else "abstract", "")),
                "type": "alarm",
                "severity": str(
                    spec.get("clear_severity" if clear else "severity", "Minor")
                ),
                "content": str(spec.get("clear_content" if clear else "content", "")),
                "starts_at": starts_at_ms,
                "ends_at": now_ms if clear else 0,
            }],
        })
        logger.info(
            "[ALARM] %s (id=%s): %s",
            "解除告警" if clear else "告警",
            event_id,
            spec.get("clear_content" if clear else "content", ""),
        )
        return info

    # ── 激活告警持久化────────────────────────────────────────

    def _save_state(self) -> None:
        """激活告警（含空闲）落盘（锁内调用；失败仅告警不中断）。

        记录 event_id / starts_at / 键（类别, 轴, 细分）——重启后据此
        重建状态机；故障消除时复用原 event_id 上传解除。
        """
        if not self._state_file:
            return
        try:
            data = {
                "idle": self._idle_active,
                "status": {
                    json.dumps([
                        k[0], k[1],
                        list(k[2]) if isinstance(k[2], tuple) else k[2],
                    ]): info
                    for k, info in self._active.items()
                },
            }
            parent = os.path.dirname(self._state_file)
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp = f"{self._state_file}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._state_file)
        except Exception:
            logger.warning("[ALARM] state save failed", exc_info=True)

    def _load_state(self) -> None:
        """启动时读取上次运行的激活告警（失败/缺失从干净状态开始）。"""
        if not self._state_file or not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file, encoding="utf-8") as f:
                data = json.load(f)
            self._restore_idle_alert(data.get("idle"))
            for raw_key, info in dict(data.get("status", {}) or {}).items():
                key = self._restore_status_key(raw_key)
                if key is None:
                    logger.warning(
                        "[ALARM] state skip bad entry %r", raw_key, exc_info=True
                    )
                    continue
                self._active[key] = {
                    "event_id": str(info["event_id"]),
                    "starts_at": int(info.get("starts_at") or 0),
                }
            if self._idle_active or self._active:
                logger.info(
                    "[ALARM] state restored from %s: idle=%s status=%d "
                    "（故障消除将复用原 id 上报解除）",
                    self._state_file, bool(self._idle_active), len(self._active),
                )
        except Exception:
            logger.warning("[ALARM] state load failed; start clean", exc_info=True)

    def _restore_idle_alert(self, idle: Any) -> None:
        """恢复上次运行遗留的空闲告警（存在非空 event_id 时）。"""
        if isinstance(idle, dict) and idle.get("event_id"):
            self._idle_active = {
                "event_id": str(idle["event_id"]),
                "starts_at": int(idle.get("starts_at") or 0),
            }

    def _restore_status_key(self, raw_key: str) -> Optional[Tuple[str, str, Any]]:
        """Parse a persisted status key into a ``(category, label, detail)`` tuple.

        Returns ``None`` when the entry is malformed (the caller logs the
        offending key and skips it), matching the previous per-entry
        try/except behaviour.
        """
        try:
            category, label, detail = json.loads(raw_key)
        except Exception:
            return None
        if str(category) == "state":
            # 起为 [维度, 码]；旧格式单 int 视为 robot 维
            if isinstance(detail, list):
                detail_key = (str(detail[0]), int(detail[1]))
            else:
                detail_key = ("robot", int(detail))
            return (str(category), str(label), detail_key)
        if str(category) in ("rpc", "camera"):
            return (str(category), str(label), str(detail))
        return (
            str(category), str(label),
            (int(detail[0]), int(detail[1])),
        )
