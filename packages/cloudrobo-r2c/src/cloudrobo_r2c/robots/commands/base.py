from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Sequence, Tuple

# Recognised motion-target keys for go_home commands.
_MOTION_KEYS = ("pose_euler", "pose_quat", "joints")


def _validate_go_home_inputs(
    pose_euler: Optional[Sequence[float]],
    pose_quat: Optional[Sequence[float]],
    joints: Any,
    gripper: Any,
) -> Tuple[Optional[Sequence[float]], Optional[Sequence[float]], Any]:
    """Validate go_home motion-target inputs and return the canonical triple.

    1. **Exactly-one constraint**: one and only one of *pose_euler*,
       *pose_quat*, *joints* must be set (non-``None``).
    2. *joints* is returned as-is (list, dict, or other adapter-specific
       type).

    Raises :class:`ValueError` on violation.
    """
    provided = [
        key
        for key, val in zip(
            _MOTION_KEYS, (pose_euler, pose_quat, joints)
        )
        if val is not None
    ]
    if len(provided) == 0:
        raise ValueError(
            "go_home requires exactly one of pose_euler / pose_quat / joints"
        )
    if len(provided) > 1:
        raise ValueError(
            f"go_home: only one of pose_euler / pose_quat / joints is "
            f"allowed, but got: {', '.join(provided)}"
        )
    return pose_euler, pose_quat, joints


def _validate_gripper(gripper: Any) -> None:
    """Validate a gripper config value at startup time.

    Accepts numeric values (float/int) or the strings ``"open"`` /
    ``"close"``.  Raises :class:`ValueError` for anything else.
    """
    if isinstance(gripper, (int, float)):
        return
    if isinstance(gripper, str):
        if gripper.strip().lower() in ("open", "close"):
            return
        raise ValueError(
            f"gripper must be a number, 'open', or 'close', got {gripper!r}"
        )
    raise ValueError(
        f"gripper must be a number, 'open', or 'close', "
        f"got {type(gripper).__name__}"
    )


class AdapterCommand(ABC):
    """A named operation instantiated per YAML command block.

    Subclasses implement :meth:`execute`.  The constructor receives the
    adapter instance and the YAML command-block dict as *config*.

    Command classes are registered on the adapter via
    :meth:`IRobotHardwareAdapter.register_command_class` and instantiated
    by the factory for each ``commands.<instance_name>`` entry whose
    ``type`` field matches the registered name.

    Class Attributes:
        requires_pause: If ``True`` (default), the keyboard dispatcher
            will only execute this command when the control flow is
            paused (:kbd:`Space`).  Set to ``False`` for commands
            that should always be executable (e.g. emergency stop).
    """

    requires_pause: bool = True

    def __init__(self, adapter: Any, config: dict[str, Any]) -> None:
        self.adapter = adapter
        self.config = config

    @abstractmethod
    def execute(self, **kwargs: Any) -> None:
        """Execute the command with runtime *kwargs* (override YAML values)."""
        raise NotImplementedError
