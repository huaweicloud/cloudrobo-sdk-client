"""Config-driven R2C <-> model translators."""

from __future__ import annotations

import logging
import time
from typing import Any, Mapping, Optional

from cloudrobo_r2c.common.models import Actions, Observations
from cloudrobo_r2c.common.utils import summarize_observation_for_log
from cloudrobo_r2c.core.config_mapper import ConfigDrivenMapper
from cloudrobo_r2c.core.interfaces import IModelTranslator, IValueTransformer, ObservationLike
from cloudrobo_r2c.core.internal.helpers import to_mapping

logger = logging.getLogger(__name__)


class ConfigurableModelTranslator(IModelTranslator):
    """Translate between R2C observation payloads and model IO with config pipelines.

    Args:
        config_dict: Cloud config dictionary containing ``r2c_to_model`` and
            ``model_to_r2c`` mapping rules.
        custom_transformers: Optional ``{name: IValueTransformer}`` dict to
            register alongside the SDK built-in transformers.  Custom
            transformers take priority over builtins with the same name.
    """

    def __init__(
        self,
        config_dict: Mapping[str, Any],
        custom_transformers: Optional[Mapping[str, IValueTransformer]] = None,
    ):
        self._config_dict = dict(config_dict)
        self._custom_transformers = dict(custom_transformers) if custom_transformers else {}
        self._input_mapper = self._build_r2c_to_model_mapper(
            self._config_dict, self._custom_transformers,
        )
        self._output_mapper = self._build_model_output_mapper(
            self._config_dict, self._custom_transformers,
        )

    def r2c_to_model_input(self, observation: ObservationLike) -> Any:
        if observation is None:
            return None
        logger.debug(
            "Converting R2C observation to model input. %s",
            summarize_observation_for_log(observation),
        )
        start = time.perf_counter()
        obs_mapping = to_mapping(observation)

        # Mirror the device-translator completeness check: if a required
        # source field is missing or None, return None so the caller can
        # skip this tick rather than feeding incomplete data to the model
        # preprocessor (which would crash on None tensors).
        if self._input_mapper:
            incomplete = self._input_mapper.check_completeness(obs_mapping)
            if incomplete:
                logger.warning(
                    "Model input incomplete - skipping tick. "
                    "Not ready: %s",
                    ", ".join(incomplete),
                )
                return None

        model_input = (
            self._input_mapper.map(obs_mapping)
            if self._input_mapper
            else dict(obs_mapping)
        )
        logger.debug(
            "Translated R2C observation to model input in %.2f ms (mapped_fields=%d)",
            (time.perf_counter() - start) * 1000.0,
            len(model_input),
        )
        return model_input

    def model_output_to_r2c(self, model_output_tensor: Any) -> Actions:
        start = time.perf_counter()
        mapping = to_mapping(model_output_tensor)
        mapped = (
            self._output_mapper.map(mapping) if self._output_mapper else dict(mapping)
        )
        payload = {
            "timestamp": int(time.time() * 1000),
            "chunk_size": self._infer_chunk_size(mapped),
            **mapped,
        }
        actions = Actions.from_dict(payload)
        logger.debug(
            "Translated model output to R2C actions in %.2f ms (chunk_size=%d)",
            (time.perf_counter() - start) * 1000.0,
            payload["chunk_size"],
        )
        return actions

    @staticmethod
    def _build_r2c_to_model_mapper(
        config_dict: Mapping[str, Any],
        custom_transformers: Optional[Mapping[str, IValueTransformer]] = None,
    ) -> ConfigDrivenMapper | None:
        cfg = config_dict.get("r2c_to_model", {})
        if not isinstance(cfg, Mapping):
            cfg = {}
        mappings = cfg.get("mappings")
        if isinstance(mappings, list) and mappings:
            transformers = dict(custom_transformers) if custom_transformers else None
            return ConfigDrivenMapper.from_rule_mappings(mappings, transformers=transformers)
        return None

    @staticmethod
    def _build_model_output_mapper(
        config_dict: Mapping[str, Any],
        custom_transformers: Optional[Mapping[str, IValueTransformer]] = None,
    ) -> ConfigDrivenMapper | None:
        cfg = config_dict.get("model_to_r2c", {})
        if not isinstance(cfg, Mapping):
            cfg = {}
        mappings = cfg.get("mappings")
        if isinstance(mappings, list) and mappings:
            transformers = dict(custom_transformers) if custom_transformers else None
            return ConfigDrivenMapper.from_rule_mappings(mappings, transformers=transformers)
        return None

    @staticmethod
    def _infer_chunk_size(mapped: Mapping[str, Any]) -> int:
        joint_states = mapped.get("joint_states")
        if isinstance(joint_states, Mapping):
            position = joint_states.get("position")
            if position is not None and hasattr(position, "__len__") and not isinstance(position, (str, bytes, bytearray)):
                return len(position)
        return 1
