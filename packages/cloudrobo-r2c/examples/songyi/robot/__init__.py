"""SONGYI dual-arm robot integration (DucoCobot Thrift RPC)."""

from .songyi_adapter import SongyiHardwareAdapter, SongyiMockAdapter
from .cameras import CameraError, CameraManager
from .collector import RuntimeCollector
from .duco import DucoArm, DucoArmError
from .mock_duco import MockDucoArm, MockDucoAuxAxis
from .state_cache import StateCache
from .teleop import TeleopController

__all__ = [
    "SongyiHardwareAdapter",
    "SongyiMockAdapter",
    "CameraError",
    "CameraManager",
    "RuntimeCollector",
    "DucoArm",
    "DucoArmError",
    "MockDucoArm",
    "MockDucoAuxAxis",
    "StateCache",
    "TeleopController",
]
