"""Config-driven teleop frame <-> device command translation."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional

from cloudrobo_r2c.core.config_mapper import ConfigDrivenMapper
from cloudrobo_r2c.core.internal.helpers import to_mapping

logger = logging.getLogger(__name__)

# teleop.proto reuses observation.proto EndEffectorPoses/EndEffectorStates:
# names are left/right (arm suffixes accepted as aliases).
_SIDE_ALIASES = {
    "left": "left",
    "right": "right",
    "left_arm": "left",
    "right_arm": "right",
}

POSE_DOF = 7


def _side_of(name: Any) -> Optional[str]:
    return _SIDE_ALIASES.get(str(name).strip().lower())


def _pose_values(pose: Any) -> List[float]:
    data = getattr(pose, "data", pose)
    return [float(v) for v in data]


def teleop_to_device(frame: Any) -> Dict[str, Any]:
    """Default TeleopFrame -> device command mapping (no configuration needed).

    Left and right are independent: only sides present in the frame are emitted.
    A side that is absent gets no field at all, so the hardware adapter keeps
    its current value for that side (no fill-in, no zero-padding).

    Returns:
        Flat command dict consumed by the adapter ``send_action``:
        ``left/right_pose_cmd_ee``, ``left/right_gripper``,
        ``joint_names`` + ``arm_cmd_joint_pos``, ``teleop_command`` (lifecycle
        frames only).  Empty dict when the frame carries no device action.
    """
    command: Dict[str, Any] = {}
    if frame is None:
        return command

    command_name = getattr(frame, "command", None)
    if command_name:
        # Lifecycle frames (start/pause/resume/finish) never drive the device.
        command["teleop_command"] = str(command_name)
        return command

    poses = getattr(frame, "end_effector_poses", None)
    if poses is not None:
        names = list(getattr(poses, "names", []) or [])
        pose_values = list(getattr(poses, "pose", []) or [])
        for name, pose in zip(names, pose_values):
            side = _side_of(name)
            if side is None:
                logger.warning("[teleop_translator] unknown pose side %r; skipped", name)
                continue
            values = _pose_values(pose)
            if len(values) != POSE_DOF:
                logger.warning(
                    "[teleop_translator] %s pose expects %d values, got %d; skipped",
                    side,
                    POSE_DOF,
                    len(values),
                )
                continue
            command[f"{side}_pose_cmd_ee"] = values

    states = getattr(frame, "end_effector_states", None)
    if states is not None:
        names = list(getattr(states, "names", []) or [])
        position = list(getattr(states, "position", []) or [])
        for name, value in zip(names, position):
            side = _side_of(name)
            if side is None:
                logger.warning("[teleop_translator] unknown trigger side %r; skipped", name)
                continue
            command[f"{side}_gripper"] = float(value)

    joint_states = getattr(frame, "joint_states", None)
    if joint_states is not None:
        joint_names = list(getattr(joint_states, "names", []) or [])
        joint_position = [float(v) for v in (getattr(joint_states, "position", []) or [])]
        if joint_names and joint_position:
            command["joint_names"] = joint_names
            command["arm_cmd_joint_pos"] = joint_position

    return command


def build_teleop_to_device_mapper(
    config_dict: Mapping[str, Any],
) -> ConfigDrivenMapper | None:
    """Build the ``teleop_to_device`` mapper from a robot config (optional section).

    Same shape as the ``r2c_to_device`` section: a ``mappings`` rule list through
    the shared :class:`ConfigDrivenMapper` engine, so a model can map the teleop
    frame onto its own ``send_action`` field names.  Returns ``None`` when the
    section is absent or empty, in which case :func:`teleop_to_device` applies.
    """
    cfg = config_dict.get("teleop_to_device", {})
    if not isinstance(cfg, Mapping):
        return None
    mappings = cfg.get("mappings")
    if isinstance(mappings, list) and mappings:
        return ConfigDrivenMapper.from_rule_mappings(mappings)
    return None


def frame_to_mapping(frame: Any) -> Dict[str, Any]:
    """TeleopFrame (or plain dict) -> mapping-rule payload.

    Reuses ``to_mapping`` for the dataclass -> dict conversion, then normalizes
    per-side channels by ``names`` so rules can address them like ``r2c_to_device``
    sources (``end_effector_poses.left.pose``).

    ``extensions`` values are decoded before ``to_mapping`` recursion: an
    ``ExtensionValue`` is self-describing and would otherwise be flattened into
    a plain dict, losing ``ext_decode``.
    """
    mapping = to_mapping(frame)
    extensions = mapping.get("extensions")
    if isinstance(extensions, Mapping):
        raw_extensions = getattr(frame, "extensions", None)
        decoded: Dict[str, Any] = {}
        for key, value in extensions.items():
            original = value
            if isinstance(raw_extensions, Mapping):
                original = raw_extensions.get(str(key), value)
            ext_decode = getattr(original, "ext_decode", None)
            decoded[str(key)] = ext_decode() if callable(ext_decode) else value
        mapping = dict(mapping)
        mapping["extensions"] = _nest_dotted(decoded)
        # Keep flat views for keys that are dotted themselves (left.stick.x).
        for key, value in decoded.items():
            mapping.setdefault(f"extensions.{key}", value)

    _expand_by_name(mapping, "end_effector_poses", "pose", pose=True)
    _expand_by_name(mapping, "end_effector_states", "position")
    _expand_by_name(mapping, "end_effector_states", "force")
    _expand_by_name(mapping, "joint_states", "position")
    return mapping


def _nest_dotted(flat: Mapping[str, Any]) -> Dict[str, Any]:
    """Expand dotted keys (``left.stick.x``) into nested dicts for dotted lookup."""
    nested: Dict[str, Any] = dict(flat)
    for key, value in flat.items():
        parts = str(key).split(".")
        if len(parts) < 2:
            continue
        node = nested
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value
    return nested


def _expand_by_name(
    mapping: Dict[str, Any], section: str, field: str, *, pose: bool = False
) -> None:
    """Expand a parallel names/values array into ``<section>.<side>.<field>``."""
    group = mapping.get(section)
    if not isinstance(group, Mapping):
        return
    names = [str(n) for n in (group.get("names") or [])]
    values = list(group.get(field) or [])
    if not names:
        return
    nested: Dict[str, Any] = {}
    for name, value in zip(names, values):
        key = _side_of(name) if section != "joint_states" else str(name)
        if key is None:
            continue
        nested[key] = {field: _pose_values(value) if pose else value}
    if not nested:
        return
    merged = dict(group)
    for key, payload in nested.items():
        existing = merged.get(key)
        merged[key] = {**existing, **payload} if isinstance(existing, Mapping) else payload
    mapping[section] = merged


def is_empty_value(value: Any) -> bool:
    """Empty target value (None, empty sequence/ndarray) — no device action."""
    if value is None:
        return True
    if isinstance(value, (list, tuple)):
        return not value
    size = getattr(value, "size", None)
    return isinstance(size, int) and size == 0
