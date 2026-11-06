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
        if (logger.level == logging.DEBUG): # summarize only when necessary to log
            logger.debug(
                "Converting R2C observation to model input. %s",
                summarize_observation_for_log(observation),
            )
        start = time.perf_counter()
        obs_mapping = to_mapping(observation)

        for ext_k in obs_mapping["extensions"]:
            obs_mapping["extensions"][ext_k] = observation.extensions[ext_k].ext_decode()

        # Mirror the device-translator completeness check: if a required
        # source field is missing or None, return None so the caller can
        # skip this tick rather than feeding incomplete data to the model
        # preprocessor (which would crash on None tensors).
        if self._input_mapper:
            incomplete = self._input_mapper.check_completeness(obs_mapping)
            if incomplete["required"]:
                raise KeyError(
                    "Model input incomplete. Not ready: " +
                    ", ".join(incomplete["required"]),
                )

        try:
            model_input = (
                self._input_mapper.map(obs_mapping)
                if self._input_mapper
                else dict(obs_mapping)
            )
        except ValueError as e:
            raise ValueError(f"Convert Error: {e}, the observation is {summarize_observation_for_log(observation)}",)
        logger.info(
            "Translated R2C observation to model input in %.2f ms (mapped_fields=%d)",
            (time.perf_counter() - start) * 1000.0,
            len(model_input),
        )
        return model_input

    def model_output_to_r2c(self, model_output_tensor: Any) -> Actions:
        start = time.perf_counter()
        mapping = to_mapping(model_output_tensor)
        logger.info(
            "Converting model output to R2C Action."
        )
        if (logger.level == logging.DEBUG): # summarize only when necessary to log
            logger.debug("Model Output: %s",
                summarize_observation_for_log(model_output_tensor),)

        if self._output_mapper:
            incomplete = self._output_mapper.check_completeness(mapping)
            if incomplete["required"]:
                raise KeyError(
                    "R2C action incomplete. Not ready: " +
                    ", ".join(incomplete["required"]) +
                    ", please check r2c config",
                )

        try:
            mapped = (
                self._output_mapper.map(mapping) if self._output_mapper else dict(mapping)
            )
        except ValueError as e:
            raise ValueError(f"Convert Error: {e}, the model output is {summarize_observation_for_log(model_output_tensor)}")

        payload = {
            "timestamp": int(time.time() * 1000),
            **mapped,
        }
        if "chunk_size" not in payload:
            payload["chunk_size"] = self._infer_chunk_size(mapped)
        actions = Actions.from_dict(payload)
        if (logger.level == logging.DEBUG): # summarize only when necessary to log
            logger.debug(
                "R2C Action Output: %s",
                summarize_observation_for_log(payload),
            )

        logger.info(
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
        chunk_size = 0
        chunk_size = ConfigurableModelTranslator.infer_chunk_size_by_joint_states(mapped)
        if chunk_size != 0:
            return chunk_size
        chunk_size = ConfigurableModelTranslator.infer_chunk_size_by_end_effector_states(mapped)
        if chunk_size != 0:
            return chunk_size
        return ConfigurableModelTranslator.infer_chunk_size_by_end_effector_poses(mapped)

    @staticmethod
    def infer_chunk_size_by_joint_states(mapped: Mapping[str, Any]) -> Optional[int]:
        joint_states = mapped.get("joint_states")
        if isinstance(joint_states, Mapping):
            position = joint_states.get("position")
            if position is not None and hasattr(position, "__len__") and not isinstance(position, (str, bytes, bytearray)):
                return len(position)
            velocity = joint_states.get("velocity")
            if velocity is not None and hasattr(velocity, "__len__") and not isinstance(velocity, (str, bytes, bytearray)):
                return len(velocity)
            torque = joint_states.get("torque")
            if torque is not None and hasattr(torque, "__len__") and not isinstance(torque, (str, bytes, bytearray)):
                return len(torque)
        return 0

    @staticmethod
    def infer_chunk_size_by_end_effector_states(mapped: Mapping[str, Any]) -> Optional[int]:
        end_effector_states = mapped.get("end_effector_states")
        if isinstance(end_effector_states, Mapping):
            position = end_effector_states.get("position")
            if position is not None and hasattr(position, "__len__") and not isinstance(position, (str, bytes, bytearray)):
                return len(position)
            velocity = end_effector_states.get("velocity")
            if velocity is not None and hasattr(velocity, "__len__") and not isinstance(velocity, (str, bytes, bytearray)):
                return len(velocity)
            torque = end_effector_states.get("torque")
            if torque is not None and hasattr(torque, "__len__") and not isinstance(torque, (str, bytes, bytearray)):
                return len(torque)
        return 0
        
    @staticmethod
    def infer_chunk_size_by_end_effector_poses(mapped: Mapping[str, Any]) -> Optional[int]:
        end_effector_poses = mapped.get("end_effector_poses")
        if isinstance(end_effector_poses, Mapping):
            for pose_v in end_effector_poses.values():
                poses = pose_v.get("pose")
                if poses is not None and hasattr(poses, "__len__") and not isinstance(poses, (str, bytes, bytearray)):
                    return len(poses)
        return 0
