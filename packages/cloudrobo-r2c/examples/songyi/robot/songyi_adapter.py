"""SONGYI hardware adapter: existing main-session pipeline -> DucoCobot.

``send_action`` executes directly (no intermediate queue): each action
step's joint positions go to ``servoj(block=False)`` on both arms. While
teleop is active, arriving actions are dropped.
"""

from __future__ import annotations

import logging
import math
import shlex
import subprocess
import threading
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence

from cloudrobo_r2c.core.interfaces import ICameraFrameSource, IRobotHardwareAdapter
from cloudrobo_r2c.robots.commands.base import AdapterCommand

from . import app as songyi_app
from .cameras import build_camera_manager, from_jpeg_bytes, to_jpeg_bytes
from .collector import EE_STATE_NAMES, JOINT_NAMES, expand_to_14

logger = logging.getLogger(__name__)

_LEFT_ARM_JOINTS = 7

# 左/右臂双位姿切换（键盘 l/r）：关节角为**度**，下发前转弧度
LEFT_TOGGLE_POSITIONS_DEG = [
    [-123.16, -60.17, 90.0, 130.78, -53.71, -58.68, -28.77],
    [-87.52, -81.66, 90.0, 102.71, -4.38, -6.22, 0.02],
]
RIGHT_TOGGLE_POSITIONS_DEG = [
    [120.39, -63.71, -90.0, 135.44, 48.34, -60.22, 29.66],
    [85.29, -85.64, -90.0, 97.26, -1.18, 6.71, 1.49],
]

# l/r 切换的「到位」判定容差：实际关节与上次目标的各关节差均 ≤ 该值
# （弧度）才算到位，才前进到下一步；过远则重发上次目标
_ARM_TOGGLE_TOLERANCE_RAD = 0.2


class _GoHomeResetCommand(AdapterCommand):
    """键盘 go_home（按 H）复位：双臂 servoj 到 reset_pose。

    ``requires_pause = False``——复位随时可用（无需先空格暂停）。
    复位前清空最新单步指令（见
    :meth:`SongyiHardwareAdapter.reset_to_reset_pose`）。
    """

    requires_pause = False

    def execute(self, **kwargs: Any) -> None:
        self.adapter.reset_to_reset_pose()


class _ArmToggleCommand(AdapterCommand):
    """键盘左/右臂双位姿切换（l/r）：每按一次在两个预制位姿间交替。

    ``requires_pause = False``。位姿为**度**（模块常量），下发前转弧度。
    """

    requires_pause = False

    def __init__(self, adapter: Any, config: dict[str, Any]) -> None:
        super().__init__(adapter, config)
        self._side = str(config.get("side", ""))

    def execute(self, **kwargs: Any) -> None:
        self.adapter.toggle_arm(self._side)


class _WaistJogCommand(AdapterCommand):
    """键盘腰部点动命令（a/d/s）：direction +1=增加 / -1=减少 / 0=回 0。

    ``requires_pause = False``——点动随时可用。步长与限位在
    ``custom_config.waist`` 节（``jog_step_rad``/``jog_max_rad``/
    ``jog_min_rad``，弧度）。
    """

    requires_pause = False

    def __init__(self, adapter: Any, config: dict[str, Any]) -> None:
        super().__init__(adapter, config)
        self._direction = int(config.get("direction", 0))

    def execute(self, **kwargs: Any) -> None:
        self.adapter.waist_jog(self._direction)


class _ToggleGripperCommand(AdapterCommand):
    """键盘切换夹爪开合（g）：开 ↔ 合交替。

    ``requires_pause = False``——本地显式操作。开=10.0（×100=1000
    松开）、合=0.0（0=夹紧）；初始视为闭合，首次按 g 张开。
    """

    requires_pause = False

    def execute(self, **kwargs: Any) -> None:
        self.adapter.toggle_grippers()


class SongyiHardwareAdapter(IRobotHardwareAdapter, ICameraFrameSource):
    """Adapter for the SONGYI dual-arm robot (demo SDK, Thrift RPC).

    实现 :class:`ICameraFrameSource`（相机推流共享帧槽）：相机设备由
    本适配器独占持有（connect 打开），connect 后启动后台采集线程持续
    写帧槽，推理侧 ``get_observation`` 与推流侧 ``CameraCapture`` 共用
    同一帧槽，从根源上避免 V4L2/RealSense 设备独占冲突。
    """

    # 仿真面开关（类属性）：真机 False——构造时剥除 devices.mock 键；
    # SongyiMockAdapter 置 True——构造时强制 devices.mock=true。
    # 两类运行时路径互不触达，真机进程不加载 mock_duco（lazy import）。
    mock_enabled = False

    def __init__(self, config: Mapping[str, Any]):
        cfg = dict(config or {})
        self._cfg = cfg
        self._devices_cfg: Dict[str, Any] = dict(cfg.get("devices", {}) or {})
        # 真机：剥除 mock 键（mock 走 SongyiMockAdapter）
        if not self.mock_enabled:
            self._devices_cfg.pop("mock", None)
        if not self._devices_cfg:
            raise ValueError(
                "custom_config.devices is required (sdk_path/left/right/...)"
            )
        self._max_step_rad: Optional[float] = cfg.get("max_step_rad")
        if self._max_step_rad is not None:
            self._max_step_rad = float(self._max_step_rad)
        self._state_max_age_ms = int(cfg.get("state_max_age_ms", 60))
        self._task_name = str(cfg.get("task", "default_task"))
        # 硬件层 dry_run：true 时 send_action 只打印不执行（独立于
        # runtime.dry_run 的 sync_client 层拦截，双保险）
        self._dry_run = bool(cfg.get("dry_run", False))
        # 末端动作 IK 试解门控（custom_config.ik_check，默认关）：
        # 开启后末端 action 先 try cal_ikine，无解则跳过该侧 servol
        self._ik_check = bool(cfg.get("ik_check", False))
        # 遥操/协助进入时运行的外部脚本（权责外移至硬件 adapter；
        # TeleopController 不再执行任何 shell 命令）。用 shlex 拆分 +
        # shell=False 防注入；为空则不运行。
        teleop_cfg = dict(cfg.get("teleop", {}) or {})
        self._teleop_enter_cmd = str(teleop_cfg.get("enter_cmd", "")).strip()
        try:
            self._teleop_enter_timeout_s = max(
                float(teleop_cfg.get("enter_timeout_s", 10.0)), 1.0
            )
        except (TypeError, ValueError):
            self._teleop_enter_timeout_s = 10.0
        # 回放：record_observation 配置后，runtime/observation 来自录制文件
        self._cameras_cfg: Dict[str, Any] = dict(cfg.get("cameras", {}) or {})
        self._cameras: Optional[Any] = None
        # 相机推流帧槽（后台采集线程写 / 推理+推流读）：
        #   _latest_frames: cam_key -> BGR ndarray（统一格式，供推流 get_latest_frame）
        #   _latest_raw:    cam_key -> 原始 jpeg bytes（teleimager 直传；cv2 后端为 None）
        #   _latest_ts:     cam_key -> time.monotonic() 采集时间戳
        self._latest_frames: Dict[str, Any] = {}
        self._latest_raw: Dict[str, Optional[bytes]] = {}
        self._latest_ts: Dict[str, float] = {}
        self._camera_lock = threading.Lock()
        self._camera_thread: Optional[threading.Thread] = None
        self._camera_running = False
        self._playback_cfg: Optional[Dict[str, Any]] = None
        record = cfg.get("record_observation")
        if record:
            if isinstance(record, str):
                self._playback_cfg = {"path": record}
            elif isinstance(record, Mapping):
                self._playback_cfg = dict(record)
            else:
                raise ValueError(
                    "record_observation must be a file path or "
                    "{path, loop, fps} mapping"
                )
        self._last_commanded: Optional[List[float]] = None
        self._last_observation: Optional[Mapping[str, Any]] = None
        self._aux_last: Dict[str, List[float]] = {}
        self._connected = False
        # ── 腰部点动（键盘 a/d/s）────────────────────────────────────
        waist_cfg = dict(self._cfg.get("waist", {}) or {})
        self._waist_jog_step = float(waist_cfg.get("jog_step_rad", 0.05))
        self._waist_jog_max = float(waist_cfg.get("jog_max_rad", 1.0))
        self._waist_jog_min = float(waist_cfg.get("jog_min_rad", -1.0))
        self._waist_jog_target: Optional[float] = None
        # 命令在 __init__ 注册（sync_client 启动时的 keymap 校验先于
        # connect()——connect 里注册会导致配置 keymap 校验失败）
        self.register_command(
            "waist_increase", _WaistJogCommand(self, {"direction": 1})
        )
        self.register_command(
            "waist_decrease", _WaistJogCommand(self, {"direction": -1})
        )
        self.register_command(
            "waist_reset", _WaistJogCommand(self, {"direction": 0})
        )
        # 左/右臂双位姿切换（l/r）：交替状态按侧独立维护
        self._arm_toggle_positions: Dict[str, List[List[float]]] = {
            "left": LEFT_TOGGLE_POSITIONS_DEG,
            "right": RIGHT_TOGGLE_POSITIONS_DEG,
        }
        self._arm_toggle_index: Dict[str, int] = {"left": 0, "right": 0}
        self.register_command(
            "left_toggle", _ArmToggleCommand(self, {"side": "left"})
        )
        self.register_command(
            "right_toggle", _ArmToggleCommand(self, {"side": "right"})
        )
        # 夹爪开合切换（g）：开/合状态由 adapter 维护
        self._gripper_open = False
        self.register_command(
            "toggle_gripper", _ToggleGripperCommand(self, {})
        )
        # 键盘 v/x 模式切换已移除：遥操/协助进出仅经云端 command
        # 下发（sync_client 直订阅 → ModeControl → adapter 动作回调）

    # ── lifecycle ────────────────────────────────────────────────────

    def connect(self) -> None:
        logger.info(
            "adapter connect: playback=%s dry_run=%s",
            bool(self._playback_cfg),
            self._dry_run,
        )
        if self._dry_run:
            logger.warning(
                "hardware dry_run is ENABLED: actions will be logged, "
                "never sent to the robot"
            )
        # 告警管理（custom_config.alarms：空闲 + 硬件状态 + 相机）；
        # 推式发布：configure 后 start_alarms 启动检测线程，事件
        # 逐条经 EventManager 单例推送（未接线时入缓存待补发）
        songyi_app.configure_alarms(
            dict(self._cfg.get("alarms", {}) or {}),
            cameras_fn=lambda: self._cameras,
        )
        songyi_app.start_alarms()
        songyi_app.configure_app(
            self._devices_cfg,
            teleop_cfg=dict(self._cfg.get("teleop", {}) or {}),
            granularity=dict(self._cfg.get("granularity", {}) or {}),
            location=str(self._devices_cfg.get("location", "")),
            reset_pose=list(self._cfg.get("reset_pose", []) or []),
            playback_cfg=self._playback_cfg,
            action_clear_cb=None,
            waist_command_cb=self.command_waist,
            # 朝向标定（runtime location 第一段）：waist 配置节的
            # orientation_invert / orientation_offset_deg
            heading_cfg=dict(self._cfg.get("waist", {}) or {}),
            # reset_on=false 的进/出场景：按当前位伺服一次就地停止
            teleop_hold_fn=self.hold_current_pose,
        )
        if self._playback_cfg:
            # 回放模式：状态来自文件，不连接真机；动作仅记录不执行
            self._connected = True
            logger.warning(
                "playback mode: keep-alive NOT started (no robot attached); "
                "runtime/observation sourced from %s",
                self._playback_cfg.get("path"),
            )
            return
        arms = songyi_app.connect_arms(
            self._resolve_q_near(
                self._devices_cfg, list(self._cfg.get("reset_pose") or [])
            )
        )
        self._connected = True

        # 键盘 go_home（按 H，runtime.keyboard_control.enabled 时生效）：
        # 双臂复位到 reset_pose 并清空最新单步指令
        self.register_command("go_home", _GoHomeResetCommand(self, {}))
        # 点动基准归位：首次按 a/d 从腰部实际关节位取基准
        self._waist_jog_target = None

        # 腰部/头部（可选使能）：enabled=true 时连接并循环等待外部上电使能；
        # mock 开关随 devices.mock 继承（SongyiMockAdapter 置位，真机恒 False）
        aux_cfg = {
            "sdk_path": self._devices_cfg.get("sdk_path", "demo"),
            "socket_timeout_ms": self._devices_cfg.get("socket_timeout_ms", 100),
            "mock": bool(self._devices_cfg.get("mock", False)),
            "waist": self._cfg.get("waist"),
            "head": self._cfg.get("head"),
        }
        songyi_app.connect_aux_axes(aux_cfg)

        logger.info("keepalive thread started")

        # 相机（head / wrist_left / wrist_right）：connect 打开，后台采集
        # 线程持续写共享帧槽（推理 get_observation 与推流 get_latest_frame 共用）
        if self._cameras_cfg:
            self._cameras = build_camera_manager(self._cameras_cfg)
            self._cameras.open_all()
            self._start_camera_capture_thread()

        # 夹爪初始化（Reg 256，阻塞约 3s/只；gripper_init_on_connect=false 跳过）
        if self._cfg.get("gripper_init_on_connect", True):
            full_calibration = bool(self._cfg.get("gripper_full_calibration", False))
            for name, arm in arms.items():
                if arm.gripper_enabled:
                    try:
                        arm.gripper_init(full_calibration=full_calibration)
                    except Exception:
                        logger.exception("gripper init failed (%s)", name)

    def disconnect(self) -> None:
        songyi_app.stop_alarms()
        self._stop_camera_capture_thread()
        if self._cameras is not None:
            try:
                self._cameras.close_all()
            except Exception:
                logger.exception("cameras close failed")
            finally:
                self._cameras = None
        songyi_app.disconnect_arms()
        self._connected = False

    # ── ICameraFrameSource（相机推流共享帧槽） ──────────────────────────────

    def _camera_capture_fps(self) -> int:
        """采集线程目标帧率：cameras 配置的 fps 最小值（缺省 30）。

        各相机可能配置不同 fps，取最小值保证最慢的那路也能按帧槽 keep-latest
        覆盖；无相机时返回 30（线程未启动，实际不使用）。
        """
        fps_values: List[int] = []
        for cfg in (self._cameras_cfg or {}).values():
            if isinstance(cfg, dict) and cfg.get("fps"):
                fps_values.append(int(cfg["fps"]))
        if not fps_values:
            return 30
        return max(min(fps_values), 1)

    def _start_camera_capture_thread(self) -> None:
        """启动相机采集线程：read_all() → 写共享帧槽（ndarray + raw bytes）。"""
        if self._camera_running or self._camera_thread is not None:
            return
        self._camera_running = True
        self._camera_thread = threading.Thread(
            target=self._camera_capture_loop,
            name="songyi-camera-capture",
            daemon=True,
        )
        self._camera_thread.start()
        logger.info(
            "camera capture thread started (fps=%s)",
            self._camera_capture_fps(),
        )

    def _camera_capture_loop(self) -> None:
        """后台循环：按相机帧率读帧写共享帧槽（keep-latest）。

        帧槽统一存 ndarray（推流 `CameraCapture` 需要 BGR ndarray）：
        teleimager 后端的 jpeg bytes 写入前 imdecode；同时缓存原始
        bytes 供推理侧观测上传直传（不做解码再压缩）。
        """
        interval = 1.0 / float(self._camera_capture_fps())
        while self._camera_running:
            if self._cameras is None:
                time.sleep(interval)
                continue
            try:
                frames = self._cameras.read_all()
            except Exception:
                logger.warning("camera read_all failed", exc_info=True)
                frames = {}
            with self._camera_lock:
                for name, value in frames.items():
                    self._latest_ts[name] = time.monotonic()
                    if isinstance(value, (bytes, bytearray)):
                        # teleimager 后端：原始 bytes 缓存（推理直传）+
                        # 解码 ndarray（推流）
                        self._latest_raw[name] = bytes(value)
                        try:
                            self._latest_frames[name] = from_jpeg_bytes(value)
                        except Exception:
                            logger.warning(
                                "camera %r jpeg decode failed; frame omitted", name,
                                exc_info=True,
                            )
                    else:
                        # cv2 后端：ndarray 原样（推理侧走编码）
                        self._latest_frames[name] = value
                        self._latest_raw[name] = None
            time.sleep(interval)

    def _stop_camera_capture_thread(self) -> None:
        """停止相机采集线程并清理帧槽。"""
        self._camera_running = False
        thread, self._camera_thread = self._camera_thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        with self._camera_lock:
            self._latest_frames.clear()
            self._latest_raw.clear()
            self._latest_ts.clear()

    def get_latest_frame(self, camera_id: str) -> Optional[Any]:
        """返回指定相机（按 cameras 配置 name）的最近一帧 BGR ndarray。

        与推流侧 `CameraCapture` 共享帧槽；内部加锁并返回副本，避免与
        采集线程写竞争。相机不存在或尚无帧时返回 None。
        """
        with self._camera_lock:
            frame = self._latest_frames.get(camera_id)
            return None if frame is None else frame.copy()

    def get_latest_timestamp(self, camera_id: str) -> float:
        """返回最近一帧的采集时间戳（time.monotonic()），无帧返回 0.0。"""
        with self._camera_lock:
            return self._latest_ts.get(camera_id, 0.0)

    # ── hardware.topics 回调面 ───────────────────────────────────────
    # TopicRuntime 的 collector/on_message 名解析到本 adapter 的方法；
    # 转发到 songyi_app 模块实现。

    def get_runtime(self, tick: int = 0) -> Optional[Dict[str, Any]]:
        return songyi_app.get_runtime(tick)


    def teleop_enter(self) -> None:
        self._run_teleop_enter_script()
        songyi_app.teleop_enter()

    def teleop_exit(self) -> None:
        songyi_app.teleop_exit()

    def assist_enter(self) -> None:
        self._run_teleop_enter_script()
        songyi_app.assist_enter()

    def assist_exit(self) -> None:
        songyi_app.assist_exit()

    def _run_teleop_enter_script(self) -> None:
        """进入遥操/协助时运行配置的 enter 脚本（权责在硬件 adapter）。

        不做 shell 拼接，用 :func:`shlex.split` 拆成参数列表并以
        ``shell=False`` 启动，杜绝命令注入；超时后终止子进程。
        """
        if not self._teleop_enter_cmd:
            return
        try:
            argv = shlex.split(self._teleop_enter_cmd)
        except ValueError as exc:
            logger.warning("invalid teleop enter_cmd %r: %s", self._teleop_enter_cmd, exc)
            return
        logger.info("teleop adapter runs enter script: %s", self._teleop_enter_cmd)
        try:
            proc = subprocess.Popen(
                argv,
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            logger.warning("teleop enter script failed to start: %s", exc)
            return
        try:
            rc = proc.wait(timeout=self._teleop_enter_timeout_s)
            logger.info("teleop enter script finished (rc=%s)", rc)
        except subprocess.TimeoutExpired:
            logger.error(
                "teleop enter script exceeded %.1fs; terminating",
                self._teleop_enter_timeout_s,
            )
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()

    def on_waist_command(self, message: Any) -> None:
        songyi_app.on_waist_command(message)

    # ── observation ──────────────────────────────────────────────────

    def get_observation(self) -> Mapping[str, Any]:
        """Read latest state and return the obs dict.

        Playback mode: 14 arm joint angles from the recorded file
        (left J1-J7 + right J8-J14) + waist 0.0 placeholder (15th).
        Live mode: via the shared cache, whose frame the runtime
        collector also reuses; 15th dim = waist controllable joint.
        """
        playback = songyi_app.get_playback()
        if playback is not None:
            arms = playback.states_observation()
            ee = playback.ee_states()["position"]
            # 录制文件不含腰部：joint_states 末位补 0.0（保持 15 维）
            positions = expand_to_14(arms["left"] + arms["right"]) + [0.0]
            ee_poses = playback.ee_poses()
            return {
                "timestamp": int(time.time() * 1000),
                "task": self._task_name,
                "joint_states": {
                    "names": list(JOINT_NAMES),
                    "position": positions,
                    "velocity": [],
                    "torque": [],
                },
                "end_effector_poses": {
                    "names": ee_poses["names"],
                    "pose": ee_poses["pose"],
                },
            }
        cache = songyi_app.get_state_cache()
        arms = list(songyi_app.get_arms().values())

        def _merged(method: str) -> List[float]:
            merged: List[float] = []
            for arm in arms:
                merged.extend(getattr(arm, method)())
            return merged

        # 位姿/关节等取最新：force 忽略缓存新鲜度阈值全量重取
        # （get_robot_status 仍受 status_min_interval_s 节流兜底，读取
        # 失败回落旧缓存）；先刷新缓存，随后 tcp_poses 复用同一份最新
        # status（单 RPC 快照一致）
        state = cache.get(
            {
                "position": lambda: _merged("get_positions"),
                "velocity": lambda: _merged("get_velocities"),
                "torque": lambda: _merged("get_torques"),
            },
            force=True,
        )
        tcp_poses = songyi_app.get_collector_tcp_poses()
        position = state.get("position", [])
        if not position:
            # 读数为空（启动初期/读数失败）：回退上一帧有效观测，
            # 避免把空关节发布出去
            if self._last_observation is not None:
                return self._last_observation
            logger.warning("get_observation: empty joint read; no previous frame")

        left_grip, right_grip = songyi_app.get_collector_grip_values()
        names = list(JOINT_NAMES)
        # 15 维：双臂 14 + 腰部可控关节（最后一维；未使能时为 0.0）
        position15 = expand_to_14(position) + [songyi_app.get_waist_position()]

        # 图片统一为 jpeg bytes 原始字节直传：teleimager 走 raw 通道
        # 不解码再压缩；cv2 后端 ndarray 就地编码。数据源为共享帧槽
        # （后台采集线程写入），推理与推流共用同一帧槽，观测循环不再
        # 被相机读取阻塞。
        jpeg_quality = int(self._cameras_cfg.get("jpeg_quality", 95))
        images: Dict[str, bytes] = {}
        with self._camera_lock:
            raw_names = list(self._latest_frames.keys())
            raw_snapshot = {
                name: self._latest_raw.get(name) for name in raw_names
            }
            frames_snapshot = {
                name: self._latest_frames[name].copy()
                for name in raw_names
            }
        for name in raw_names:
            raw = raw_snapshot.get(name)
            if raw is not None:
                # teleimager 后端：原始 bytes 直传（不解码再压缩）
                images[name] = raw
            else:
                # cv2 后端：帧槽 ndarray 就地编码
                frame = frames_snapshot.get(name)
                if frame is not None:
                    images[name] = to_jpeg_bytes(frame, quality=jpeg_quality)

        observation = {
            "timestamp": int(time.time() * 1000),
            "task": self._task_name,
            "images": images,
            "tcp_poses": tcp_poses,
            "joint_states": {
                "names": names,
                "position": position15,
                "velocity": expand_to_14(state.get("velocity", [])) + [0.0],
                "torque": expand_to_14(state.get("torque", [])) + [0.0],
            },
            "end_effector_states": {
                "names": list(EE_STATE_NAMES),
                "position": [left_grip, right_grip],
            },
        }
        if position:
            self._last_observation = observation
        return observation

    # ── action ───────────────────────────────────────────────────────

    def send_action(self, command: Mapping[str, Any]) -> None:
        """Execute one action step immediately (dual-arm, left/right expanded).

        即发即弃（不记录/不重发）：双臂 action 保活机制已取消
        （max_step_rad=3.14 后单步可达目标）。遥操期间丢弃云端动作。
        """
        # 空闲告警计时打点：收到 action 即活动（含后续被门控丢弃的，
        # 云端仍在下发=会话不空闲）
        songyi_app.note_action_activity()
        if songyi_app.get_playback() is not None:
            logger.debug("[PLAYBACK] cloud action dropped: %s", command)
            return
        if songyi_app.is_teleop_active():
            logger.debug("teleop active: action step dropped")
            return

        # 硬件层 dry_run：打印 observation/action 供链路比对，不下发硬件
        if self._dry_run:
            logger.info("[DRY_RUN] observation: %s", self._last_observation)
            logger.info("[DRY_RUN] action: %s", command)
            return
        self._apply_command(command)

    def _apply_command(self, command: Mapping[str, Any]) -> bool:
        """Dispatch one device command (grippers + joints).

        腰部不随 action 下发（extensions 通道已删除）：唯一控制入口为
        waist_command topic（``command_waist``）。
        """
        self._command_grippers(command.get("grippers"))
        # 按 names 匹配的单侧夹爪：缺侧不下发（旧 grippers 列表
        # 形态保留兼容——其 [x] 单值无法区分左右，优先用单侧键）
        self._command_gripper_side("left", command.get("left_gripper"))
        self._command_gripper_side("right", command.get("right_gripper"))

        left_pose = command.get("left_pose")
        right_pose = command.get("right_pose")
        if left_pose or right_pose:
            self._execute_cartesian(left_pose, right_pose)
            return True
        self._execute_joints(
            command.get("left_joints"), command.get("right_joints"), command
        )
        return True

    @staticmethod
    def _resolve_q_near(
        devices_cfg: Mapping[str, Any], reset_pose: Sequence[float]
    ) -> Dict[str, Any]:
        """解析 q_near 配置：``"home"`` 关键字 → reset_pose 对应
        臂段（左 [0:7] / 右 [7:14]，弧度）；7 关节列表原样透传；未配置
        → 缺失（DucoArm 按末端移动默认行为，vendor 参数为空）。

        devices 顶层键作为两侧缺省（臂级配置优先）。reset_pose 不足
        14 维时原样返回（无法解析 home）。
        """
        cfg = dict(devices_cfg or {})
        pose = [float(v) for v in (reset_pose or [])]
        if len(pose) < 2 * 7:
            return cfg
        segments = {"left": pose[:7], "right": pose[7:14]}

        def _resolve(value: Any, side: str) -> Any:
            if (
                isinstance(value, str)
                and value.strip().lower() == "home"
            ):
                return list(segments[side])
            return value

        common_q = cfg.get("q_near")
        for side, segment in segments.items():
            entry = cfg.get(side)
            if not isinstance(entry, dict):
                continue
            if "q_near" in entry:
                entry["q_near"] = _resolve(entry["q_near"], side)
            elif common_q is not None:
                entry["q_near"] = _resolve(common_q, side)
        return cfg

    def hold_current_pose(self) -> None:
        """按当前实际关节位伺服一次（就地停止，遥操进/出 reset_on=false 场景）。

        双臂：读实际关节角 servoj 到当前位（控制器停止向上一流式目标
        运动）；腰部：读实际关节按可控段伺服。安全动作，不受 dry_run
        门控（就地停止方向安全）。
        """
        arms = songyi_app.get_arms()
        for arm_name in ("left", "right"):
            arm = arms.get(arm_name)
            if arm is None or not arm.connected:
                continue
            try:
                positions = arm.get_positions()
            except Exception:
                logger.warning(
                    "hold: arm %r 关节读取失败；跳过", arm_name, exc_info=True
                )
                continue
            if not positions or len(positions) < _LEFT_ARM_JOINTS:
                logger.warning(
                    "hold: arm %r 关节读取不完整 (%d)；跳过",
                    arm_name,
                    len(positions or []),
                )
                continue
            target = [float(v) for v in positions[:_LEFT_ARM_JOINTS]]
            try:
                arm.servoj(target)
                logger.info("hold: arm %r -> %s", arm_name, target)
            except Exception:
                logger.exception("hold: arm %r servoj failed", arm_name)
        axis = songyi_app.get_aux().get("waist")
        if axis is not None and axis.connected:
            try:
                positions = axis.get_positions()
                control = [float(v) for v in positions[axis.fixed_count:]]
                if control:
                    # 双通道钉住当前位：servoj 非阻塞按当前位伺服
                    # （打断旧目标平滑跟踪）+ 二次钉住（防单一指令被
                    # 控制器忽略——腰部 hold 不生效的现场表现）
                    axis.servoj(control)
                    logger.debug(
                        "[WAIST-HOLD-DIAG] waist hold servoj -> %s", control
                    )
                    axis.servoj(control)
                    logger.debug(
                        "[WAIST-HOLD-DIAG] waist hold servoj(2) -> %s", control
                    )
                else:
                    logger.debug(
                        "[WAIST-HOLD-DIAG] waist positions empty (%s); skip",
                        positions,
                    )
            except Exception:
                logger.exception("[WAIST-HOLD-DIAG] waist hold failed")
        else:
            logger.debug(
                "[WAIST-HOLD-DIAG] waist axis absent/not connected: %s",
                {k: getattr(a, "connected", None)
                 for k, a in songyi_app.get_aux().items()},
            )

    def reset_to_reset_pose(self) -> None:
        """键盘 go_home（按 H）复位：双臂 servoj 到 reset_pose。

        **复位前清空最新腰部指令**：否则腰部续发线程会用复位前的旧
        目标续发（覆盖复位状态）。清空后直到下一条真实腰部指令到达前
        不再续发。回放模式无实体臂，跳过；硬件层 dry_run 只打印不下发。
        """
        if songyi_app.get_playback() is not None:
            logger.info("go_home reset skipped: playback mode (no robot)")
            return
        if self._dry_run:
            logger.info("[DRY_RUN] go_home reset skipped (would move to reset_pose)")
            return
        reset_pose = [float(v) for v in (self._cfg.get("reset_pose") or [])]
        if not reset_pose:
            logger.warning("go_home: custom_config.reset_pose 未配置；跳过复位")
            return
        # 复位最大速度（弧度/s，custom_config.reset_velocity）；不配则用
        # 各臂 velocity（servoj velocity=None 回退）
        reset_velocity = self._cfg.get("reset_velocity")
        if reset_velocity is not None:
            reset_velocity = float(reset_velocity)
        # 复位同时张开双臂夹爪（10.0=松开 → Reg 259=1000；非阻塞写，
        # 与臂复位并行）
        self._command_grippers([10.0, 10.0])
        songyi_app.reset_to_pose(reset_pose, velocity=reset_velocity)
        logger.info(
            "go_home: 双臂已复位到 reset_pose (velocity=%s)，夹爪已张开",
            reset_velocity,
        )

    def _execute_cartesian(self, left_pose: Any, right_pose: Any) -> None:
        arms = songyi_app.get_arms()
        try:
            left_target = self._valid_pose6(left_pose)
            right_target = self._valid_pose6(right_pose)
            if left_target:
                left = arms.get("left")
                if left is not None and left.connected and self._ik_ok(
                    left, left_target
                ):
                    # 速度按臂配置 tcp_velocity（末端通道）
                    left.servol(left_target)
            if right_target:
                right = arms.get("right")
                if right is not None and right.connected and self._ik_ok(
                    right, right_target
                ):
                    right.servol(right_target)
        except Exception:
            logger.exception("send_action: servol failed")

    def _ik_ok(self, arm: DucoArm, target: List[float]) -> bool:
        """末端动作 IK 试解门控（``custom_config.ik_check`` 开关）。

        开启时先 ``cal_ikine`` 检测解是否存在：无解/异常 → 该侧不执行
        servol（warning 日志）；关闭或缺 check_ik 方法（fake/旧臂）直接放行。
        """
        if not self._ik_check:
            return True
        checker = getattr(arm, "check_ik", None)
        if not callable(checker):
            return True
        arm_name = getattr(arm, "name", "?")
        try:
            ok = bool(checker(target))
        except Exception:
            logger.warning(
                "[IK] %s cal_ikine 异常；视为无解，跳过本次末端动作",
                arm_name,
                exc_info=True,
            )
            return False
        if not ok:
            logger.warning(
                "[IK] %s cal_ikine 无解；跳过本次末端动作", arm_name
            )
        return ok

    @staticmethod
    def _valid_pose6(pose: Any) -> Optional[List[float]]:
        """末端位姿目标校验：仅接受 **6 维 xyzrpy**（米/弧度），原样直发。

        末端位姿已全面改 6 维（xyz+quat 的 7 维形态废止）；其他
        长度非法，返回 None（调用方跳过）。
        """
        if not pose:
            return None
        vals = [float(v) for v in pose]
        if len(vals) != 6:
            logger.warning(
                "cartesian pose 需为 6 维 xyzrpy，收到 %d 维；跳过", len(vals)
            )
            return None
        return vals

    def _execute_joints(
        self, left_joints: Any, right_joints: Any, command: Mapping[str, Any]
    ) -> None:
        arms = songyi_app.get_arms()
        left: Optional[DucoArm] = arms.get("left")
        right: Optional[DucoArm] = arms.get("right")

        # Expanded form: left_joints/right_joints (7 each)
        if left_joints or right_joints:
            reference = (
                songyi_app.get_state_cache().positions() or self._last_commanded or []
            )
            self._servoj_expanded(left_joints, right_joints, left, right, reference)
            return

        # Backward-compatible flat form: positions（14=双臂；7-13=仅左臂/
        # 左臂+部分右臂——缺失的臂不下发）
        positions = self._extract_positions(command)
        if positions is None:
            # 纯夹爪指令（无臂关节数据）属正常形态，不告警
            if not command.get("grippers"):
                logger.warning(
                    "send_action: no joint positions in step; skipped"
                )
            return
        self._servoj_flat(positions, left, right)
        self._last_commanded = list(positions)

    def _servoj_expanded(
        self,
        left_joints: Any,
        right_joints: Any,
        left: Optional[DucoArm],
        right: Optional[DucoArm],
        reference: List[float],
    ) -> None:
        try:
            if left_joints and len(left_joints) == _LEFT_ARM_JOINTS:
                if left is not None and left.connected:
                    left.servoj(
                        self._clamp_step(
                            [float(v) for v in left_joints], reference
                        )
                    )
            if right_joints and len(right_joints) == _LEFT_ARM_JOINTS:
                if right is not None and right.connected:
                    right.servoj(
                        self._clamp_step(
                            [float(v) for v in right_joints],
                            reference[_LEFT_ARM_JOINTS:],
                        )
                    )
        except Exception:
            logger.exception("send_action: servoj failed")

    def _servoj_flat(
        self,
        positions: List[float],
        left: Optional[DucoArm],
        right: Optional[DucoArm],
    ) -> None:
        left_positions: Optional[List[float]] = None
        right_positions: Optional[List[float]] = None
        if len(positions) >= 2 * _LEFT_ARM_JOINTS:
            left_positions = positions[:_LEFT_ARM_JOINTS]
            right_positions = positions[_LEFT_ARM_JOINTS : 2 * _LEFT_ARM_JOINTS]
        elif len(positions) >= _LEFT_ARM_JOINTS:
            left_positions = positions[:_LEFT_ARM_JOINTS]
        if left_positions is None and right_positions is None:
            logger.warning(
                "send_action: need at least %d joints, got %d",
                _LEFT_ARM_JOINTS,
                len(positions),
            )
            return

        # clamp against actual position when available, else last commanded
        reference = self._last_commanded or songyi_app.get_state_cache().positions()
        left_target: Optional[List[float]] = None
        right_target: Optional[List[float]] = None
        if left_positions is not None:
            left_ref = (reference or left_positions)[:_LEFT_ARM_JOINTS]
            left_target = self._clamp_step(left_positions, left_ref)
        if right_positions is not None:
            right_ref = (reference or (right_positions * 2))[
                _LEFT_ARM_JOINTS : 2 * _LEFT_ARM_JOINTS
            ]
            right_target = self._clamp_step(right_positions, right_ref)
        try:
            if left_target is not None and left is not None and left.connected:
                left.servoj(left_target)
            if right_target is not None and right is not None and right.connected:
                right.servoj(right_target)
        except Exception:
            logger.exception("send_action: servoj failed")

    def _command_gripper_side(self, side: str, value: Any) -> None:
        """单侧夹爪下发（left_gripper/right_gripper，0-10）。

        `value` 缺失（None）= 该侧夹爪不在本 action 中 → 不下发
        。0-10 → ×100。
        """
        if value is None:
            return
        arm = songyi_app.get_arms().get(side)
        if arm is None or not arm.connected or not arm.gripper_enabled:
            return
        try:
            clamped = max(0.0, min(10.0, float(value)))
        except (TypeError, ValueError):
            return
        try:
            arm.gripper_command(round(clamped * 100))
            logger.debug("gripper %s -> %.1f", side, clamped)
        except Exception:
            logger.exception("gripper command failed (%s)", side)

    def _command_grippers(self, grippers: Any) -> None:
        """Gripper hook: [left_ee, right_ee] 0-10 (0=close, 10=open).

        Values are scaled ×100 to the Modbus register range 0-1000 and
        sent to each arm's tool-485 gripper (dedup happens in DucoArm —
        unchanged targets are not re-sent at action rate).
        """
        if not grippers:
            return
        try:
            values = [float(v) for v in grippers]
        except (TypeError, ValueError):
            return
        clamped = [max(0.0, min(10.0, v)) for v in values]

        arms = songyi_app.get_arms()
        sides = ("left", "right")
        for index, side in enumerate(sides):
            if index >= len(clamped):
                break
            arm = arms.get(side)
            if arm is not None and arm.connected and arm.gripper_enabled:
                try:
                    arm.gripper_command(round(clamped[index] * 100))
                except Exception:
                    logger.exception("gripper command failed (%s)", side)

    @staticmethod
    def _positions_close(
        a: List[float], b: List[float], tolerance_rad: float
    ) -> bool:
        """到位判定：两关节向量长度一致且各关节差 ≤ 容差（弧度）。"""
        if len(a) != len(b):
            return False
        return all(abs(x - y) <= tolerance_rad for x, y in zip(a, b))

    def toggle_grippers(self) -> None:
        """键盘 g：切换双臂夹爪开/合（10.0=张开 / 0.0=夹紧）。

        本地显式操作：不受 hold/dry_run 门控（与 a/d/s 点动、h 复位
        同策略）。开/合状态由本 adapter 维护，初始视为闭合。
        """
        self._gripper_open = not self._gripper_open
        value = 10.0 if self._gripper_open else 0.0
        self._command_grippers([value, value])
        logger.info(
            "gripper toggle -> %s", "open(10.0)" if self._gripper_open else "close(0.0)"
        )

    def toggle_arm(self, side: str) -> None:
        """键盘 l/r：左/右臂按 **四步循环** 切换关节位姿（带到位门控）。

        循环：**位姿 1 → home → 位姿 2 → home**（home 取
        ``custom_config.reset_pose`` 对应臂段，弧度；位姿为度转弧度）。
        每步经 ``servoj`` 伺服到该步目标。**到位门控**：与上一次按下
        目标的各关节差 ≤ ``_ARM_TOGGLE_TOLERANCE_RAD`` 才前进到下一步；
        过远则重发上一次目标（就地拉回）。遥操进行中/回放丢弃；
        硬件层 dry_run 只更新循环状态不下发；臂未连接跳过。
        """
        cycle = self._arm_toggle_cycle(side)
        if not cycle:
            return
        if songyi_app.get_playback() is not None:
            logger.debug("arm toggle: playback; skipped")
            return
        if songyi_app.is_teleop_active():
            logger.debug("arm toggle: teleop active; skipped")
            return
        arm = songyi_app.get_arms().get(side)
        if arm is None or not arm.connected:
            logger.debug("arm toggle: arm %r not connected; skipped", side)
            return
        prev = self._arm_toggle_index.get(side)
        target_index = self._arm_toggle_target_index(side, arm, cycle, prev)
        target = cycle[target_index]
        self._execute_arm_toggle(side, arm, cycle, target, target_index)

    def _arm_toggle_target_index(
        self, side: str, arm: Any, cycle: List[List[float]], prev: Optional[int]
    ) -> int:
        """Compute the next cycle index, honouring the arrive-at-position gate.

        首次按下切到位姿 1；已到位（各关节差 ≤ 容差）前进一步，未到位则
        重发上一次目标（就地拉回）；读取关节失败按位姿 1 处理。
        """
        if prev is None:
            # 首按：切到位姿 1
            return 0
        last_target = cycle[prev % len(cycle)]
        try:
            actual = [
                float(v) for v in arm.get_positions()[: len(last_target)]
            ]
        except Exception:
            logger.warning("arm %s toggle: 关节读取失败", side, exc_info=True)
            actual = None
        if actual and self._positions_close(
            actual, last_target, _ARM_TOGGLE_TOLERANCE_RAD
        ):
            # 已到位 → 前进到下一步
            return (prev + 1) % len(cycle)
        # 未到位 → 回到上一次按下的位置（重发上次目标）
        logger.info(
            "arm %s toggle: 未到位（距上次目标过远），重发上次位姿",
            side,
        )
        return prev % len(cycle)

    def _execute_arm_toggle(
        self,
        side: str,
        arm: Any,
        cycle: List[List[float]],
        target: List[float],
        target_index: int,
    ) -> None:
        """下发一次 arm toggle 目标（硬件层 dry_run 只更新循环状态不下发）。"""
        if self._dry_run:
            logger.info(
                "[DRY_RUN] arm %s toggle -> step%d/%d %s (rad)",
                side, target_index + 1, len(cycle), target,
            )
            self._arm_toggle_index[side] = target_index
            return
        try:
            reset_velocity = self._cfg.get("reset_velocity")
            if reset_velocity is not None:
                reset_velocity = float(reset_velocity)
            arm.servoj(target, velocity=reset_velocity)
        except Exception:
            logger.exception("arm %s toggle failed", side)
            return
        self._arm_toggle_index[side] = target_index
        logger.info(
            "arm %s toggle -> step%d/%d %s (rad)", side, target_index + 1,
            len(cycle), target,
        )

    def _arm_toggle_cycle(self, side: str) -> List[List[float]]:
        """构建 l/r 切换循环（弧度）：位姿 1 → home → 位姿 2 → home。

        ``reset_pose`` 缺失/不完整时退化为两步循环（位姿 1 ↔ 位姿 2）。
        """
        deg_positions = self._arm_toggle_positions.get(side) or []
        if len(deg_positions) < 2:
            return []
        pos1 = [math.radians(v) for v in deg_positions[0]]
        pos2 = [math.radians(v) for v in deg_positions[1]]
        reset_pose = [float(v) for v in (self._cfg.get("reset_pose") or [])]
        home = (
            reset_pose[:_LEFT_ARM_JOINTS]
            if side == "left"
            else reset_pose[_LEFT_ARM_JOINTS : 2 * _LEFT_ARM_JOINTS]
        )
        if len(home) != _LEFT_ARM_JOINTS:
            logger.warning(
                "arm toggle: reset_pose 缺 %s 臂段，退化为两步循环", side
            )
            return [pos1, pos2]
        return [pos1, home, pos2, home]

    def command_waist(self, positions: Any) -> None:
        """waist_command topic 唯一控制入口：腰部可控关节 servoj 下发。

        收到 topic 才控制（action 不再携带腰部）。**目标先夹紧到配置
        限位**（``jog_min_rad``/``jog_max_rad``，弧度）再下发与记录——
        超限指令不执行（步长夹紧只防跳点，不构成限位）。回放模式丢弃；
        teleop 进行中丢弃（臂由 VR 接管，避免混合控制）；硬件层 dry_run
        只打印不下发。其余经 ``_command_aux``：可控段步长按
        ``max_step_rad`` 夹紧、固定前缀自动拼回（前 3 关节任何通道都
        不可触碰）。
        """
        if songyi_app.get_playback() is not None:
            logger.debug("[PLAYBACK] waist command dropped: %s", positions)
            return
        if songyi_app.is_teleop_active():
            logger.debug("teleop active: waist command dropped")
            return
        try:
            clamped = [
                max(self._waist_jog_min, min(self._waist_jog_max, float(v)))
                for v in positions
            ]
        except (TypeError, ValueError):
            logger.warning("waist command: 非法 position %r；丢弃", positions)
            return
        if not clamped:
            logger.warning("waist command: empty position；丢弃")
            return
        if clamped != [
            float(v) for v in positions
        ]:
            logger.info(
                "waist command %s 超限位，夹紧为 %s", positions, clamped
            )
        if self._dry_run:
            logger.info("[DRY_RUN] waist command: %s", clamped)
            return
        self._command_aux("waist", clamped)

    def waist_jog(self, direction: int) -> None:
        """键盘点动腰部可控关节（第 4 关节）：+1 增加 / -1 减少 / 0 回 0。

        目标角自上次点动累计（首次按 a/d 从腰部实际关节位取基准），
        步长 ``jog_step_rad``，限位 ``jog_min_rad``/``jog_max_rad``
        （均为弧度，custom_config.waist 节）。下发复用 ``_command_aux``
        （固定前缀拼回 + ``max_step_rad`` 步长夹紧双保险）。点动目标
        **同时记录为最新腰部指令**——保活按新目标续发，否则 0.3s 后
        保活会把腰部拽回旧的云端目标（点动被覆盖）。模块未连接 /
        teleop 进行中跳过；硬件层 dry_run 只打印不下发。
        """
        axis = songyi_app.get_aux().get("waist")
        if axis is None or not axis.connected:
            logger.debug("waist jog: 模块未连接；跳过")
            return
        if songyi_app.is_teleop_active():
            logger.debug("waist jog: teleop 进行中；跳过")
            return
        if self._waist_jog_target is None:
            self._waist_jog_target = songyi_app.get_waist_position()
        if direction == 0:
            target = 0.0
        else:
            target = self._waist_jog_target + direction * self._waist_jog_step
        target = max(self._waist_jog_min, min(self._waist_jog_max, target))
        self._waist_jog_target = target
        if self._dry_run:
            logger.info("[DRY_RUN] waist jog -> %.3f rad", target)
            return
        self._command_aux("waist", [target])
        logger.info("waist jog -> %.3f rad", target)

    def _command_aux(self, kind: str, joints: Any) -> None:
        """Waist/head hook: 可控关节目标 (rad)，servoj 非阻塞下发。

        入参只承载**可控关节**（腰部第 4 关节起；前 3 关节写死 demo
        值，由 ``DucoAuxAxis`` 下发时自动拼回，任何通道都无法触碰）。
        模块未配置/未连接（未使能）时静默跳过；可控段步长按
        ``max_step_rad`` 相对当前实际关节位夹紧（首步前读真实位置），
        并按 ``实际关节数 - 固定数`` 截断超发值。
        """
        if not joints:
            return
        axis = songyi_app.get_aux().get(kind)
        if axis is None or not axis.connected:
            return
        try:
            target = [float(v) for v in joints]
        except (TypeError, ValueError):
            return
        if not target:
            return
        try:
            fixed_n = axis.fixed_count
            reference = axis.get_positions() or self._aux_last.get(kind) or []
            if fixed_n and len(reference) > fixed_n:
                # 只允许控制固定前缀之后的关节，超发截断
                target = target[: len(reference) - fixed_n]
                ref_control = reference[fixed_n:]
            else:
                ref_control = (
                    reference[fixed_n:] if len(reference) >= fixed_n else []
                ) or target
            target = self._clamp_step(target, ref_control)
            axis.servoj(target)
            self._aux_last[kind] = axis.fixed_joints + target
            logger.debug("aux %s -> %s", kind, target)
        except Exception:
            logger.exception("aux %s command failed", kind)

    def _clamp_step(
        self, target: List[float], reference: List[float]
    ) -> List[float]:
        """Clamp per-joint deltas to ``max_step_rad`` (safety bound)."""
        if self._max_step_rad is None:
            return list(target)
        clamped: List[float] = []
        for index, value in enumerate(target):
            ref = float(reference[index]) if index < len(reference) else value
            delta = value - ref
            if abs(delta) > self._max_step_rad:
                delta = self._max_step_rad if delta > 0 else -self._max_step_rad
                value = ref + delta
            clamped.append(value)
        return clamped

    @staticmethod
    def _extract_positions(command: Mapping[str, Any]) -> Optional[List[float]]:
        raw = command.get("positions", command.get("position"))
        if raw is None:
            joint_states = command.get("joint_states")
            if isinstance(joint_states, Mapping):
                raw = joint_states.get("position")
        if raw is None:
            return None
        try:
            return [float(v) for v in raw]
        except (TypeError, ValueError):
            return None


class SongyiMockAdapter(SongyiHardwareAdapter):
    """软件仿真 adapter：mock_duco 同名同参仿真，无 Thrift/485 硬件。

    与 :class:`SongyiHardwareAdapter` 共享全部上层逻辑（观测/动作/告警/
    遥操门控/键盘命令），仅设备创建走 ``mock_duco`` 软件仿真（双 entry
    point 形态中的 mock 面；真机面为 ``SongyiHardwareAdapter``）。构造
    时强制 ``devices.mock: true``——真机 adapter 构造时剥除该键，两类
    运行时路径互不触达；真机进程不加载 mock_duco（lazy import）。

    mock 事件源：``custom_config.mock_events.enabled``（默认
    开）时 connect 启动 :class:`~songyi_robot.mock_duco.MockEventSource`
    ——周期构造 mock 告警 Event 并经 EventManager 单例推送（原
    MockEventPublisher 直捅 transport 废止）；disconnect 停止。
    """

    mock_enabled = True

    def __init__(self, config: Mapping[str, Any]):
        merged = dict(config or {})
        devices = dict(merged.get("devices", {}) or {})
        devices["mock"] = True
        merged["devices"] = devices
        super().__init__(merged)
        self._mock_event_source: Optional[Any] = None

    def connect(self) -> None:
        super().connect()
        mock_events_cfg = dict(self._cfg.get("mock_events", {}) or {})
        if mock_events_cfg.get("enabled", True):
            from .mock_duco import MockEventSource

            self._mock_event_source = MockEventSource(mock_events_cfg)
            self._mock_event_source.start()
        else:
            logger.info("mock events disabled (mock_events.enabled=false)")

    def disconnect(self) -> None:
        source, self._mock_event_source = self._mock_event_source, None
        if source is not None:
            source.stop()
        super().disconnect()


def create_songyi_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> "SongyiHardwareAdapter":
    """Entry-point factory for the real-robot adapter (r2c_sdk.adapters)."""
    return SongyiHardwareAdapter(config=config, **extra_kwargs)


def create_songyi_mock_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> "SongyiMockAdapter":
    """Entry-point factory for the software-simulation adapter."""
    return SongyiMockAdapter(config=config, **extra_kwargs)
