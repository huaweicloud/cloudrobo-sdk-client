"""Generic adapter commands — ``GoHomeCommand`` (4-key YAML → adapter)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from cloudrobo_r2c.robots.commands.base import (
    AdapterCommand,
    _validate_go_home_inputs,
    _validate_gripper,
)

logger = logging.getLogger(__name__)

_RECOGNISED_KEYS = {"pose_euler", "pose_quat", "joints", "gripper"}


class GoHomeCommand(AdapterCommand):
    """Canonical go_home dispatch — 4-key YAML → ``adapter.send_action``.

    Supported YAML keys (exactly one of the three motion keys):
        pose_euler: [x, y, z, roll, pitch, yaw]   # metres + radians
        pose_quat:  [x, y, z, qw, qx, qy, qz]    # metres + quaternion
        joints:     [j1, j2, ..., jN]             # radians
        gripper:    float | "open" | "close"      (optional)

    Constraints:
        * **Exactly-one** of pose_euler / pose_quat / joints is required.
          Supplying multiple or none raises ``ValueError`` at startup.
        * **Arm-before-gripper** atomicity: the arm command is sent first.
          If the arm command fails, the gripper is never moved.
        * Unknown YAML keys raise :class:`ValueError` at startup.

    Validation happens at construction time so misconfigured YAML fails
    fast at startup rather than silently at runtime.
    """

    def __init__(self, adapter: Any, config: dict[str, Any]) -> None:
        super().__init__(adapter, config)

        # ── startup validation: fail fast on misconfigured YAML ──────
        unknown = set(self.config.keys()) - _RECOGNISED_KEYS
        if unknown:
            raise ValueError(
                f"GoHomeCommand: unknown keys: {sorted(unknown)}. "
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
        # ── 1. resolve values: kwargs override YAML config ───────────
        pose_euler: Optional[Sequence[float]] = kwargs.get(
            "pose_euler", self.config.get("pose_euler")
        )
        pose_quat: Optional[Sequence[float]] = kwargs.get(
            "pose_quat", self.config.get("pose_quat")
        )
        joints: Any = kwargs.get("joints", self.config.get("joints"))
        gripper: Any = kwargs.get("gripper", self.config.get("gripper"))

        # ── 2. exactly-one motion-target constraint ──────────────────
        pose_euler, pose_quat, joints = _validate_go_home_inputs(
            pose_euler=(
                _as_floats(pose_euler, 6, "pose_euler") if pose_euler is not None else None
            ),
            pose_quat=(
                _as_floats(pose_quat, 7, "pose_quat") if pose_quat is not None else None
            ),
            joints=(
                _as_list(joints, "joints") if joints is not None else None
            ),
            gripper=gripper,
        )

        # ── 3. build arm command ─────────────────────────────────────
        arm_cmd: Dict[str, Any] = {}
        if pose_euler is not None:
            arm_cmd["pose_euler"] = list(pose_euler)
        elif pose_quat is not None:
            arm_cmd["pose_quat"] = list(pose_quat)
        else:
            arm_cmd["joint_target"] = list(joints)  # type: ignore[arg-type]

        # ── 4. execute arm-first, then gripper (atomic) ──────────────
        logger.info("[go_home] sending arm command: %s", arm_cmd)
        self.adapter.send_action(arm_cmd)

        if gripper is not None:
            gripper_cmd: Dict[str, Any] = {}
            if isinstance(gripper, str):
                gripper_cmd["gripper_action"] = gripper.strip().lower()
            else:
                gripper_cmd["gripper"] = float(gripper)
            logger.info("[go_home] arm ok, sending gripper command: %s", gripper_cmd)
            self.adapter.send_action(gripper_cmd)


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------


def _as_floats(value: Any, expected_len: int, name: str) -> List[float]:
    if not isinstance(value, (list, tuple)) or len(value) != expected_len:
        raise ValueError(
            f"{name} must be a list of {expected_len} floats, got {value!r}"
        )
    return [float(v) for v in value]


def _as_list(value: Any, name: str) -> List[float]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a list, got {type(value).__name__}")
    return [float(v) for v in value]
