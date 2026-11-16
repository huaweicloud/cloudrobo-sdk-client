"""Config-driven device <-> R2C translators."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Mapping, Optional

from cloudrobo_r2c.common.models.wrappers.observations import Observations
from cloudrobo_r2c.core.config_mapper import ConfigDrivenMapper
from cloudrobo_r2c.core.interfaces import IDeviceTranslator, ObservationLike
from cloudrobo_r2c.core.internal.helpers import next_global_observation_id, to_mapping

logger = logging.getLogger(__name__)

# teleop.proto names end-effector channels by hand: left/right (arm suffixes
# accepted as aliases).
_SIDE_ALIASES = {
    "left": "left",
    "right": "right",
    "left_arm": "left",
    "right_arm": "right",
}


def _side_of(name: Any) -> Optional[str]:
    return _SIDE_ALIASES.get(str(name).strip().lower())


def _pose_values(pose: Any) -> List[float]:
    data = getattr(pose, "data", pose)
    return [float(v) for v in data]


def _decode_extensions(mapping: Mapping[str, Any], source: Any) -> Dict[str, Any]:
    """Return a copy of *mapping* with ``extensions`` values decoded.

    Shared by ``r2c_to_device`` (Actions) and the teleop payload: both carry
    ``ExtensionValue`` objects that must be ``ext_decode``-d before a mapping
    rule can consume them.  *source* supplies the original objects, because
    ``to_mapping`` recursion turns them into plain dicts and loses
    ``ext_decode``.  The input mapping is not mutated.
    """
    result = dict(mapping)
    extensions = result.get("extensions")
    if not isinstance(extensions, Mapping):
        return result
    raw_extensions = getattr(source, "extensions", None)
    decoded: Dict[str, Any] = {}
    for key, value in extensions.items():
        original = value
        if isinstance(raw_extensions, Mapping):
            original = raw_extensions.get(str(key), value)
        ext_decode = getattr(original, "ext_decode", None)
        decoded[str(key)] = ext_decode() if callable(ext_decode) else value
    result["extensions"] = decoded
    return result


def _teleop_frame_to_mapping(frame: Any) -> Dict[str, Any]:
    """TeleopFrame (or plain dict) -> mapping-rule payload.

    Reuses ``to_mapping`` for the dataclass -> dict conversion and
    ``_decode_extensions`` for extension values, then normalizes per-side
    channels by ``names`` so rules can address them like ``r2c_to_device``
    sources (``end_effector_poses.left.pose``).
    """
    mapping = _decode_extensions(to_mapping(frame), frame)
    extensions = mapping.get("extensions")
    if isinstance(extensions, Mapping):
        mapping["extensions"] = _nest_dotted(extensions)
        # Keep flat views for keys that are dotted themselves (left.stick.x).
        for key, value in extensions.items():
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


def _is_empty_value(value: Any) -> bool:
    """Empty target value (None, empty sequence/ndarray) — no device action."""
    if value is None:
        return True
    if isinstance(value, (list, tuple)):
        return not value
    size = getattr(value, "size", None)
    return isinstance(size, int) and size == 0


def _drop_empty_targets(command: Mapping[str, Any]) -> Dict[str, Any]:
    """Drop empty target fields from a mapped device command.

    Shared device-command post-processing: an empty target (``[]`` / ``None``)
    carries no device action and would make the adapter reject the whole action
    as incomplete, so it must not reach ``send_action``.
    """
    return {key: value for key, value in command.items() if not _is_empty_value(value)}


def _apply_mapper(mapper: ConfigDrivenMapper | None, payload: Mapping[str, Any]) -> Any:
    """Map *payload* through *mapper*, or pass it through when no mapper exists.

    Shared by ``device_to_r2c`` / ``r2c_to_device`` / ``teleop_to_device`` so all
    three follow the same "configured rules, otherwise raw payload" contract.
    """
    return mapper.map(payload) if mapper is not None else dict(payload)


class ConfigurableDeviceTranslator(IDeviceTranslator):
    """Translate raw device observations and R2C actions via YAML-like mapping rules."""

    def __init__(self, config_dict: Mapping[str, Any]):
        self._config_dict = dict(config_dict)
        self._obs_mapper = self._build_device_to_r2c_mapper(self._config_dict)
        self._r2c_to_device_mapper = self._build_r2c_to_device_mapper(self._config_dict)
        self._teleop_to_device_mapper = self._build_teleop_to_device_mapper(self._config_dict)
        self._default_task = self._resolve_default_task(self._config_dict)

    @classmethod
    def from_config(cls, config_dict: Mapping[str, Any]) -> "ConfigurableDeviceTranslator":
        """Build translator from edge/robot config mapping."""
        return cls(config_dict)

    def device_to_r2c(self, raw_device_observation: Any) -> ObservationLike:
        mapping = to_mapping(raw_device_observation)

        # Check completeness BEFORE building the observation: if any
        # required source field is missing or resolves to None, skip this
        # tick entirely and log a single line warning listing what is not
        # ready - no exception traceback, because missing/None fields are
        # expected during startup (cameras initialising, etc.).
        if self._obs_mapper:
            incomplete = self._obs_mapper.check_completeness(mapping)
            if incomplete.get("required"):
                not_ready: list[str] = []
                if incomplete.get("required"):
                    not_ready.extend(incomplete["required"])
                if incomplete.get("optional"):
                    not_ready.extend(incomplete["optional"])
                logger.warning(
                    "Observation incomplete - skipping tick. Not ready: %s",
                    ", ".join(not_ready),
                )
                return None  # sentinel: skip this tick

        observation_dict = {
            "timestamp": int(time.time() * 1000),
            "task": self._default_task,
            "id": next_global_observation_id(),
        }
        mapped = _apply_mapper(self._obs_mapper, mapping)

        observation_dict.update(mapped)
        # No fallback here: a failure to build the Observation model is a
        # hard error and propagates to the caller.  Returning the raw
        # mapped dict instead used to mask the root cause - the dict lacks
        # timestamp/task and would later fail in publish_observations with
        # a misleading "Missing required field(s): timestamp, task".
        return Observations.from_dict(observation_dict)

    def r2c_to_device(self, action_step: Mapping[str, Any]) -> Any:
        mapping = _decode_extensions(to_mapping(action_step), action_step)
        return _apply_mapper(self._r2c_to_device_mapper, mapping)

    def teleop_to_device(self, frame: Any) -> Any:
        """Translate a TeleopFrame into a device command (plan B: reuse send_action).

        Same contract as ``r2c_to_device``: the ``teleop_to_device.mappings``
        rules when configured, otherwise the payload passes through unchanged
        (a robot without that section therefore issues no teleop command).

        Left and right stay independent: a side absent from the frame produces no
        field, so the adapter holds that side at its current device value.
        """
        mapped = _apply_mapper(
            self._teleop_to_device_mapper, _teleop_frame_to_mapping(frame)
        )
        return _drop_empty_targets(mapped)

    @staticmethod
    def _build_device_to_r2c_mapper(
        config_dict: Mapping[str, Any],
    ) -> ConfigDrivenMapper | None:
        cfg = config_dict.get("device_to_r2c", {})
        if not isinstance(cfg, Mapping):
            cfg = {}
        mappings = cfg.get("mappings")
        if isinstance(mappings, list) and mappings:
            return ConfigDrivenMapper.from_rule_mappings(mappings)
        return None

    @staticmethod
    def _build_r2c_to_device_mapper(
        config_dict: Mapping[str, Any],
    ) -> ConfigDrivenMapper | None:
        cfg = config_dict.get("r2c_to_device", {})
        if not isinstance(cfg, Mapping):
            return None
        mappings = cfg.get("mappings")
        if isinstance(mappings, list) and mappings:
            return ConfigDrivenMapper.from_rule_mappings(mappings)
        return None

    @staticmethod
    def _build_teleop_to_device_mapper(
        config_dict: Mapping[str, Any],
    ) -> ConfigDrivenMapper | None:
        cfg = config_dict.get("teleop_to_device", {})
        if not isinstance(cfg, Mapping):
            return None
        mappings = cfg.get("mappings")
        if isinstance(mappings, list) and mappings:
            return ConfigDrivenMapper.from_rule_mappings(mappings)
        return None

    @staticmethod
    def _resolve_default_task(config_dict: Mapping[str, Any]) -> str:
        translator_cfg = config_dict.get("translator", {})
        if isinstance(translator_cfg, Mapping):
            task_value = translator_cfg.get("task")
            if isinstance(task_value, str) and task_value:
                return task_value

        for key in ("task", "task_name"):
            task_value = config_dict.get(key)
            if isinstance(task_value, str) and task_value:
                return task_value
        return "default_task"
