"""LeRobot-specific :class:`AdapterCommand` for the ``go_home`` preset.

LeRobot robots are driven through ``robot.send_action({...})`` with a
joint-name → value mapping.  Two forms are supported:

- ``joints`` as a **dict** → forwarded verbatim to ``adapter.send_action``.
- ``joints`` as a **list** → requires ``joint_names`` in config; the list
  values are mapped to ``{joint_names[i]: joints[i]}`` in order.

``pose_euler`` / ``pose_quat`` are NOT supported (LeRobot has no IK)
and are rejected with ``ValueError``.  ``gripper`` is ignored silently
(LeRobot has no standalone gripper channel).
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional, Sequence

from cloudrobo_r2c.robots.commands.base import AdapterCommand, _validate_go_home_inputs

logger = logging.getLogger(__name__)

_RECOGNISED_KEYS = {
    "pose_euler", "pose_quat", "joints", "gripper", "joint_names",
}


class LeRobotGoHomeCommand(AdapterCommand):
    """LeRobot go_home — dict or list ``joints`` → ``robot.send_action``.

    Config keys::

        joints: dict | list     # target positions (required)
        joint_names: list[str]  # joint name order when joints is a list
        gripper:                (silently ignored)

    Logs joint state before and after the move so users can observe the
    home operation taking effect.

    Constraints:
        * ``joints`` is required; ``pose_euler`` / ``pose_quat`` are
          not supported (LeRobot has no IK) and raise at startup.
        * When ``joints`` is a list, ``joint_names`` must also be
          provided and match the list length.
        * Unknown YAML keys raise :class:`ValueError` at startup.
    """

    def __init__(self, adapter: Any, config: dict[str, Any]) -> None:
        super().__init__(adapter, config)

        # ── startup validation: fail fast on misconfigured YAML ──────
        unknown = set(self.config.keys()) - _RECOGNISED_KEYS
        if unknown:
            raise ValueError(
                f"LeRobotGoHomeCommand: unknown keys: {sorted(unknown)}. "
                f"Allowed: {', '.join(sorted(_RECOGNISED_KEYS))}."
            )

        # pose_euler / pose_quat are not supported by LeRobot
        if "pose_euler" in self.config:
            raise ValueError(
                "LeRobotGoHomeCommand: pose_euler is not supported "
                "(LeRobot has no IK). Use 'joints' as a dict or list."
            )
        if "pose_quat" in self.config:
            raise ValueError(
                "LeRobotGoHomeCommand: pose_quat is not supported "
                "(LeRobot has no IK). Use 'joints' as a dict or list."
            )

        # joints is required
        joints = self.config.get("joints")
        if joints is None:
            raise ValueError(
                "LeRobotGoHomeCommand: 'joints' is required. "
                "Provide joints as a dict {name: value} or "
                "list [j1, j2, ...] with joint_names."
            )

        # If joints is a list, joint_names must be present and match length
        if isinstance(joints, (list, tuple)) and not isinstance(
            joints, (str, bytes)
        ):
            joint_names = self.config.get("joint_names")
            if not isinstance(joint_names, (list, tuple)) or isinstance(
                joint_names, (str, bytes)
            ):
                raise ValueError(
                    "LeRobotGoHomeCommand: 'joints' is a list; "
                    "'joint_names' must be a non-empty list of joint name strings."
                )
            if len(joint_names) != len(joints):
                raise ValueError(
                    f"LeRobotGoHomeCommand: len(joint_names)="
                    f"{len(joint_names)} != len(joints)={len(joints)}."
                )
        elif not isinstance(joints, dict):
            raise ValueError(
                f"LeRobotGoHomeCommand: 'joints' must be a dict or list, "
                f"got {type(joints).__name__}."
            )

    def execute(self, **kwargs: Any) -> None:
        # ── 1. resolve: kwargs override YAML ─────────────────────────
        pose_euler: Optional[Sequence[float]] = kwargs.get(
            "pose_euler", self.config.get("pose_euler")
        )
        pose_quat: Optional[Sequence[float]] = kwargs.get(
            "pose_quat", self.config.get("pose_quat")
        )
        joints: Any = kwargs.get("joints", self.config.get("joints"))

        # ── 2. runtime safety: reject unsupported motion keys ─────────
        if joints is None:
            raise ValueError(
                "go_home for LeRobot requires 'joints' as a dict or list. "
                "pose_euler / pose_quat are not supported (LeRobot has no IK)."
            )
        if pose_euler is not None or pose_quat is not None:
            raise ValueError(
                "go_home for LeRobot only supports 'joints'. "
                "pose_euler / pose_quat are not supported (LeRobot has no IK)."
            )

        # ── 3. log pre-move state ────────────────────────────────────
        try:
            before = self.adapter.get_observation()
            before_parts = [
                f"{k}={v:.4f}"
                for k, v in before.items()
                if isinstance(v, (int, float))
            ]
            logger.info(
                "[go_home] before: %s",
                ", ".join(before_parts) if before_parts else str(before),
            )
        except Exception:
            logger.debug("[go_home] failed to read pre-move state", exc_info=True)

        # ── 4. dispatch joints → send_action ─────────────────────────
        if isinstance(joints, Mapping):
            payload = dict(joints)
            target_parts = [f"{k} → {v:.4f}" for k, v in payload.items()]
            logger.info("[go_home] target: %s", ", ".join(target_parts))
            self.adapter.send_action(payload)
            self._log_post_move()
            return

        if isinstance(joints, (list, tuple, Sequence)) and not isinstance(
            joints, (str, bytes)
        ):
            joint_names: Any = self.config.get("joint_names")
            if not isinstance(joint_names, (list, tuple, Sequence)) or isinstance(
                joint_names, (str, bytes)
            ):
                raise ValueError(
                    "LeRobotGoHomeCommand: 'joints' is a list; "
                    "'joint_names' must be a list of joint name strings."
                )
            if len(joint_names) != len(joints):
                raise ValueError(
                    f"LeRobotGoHomeCommand: len(joint_names)="
                    f"{len(joint_names)} != len(joints)={len(joints)}."
                )
            payload = {
                str(joint_names[i]): float(joints[i])
                for i in range(len(joints))
            }
            target_parts = [f"{name} → {val:.4f}" for name, val in payload.items()]
            logger.info("[go_home] target: %s", ", ".join(target_parts))
            self.adapter.send_action(payload)
            self._log_post_move()
            return

        raise ValueError(
            f"LeRobotGoHomeCommand: 'joints' must be a dict or list, "
            f"got {type(joints).__name__}."
        )

    def _log_post_move(self) -> None:
        """Log joint state after a successful home move."""
        try:
            after = self.adapter.get_observation()
            after_parts = [
                f"{k}={v:.4f}"
                for k, v in after.items()
                if isinstance(v, (int, float))
            ]
            logger.info(
                "[go_home] after:  %s",
                ", ".join(after_parts) if after_parts else str(after),
            )
        except Exception:
            logger.debug(
                "[go_home] failed to read post-move state", exc_info=True
            )
