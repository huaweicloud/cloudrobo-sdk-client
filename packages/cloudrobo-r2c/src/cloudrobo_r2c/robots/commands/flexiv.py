"""Flexiv-specific :class:`AdapterCommand` for the ``go_home`` preset.

Calls Flexiv adapter internal methods directly since the adapter's
:meth:`send_action` does not understand the 4-key go_home dict format.
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


def _as_floats(value: Any, expected_len: int, name: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != expected_len:
        raise ValueError(
            f"{name} must be a list of {expected_len} floats, got {value!r}"
        )
    return [float(v) for v in value]


def _as_list(value: Any, name: str) -> list[float]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a list, got {type(value).__name__}")
    return [float(v) for v in value]


class FlexivGoHomeCommand(AdapterCommand):
    """Flexiv go_home — 4-key YAML → adapter internal motion methods.

    Config keys (exactly one of the three motion keys required):
        pose_euler: [x, y, z, roll, pitch, yaw]  (metres + radians)
        pose_quat:  [x, y, z, qw, qx, qy, qz]   (metres + unit quaternion)
        joints:     [j1, ..., j6]                 (radians)
        gripper:    float | "open" | "close"      (optional)

    Constraints:
        * **Exactly-one** of pose_euler / pose_quat / joints.
        * **Arm-before-gripper**: arm motion completes first; gripper
          is only moved after a successful arm move.
        * Unknown YAML keys raise :class:`ValueError` at startup.
    """

    def __init__(self, adapter: Any, config: dict[str, Any]) -> None:
        super().__init__(adapter, config)

        # ── startup validation: fail fast on misconfigured YAML ──────
        unknown = set(self.config.keys()) - _RECOGNISED_KEYS
        if unknown:
            raise ValueError(
                f"FlexivGoHomeCommand: unknown keys: {sorted(unknown)}. "
                f"Allowed: {', '.join(sorted(_RECOGNISED_KEYS))}."
            )

        pose_euler = self.config.get("pose_euler")
        pose_quat = self.config.get("pose_quat")
        joints = self.config.get("joints")
        gripper = self.config.get("gripper")

        # Exactly-one motion-target constraint
        _validate_go_home_inputs(
            pose_euler=pose_euler,
            pose_quat=pose_quat,
            joints=joints,
            gripper=gripper,
        )

        # Format validation of whichever motion key is present
        if pose_euler is not None:
            _as_floats(pose_euler, 6, "pose_euler")
        elif pose_quat is not None:
            _as_floats(pose_quat, 7, "pose_quat")
        else:
            _as_list(joints, "joints")

        # Gripper format validation (optional key)
        if gripper is not None:
            _validate_gripper(gripper)

    def execute(self, **kwargs: Any) -> None:
        # ── 1. resolve: kwargs override YAML.  Use explicit None
        #        checks (not ``or``) so falsy values like 0 are kept. ──
        pose_euler: Optional[Sequence[float]] = kwargs.get(
            "pose_euler",
            self.config.get("pose_euler"),
        )
        pose_quat: Optional[Sequence[float]] = kwargs.get(
            "pose_quat",
            self.config.get("pose_quat"),
        )
        joints: Any = kwargs.get("joints", self.config.get("joints"))
        gripper: Any = kwargs.get("gripper", self.config.get("gripper"))

        # ── 2. exactly-one motion-target constraint ──────────────────
        _validate_go_home_inputs(
            pose_euler=pose_euler,
            pose_quat=pose_quat,
            joints=joints,
            gripper=gripper,
        )

        adapter = self.adapter
        adapter._ensure_connected()

        # ── 3. arm motion (one path only) ─────────────────────────────
        if joints is not None:
            joints_list = [float(v) for v in joints]
            logger.info("[go_home] moving to joints: %s", joints_list)
            adapter._move_to_joint_positions(joints_list)
        elif pose_euler is not None:
            pose_list = [float(v) for v in pose_euler]
            logger.info("[go_home] moving to pose_euler: %s", pose_list)
            adapter.move_to_pose(pose_list)
        else:
            # pose_quat guaranteed non-None by _validate_go_home_inputs
            pose_list = [float(v) for v in pose_quat]  # type: ignore[arg-type]
            position = pose_list[:3]
            quat_wxyz = pose_list[3:7]
            logger.info("[go_home] moving to pose_quat: %s", pose_list)
            adapter._send_cartesian_pose(position, quat_wxyz=quat_wxyz)

        # ── 4. gripper (after arm success) ───────────────────────────
        if gripper is not None:
            if isinstance(gripper, str):
                action = gripper.strip().lower()
                if action == "open":
                    key = "gripper_open_width"
                elif action == "close":
                    key = "gripper_close_width"
                else:
                    raise ValueError(
                        f"gripper must be a number, 'open', or 'close', "
                        f"got {gripper!r}"
                    )
                width = adapter.config.get(key)
                if width is None:
                    raise ValueError(
                        f"go_home gripper={action!r}: adapter config key "
                        f"{key!r} is required"
                    )
                logger.info("[go_home] gripper %s → %.4f m", action, float(width))
                adapter._move_gripper(float(width))
            else:
                width = float(gripper)
                logger.info("[go_home] gripper → %.4f m", width)
                adapter._move_gripper(width)
