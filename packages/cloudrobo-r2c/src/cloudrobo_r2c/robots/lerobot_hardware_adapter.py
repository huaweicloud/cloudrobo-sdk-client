"""Generic LeRobot-backed implementation of :class:`IRobotHardwareAdapter`."""

from __future__ import annotations

import importlib
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

import yaml

import cv2

from cloudrobo_r2c.core.interfaces import ICameraFrameSource, IRobotHardwareAdapter


def create_lerobot_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> IRobotHardwareAdapter:
    """Entry_point factory for LeRobotHardwareAdapter.

    Mirrors :meth:`LeRobotHardwareAdapter.from_config_file` for the
    ``runtime.robot_connect`` / ``runtime.robot_calibrate`` keys: the
    entry-point path constructs the adapter directly (no config file), so
    connect kwargs must be extracted here — otherwise ``robot.connect()``
    always falls back to its defaults (e.g. ``calibrate=True``, triggering
    interactive calibration in headless deployments).
    """
    adapter_cfg = dict(config)
    connect_kwargs = LeRobotHardwareAdapter._extract_connect_kwargs(adapter_cfg)
    return LeRobotHardwareAdapter(
        config=adapter_cfg,
        connect_kwargs=connect_kwargs,
    )


RobotConfigDecoder = Callable[[Mapping[str, Any]], Any]
RobotFactory = Callable[[Any], Any]
logger = logging.getLogger(__name__)

logging.getLogger("can.interfaces.socketcan").setLevel(logging.WARNING)


@dataclass
class LeRobotHardwareAdapter(IRobotHardwareAdapter, ICameraFrameSource):
    """Generic hardware adapter for LeRobot runtime objects.

    The adapter expects a YAML config that includes a ``robot`` section
    compatible with LeRobot's ``RobotConfig`` schema.

    相机推流：LeRobot robot 实例持有 ``self.cameras``（``{cam_key: Camera}``，
    每路有 ``read_latest()``）。适配器在 ``connect()`` 后启动独立后台线程，
    按相机帧率把 ``cam.read_latest()`` 写入共享帧槽（``_latest_frames`` /
    ``_latest_ts``），推流侧 ``CameraCapture`` 通过 ``get_latest_frame`` 读取。
    """

    config: Mapping[str, Any]
    robot_config_decoder: Optional[RobotConfigDecoder] = None
    robot_factory: Optional[RobotFactory] = None
    connect_kwargs: Dict[str, Any] = field(default_factory=dict)

    _robot: Any = field(default=None, init=False, repr=False)
    _connected: bool = field(default=False, init=False, repr=False)
    # 相机推流帧槽（cam_key -> 最近帧副本 / time.monotonic()）
    _latest_frames: Dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _latest_ts: Dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _camera_lock: Any = field(default=None, init=False, repr=False)
    _camera_threads: Dict[str, threading.Thread] = field(
        default_factory=dict, init=False, repr=False
    )
    _camera_stop_events: Dict[str, threading.Event] = field(
        default_factory=dict, init=False, repr=False
    )

    @classmethod
    def from_config_file(
        cls,
        config_path: str | Path,
        *,
        robot_config_decoder: Optional[RobotConfigDecoder] = None,
        robot_factory: Optional[RobotFactory] = None,
    ) -> "LeRobotHardwareAdapter":
        """Build adapter from YAML configuration file."""
        path = Path(config_path)
        logger.debug("Loading LeRobot adapter config from file: %s", path)
        with path.open("r", encoding="utf-8") as handle:
            cfg = yaml.safe_load(handle) or {}
        if not isinstance(cfg, Mapping):
            raise ValueError(f"Top-level config must be a mapping, got {type(cfg)!r}")
        logger.debug("Loaded adapter config with top-level keys: %s", list(cfg.keys()))

        connect_kwargs = cls._extract_connect_kwargs(cfg)
        logger.debug(
            "Extracted robot connect kwargs from config: %s",
            connect_kwargs,
        )

        return cls(
            config=cfg,
            robot_config_decoder=robot_config_decoder,
            robot_factory=robot_factory,
            connect_kwargs=connect_kwargs,
        )

    def __post_init__(self) -> None:
        from cloudrobo_r2c.robots.commands.lerobot import LeRobotGoHomeCommand
        self.register_command_class("go_home", LeRobotGoHomeCommand)


    def connect(self) -> None:
        if self._connected:
            logger.debug("connect() skipped because adapter is already connected.")
            return

        logger.debug("connect() starting robot initialization.")
        robot = self._ensure_robot_instance()
        connect = getattr(robot, "connect", None)
        if not callable(connect):
            raise TypeError("LeRobot robot instance must expose a callable connect()")

        logger.debug(
            "Invoking robot.connect with kwargs=%s (fallback on TypeError enabled).",
            self.connect_kwargs,
        )
        if self.connect_kwargs:
            try:
                connect(**self.connect_kwargs)
            except TypeError:
                logger.debug(
                    "robot.connect(**kwargs) raised TypeError; retrying with connect()."
                )
                connect()
        else:
            connect()
        self._connected = True
        logger.debug("Adapter connected successfully.")
        self._start_camera_capture_threads()

    def disconnect(self) -> None:
        self._stop_camera_capture_threads()
        if self._robot is None:
            logger.debug(
                "disconnect() called before robot creation; marking adapter disconnected."
            )
            self._connected = False
            return

        disconnect = getattr(self._robot, "disconnect", None)
        if callable(disconnect):
            logger.debug("Invoking robot.disconnect().")
            disconnect()
        else:
            logger.debug(
                "Robot has no callable disconnect(); skipping robot disconnect."
            )
        self._connected = False
        logger.debug("Adapter disconnected successfully.")

    def get_observation(self) -> Mapping[str, Any]:
        robot = self._require_connected_robot()
        get_observation = getattr(robot, "get_observation", None)
        if not callable(get_observation):
            raise TypeError("LeRobot robot instance must expose get_observation()")

        observation = get_observation()
        if not isinstance(observation, Mapping):
            raise TypeError(
                f"robot.get_observation() must return a mapping, got {type(observation)!r}"
            )
        logger.debug(
            "Collected observation from robot with keys: %s",
            list(observation.keys()),
        )
        return observation

    def send_action(self, command: Mapping[str, Any]) -> None:
        robot = self._require_connected_robot()
        send_action = getattr(robot, "send_action", None)
        if not callable(send_action):
            raise TypeError("LeRobot robot instance must expose send_action(command)")
        logger.debug(
            "Sending action to robot with command keys: %s", list(command.keys())
        )
        send_action(command)

    def get_runtime(self, tick: int = 0) -> Mapping[str, Any]:
        """Collector for the state/current publish topic (CurrentState frame).

        从最新观测提取 joint_states：优先 ``joint_states`` 键；缺省回退
        到 ``hardware.config`` 的 ``runtime_joint_names``（缺省
        ``so101`` 风格 `.pos` 键列表，直接读 ``obs[<name>]``）。
        ``location`` 取配置 ``hardware.config.location``（缺省空串）。
        供 ``hardware.topics`` 的 ``{project_id}/{device_id}/state/current``
        发布绑定调用。
        """
        obs = self.get_observation()
        if not isinstance(obs, Mapping):
            obs = {}
        js = obs.get("joint_states") or {}
        if isinstance(js, Mapping) and (js.get("names") or js.get("position")):
            names = [str(v) for v in js.get("names", [])]
            position = [float(v) for v in js.get("position", [])]
            velocity = [float(v) for v in js.get("velocity", [])]
            torque = [float(v) for v in js.get("torque", [])]
        else:
            joint_keys = self.config.get("runtime_joint_names")
            if not joint_keys:
                joint_keys = [
                    "shoulder_pan.pos",
                    "shoulder_lift.pos",
                    "elbow_flex.pos",
                    "wrist_flex.pos",
                    "wrist_roll.pos",
                    "gripper.pos",
                ]
            names = [str(k) for k in joint_keys]
            position = []
            for k in joint_keys:
                val = obs.get(k)
                position.append(0.0 if val is None else float(val))
            velocity = []
            torque = []
        return {
            "timestamp": int(time.time() * 1000),
            "location": str(self.config.get("location", "")),
            "joint_states": {
                "type": [f"J{i}" for i in range(1, len(names) + 1)],
                "names": names,
                "position": position,
                "velocity": velocity,
                "torque": torque,
            },
        }

    # ── ICameraFrameSource（相机推流共享帧槽） ──────────────────────────────

    def _camera_capture_keys(self) -> Dict[str, Dict[str, Any]]:
        """从配置解析要采集的相机：{cam_key: {fps}}。

        配置源（任选其一，按优先级）：
          - ``config.camera_streaming.camera_sources``: {cam_key: {fps}}
          - ``config.camera_streaming.camera_source_map``: {camera_id: cam_key}
            → 采集 value（cam_key 即帧槽 key）
          - ``config.camera_streaming.camera_device_map``: {推流key: cam_key}
            → 采集 value（cam_key 即帧槽 key）
          - 兜底：robot.cameras 全部 key（fps 取 Camera.read 默认）
        """
        streaming = self.config.get("camera_streaming") or {}
        if isinstance(streaming, Mapping):
            sources = streaming.get("camera_sources")
            if isinstance(sources, Mapping) and sources:
                return {str(k): dict(v) for k, v in sources.items()}
            srcmap = streaming.get("camera_source_map")
            if isinstance(srcmap, Mapping) and srcmap:
                return {str(v): {} for v in srcmap.values()}
            devmap = streaming.get("camera_device_map")
            if isinstance(devmap, Mapping) and devmap:
                return {str(v): {} for v in devmap.values()}
        return {}

    def _start_camera_capture_threads(self) -> None:
        """启动 LeRobot 相机采集线程（读 robot.cameras 写共享帧槽）。"""
        robot = self._robot
        if robot is None:
            return
        cameras = getattr(robot, "cameras", None)
        if not cameras:
            logger.debug(
                "LeRobot robot has no cameras attribute; camera streaming "
                "will be unavailable"
            )
            return

        keys = self._camera_capture_keys()
        if not keys:
            # 未配置：默认采集 robot.cameras 全部（保持能力上报可用）
            keys = {str(k): {} for k in cameras.keys()}

        if self._camera_lock is None:
            self._camera_lock = threading.Lock()

        for cam_key, opts in keys.items():
            cam = cameras.get(cam_key)
            if cam is None:
                logger.warning(
                    "LeRobot camera %r not found in robot.cameras (have %s); skipping",
                    cam_key,
                    list(cameras.keys()),
                )
                continue
            fps = int(opts.get("fps", 0) or 0)
            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._camera_capture_loop,
                args=(cam_key, cam, stop_event, fps),
                name=f"lerobot-cam-{cam_key}",
                daemon=True,
            )
            self._camera_stop_events[cam_key] = stop_event
            self._camera_threads[cam_key] = thread
            thread.start()
            logger.info(
                "LeRobot camera capture started: %s (fps=%s)",
                cam_key,
                fps if fps > 0 else "default",
            )

    def _camera_capture_loop(
        self,
        cam_key: str,
        cam: Any,
        stop_event: threading.Event,
        fps: int,
    ) -> None:
        """后台线程：按相机帧率循环读帧写共享帧槽（keep-latest）。

        帧槽统一存 BGR ndarray（与 ``CameraCapture``、``dummy_robot``、
        ``songyi_adapter`` 等推流侧约定一致）。LeRobot OpenCV camera 默认
        ``color_mode=RGB``，这里在写入帧槽前归一化为 BGR。

        注意：观测上行路径（``get_observation`` → ``robot.get_observation()``
        → LeRobot 直接返回 RGB → ``device_to_r2c`` 的 ``rgb_to_bgr`` transform
        → BGR → ``ndarray_to_jpeg``）与本帧槽完全分离，不受此处转换影响。
        """
        if fps <= 0:
            fps = 30
        interval = 1.0 / float(fps)
        while not stop_event.is_set():
            try:
                frame = cam.read_latest()
                if frame is not None:
                    # LeRobot OpenCV camera 默认 color_mode=RGB；
                    # 推流侧 CameraCapture 约定帧槽为 BGR，这里归一化。
                    if frame.ndim == 3 and frame.shape[2] == 3:
                        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    with self._camera_lock:
                        self._latest_frames[cam_key] = frame.copy()
                        self._latest_ts[cam_key] = time.monotonic()
            except Exception:
                logger.debug(
                    "LeRobot camera %r read failed", cam_key, exc_info=True
                )
            stop_event.wait(timeout=interval)

    def _stop_camera_capture_threads(self) -> None:
        """停止相机采集线程并清理帧槽。"""
        for cam_key, stop_event in self._camera_stop_events.items():
            stop_event.set()
        for cam_key, thread in self._camera_threads.items():
            if thread.is_alive():
                thread.join(timeout=2.0)
        self._camera_threads.clear()
        self._camera_stop_events.clear()
        self._latest_frames.clear()
        self._latest_ts.clear()

    def get_latest_frame(self, camera_id: str) -> Optional[Any]:
        """返回指定相机（按 LeRobot cam_key）的最近一帧，无帧返回 None。

        与推流侧共享帧槽；内部加锁并返回副本，避免与采集线程写竞争。
        """
        if self._camera_lock is None:
            return None
        with self._camera_lock:
            frame = self._latest_frames.get(camera_id)
            return None if frame is None else frame.copy()

    def get_latest_timestamp(self, camera_id: str) -> float:
        """返回最近一帧的采集时间戳（time.monotonic()），无帧返回 0.0。"""
        if self._camera_lock is None:
            return 0.0
        with self._camera_lock:
            return self._latest_ts.get(camera_id, 0.0)

    def _require_connected_robot(self) -> Any:
        if not self._connected or self._robot is None:
            logger.debug(
                "_require_connected_robot check failed (connected=%s, robot_exists=%s).",
                self._connected,
                self._robot is not None,
            )
            raise RuntimeError("Adapter is not connected. Call connect() first.")
        return self._robot

    def _ensure_robot_instance(self) -> Any:
        if self._robot is not None:
            logger.debug("Reusing existing robot instance.")
            return self._robot

        logger.debug("Creating new robot instance from adapter config.")
        robot_mapping = self._extract_robot_mapping(self.config)
        decoder = self.robot_config_decoder or self._default_robot_config_decoder
        logger.debug(
            "Using robot config decoder: %s", getattr(decoder, "__name__", decoder)
        )
        robot_cfg_obj = decoder(robot_mapping)

        factory = self.robot_factory or self._default_robot_factory
        logger.debug("Using robot factory: %s", getattr(factory, "__name__", factory))
        self._robot = factory(robot_cfg_obj)
        logger.debug(
            "Robot instance created successfully: %s", type(self._robot).__name__
        )
        return self._robot

    @staticmethod
    def _extract_robot_mapping(cfg: Mapping[str, Any]) -> Mapping[str, Any]:
        robot_section = cfg.get("robot")
        if not isinstance(robot_section, Mapping):
            raise ValueError("robot must be a mapping in robot config")

        normalized_robot_section: Dict[str, Any] = dict(robot_section)
        logger.debug(
            "Normalizing robot config section with keys: %s",
            list(normalized_robot_section.keys()),
        )
        if not normalized_robot_section.get("type"):
            robot_type = normalized_robot_section.get("robot_type")
            if robot_type:
                normalized_robot_section["type"] = robot_type
                logger.debug("Mapped robot.robot_type=%s to robot.type.", robot_type)

        if not normalized_robot_section.get("type"):
            raise ValueError(
                "robot.type (or robot.robot_type) is required in robot config"
            )
        return normalized_robot_section

    @staticmethod
    def _extract_connect_kwargs(cfg: Mapping[str, Any]) -> Dict[str, Any]:
        runtime_section = cfg.get("runtime") or {}
        if runtime_section and not isinstance(runtime_section, Mapping):
            raise ValueError("runtime must be a mapping when provided")
        logger.debug(
            "Parsing runtime section for connect kwargs. runtime keys=%s",
            (
                list(runtime_section.keys())
                if isinstance(runtime_section, Mapping)
                else []
            ),
        )

        connect_kwargs: Dict[str, Any] = {}
        raw_connect_kwargs = runtime_section.get("robot_connect")
        if raw_connect_kwargs is not None:
            if not isinstance(raw_connect_kwargs, Mapping):
                raise ValueError(
                    "runtime.robot_connect must be a mapping when provided"
                )
            connect_kwargs.update(dict(raw_connect_kwargs))

        if bool(runtime_section.get("robot_calibrate", False)):
            connect_kwargs.setdefault("calibrate", True)
            logger.debug(
                "Enabled calibrate=True via runtime.robot_calibrate configuration."
            )

        return connect_kwargs

    @staticmethod
    def _import_so_follower_compat() -> None:
        """兼容不同 lerobot 版本的 so_follower / so101_follower 模块导入。

        不同 lerobot 版本对 SO-100/101 从臂模块的命名不一致：
          - 新版本（0.5.x / 0.4.4 等）: ``lerobot.robots.so_follower``
          - 部分版本: ``lerobot.robots.so101_follower``（子模块直接叫
            ``so101_follower``，此时导入 ``so_follower`` 会 ImportError）

        两个模块导入都只为触发 ``@RobotConfig.register_subclass(...)``
        装饰器注册（so100_follower / so101_follower 类型），导入失败时
        不阻断整体解码——若最终 decode 仍报 "Couldn't find a choice class
        for 'so101_follower'"，说明该版本命名不同，此处仅记录告警。

        注意：不能直接 ``from lerobot.robots import so_follower``，否则在
        子模块名为 ``so101_follower`` 的版本上会把整个机器人解码逻辑
        拖垮（ImportError 冒泡到外层，掩盖真正错误）。
        """
        for module_name in ("so_follower", "so101_follower"):
            try:
                importlib.import_module(f"lerobot.robots.{module_name}")
                logger.debug(
                    "Imported lerobot.robots.%s for robot subclass registration.",
                    module_name,
                )
                # 任一成功即可（两个模块注册的是同一组类型）
                return
            except ImportError:
                logger.warning(
                    "lerobot.robots.%s is not importable in this lerobot version; "
                    "trying alternate module name.",
                    module_name,
                    exc_info=True,
                )
        logger.warning(
            "Neither lerobot.robots.so_follower nor lerobot.robots.so101_follower "
            "is importable; SO-100/101 robot types may be unavailable for "
            "draccus.decode."
        )

    @staticmethod
    def _default_robot_factory(robot_config_obj: Any) -> Any:
        try:
            from lerobot.robots import make_robot_from_config
        except ImportError as exc:  # pragma: no cover - optional runtime dependency
            raise RuntimeError(
                "LeRobot is not installed. Please `pip install lerobot`."
            ) from exc
        logger.debug(
            "Building robot instance via lerobot.robots.make_robot_from_config."
        )
        return make_robot_from_config(robot_config_obj)

    @staticmethod
    def _import_builtin_lerobot_robots() -> None:
        """Import every built-in robot submodule under ``lerobot.robots``.

        The robots shipped inside ``lerobot.robots`` (``so_follower``,
        ``koch_follower``, ``reachy2``, ...) register their ``robot.type``
        subclass with ``RobotConfig.register_subclass`` lazily, when the
        submodule is imported.  The lerobot CLIs trigger this with explicit
        ``from lerobot.robots import so_follower`` statements at module top
        level; ``register_third_party_plugins`` only covers *third-party*
        plugins and skips these built-ins.  Without importing them,
        ``draccus.decode(RobotConfig, {"type": "so101_follower", ...})``
        fails with "Couldn't find a choice class".

        Each import is best-effort so a robot whose optional SDK is missing
        (e.g. ``reachy2_sdk``) does not block registration of the others.
        """
        import importlib
        import pkgutil

        try:
            import lerobot.robots as lerobot_robots_pkg
        except ImportError:  # pragma: no cover - optional runtime dependency
            return

        for module_info in pkgutil.iter_modules(lerobot_robots_pkg.__path__):
            if not module_info.ispkg:
                continue
            try:
                importlib.import_module(f"lerobot.robots.{module_info.name}")
            except ImportError:
                logger.debug(
                    "Skipped lerobot.robots.%s (optional dependency missing).",
                    module_info.name,
                )
            except Exception:  # pragma: no cover - defensive
                logger.debug(
                    "Failed to import lerobot.robots.%s; its robot type may be unavailable.",
                    module_info.name,
                    exc_info=True,
                )

    @staticmethod
    def _default_robot_config_decoder(robot_mapping: Mapping[str, Any]) -> Any:
        logger.debug(
            "Decoding RobotConfig from mapping with keys: %s",
            list(robot_mapping.keys()),
        )
        try:
            import draccus
            from lerobot.cameras.opencv.configuration_opencv import (
                OpenCVCameraConfig,
            )  # noqa: F401
            from lerobot.cameras.realsense.configuration_realsense import (
                RealSenseCameraConfig,
            )  # noqa: F401
            from lerobot.robots import (  # noqa: F401
                RobotConfig
            )
            LeRobotHardwareAdapter._import_so_follower_compat()
        except ImportError as exc:  # pragma: no cover - optional runtime dependency
            raise RuntimeError(
                "LeRobot and draccus must be installed in the robot environment to build RobotConfig."
            ) from exc
        # Built-in robot submodules (e.g. lerobot.robots.so_follower) and
        # third-party plugins (e.g. lerobot_robot_galaxea_a1z) both register
        # their robot.type subclass via register_subclass on import. The lerobot
        # CLIs import both at startup; we must do the same here, otherwise
        # draccus.decode fails with "Couldn't find a choice class".
        LeRobotHardwareAdapter._import_builtin_lerobot_robots()

        try:
            from lerobot.utils.import_utils import register_third_party_plugins
        except ImportError:  # pragma: no cover - older lerobot without plugin discovery
            register_third_party_plugins = None  # type: ignore[assignment]

        if register_third_party_plugins is not None:
            register_third_party_plugins()

        if hasattr(draccus, "decode"):
            logger.debug("Using draccus.decode API to decode RobotConfig.")
            return draccus.decode(RobotConfig, dict(robot_mapping))

        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as handle:
            yaml.safe_dump(dict(robot_mapping), handle, sort_keys=False)
            temp_path = handle.name
        logger.debug(
            "draccus.decode unavailable; wrote temp robot config for draccus.load at %s.",
            temp_path,
        )

        try:
            with open(temp_path, "r", encoding="utf-8") as handle:
                logger.debug("Loading RobotConfig via draccus.load from temp file.")
                return draccus.load(RobotConfig, handle)
        finally:
            try:
                os.unlink(temp_path)
                logger.debug("Deleted temporary robot config file: %s", temp_path)
            except OSError:
                logger.debug("Failed to delete temp robot config file: %s", temp_path)
                pass
