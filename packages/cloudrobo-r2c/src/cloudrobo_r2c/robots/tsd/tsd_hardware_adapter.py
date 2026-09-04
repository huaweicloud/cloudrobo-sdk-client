"""TSD 机器人硬件适配器。

本模块负责把 xapi-python包 暴露出来的控制能力接到 R2C SDK 的统一接口：
1. `connect()` / `disconnect()`：连接生命周期
2. `get_observation()`：设备侧状态采集
3. `send_action()`：设备侧动作执行

当前设计重点：
1. observation 采用"平铺字典 + YAML 映射"的方式，便于快速接入
2. 标准动作主链路优先保障关节 `movj`
3. 显式命令能力集中在 `_dispatch_command()`，便于后续扩展
4. 对网络异常 `-99` 做自动重连，减少现场断网导致的人工重启

补充说明：
1. "自动发送命令"与"手动触发命令"属于云侧/上层调用方式差异
2. 本 adapter 只负责执行收到的命令，不区分这些命令来自自动模式还是手动模式

常用启动命令：
1. 真实机器人：
   `python -m cloudrobo_r2c.cloudroboclient --project-id test --device-id tsd --client-config config/client_config.yaml --robot-config config/robot_tsd_config.yaml`
2. Dummy:
   `python -m cloudrobo_r2c.cloudroboclient --project-id test --device-id tsd --client-config config/client_config.yaml --robot-config config/robot_tsd_dummy_config.yaml`
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping

from cloudrobo_r2c.core.interfaces import IRobotHardwareAdapter

logger = logging.getLogger(__name__)


def create_tsd_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> IRobotHardwareAdapter:
    """Entry_point factory for TSDHardwareAdapter."""
    return TSDHardwareAdapter(config=dict(config))


_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _flatten_point(prefix: str, point: Any) -> Dict[str, Any]:
    """把控制器 Point 类对象展开成标量字段。

    例如：
    - prefix="tcp"      -> tcp_x, tcp_y, ..., tcp_c
    - prefix="tcp_user" -> tcp_user_x, ..., tcp_user_c
    """
    result: Dict[str, Any] = {}
    pose = getattr(point, "pose", point)
    for axis in ("x", "y", "z", "a", "b", "c"):
        value = getattr(pose, axis, None)
        if value is not None:
            result[f"{prefix}_{axis}"] = float(value)
    return result


def _flatten_joint(joint: Any) -> Dict[str, Any]:
    """把控制器 Joint 类对象展开成 `joint_1` ~ `joint_6`。"""
    result: Dict[str, Any] = {}
    for index in range(1, 7):
        value = getattr(joint, f"j{index}", None)
        if value is not None:
            result[f"joint_{index}"] = float(value)
    return result


def _resolve_env_vars(value: Any) -> Any:
    """递归解析配置中的环境变量占位符。

    支持两种形式：
    - `${VAR}`
    - `${VAR:-default}`
    """
    if isinstance(value, str):

        def _replace(match: "re.Match[str]") -> str:
            expression = str(match.group(1))
            if ":-" in expression:
                var_name, default_value = expression.split(":-", 1)
                return os.environ.get(var_name.strip(), default_value)
            return os.environ.get(expression.strip(), "")

        return _ENV_VAR_PATTERN.sub(_replace, value)

    if isinstance(value, dict):
        return {key: _resolve_env_vars(item) for key, item in value.items()}

    if isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]

    return value


def _is_network_error(exc: Exception) -> bool:
    """判断异常是否属于通讯层故障。

    当前重点识别两类情况：
    1. 适配器层抛出的 `ConnectionError`
    2. XAPI 抛出的 `RobException(error_code=-99)`
    """
    if isinstance(exc, ConnectionError):
        return True
    try:
        from xapi.utils import RobException

        return isinstance(exc, RobException) and exc.error_code == -99
    except ImportError:
        return False


def _with_reconnect(func):
    """为关键硬件操作提供自动重连能力。

    用在：
    - `get_observation()`
    - `send_action()`

    这样做的目的是让现场瞬时断网时，调用方仍然走统一接口，不需要在上层重复写重连逻辑。
    """
    import functools

    @functools.wraps(func)
    def wrapper(self: "TSDHardwareAdapter", *args, **kwargs):
        max_retries: int = getattr(self, "_reconnect_max_retries", 3)
        base_delay: float = getattr(self, "_reconnect_delay_s", 1.0)

        for attempt in range(max_retries + 1):
            try:
                return func(self, *args, **kwargs)
            except Exception as exc:
                if not _is_network_error(exc):
                    raise
                if attempt >= max_retries:
                    logger.error(
                        "[TSD] Max reconnect attempts (%d) reached for %s",
                        max_retries,
                        func.__name__,
                    )
                    raise

                delay = base_delay * (2**attempt)
                logger.warning(
                    "[TSD] Network error in %s (%s), reconnecting "
                    "(attempt %d/%d, delay=%.1fs)...",
                    func.__name__,
                    exc,
                    attempt + 1,
                    max_retries,
                    delay,
                )
                time.sleep(delay)
                try:
                    self._do_reconnect()
                except Exception as reconnect_exc:
                    logger.error("[TSD] Reconnect failed: %s", reconnect_exc)

        # All retries exhausted with only network errors and reconnect failures
        return None

    return wrapper


@dataclass
class TSDHardwareAdapter(IRobotHardwareAdapter):
    """TSD 机器人在 R2C SDK 中的硬件适配实现。

    说明：
    1. `config` 对应 `robot_tsd_config.yaml -> hardware.custom_config`
    2. 观测数据先组织成设备侧平铺字典，再交给 configurable translator 映射
    3. 标准 action 主链路只依赖前 6 个关节目标
    4. `di` / `do` / `alarm_info` 目前只在本地 observation 中采集，不进入标准 30 维 schema
    """

    config: Mapping[str, Any]

    _handle: int = field(default=-1, init=False, repr=False)
    _connected: bool = field(default=False, init=False, repr=False)
    _xapi: Any = field(default=None, init=False, repr=False)
    _default_speed: int = field(default=30, init=False, repr=False)
    _reconnect_max_retries: int = field(default=3, init=False, repr=False)
    _reconnect_delay_s: float = field(default=1.0, init=False, repr=False)
    _auto_enable_servo: bool = field(default=True, init=False, repr=False)
    _auto_set_mode: bool = field(default=True, init=False, repr=False)
    _auto_mode_value: int = field(default=100, init=False, repr=False)
    _auto_set_speed: bool = field(default=True, init=False, repr=False)
    _mock_mode: bool = field(default=False, init=False, repr=False)
    _mock_state: Dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _last_command: Dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _ip: str = field(default="192.168.1.6", init=False, repr=False)
    _recording: bool = field(default=False, init=False, repr=False)
    _recorded_steps: List[Dict[str, Any]] = field(
        default_factory=list,
        init=False,
        repr=False,
    )

    def connect(self) -> None:
        """连接控制器，并按配置执行初始化动作。

        connect() 只做"启动准备"相关动作：
        1. 校验配置
        2. 建立 XAPI 连接
        3. 视配置决定是否自动：
           - 上使能
           - 切控制模式
           - 设默认速度
        """
        from cloudrobo_r2c.robots.tsd.tsd_config_validator import validate_tsd_config

        # 先把环境变量展开，再做字段校验，这样配置文件既能固定写值，也能由环境注入。
        validated = validate_tsd_config(_resolve_env_vars(dict(self.config)))
        self._ip = validated["ip"]
        self._default_speed = validated["default_speed"]
        self._reconnect_max_retries = validated["reconnect_max_retries"]
        self._reconnect_delay_s = validated["reconnect_delay_s"]
        self._auto_enable_servo = validated["auto_enable_servo"]
        self._auto_set_mode = validated["auto_set_mode"]
        self._auto_mode_value = validated["auto_mode_value"]
        self._auto_set_speed = validated["auto_set_speed"]
        self._mock_mode = validated["mock_mode"]

        if self._mock_mode:
            logger.info("[TSD] Running in mock mode, skipping real robot connection.")
            self._init_mock_state()
            self._connected = True
            return

        logger.info("[TSD] Connecting to %s ...", self._ip)
        import xapi.api as xapi

        self._xapi = xapi
        self._handle = xapi.connect(self._ip)
        if self._handle < 0:
            raise ConnectionError(
                f"TSD connect failed for {self._ip}, return code: {self._handle}"
            )

        self._connected = True
        logger.info("[TSD] Connected, handle=%s", self._handle)

        if self._auto_enable_servo:
            logger.info("[TSD] Enabling servo...")
            try:
                xapi.reset(self._handle)
                time.sleep(0.05)
                xapi.enable_servo(self._handle, False)
                time.sleep(0.02)
                xapi.enable_servo(self._handle, True)
                time.sleep(0.2)
            except Exception as exc:
                logger.warning("[TSD] Servo enable failed: %s", exc)
                self.disconnect()
                raise
        else:
            logger.info("[TSD] Skipping auto servo enable by configuration.")

        if self._auto_set_mode:
            logger.info("[TSD] Setting system mode to %s...", self._auto_mode_value)
            try:
                xapi.set_system_mode(self._handle, self._auto_mode_value)
                time.sleep(0.1)
            except Exception as exc:
                logger.warning("[TSD] Set mode failed: %s", exc)
        else:
            logger.info("[TSD] Skipping auto mode setup by configuration.")

        if self._auto_set_speed:
            logger.info("[TSD] Setting speed to %d%%...", self._default_speed)
            try:
                xapi.set_speed(self._handle, self._default_speed)
                time.sleep(0.1)
            except Exception as exc:
                logger.warning("[TSD] Set speed failed: %s", exc)
        else:
            logger.info("[TSD] Skipping auto speed setup by configuration.")

    def disconnect(self) -> None:
        """断开控制器连接并清理本地连接状态。"""
        if self._connected and self._handle >= 0 and self._xapi is not None:
            try:
                self._xapi.disconnect(self._handle)
                logger.info("[TSD] Disconnected, handle=%s", self._handle)
            except Exception as exc:
                logger.warning("[TSD] Disconnect error: %s", exc)
        self._handle = -1
        self._connected = False
        self._xapi = None
        self._mock_state = {}

    def _init_mock_state(self) -> None:
        """Initialize mock robot state with default values.

        This creates a simulated observation that mirrors the structure
        of real robot state, allowing the adapter to be tested without
        a physical robot connection.
        """
        self._mock_state = {
            "timestamp": int(time.time() * 1000),
            "link": 1,
            "enable": 1,
            "alarm": 0,
            "mode": 100,
            "run_state": 2,
            "in_pos": 1,
            "remote": 1,
            "cmd_queue": 0,
            "speed": self._default_speed,
            "uf_no": 0,
            "tf_no": 0,
            "rgm_state": 0,
            "joint_1": 0.0,
            "joint_2": 0.0,
            "joint_3": 0.0,
            "joint_4": 0.0,
            "joint_5": 0.0,
            "joint_6": 0.0,
            "tcp_x": 0.0,
            "tcp_y": 0.0,
            "tcp_z": 0.0,
            "tcp_a": 0.0,
            "tcp_b": 0.0,
            "tcp_c": 0.0,
            "tcp_user_x": 0.0,
            "tcp_user_y": 0.0,
            "tcp_user_z": 0.0,
            "tcp_user_a": 0.0,
            "tcp_user_b": 0.0,
            "tcp_user_c": 0.0,
        }
        logger.info("[TSD] Mock state initialized with default values")

    def _do_reconnect(self) -> None:
        """内部重连流程：先断，再连。"""
        try:
            self.disconnect()
        except OSError as exc:
            logger.debug("[TSD] Disconnect during reconnect failed (ignored): %s", exc)
        self.connect()

    def _ensure_connected(self) -> None:
        """确保当前已经建立有效连接。"""
        if not self._connected:
            raise ConnectionError("TSD adapter is not connected")

    @_with_reconnect
    def get_observation(self) -> Mapping[str, Any]:
        """采集当前控制器状态，并组织成设备侧 observation 字典。

        当前 observation 来源主要是：
        1. `xapi.get_all_system_state()`
        2. 有报警时额外补 `get_system_alarm_info()`

        返回的是"设备侧原始字典"，不是最终 protobuf 结构。
        最终结构由 YAML 中 `device_to_r2c` 负责映射。
        """
        self._ensure_connected()

        observation: Dict[str, Any] = {"timestamp": int(time.time() * 1000)}

        if self._mock_mode:
            # In mock mode, return simulated state
            observation.update(dict(self._mock_state))
            # 本地录制仅用于保留 observation 快照，不参与主控制链路。
            if self._recording:
                self._recorded_steps.append(dict(observation))
            return observation

        try:
            state = self._xapi.get_all_system_state(self._handle)
            if state is not None:
                # 这批字段是当前 30 维标准 observation schema 的主要来源。
                observation["link"] = int(state.link)
                observation["enable"] = int(state.enable)
                observation["alarm"] = int(state.alarm)
                observation["mode"] = int(state.mode)
                observation["run_state"] = int(state.state)
                observation["in_pos"] = int(state.in_pos)
                observation["remote"] = int(state.remote)
                observation["cmd_queue"] = int(state.cmd_queue)
                observation["speed"] = int(state.speed)
                observation["uf_no"] = int(state.uf_no)
                observation["tf_no"] = int(state.tf_no)
                observation["rgm_state"] = int(state.rgm_state)
                observation.update(_flatten_joint(state.joint))
                observation.update(_flatten_point("tcp", state.world_point))
                observation.update(_flatten_point("tcp_user", state.user_point))

                # DI / DO 当前会采集，但尚未并入标准 30 维 observation schema。
                # 保留在本地字典里，后续若要扩展 schema 或单独通道，可以直接复用。
                try:
                    observation["di"] = [int(state.di_list[index]) for index in range(32)]
                except (ValueError, IndexError) as exc:
                    logger.debug("[TSD] Failed to read di list: %s", exc)
                try:
                    observation["do"] = [int(state.do_list[index]) for index in range(32)]
                except (ValueError, IndexError) as exc:
                    logger.debug("[TSD] Failed to read do list: %s", exc)
        except ConnectionError:
            raise
        except Exception as exc:
            logger.warning("[TSD] get_all_system_state failed: %s", exc)

        # 只有存在报警时才额外拉详细报警信息，避免每个周期都增加一次额外调用。
        if observation.get("alarm", 0) > 0:
            try:
                alarms = self._xapi.get_system_alarm_info(self._handle)
                if alarms:
                    observation["alarm_info"] = alarms
            except Exception as exc:
                logger.debug("[TSD] get_system_alarm_info failed: %s", exc)

        # 本地录制仅用于保留 observation 快照，不参与主控制链路。
        if self._recording:
            self._recorded_steps.append(dict(observation))

        return observation

    @_with_reconnect
    def send_action(self, command: Mapping[str, Any]) -> None:
        """执行一条设备侧动作命令。

        支持三类输入：
        1. 显式命令：`{"command": "...", "params": {...}}`
        2. 关节动作：`{"joint_1": ..., ..., "joint_6": ...}`
        3. 笛卡尔动作：`{"x": ..., "y": ..., "z": ..., ...}`

        当前标准 R2C 主链路最稳定的是第 2 类。
        其中第 1 类显式命令目前属于 adapter 内部保留能力，
        当前 `examples/tsd_cloud_adapter.py` 不把它作为公开联调入口。
        """
        self._ensure_connected()
        # 去重的目的是避免同一条 action 在短时间内被重复执行，导致日志和运动指令刷屏。
        if command == self._last_command:
            return
        self._last_command = dict(command)

        if self._mock_mode:
            # In mock mode, update mock state with command values
            self._update_mock_state_from_command(command)
            logger.info("[TSD] Mock action sent: %s", command)
            return

        explicit_command = command.get("command")
        if explicit_command:
            self._dispatch_command(str(explicit_command), dict(command.get("params", {})))
            return

        if self._has_joint_keys(command):
            self._exec_movj_joint(command)
            return

        if all(key in command for key in ("x", "y", "z")):
            self._exec_movj_point(command)
            return

        logger.warning(
            "[TSD] Unable to detect command type from dict keys=%s",
            list(command.keys()),
        )

    def _update_mock_state_from_command(self, command: Mapping[str, Any]) -> None:
        """Update mock state based on received command.

        This simulates the robot moving to the target position by updating
        the joint values in the mock state. This allows testing the full
        observation → action → observation loop without a real robot.
        """
        # Handle direct joint keys: joint_1, joint_2, etc.
        for i in range(1, 7):
            key = f"joint_{i}"
            if key in command:
                self._mock_state[key] = float(command[key])

        # Handle R2C action format: joint_states.position
        joint_states = command.get("joint_states")
        if isinstance(joint_states, Mapping):
            positions = joint_states.get("position", [])
            if positions and isinstance(positions, (list, tuple)):
                # Take the first step if it's a trajectory
                first_step = positions[0] if isinstance(positions[0], (list, tuple)) else positions
                for i, pos in enumerate(first_step[:6]):
                    self._mock_state[f"joint_{i+1}"] = float(pos)

        # Handle Cartesian point
        if all(key in command for key in ("x", "y", "z")):
            self._mock_state["tcp_x"] = float(command.get("x", 0.0))
            self._mock_state["tcp_y"] = float(command.get("y", 0.0))
            self._mock_state["tcp_z"] = float(command.get("z", 0.0))
            self._mock_state["tcp_a"] = float(command.get("a", 0.0))
            self._mock_state["tcp_b"] = float(command.get("b", 0.0))
            self._mock_state["tcp_c"] = float(command.get("c", 0.0))

        # Update timestamp
        self._mock_state["timestamp"] = int(time.time() * 1000)
        logger.debug("[TSD] Mock state updated: %s", {k: v for k, v in self._mock_state.items() if k.startswith("joint")})

    # Dispatch table mapping command names to handler callables.
    # To add a new command, write a handler method and add an entry below.
    _COMMAND_HANDLERS: ClassVar[Dict[str, "TSDHardwareAdapter._CommandHandler"]] = {
        "abort":          lambda self, p: self._h_simple(lambda: self._xapi.abort(self._handle), "[TSD] abort executed"),
        "enable_servo":   lambda self, p: self._h_enable_servo(p),
        "jogl":           lambda self, p: self._h_jogl(p),
        "jogl_rel":       lambda self, p: self._h_jogl_rel(p),
        "movc":           lambda self, p: self._exec_movc(p),
        "movj":           lambda self, p: self._dispatch_movj(p),
        "movl":           lambda self, p: self._exec_movl(p),
        "pause":          lambda self, p: self._h_simple(lambda: self._xapi.pause(self._handle), "[TSD] pause executed"),
        "reset":          lambda self, p: self._h_simple(lambda: self._xapi.reset(self._handle), "[TSD] reset executed"),
        "resume":         lambda self, p: self._h_simple(lambda: self._xapi.resume(self._handle), "[TSD] resume executed"),
        "run":            lambda self, p: self._h_run(p),
        "set_ao":         lambda self, p: self._h_set_raw("set_ao", p, lambda h, v: self._xapi.set_ao(h, v["index"], v["value"]), "index/value"),
        "set_do":         lambda self, p: self._h_set_raw("set_do", p, lambda h, v: self._xapi.set_do(h, v["index"], v["state"]), "index/state"),
        "set_mode":       lambda self, p: self._h_set_int("mode", p, 100, lambda h, v: self._xapi.set_system_mode(h, v)),
        "set_remote":     lambda self, p: self._h_set_bool("is_remote", p, True, lambda h, v: self._xapi.set_remote(h, v)),
        "set_speed":      lambda self, p: self._h_set_int("speed", p, 50, lambda h, v: self._xapi.set_speed(h, v)),
        "set_tfno":       lambda self, p: self._h_set_int("tf_no", p, 0, lambda h, v: self._xapi.set_tfno(h, v)),
        "set_ufno":       lambda self, p: self._h_set_int("uf_no", p, 0, lambda h, v: self._xapi.set_ufno(h, v)),
        "stop":           lambda self, p: self._h_simple(lambda: self._xapi.stop(self._handle), "[TSD] stop executed"),
        "wait_move_done": lambda self, p: self._h_wait_move_done(p),
    }

    def _dispatch_command(self, command_name: str, params: Dict[str, Any]) -> None:
        """把显式命令分发到具体 XAPI 调用。

        这里集中维护"命令名 -> XAPI API"的映射关系。
        后续若要新增本地命令支持，优先在这里扩展。
        当前这些命令主要作为内部预留能力保留，不代表它们已经接入
        `tsd_cloud_adapter.py` 的公开交互入口。
        """
        logger.info("[TSD] Dispatching command: %s, params: %s", command_name, params)

        handler = self._COMMAND_HANDLERS.get(command_name)
        if handler is None:
            logger.warning("[TSD] Unknown command: %s", command_name)
            return
        handler(self, params)

    # -- helper helpers --------------------------------------------------

    _CommandHandler = Any  # type alias for dispatch callable

    @staticmethod
    def _h_simple(xapi_fn: Callable[[], None], log_msg: str) -> None:
        """Run a single XAPI call and log the result."""
        xapi_fn()
        logger.info(log_msg)

    def _h_set_int(
        self,
        param_name: str,
        params: Dict[str, Any],
        default: int,
        xapi_fn: Callable[[int, int], None],
    ) -> None:
        value = int(params.get(param_name, default))
        xapi_fn(self._handle, value)
        logger.info("[TSD] %s: %s", param_name, value)

    def _h_set_bool(
        self,
        param_name: str,
        params: Dict[str, Any],
        default: bool,
        xapi_fn: Callable[[int, bool], None],
    ) -> None:
        value = bool(params.get(param_name, default))
        xapi_fn(self._handle, value)
        logger.info("[TSD] %s: %s", param_name, value)

    def _h_set_raw(
        self,
        cmd_name: str,
        params: Dict[str, Any],
        xapi_fn: Callable[[int, Dict[str, Any]], None],
        keys: str,
    ) -> None:
        try:
            xapi_fn(self._handle, params)
            logger.info("[TSD] %s: %s", cmd_name, keys)
        except Exception as exc:
            logger.error("[TSD] %s failed: %s", cmd_name, exc)

    # -- per-command handlers --------------------------------------------

    def _h_jogl(self, params: Dict[str, Any]) -> None:
        frame_direction = int(params.get("frame_direction", 0))
        direction = int(params.get("direction", 0))
        move_type = int(params.get("type", 1))
        rtcp = int(params.get("rtcp", 0))
        self._xapi.jogl(self._handle, frame_direction, direction, move_type, rtcp)
        logger.info(
            "[TSD] jogl: axis=%d dir=%d type=%d",
            frame_direction,
            direction,
            move_type,
        )

    def _h_jogl_rel(self, params: Dict[str, Any]) -> None:
        frame_direction = int(params.get("frame_direction", 0))
        delta = float(params.get("delta", 0.0))
        move_type = int(params.get("type", 1))
        rtcp = int(params.get("rtcp", 0))
        self._xapi.jogl_rel(
            self._handle,
            frame_direction,
            delta,
            move_type,
            rtcp,
        )
        logger.info(
            "[TSD] jogl_rel: axis=%d delta=%.2f type=%d",
            frame_direction,
            delta,
            move_type,
        )

    def _h_enable_servo(self, params: Dict[str, Any]) -> None:
        enable = params.get("enable", True)
        self._xapi.enable_servo(self._handle, enable)
        logger.info("[TSD] enable_servo: %s", enable)

    def _h_run(self, params: Dict[str, Any]) -> None:
        program_name = params.get("program_name")
        if not program_name:
            raise ValueError("program_name must be provided")
        self._xapi.run(
            self._handle,
            program_name,
            params.get("start_line", 1),
        )
        logger.info("[TSD] run: program=%s", program_name)

    def _h_wait_move_done(self, params: Dict[str, Any]) -> None:
        timeout = params.get("timeout_ms", -1)
        self._xapi.wait_move_done(self._handle, timeout)
        logger.info("[TSD] wait_move_done (timeout=%s)", timeout)

    def _dispatch_movj(self, params: Dict[str, Any]) -> None:
        """Route movj to the right execution method."""
        if self._has_joint_keys(params):
            self._exec_movj_joint(params)
        elif all(key in params for key in ("x", "y", "z")):
            self._exec_movj_point(params)

    @staticmethod
    def _has_joint_keys(values: Mapping[str, Any]) -> bool:
        """判断映射里是否包含 6 个关节目标字段。"""
        return all(f"joint_{index}" in values for index in range(1, 7))

    def _exec_movj_joint(self, params: Mapping[str, Any]) -> None:
        """执行关节空间 `movj`。

        这是当前标准云侧动作主链路最终落到设备侧的主要执行入口。
        """
        from xapi.api.utils import Joint

        values = [float(params.get(f"joint_{index}", 0.0)) for index in range(1, 7)]
        # XAPI Joint 结构支持扩展轴位，这里统一补齐到 9 维，未使用部分补 0。
        while len(values) < 9:
            values.append(0.0)
        self._xapi.movj(self._handle, Joint(*values))
        logger.info(
            "[TSD] movj (joint): [%s]",
            ", ".join(f"{value:.2f}" for value in values[:6]),
        )

    def _exec_movj_point(self, params: Mapping[str, Any]) -> None:
        """执行笛卡尔目标形式的 `movj`。"""
        from xapi.api.utils import Point

        x = float(params.get("x", 0.0))
        y = float(params.get("y", 0.0))
        z = float(params.get("z", 0.0))
        a = float(params.get("a", 0.0))
        b = float(params.get("b", 0.0))
        c = float(params.get("c", 0.0))
        self._xapi.movj(self._handle, Point((x, y, z, a, b, c)))
        logger.info(
            "[TSD] movj (Cartesian): [%.2f, %.2f, %.2f, %.2f, %.2f, %.2f]",
            x,
            y,
            z,
            a,
            b,
            c,
        )

    def _exec_movl(self, params: Mapping[str, Any]) -> None:
        """执行线性笛卡尔运动 `movl`。"""
        from xapi.api.utils import Point

        x = float(params.get("x", 0.0))
        y = float(params.get("y", 0.0))
        z = float(params.get("z", 0.0))
        a = float(params.get("a", 0.0))
        b = float(params.get("b", 0.0))
        c = float(params.get("c", 0.0))
        self._xapi.movl(self._handle, Point((x, y, z, a, b, c)))
        logger.info(
            "[TSD] movl: [%.2f, %.2f, %.2f, %.2f, %.2f, %.2f]",
            x,
            y,
            z,
            a,
            b,
            c,
        )

    def _exec_movc(self, params: Mapping[str, Any]) -> None:
        """执行圆弧笛卡尔运动 `movc`。"""
        from xapi.api.utils import Point

        middle = params.get("middle_point", {})
        target = params.get("target_point", {})
        middle_point = Point(
            (
                float(middle.get("x", 0.0)),
                float(middle.get("y", 0.0)),
                float(middle.get("z", 0.0)),
                float(middle.get("a", 0.0)),
                float(middle.get("b", 0.0)),
                float(middle.get("c", 0.0)),
            )
        )
        target_point = Point(
            (
                float(target.get("x", 0.0)),
                float(target.get("y", 0.0)),
                float(target.get("z", 0.0)),
                float(target.get("a", 0.0)),
                float(target.get("b", 0.0)),
                float(target.get("c", 0.0)),
            )
        )
        self._xapi.movc(self._handle, middle_point, target_point)
        logger.info("[TSD] movc executed")

    def start_recording(self) -> None:
        """开始本地 observation 录制。"""
        self._recording = True
        self._recorded_steps = []
        logger.info("[TSD] Recording started")

    def stop_recording(self) -> None:
        """停止本地 observation 录制。"""
        self._recording = False
        logger.info("[TSD] Recording stopped, %d steps captured", len(self._recorded_steps))

    def save_episode(self, path: str) -> None:
        """Save captured observations to a JSON file.

        This is a lightweight local export helper. It is not a complete
        LeRobot dataset writer.
        """
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8") as file:
            json.dump(self._recorded_steps, file, ensure_ascii=False, indent=2)
        logger.info("[TSD] Episode saved to %s (%d steps)", output, len(self._recorded_steps))
