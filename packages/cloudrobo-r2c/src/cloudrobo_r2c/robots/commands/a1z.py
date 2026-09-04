"""A1Z-specific :class:`AdapterCommand` implementations.

- ``A1ZGoHomeCommand`` — drives ``adapter.move_to(joints=...)`` then
  ``adapter.set_gripper(...)``, following the generic 4-key YAML schema.
- ``A1ZEstopCommand`` — engages / releases the soft emergency stop.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from cloudrobo_r2c.robots.commands.base import (
    AdapterCommand,
    _validate_go_home_inputs,
    _validate_gripper,
)

logger = logging.getLogger(__name__)

_RECOGNISED_KEYS = {"pose_euler", "pose_quat", "joints", "gripper"}


class A1ZGoHomeCommand(AdapterCommand):
    """A1Z go_home — ``joints`` only → ``adapter.move_to`` + ``adapter.set_gripper``.

    Config keys::

        joints:  [j1..j6]       # target joint angles (rad), required
        gripper: float | "open" | "close"   # optional

    ``pose_euler`` / ``pose_quat`` are rejected (A1Z has no built-in IK).

    Execution order: move_to first, then set_gripper (arm-before-gripper).
    """

    def __init__(self, adapter: Any, config: dict[str, Any]) -> None:
        super().__init__(adapter, config)

        # Fail fast on unknown keys
        unknown = set(self.config.keys()) - _RECOGNISED_KEYS
        if unknown:
            raise ValueError(
                f"A1ZGoHomeCommand: unknown keys: {sorted(unknown)}. "
                f"Allowed: {', '.join(sorted(_RECOGNISED_KEYS))}."
            )

        if "pose_euler" in self.config:
            raise ValueError(
                "A1ZGoHomeCommand: pose_euler is not supported "
                "(A1Z has no built-in IK). Use 'joints' as a list of 6 floats (rad)."
            )
        if "pose_quat" in self.config:
            raise ValueError(
                "A1ZGoHomeCommand: pose_quat is not supported "
                "(A1Z has no built-in IK). Use 'joints' as a list of 6 floats (rad)."
            )

        joints = self.config.get("joints")
        if joints is None:
            raise ValueError(
                "A1ZGoHomeCommand: 'joints' is required. "
                "Provide a list of 6 floats in radians."
            )
        if not isinstance(joints, (list, tuple)) or len(joints) != 6:
            raise ValueError(
                f"A1ZGoHomeCommand: 'joints' must be a list of 6 floats, "
                f"got {joints!r}"
            )

        gripper = self.config.get("gripper")
        if gripper is not None:
            _validate_gripper(gripper)

    def execute(self, **kwargs: Any) -> None:
        joints = kwargs.get("joints", self.config.get("joints"))
        gripper = kwargs.get("gripper", self.config.get("gripper"))

        if joints is None:
            raise ValueError("A1Z go_home: 'joints' is required")

        joints_list = [float(v) for v in joints]
        if len(joints_list) != 6:
            raise ValueError(
                f"A1Z go_home: 'joints' must have 6 elements, got {len(joints_list)}"
            )

        logger.info("[go_home] moving to joints: %s", [f"{v:.3f}" for v in joints_list])
        self.adapter.move_to(joints=joints_list)

        if gripper is not None:
            if isinstance(gripper, str):
                logger.info("[go_home] setting gripper action: %s", gripper)
                self.adapter.set_gripper(action=gripper.strip().lower())
            else:
                logger.info("[go_home] setting gripper width: %s", gripper)
                self.adapter.set_gripper(width=float(gripper))


class A1ZEstopCommand(AdapterCommand):
    """A1Z soft emergency stop — engage or release the estop latch.

    Config keys::

        action: "estop" | "release"   # default "estop"

    When engaged, the arm holds position with gravity compensation but
    rejects all new position commands.  ``release`` restores normal PD
    control at the current pose.
    """

    requires_pause = False  # always executable

    _RECOGNISED_KEYS = {"action"}

    def __init__(self, adapter: Any, config: dict[str, Any]) -> None:
        super().__init__(adapter, config)
        unknown = set(self.config.keys()) - self._RECOGNISED_KEYS
        if unknown:
            raise ValueError(
                f"A1ZEstopCommand: unknown keys: {sorted(unknown)}. "
                f"Allowed: {', '.join(sorted(self._RECOGNISED_KEYS))}."
            )
        action = self.config.get("action", "estop")
        if action not in ("estop", "release"):
            raise ValueError(
                f"A1ZEstopCommand: 'action' must be 'estop' or 'release', "
                f"got {action!r}"
            )

    def execute(self, **kwargs: Any) -> None:
        action = kwargs.get("action", self.config.get("action", "estop"))
        if action == "release":
            self.adapter.release()
        else:
            self.adapter.estop()
