"""Example command implementations for MyRobot adapter."""

from __future__ import annotations

from typing import Any

from cloudrobo_r2c.robots.commands.base import AdapterCommand


class MyRobotHomeCommand(AdapterCommand):
    """Send robot to home position via ``send_action``."""

    def execute(self, **kwargs: Any) -> None:
        target = self.config.get("joints")
        if target is not None:
            self.adapter.send_action({"joint_target": list(target)})
