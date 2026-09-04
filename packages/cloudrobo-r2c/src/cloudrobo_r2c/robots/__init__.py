"""Robot-specific hardware adapters.

All adapter imports are lazy.  Use ``RobotFactory.create_hardware_adapter()``
as the primary API -- individual adapter classes are re-exported for convenience
but only imported on first access.
"""

from __future__ import annotations

from typing import Any

from .robot_factory import AdapterRegistry, RobotFactory

__all__ = [
    "LeRobotHardwareAdapter",
    "Ros2HardwareAdapter",
    "VendorSDKHardwareAdapter",
    "DummyRobotHardwareAdapter",
    "UR5eHardwareAdapter",
    "PlaybackRobotAdapter",
    "AdapterRegistry",
    "RobotFactory",
]

_MODULE_MAP: dict[str, str] = {
    "LeRobotHardwareAdapter": ".lerobot_hardware_adapter",
    "Ros2HardwareAdapter": ".ros2_hardware_adapter",
    "VendorSDKHardwareAdapter": ".vendor_sdk_hardware_adapter",
    "DummyRobotHardwareAdapter": ".dummy_robot",
    "UR5eHardwareAdapter": ".ur5e",
    "PlaybackRobotAdapter": ".playback_adapter",
    "Q25HardwareAdapter": ".q25_hardware_adapter",
}


def __getattr__(name: str) -> Any:
    if name in _MODULE_MAP:
        import importlib

        module = importlib.import_module(_MODULE_MAP[name], __package__)
        attr = getattr(module, name)
        # Cache in module dict so __getattr__ is only called once per name
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
