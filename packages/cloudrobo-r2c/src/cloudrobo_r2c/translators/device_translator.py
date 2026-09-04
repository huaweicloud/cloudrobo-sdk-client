"""Config-driven device <-> R2C translators."""

from __future__ import annotations

import logging
import time
from typing import Any, Mapping

from cloudrobo_r2c.common.models import Observations
from cloudrobo_r2c.core.config_mapper import ConfigDrivenMapper
from cloudrobo_r2c.core.interfaces import IDeviceTranslator, ObservationLike
from cloudrobo_r2c.core.internal.helpers import (next_global_observation_id,
                                           to_mapping)

logger = logging.getLogger(__name__)


class ConfigurableDeviceTranslator(IDeviceTranslator):
    """Translate raw device observations and R2C actions via YAML-like mapping rules."""

    def __init__(self, config_dict: Mapping[str, Any]):
        self._config_dict = dict(config_dict)
        self._obs_mapper = self._build_device_to_r2c_mapper(self._config_dict)
        self._r2c_to_device_mapper = self._build_r2c_to_device_mapper(self._config_dict)
        self._default_task = self._resolve_default_task(self._config_dict)

    @classmethod
    def from_config(
        cls, config_dict: Mapping[str, Any]
    ) -> "ConfigurableDeviceTranslator":
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
            if incomplete:
                logger.warning(
                    "Observation incomplete - skipping tick. "
                    "Not ready: %s",
                    ", ".join(incomplete),
                )
                return None  # sentinel: skip this tick

        observation_dict = {
            "timestamp": int(time.time() * 1000),
            "task": self._default_task,
            "id": next_global_observation_id(),
        }
        mapped = self._obs_mapper.map(mapping) if self._obs_mapper else dict(mapping)

        observation_dict.update(mapped)
        try:
            return Observations.from_dict(observation_dict)
        except Exception as exc:
            logger.warning(
                "Failed to device_to_r2c: %s",
                exc,
                exc_info=True,
            )
            return mapped

    def r2c_to_device(self, action_step: Mapping[str, Any]) -> Any:
        mapping = to_mapping(action_step)
        device_command = (
            self._r2c_to_device_mapper.map(mapping)
            if self._r2c_to_device_mapper
            else dict(mapping)
        )
        return device_command

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
