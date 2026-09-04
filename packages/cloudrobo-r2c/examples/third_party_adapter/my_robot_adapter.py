"""Minimal third-party robot adapter demonstrating entry_point registration.

Usage (after installing this package)::

    from cloudrobo_r2c.robots import RobotFactory

    adapter = RobotFactory.create_hardware_adapter({
        "hardware": {
            "type": "my_robot",
            "config": {
                "ip": "192.168.1.100",
                "port": 502,
                "joint_names": ["j1", "j2", "j3", "j4", "j5", "j6"],
            },
        }
    })
    adapter.connect()
    obs = adapter.get_observation()
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, List, Mapping

from cloudrobo_r2c.core.interfaces import IRobotHardwareAdapter

logger = logging.getLogger(__name__)


def create_my_robot_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> IRobotHardwareAdapter:
    """Entry_point factory — called by ``AdapterRegistry`` via ``RobotFactory``."""
    return MyRobotHardwareAdapter(config=dict(config))


@dataclass
class MyRobotHardwareAdapter(IRobotHardwareAdapter):
    """Example third-party adapter wrapping ``MyRobot`` SDK."""

    config: Mapping[str, Any]

    _connected: bool = field(default=False, init=False, repr=False)
    _joint_names: List[str] = field(default_factory=list, init=False, repr=False)
    _state: List[float] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        from my_robot_commands import MyRobotHomeCommand
        self.register_command_class("go_home", MyRobotHomeCommand)

    def connect(self) -> None:
        if self._connected:
            return
        self._joint_names = list(self.config.get("joint_names", []))
        if not self._joint_names:
            self._joint_names = [
                "joint_1", "joint_2", "joint_3",
                "joint_4", "joint_5", "joint_6",
            ]
        self._state = [0.0] * len(self._joint_names)
        self._connected = True
        logger.info(
            "MyRobot connected to %s:%s",
            self.config.get("ip"), self.config.get("port"),
        )

    def disconnect(self) -> None:
        self._connected = False
        logger.info("MyRobot disconnected")

    def get_observation(self) -> Mapping[str, Any]:
        if not self._connected:
            raise RuntimeError("Not connected")
        self._state = [v + 0.01 for v in self._state]
        return dict(zip(self._joint_names, self._state))

    def send_action(self, command: Mapping[str, Any]) -> None:
        if not self._connected:
            raise RuntimeError("Not connected")
        logger.info("MyRobot action: %s", command)
