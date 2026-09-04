"""Generic LeRobot-backed implementation of :class:`IRobotHardwareAdapter`."""

from __future__ import annotations

import os
import tempfile
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

import yaml

from cloudrobo_r2c.core.interfaces import IRobotHardwareAdapter


def create_lerobot_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> IRobotHardwareAdapter:
    """Entry_point factory for LeRobotHardwareAdapter.

    Mirrors :meth:`LeRobotHardwareAdapter.from_config_file` for the
    ``runtime.robot_connect`` / ``runtime.robot_calibrate`` keys: the
    entry-point path constructs the adapter directly (no config file), so
    connect kwargs must be extracted here ¡ª otherwise ``robot.connect()``
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
class LeRobotHardwareAdapter(IRobotHardwareAdapter):
    """Generic hardware adapter for LeRobot runtime objects.

    The adapter expects a YAML config that includes a ``robot`` section
    compatible with LeRobot's ``RobotConfig`` schema.
    """

    config: Mapping[str, Any]
    robot_config_decoder: Optional[RobotConfigDecoder] = None
    robot_factory: Optional[RobotFactory] = None
    connect_kwargs: Dict[str, Any] = field(default_factory=dict)

    _robot: Any = field(default=None, init=False, repr=False)
    _connected: bool = field(default=False, init=False, repr=False)

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

    def disconnect(self) -> None:
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
        except ImportError as exc:  # pragma: no cover - optional runtime dependency
            raise RuntimeError(
                "LeRobot and draccus must be installed in the robot environment to build RobotConfig."
            ) from exc

        # Third-party robot plugins (e.g. lerobot_robot_galaxea_a1z) register
        # their robot.type subclass via register_subclass on import. The lerobot
        # CLIs call this at startup; we must do the same here, otherwise
        # draccus.decode fails with "Couldn't find a choice class".
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
