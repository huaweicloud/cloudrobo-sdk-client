"""Hardware adapter factory for robot runtime selection.

All adapter imports are lazy (inside the factory method), so users only need
to install dependencies for the specific robot type they are using.
"""

from __future__ import annotations

import logging
import re
import warnings
from importlib.metadata import entry_points
from typing import Any, Callable, Mapping, MutableMapping, Optional

from cloudrobo_r2c.core.interfaces import AdapterFactory, IRobotHardwareAdapter
from cloudrobo_r2c.core.internal.class_loading import ensure_subclass, load_class

RawSDKRobotFactory = Callable[[Mapping[str, Any]], Any]

logger = logging.getLogger(__name__)

_MAX_COMMAND_NAME_LEN = 64
_VALID_COMMAND_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_command_name(name: str, role: str) -> None:
    """Validate a command instance name or type name.

    Rules:
    - Non-empty string
    - Max ``_MAX_COMMAND_NAME_LEN`` characters
    - Valid identifier: starts with letter / underscore, rest
      letters / digits / underscores
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError(
            f"{role} must be a non-empty string, got {name!r}"
        )
    name = name.strip()
    if len(name) > _MAX_COMMAND_NAME_LEN:
        raise ValueError(
            f"{role} {name!r} is too long: {len(name)} chars "
            f"(max {_MAX_COMMAND_NAME_LEN})."
        )
    if not _VALID_COMMAND_NAME_RE.match(name):
        raise ValueError(
            f"{role} {name!r} is not a valid identifier. "
            f"Must start with a letter or underscore, followed by "
            f"letters, digits, or underscores (e.g. 'go_home', 'state')."
        )

_ADAPTER_ENTRY_POINT_GROUP = "r2c_sdk.adapters"

# Legacy adapter types that are handled via hard-coded paths in RobotFactory
# instead of entry_points.  Custom is the only type that is intentionally NOT
# an entry_point because it uses class_path dynamic loading.
LEGACY_HARDWARE_TYPES = [
    "custom",
]


class AdapterRegistry:
    """Discover and cache hardware adapter factories from entry_points.

    Scans the ``r2c_sdk.adapters`` entry_point group lazily - individual
    factories are only imported on first :meth:`get`.
    """

    _scanned: bool = False
    _entry_points: dict[str, Any] = {}
    _cache: dict[str, AdapterFactory] = {}
    _errors: dict[str, ValueError] = {}

    @classmethod
    def _ensure_scanned(cls) -> None:
        if cls._scanned:
            return
        cls._scanned = True
        try:
            grouped: dict[str, Any] = {}
            for ep in entry_points(group=_ADAPTER_ENTRY_POINT_GROUP):
                # Multiple distributions may register adapters under the same
                # name in this shared group (e.g. the legacy `r2c_sdk` /
                # `hw-r2c-sdk` package predates the rename to `cloudrobo_r2c`).
                # A flat `name -> ep` map lets a stale sibling overwrite ours,
                # which then fails the isinstance check because the two packages
                # define distinct `IRobotHardwareAdapter` classes. Always prefer
                # the entry point defined inside the current package.
                prev = grouped.get(ep.name)
                if prev is None or cls._is_ours(ep):
                    grouped[ep.name] = ep
            cls._entry_points = grouped
        except Exception:
            logger.warning(
                "Failed to scan entry_point group %r",
                _ADAPTER_ENTRY_POINT_GROUP,
                exc_info=True,
            )

    @staticmethod
    def _is_ours(ep: Any) -> bool:
        """True if *ep* targets a factory inside the ``cloudrobo_r2c`` package."""
        return str(getattr(ep, "value", "") or "").startswith("cloudrobo_r2c.")

    @classmethod
    def get(cls, type_name: str) -> Optional[AdapterFactory]:
        """Return factory for *type_name*, or ``None`` if not registered.

        Loads the entry_point lazily on first access and caches the result.
        If a previous load for *type_name* failed, raises ``ValueError``
        with the cached error context.
        """
        cls._ensure_scanned()
        if type_name in cls._errors:
            raise cls._errors[type_name]
        if type_name in cls._cache:
            return cls._cache[type_name]

        ep = cls._entry_points.get(type_name)
        if ep is None:
            return None

        try:
            factory = ep.load()
        except Exception as exc:
            cached_err = ValueError(
                f"Failed to load adapter entry_point {type_name!r} "
                f"(package: {ep.value!r}): {exc}"
            )
            cached_err.__cause__ = exc
            cls._errors[type_name] = cached_err
            raise cached_err from exc

        cls._cache[type_name] = factory
        return factory

    @classmethod
    def available_types(cls) -> list[str]:
        """Return all registered adapter type names from entry_points."""
        cls._ensure_scanned()
        return sorted(cls._entry_points.keys())

    @classmethod
    def reset(cls) -> None:
        """Clear all cached scan/load state.

        Intended for tests that mutate the registry's class-level state
        (``_entry_points`` / ``_cache`` / ``_errors``). The next access
        re-scans the real entry_points group, restoring the canonical
        adapter set so subsequent tests are not poisoned.
        """
        cls._scanned = False
        cls._entry_points = {}
        cls._cache = {}
        cls._errors = {}


class RobotFactory:
    """Build robot hardware adapters from a unified configuration mapping.

    Supports the following ``hardware.type`` values:

    - ``"lerobot"``: create :class:`LeRobotHardwareAdapter`
    - ``"ros2"``: create :class:`Ros2HardwareAdapter`
    - ``"raw_sdk"``: create :class:`VendorSDKHardwareAdapter`
    - ``"dummy"``: create :class:`DummyRobotHardwareAdapter`
    - ``"ur5e_rtde"``: create :class:`UR5eHardwareAdapter`
    - ``"zenoh_ros1"``: create :class:`ZenohRos1HardwareAdapter`
    - ``"playback"``: create :class:`PlaybackRobotAdapter`
    - ``"custom"``: load adapter class from dotted import path
    """

    # -- public entry point --------------------------------------------------

    @classmethod
    def create_hardware_adapter(
        cls,
        config: Mapping[str, Any],
        *,
        raw_sdk_robot_factory: Optional[RawSDKRobotFactory] = None,
    ) -> IRobotHardwareAdapter:
        """Create a hardware adapter from config.

        Expected shape (both top-level and nested under ``hardware`` are supported)::

            hardware:
              type: lerobot | ros2 | raw_sdk
              config: {...}
        """
        hardware_cfg = cls._extract_hardware_mapping(config)
        hardware_type = str(hardware_cfg.get("type", "")).strip().lower()
        if not hardware_type:
            raise ValueError("hardware.type is required")

        # entry_point registry path
        adapter = cls._try_entry_point_adapter(
            hardware_type, hardware_cfg, config, raw_sdk_robot_factory
        )
        if adapter is not None:
            return adapter

        # legacy hard-coded paths
        builder_name = cls._LEGACY_BUILDERS.get(hardware_type)
        if builder_name is not None:
            return getattr(cls, builder_name)(
                hardware_cfg, config, raw_sdk_robot_factory
            )

        entry_types = AdapterRegistry.available_types()
        all_types = sorted(set(LEGACY_HARDWARE_TYPES + entry_types))
        raise ValueError(
            f"Unsupported hardware.type: {hardware_type!r}. "
            f"Available: {', '.join(all_types)}"
        )

    # -- entry_point registry path -------------------------------------------

    @classmethod
    def _try_entry_point_adapter(
        cls,
        hardware_type: str,
        hardware_cfg: Mapping[str, Any],
        config: Mapping[str, Any],
        raw_sdk_robot_factory: Optional[RawSDKRobotFactory],
    ) -> Optional[IRobotHardwareAdapter]:
        adapter_cfg = hardware_cfg.get("config")
        if isinstance(adapter_cfg, Mapping) or "config" in hardware_cfg:
            if not isinstance(adapter_cfg, Mapping):
                adapter_cfg = {}
            factory = AdapterRegistry.get(hardware_type)
            if factory is not None:
                extra_kwargs: dict[str, Any] = {}
                if raw_sdk_robot_factory is not None:
                    extra_kwargs["raw_sdk_robot_factory"] = raw_sdk_robot_factory
                _config_dir = config.get("_config_dir", "")
                if _config_dir:
                    extra_kwargs["_config_dir"] = _config_dir
                adapter = factory(config=dict(adapter_cfg), **extra_kwargs)
                if not isinstance(adapter, IRobotHardwareAdapter):
                    raise TypeError(
                        f"Adapter factory for type {hardware_type!r} returned "
                        f"{type(adapter).__qualname__!r}, expected an "
                        f"IRobotHardwareAdapter instance"
                    )
                cls._register_adapter_commands(adapter, adapter_cfg)
                return adapter
        return None

    # -- legacy type builders ------------------------------------------------

    @classmethod
    def _create_lerobot_adapter(
        cls,
        hardware_cfg: Mapping[str, Any],
        config: Mapping[str, Any],
        raw_sdk_robot_factory: Optional[RawSDKRobotFactory],
    ) -> IRobotHardwareAdapter:
        from cloudrobo_r2c.robots.lerobot_hardware_adapter import (
            LeRobotHardwareAdapter,
        )

        lerobot_cfg = cls._require_mapping(hardware_cfg, "lerobot_config")
        adapter_cfg: MutableMapping[str, Any] = {"robot": dict(lerobot_cfg)}
        runtime_cfg = hardware_cfg.get("runtime")
        if isinstance(runtime_cfg, Mapping):
            adapter_cfg["runtime"] = dict(runtime_cfg)
        adapter = LeRobotHardwareAdapter(config=adapter_cfg)
        cls._register_adapter_commands(adapter, lerobot_cfg)
        return adapter

    @classmethod
    def _create_ros2_adapter(
        cls,
        hardware_cfg: Mapping[str, Any],
        config: Mapping[str, Any],
        raw_sdk_robot_factory: Optional[RawSDKRobotFactory],
    ) -> IRobotHardwareAdapter:
        from cloudrobo_r2c.robots.ros2_hardware_adapter import (
            Ros2HardwareAdapter,
        )

        ros2_cfg = cls._require_mapping(hardware_cfg, "ros2_config")
        adapter = Ros2HardwareAdapter(config={"ros2": dict(ros2_cfg)})
        cls._register_adapter_commands(adapter, ros2_cfg)
        return adapter

    @classmethod
    def _create_raw_sdk_adapter(
        cls,
        hardware_cfg: Mapping[str, Any],
        config: Mapping[str, Any],
        raw_sdk_robot_factory: Optional[RawSDKRobotFactory],
    ) -> IRobotHardwareAdapter:
        from cloudrobo_r2c.robots.vendor_sdk_hardware_adapter import (
            VendorSDKHardwareAdapter,
        )

        raw_sdk_cfg = cls._require_mapping(hardware_cfg, "raw_sdk_config")
        if raw_sdk_robot_factory is None:
            raise ValueError(
                "raw_sdk_robot_factory is required when hardware.type is 'raw_sdk'"
            )
        adapter = VendorSDKHardwareAdapter(
            config=dict(raw_sdk_cfg),
            robot_factory=raw_sdk_robot_factory,
        )
        cls._register_adapter_commands(adapter, raw_sdk_cfg)
        return adapter

    @classmethod
    def _create_ur5e_adapter(
        cls,
        hardware_cfg: Mapping[str, Any],
        config: Mapping[str, Any],
        raw_sdk_robot_factory: Optional[RawSDKRobotFactory],
    ) -> IRobotHardwareAdapter:
        from cloudrobo_r2c.robots.ur5e import UR5eHardwareAdapter

        ur5e_cfg = cls._require_mapping(hardware_cfg, "ur5e_config")
        adapter = UR5eHardwareAdapter(config=dict(ur5e_cfg))
        cls._register_adapter_commands(adapter, ur5e_cfg)
        return adapter

    @classmethod
    def _create_zenoh_ros1_adapter(
        cls,
        hardware_cfg: Mapping[str, Any],
        config: Mapping[str, Any],
        raw_sdk_robot_factory: Optional[RawSDKRobotFactory],
    ) -> IRobotHardwareAdapter:
        from cloudrobo_r2c.robots.zenoh_ros1_adapter import (
            ZenohRos1HardwareAdapter,
        )

        zenoh_cfg = cls._require_mapping(hardware_cfg, "zenoh_ros1_config")
        adapter = ZenohRos1HardwareAdapter(config=dict(zenoh_cfg))
        cls._register_adapter_commands(adapter, zenoh_cfg)
        return adapter

    @classmethod
    def _create_flexiv_adapter(
        cls,
        hardware_cfg: Mapping[str, Any],
        config: Mapping[str, Any],
        raw_sdk_robot_factory: Optional[RawSDKRobotFactory],
    ) -> IRobotHardwareAdapter:
        from cloudrobo_r2c.robots.flexiv_hardware_adapter import (
            FlexivHardwareAdapter,
        )

        flexiv_cfg = cls._require_mapping(hardware_cfg, "flexiv_config")
        adapter = FlexivHardwareAdapter(config=dict(flexiv_cfg))
        cls._register_adapter_commands(adapter, flexiv_cfg)
        return adapter

    @classmethod
    def _create_moz1_adapter(
        cls,
        hardware_cfg: Mapping[str, Any],
        config: Mapping[str, Any],
        raw_sdk_robot_factory: Optional[RawSDKRobotFactory],
    ) -> IRobotHardwareAdapter:
        from cloudrobo_r2c.robots.moz1_hardware_adapter import (
            MozHardwareAdapter,
        )

        moz_cfg = cls._require_mapping(hardware_cfg, "moz1_config")
        adapter = MozHardwareAdapter(config=dict(moz_cfg))
        cls._register_adapter_commands(adapter, moz_cfg)
        return adapter

    @classmethod
    def _create_playback_adapter(
        cls,
        hardware_cfg: Mapping[str, Any],
        config: Mapping[str, Any],
        raw_sdk_robot_factory: Optional[RawSDKRobotFactory],
    ) -> IRobotHardwareAdapter:
        from cloudrobo_r2c.robots.playback_adapter import PlaybackRobotAdapter

        cfg = cls._require_mapping(hardware_cfg, "playback_config")
        adapter = PlaybackRobotAdapter(config=dict(cfg))
        cls._register_adapter_commands(adapter, cfg)
        return adapter

    @classmethod
    def _create_dummy_adapter(
        cls,
        hardware_cfg: Mapping[str, Any],
        config: Mapping[str, Any],
        raw_sdk_robot_factory: Optional[RawSDKRobotFactory],
    ) -> IRobotHardwareAdapter:
        from cloudrobo_r2c.robots.dummy_robot import DummyRobotHardwareAdapter

        dummy_cfg = hardware_cfg.get("dummy_config")
        if dummy_cfg is None:
            dummy_cfg = {}
        if not isinstance(dummy_cfg, Mapping):
            raise ValueError("dummy_config must be a mapping when provided")
        adapter = DummyRobotHardwareAdapter(
            config=dict(
                dummy_cfg,
                _config_dir=config.get("_config_dir", ""),
            )
        )
        cls._register_adapter_commands(adapter, dummy_cfg)
        return adapter

    @classmethod
    def _create_custom_adapter(
        cls,
        hardware_cfg: Mapping[str, Any],
        config: Mapping[str, Any],
        raw_sdk_robot_factory: Optional[RawSDKRobotFactory],
    ) -> IRobotHardwareAdapter:
        custom_cls_path = str(hardware_cfg.get("class_path", "")).strip()
        if not custom_cls_path:
            raise ValueError(
                "hardware.class_path is required when hardware.type is 'custom'"
            )
        warnings.warn(
            "hardware.type='custom' with class_path is deprecated. "
            "Register your adapter as an entry_point under the "
            "'cloudrobo_r2c.adapters' group instead. "
            "See https://setuptools.pypa.io/en/latest/userguide/"
            "entry_point.html for details.",
            DeprecationWarning,
            stacklevel=2,
        )
        adapter_cls = ensure_subclass(
            load_class(custom_cls_path),
            IRobotHardwareAdapter,
            path=custom_cls_path,
        )
        init_kwargs = cls._extract_custom_init_kwargs(hardware_cfg)
        adapter = adapter_cls(config=init_kwargs)
        cls._register_adapter_commands(adapter, hardware_cfg)
        return adapter

    # -- dispatch table ------------------------------------------------------

    _LEGACY_BUILDERS: dict[str, str] = {
        "lerobot": "_create_lerobot_adapter",
        "ros2": "_create_ros2_adapter",
        "raw_sdk": "_create_raw_sdk_adapter",
        "ur5e_rtde": "_create_ur5e_adapter",
        "zenoh_ros1": "_create_zenoh_ros1_adapter",
        "flexiv": "_create_flexiv_adapter",
        "playback": "_create_playback_adapter",
        "dummy": "_create_dummy_adapter",
        "custom": "_create_custom_adapter",
    }

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _warn_legacy_config(
        hardware_type: str,
        hardware_cfg: Mapping[str, Any],
    ) -> None:
        """Emit DeprecationWarning when old ``xxx_config`` key is detected."""
        legacy_key = f"{hardware_type}_config"
        if legacy_key in hardware_cfg:
            warnings.warn(
                f"hardware.{legacy_key} is deprecated. "
                f"Use hardware.config instead. "
                f"Example: hardware.type={hardware_type!r} with "
                f"hardware.config={{...}}",
                DeprecationWarning,
                stacklevel=3,
            )

    @staticmethod
    def _extract_hardware_mapping(config: Mapping[str, Any]) -> Mapping[str, Any]:
        hardware_cfg = config.get("hardware")
        if hardware_cfg is None:
            return config
        if not isinstance(hardware_cfg, Mapping):
            raise ValueError("hardware must be a mapping when provided")
        return hardware_cfg

    @staticmethod
    def _require_mapping(config: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        value = config.get(key)
        if not isinstance(value, Mapping):
            raise ValueError(f"{key} must be a mapping")
        return value

    @staticmethod
    def _register_adapter_commands(
        adapter: IRobotHardwareAdapter,
        adapter_cfg: Mapping[str, Any],
    ) -> None:
        """Instantiate commands declared in ``hardware.config.commands``.

        Each entry is keyed by an instance name (e.g. ``home_zero``) and
        must contain a ``type`` field that references a command class
        previously registered via ``adapter.register_command_class()``.
        The factory instantiates ``CommandClass(adapter, config)`` and
        registers the result under the instance name.

        Raises :class:`ValueError` on any configuration error so users
        see problems at startup, not silently at runtime.
        """
        commands_config = (
            adapter_cfg.get("commands") if isinstance(adapter_cfg, Mapping) else None
        )
        if not isinstance(commands_config, Mapping):
            return  # no commands configured — nothing to do

        classes: dict = getattr(adapter, "_registered_command_classes", {})

        for instance_name, raw_cmd_cfg in commands_config.items():
            _validate_command_name(instance_name, "Command instance name")
            if not isinstance(raw_cmd_cfg, Mapping):
                raise ValueError(
                    f"Command %r config must be a mapping, "
                    f"got {type(raw_cmd_cfg).__name__}"
                )

            type_name = raw_cmd_cfg.get("type")
            if not type_name:
                raise ValueError(
                    f"Command %r is missing required 'type' field "
                    f"(registered types: {sorted(classes.keys()) or ['(none)']})"
                )
            if not isinstance(type_name, str):
                raise ValueError(
                    f"Command %r 'type' must be a string, "
                    f"got {type(type_name).__name__}"
                )
            type_name = type_name.strip()
            _validate_command_name(type_name, f"Command {instance_name!r} type")

            cmd_cls = classes.get(type_name)
            if cmd_cls is None:
                raise ValueError(
                    f"Command type {type_name!r} is not registered on "
                    f"adapter {type(adapter).__name__!r}. "
                    f"Registered types: {sorted(classes.keys()) or ['(none)']}"
                )

            cmd_cfg = dict(raw_cmd_cfg)
            cmd_cfg.pop("type", None)  # metadata field, not a command param
            adapter.register_command(
                instance_name, cmd_cls(adapter=adapter, config=cmd_cfg)
            )

    @staticmethod
    def _extract_custom_init_kwargs(
        hardware_cfg: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        custom_cfg = hardware_cfg.get("custom_config")
        if custom_cfg is None:
            return {}
        if not isinstance(custom_cfg, Mapping):
            raise ValueError("hardware.custom_config must be a mapping when provided")
        return dict(custom_cfg)