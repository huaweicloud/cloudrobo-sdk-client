"""R2C LeRobot policy server for Observations->Actions inference flow."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
from dataclasses import dataclass
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any, Mapping, Optional, Sequence

import torch
import numpy as np

from cloudrobo_r2c.common.cli_utils import build_session_simple, load_yaml_mapping
from cloudrobo_r2c.common.models import Observations
from cloudrobo_r2c.core.interfaces import IModelTranslator
from cloudrobo_r2c.translators.translator_factory import ModelTranslatorFactory

logger = logging.getLogger(__name__)


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class R2CLeRobotPolicyServerConfig:
    """Runtime configuration for :class:`R2CLeRobotPolicyServer`."""

    model_path: str = ""
    pretrained_model_path: str = ""
    cloud_config_path: Optional[str] = None
    device: str = "cpu"
    policy_type: str = ""
    use_pre_post_processors: bool = True
    action_chunk_size: Optional[int] = None

class LeRobotPolicyRunner:
    """Load and execute a LeRobot pretrained policy."""

    def __init__(
        self,
        pretrained_path: str,
        policy_type: str,
        device: str = "cpu",
        use_pre_post_processors: bool = True,
        action_chunk_size: Optional[int] = None,
    ) -> None:
        self._pretrained_path = pretrained_path
        self._policy_type = policy_type
        self._device = device
        self._use_pre_post_processors = use_pre_post_processors
        self._action_chunk_size = action_chunk_size
        self._preprocessor: Optional[Any] = None
        self._postprocessor: Optional[Any] = None
        self._policy = self._load_policy()

    def _load_policy(self) -> Any:
        try:
            from lerobot.policies.factory import (
                get_policy_class,
                make_pre_post_processors,
            )
        except ImportError as exc:
            raise ImportError(
                "LeRobot server requires `torch` and `lerobot` to be installed."
            ) from exc

        if not self._policy_type:
            raise ValueError(
                "LeRobot policy_type is required (for example: act, diffusion, pi0)."
            )

        policy_class = get_policy_class(self._policy_type)
        policy = policy_class.from_pretrained(self._pretrained_path)
        policy.to(self._device)
        policy.eval()

        if self._use_pre_post_processors:
            try:
                override = {"device": self._device}
                self._preprocessor, self._postprocessor = make_pre_post_processors(
                    policy.config,
                    pretrained_path=self._pretrained_path,
                    preprocessor_overrides={"device_processor": override},
                    postprocessor_overrides={"device_processor": override},
                )
            except Exception as exc:
                logger.warning(
                    "[%s] Failed to initialize LeRobot pre/post processors: %s",
                    _ts(),
                    exc,
                )

        logger.info(
            "[%s] Loaded LeRobot policy type=%s path=%s device=%s",
            _ts(),
            self._policy_type,
            self._pretrained_path,
            self._device,
        )
        return policy

    def infer(self, features: Mapping[str, Any]) -> Mapping[str, Any]:
        # 1. 准备数据：类型转换 (uint8 -> float32) 和 形状转换 (HWC -> CHW)
        def _prepare_data(val: Any) -> Any:
            if isinstance(val, Mapping):
                return {k: _prepare_data(v) for k, v in val.items()}
            
            if isinstance(val, np.ndarray):
                if val.ndim == 3 and val.shape[-1] in (1, 3, 4):
                    val = np.transpose(val, (2, 0, 1))
                if val.dtype == np.uint8:
                    return val.astype(np.float32) / 255.0
                if val.dtype == np.float64:
                    return val.astype(np.float32)
                return val

            if torch.is_tensor(val):
                if val.ndim == 3 and val.shape[-1] in (1, 3, 4):
                    val = val.permute(2, 0, 1)
                if val.dtype == torch.uint8:
                    return val.float() / 255.0
                if val.dtype == torch.float64:
                    return val.float()
                return val

            if isinstance(val, (list, tuple)):
                try:
                    arr = np.array(val)
                    if arr.dtype in (np.float64, np.int64, np.int32):
                        return arr.astype(np.float32)
                except Exception as exc: 
                    logger.exception("Failed convert to ndarray: %s", exc)

            return val

        prepared: Any = _prepare_data(features)

        # 1.5. 扁平化嵌套 observation：ConfigDrivenMapper 使用 assign_dotted
        # 将 "observation.state" 写入为 {"observation": {"state": ...}}，
        # 但 LeRobot 的 batch_to_transition 期望顶层扁平 key
        # ("observation.state")。这里把嵌套结构展平以匹配 LeRobot 格式。
        prepared = self._flatten_observation(prepared)

        # 2. 预处理 (LeRobot 的 preprocessor 通常期望单步的无 batch 数据)
        if self._preprocessor is not None:
            prepared = self._apply_preprocessor(prepared)

        # 3. 增加 Batch 维度 (unsqueeze) 并推送到设备 (GPU/CPU)
        def _batch_and_to_device(data: Any, target_device: str) -> Any:
            if isinstance(data, Mapping):
                return {k: _batch_and_to_device(v, target_device) for k, v in data.items()}
            if isinstance(data, (list, tuple)):
                return type(data)(_batch_and_to_device(v, target_device) for v in data)
            if isinstance(data, np.ndarray):
                data = torch.from_numpy(data)
            if torch.is_tensor(data):
                # 核心修复：在这里加上 batch_size = 1 的维度
                return data.unsqueeze(0).to(target_device)
            return data

        prepared = _batch_and_to_device(prepared, self._device)

        # 4. 模型推理
        with torch.no_grad():
            # 优先尝试获取完整的动作序列 (ACT 等模型支持 predict_action_chunk)
            if hasattr(self._policy, "predict_action_chunk"):
                output = self._policy.predict_action_chunk(prepared)
            elif hasattr(self._policy, "select_action"):
                output = self._policy.select_action(prepared)
            else:
                output = self._policy(prepared)

            # 根据配置截取指定长度的 chunk
            if self._action_chunk_size is not None and self._action_chunk_size > 0:
                # 处理 Tensor 格式 (通常形状为 [batch_size, chunk_size, action_dim])
                if torch.is_tensor(output) and output.ndim == 3:
                    output = output[:, :self._action_chunk_size, ...]
                # 处理 Dict 格式
                elif isinstance(output, Mapping) and "action" in output:
                    action_tensor = output["action"]
                    if torch.is_tensor(action_tensor) and action_tensor.ndim == 3:
                        output["action"] = action_tensor[:, :self._action_chunk_size, ...]

        # 5. 移除 Batch 维度 (squeeze)，恢复为单步数据结构
        def _unbatch(data: Any) -> Any:
            if isinstance(data, Mapping):
                return {k: _unbatch(v) for k, v in data.items()}
            if isinstance(data, (list, tuple)):
                return type(data)(_unbatch(v) for v in data)
            if torch.is_tensor(data):
                # 如果第 0 维是 1 (也就是我们刚刚加的 batch_size)，将其去掉
                if data.ndim > 0 and data.shape[0] == 1:
                    return data.squeeze(0)
                return data
            return data

        output = _unbatch(output)

        # 6. 后处理及标准化输出
        if self._postprocessor is not None:
            output = self._postprocessor(output)

        return self._normalize_output(output, torch)

    def _apply_preprocessor(self, prepared: Any) -> Any:
        try:
            return self._preprocessor(prepared)
        except ValueError as exc:
            if not self._requires_observation_error(exc):
                raise
            for candidate in self._to_transition_payload_candidates(prepared):
                try:
                    return self._preprocessor(candidate)
                except ValueError as retry_exc:
                    if not self._requires_observation_error(retry_exc):
                        continue 
            raise exc
    @staticmethod
    def _requires_observation_error(error: Exception) -> bool:
        return "requires an observation in the transition" in str(error).lower()

    @staticmethod
    def _extract_prefixed_payload(
        payload: Mapping[str, Any], prefix: str
    ) -> dict[str, Any]:
        nested: dict[str, Any] = {}
        for key, value in payload.items():
            if not isinstance(key, str) or not key.startswith(prefix):
                continue
            suffix = key[len(prefix) :]
            if not suffix:
                continue
            cursor = nested
            parts = [segment for segment in suffix.split(".") if segment]
            for part in parts[:-1]:
                next_cursor = cursor.get(part)
                if not isinstance(next_cursor, dict):
                    next_cursor = {}
                    cursor[part] = next_cursor
                cursor = next_cursor
            if parts:
                cursor[parts[-1]] = value
        return nested

    @staticmethod
    def _flatten_observation(prepared: Any) -> Any:
        """Flatten a nested ``observation`` dict into flat ``observation.xxx`` keys.

        ``ConfigDrivenMapper`` produces ``{"observation": {"state": ..., "images": {...}}}``
        via ``assign_dotted``, but LeRobot's ``batch_to_transition`` expects top-level
        keys like ``"observation.state"`` and ``"observation.images.front"``.

        This method converts the nested form into the flat form so the LeRobot
        preprocessor pipeline can recognize the observation fields.
        """
        if not isinstance(prepared, Mapping):
            return prepared

        obs = prepared.get("observation")
        if not isinstance(obs, Mapping):
            return prepared

        result: dict[str, Any] = dict(prepared)
        del result["observation"]

        def _flatten(value: Any, prefix: str) -> dict[str, Any]:
            flat: dict[str, Any] = {}
            if not isinstance(value, Mapping):
                flat[prefix] = value
                return flat
            for key, val in value.items():
                child_key = f"{prefix}.{key}" if prefix else key
                if isinstance(val, Mapping):
                    flat.update(_flatten(val, child_key))
                else:
                    flat[child_key] = val
            return flat

        result.update(_flatten(obs, "observation"))
        return result

    @classmethod
    def _to_observation_payload(cls, prepared: Any) -> Any:
        if not isinstance(prepared, Mapping):
            return prepared

        if "observation" in prepared:
            return prepared["observation"]

        extracted = cls._extract_prefixed_payload(dict(prepared), "observation.")
        if extracted:
            return extracted
        return prepared

    @classmethod
    def _to_transition_payload(cls, prepared: Any) -> Any:
        observation = cls._to_observation_payload(prepared)
        if isinstance(prepared, Mapping):
            copied = dict(prepared)
            copied["observation"] = observation
            return copied
        return {"observation": observation}

    @classmethod
    def _to_transition_payload_candidates(cls, prepared: Any) -> list[Any]:
        transition = cls._to_transition_payload(prepared)
        observation = (
            transition.get("observation") if isinstance(transition, Mapping) else None
        )
        return [
            transition,
            {"observation": observation},
        ]

    @staticmethod
    def _normalize_output(output: Any, torch_module: Any) -> Mapping[str, Any]:
        if isinstance(output, Mapping):
            return {
                str(key): LeRobotPolicyRunner._normalize_value(value, torch_module)
                for key, value in output.items()
            }
        return {"action": LeRobotPolicyRunner._normalize_value(output, torch_module)}

    @staticmethod
    def _normalize_value(value: Any, torch_module: Any) -> Any:
        """Normalise a model output value into the R2C action shape.

        The R2C ``Actions`` payload expects a 2-D ``positions`` list
        (``[chunk_step, action_dim]``). Some LeRobot policies return a 1-D
        tensor/array for a single action step; this helper wraps a 1-D
        result into ``[res]`` so it becomes a one-step chunk. 2-D (and
        higher) outputs are returned unchanged.

        The 1-D→2-D wrap is a heuristic: it assumes a 1-D output is a
        single step, not a flattened chunk. If a model ever returns a
        legitimately 1-D multi-step output this would mis-shape it; the
        debug log lets such a case be diagnosed.
        """
        if isinstance(value, Mapping):
            return {
                str(key): LeRobotPolicyRunner._normalize_value(item, torch_module)
                for key, item in value.items()
            }

        if torch_module.is_tensor(value):
            res = value.detach().cpu().numpy().tolist()
            # 核心修复：如果模型输出的是 1D 数组（单步），套一层变成 2D（序列）
            if value.ndim == 1:
                logger.debug(
                    "_normalize_value: wrapping 1-D tensor (len=%d) into a "
                    "one-step chunk.",
                    len(res),
                )
                return [res]
            return res

        if isinstance(value, np.ndarray):
            res = value.tolist()
            # 同上，处理 NumPy 数组
            if value.ndim == 1:
                logger.debug(
                    "_normalize_value: wrapping 1-D ndarray (len=%d) into a "
                    "one-step chunk.",
                    len(res),
                )
                return [res]
            return res

        if isinstance(value, (list, tuple)):
            # 如果已经是普通的 list，检查它是不是一层扁平的数字
            if len(value) > 0 and isinstance(value[0], (int, float)):
                logger.debug(
                    "_normalize_value: wrapping flat numeric list (len=%d) "
                    "into a one-step chunk.",
                    len(value),
                )
                return [list(value)]
            return [
                LeRobotPolicyRunner._normalize_value(item, torch_module)
                for item in value
            ]

        return value

class R2CLeRobotPolicyServer:
    """Subscribe ``Observations``, run LeRobot policy, publish ``Actions``."""

    def __init__(
        self,
        session: Any,
        config: R2CLeRobotPolicyServerConfig,
        model_runner: Optional[Any] = None,
        model_translator: Optional[IModelTranslator] = None,
    ) -> None:
        self._session = session
        self.config = config

        cloud_config = load_yaml_mapping(config.cloud_config_path)
        pretrained_path = self._resolve_model_path(
            model_path=config.model_path,
            pretrained_model_path=config.pretrained_model_path,
            cloud_config=cloud_config,
        )
        if not pretrained_path:
            raise ValueError(
                "LeRobot pretrained model path is required. Configure --pretrained-model-path/--model-path, "
                "or set model.pretrained_model.path / model.pretrained_path / model.pretrained_model_path "
                "/ model.checkpoint_path in cloud config."
            )

        model_cfg = cloud_config.get("model", {})
        if not isinstance(model_cfg, Mapping):
            model_cfg = {}
        device = str(model_cfg.get("device", config.device))
        policy_type = self._resolve_policy_type(config.policy_type, model_cfg)

        
        use_pre_post_processors = bool(
            model_cfg.get("use_pre_post_processors", config.use_pre_post_processors)
        )
        
        # --- 新增读取 chunk size 的逻辑 ---
        action_chunk_size = model_cfg.get("action_chunk_size", config.action_chunk_size)
        if action_chunk_size is not None:
            action_chunk_size = int(action_chunk_size)
        # --------------------------------

        self._model_runner = model_runner or LeRobotPolicyRunner(
            pretrained_path=pretrained_path,
            policy_type=policy_type,
            device=device,
            use_pre_post_processors=use_pre_post_processors,
            action_chunk_size=action_chunk_size,  # <--- 新增参数传递
        )
        
        self._model_translator: IModelTranslator = (
            ModelTranslatorFactory.create_model_translator(
                cloud_config,
                model_translator=model_translator,
            )
        )
        # Consecutive inference failure counter. Repeated failures starve the
        # robot of actions; once this exceeds the threshold we escalate the
        # log level so the silence is not mistaken for healthy operation.
        self._consecutive_inference_failures = 0
        self._inference_failure_escalation_threshold = 3

        # ── delay_return ──────────────────────────────────────────────
        runtime_cfg = cloud_config.get("runtime", {})
        self._delay_return = float(
            runtime_cfg.get("delay_return", 0.0)
            if isinstance(runtime_cfg, Mapping)
            else 0.0
        )
        if self._delay_return > 0:
            logger.info(
                "[%s] Action return delay configured: %.1fs",
                _ts(),
                self._delay_return,
            )

    def start(self, target_device_id: Optional[str] = None) -> None:
        self._session.subscribe_observations(
            self.on_observations,
            target_device_id=target_device_id,
        )
        logger.info(
            "[%s] R2CLeRobotPolicyServer subscribed on target_device_id=%s",
            _ts(),
            target_device_id,
        )

    def close(self) -> None:
        self._session.close()

    def on_observations(self, observations: Observations) -> None:
        try:
            model_input = self._model_translator.r2c_to_model_input(observations)
        except Exception as exc:
            self._consecutive_inference_failures += 1
            if (
                self._consecutive_inference_failures
                >= self._inference_failure_escalation_threshold
            ):
                logger.error(
                    "[%s] r2c_to_model_input has failed %d times in a row "
                    "(last id=%s); last error: %s",
                    _ts(),
                    self._consecutive_inference_failures,
                    observations.id,
                    exc,
                    exc_info=True,
                )
            else:
                logger.exception(
                    "[%s] Failed r2c_to_model_input for id=%s: %s",
                    _ts(),
                    observations.id,
                    exc,
                )
            return

        # The model translator returns None when required source fields
        # are not yet ready (e.g. camera frames). It already logged a
        # single-line WARNING — skip without counting as a failure.
        if model_input is None:
            return

        try:
            raw_output = self._model_runner.infer(model_input)
            actions = self._model_translator.model_output_to_r2c(raw_output)
            actions.id = observations.id
            if self._delay_return > 0:
                time.sleep(self._delay_return)
            self._session.publish_actions(actions)
        except Exception as exc:
            self._consecutive_inference_failures += 1
            if (
                self._consecutive_inference_failures
                >= self._inference_failure_escalation_threshold
            ):
                # Repeated failures mean the robot is being starved of actions
                # — escalate from a per-message exception log to an error so
                # it is not buried as a one-off.
                logger.error(
                    "[%s] Inference has failed %d times in a row (last id=%s); "
                    "the robot is not receiving actions. Last error: %s",
                    _ts(),
                    self._consecutive_inference_failures,
                    observations.id,
                    exc,
                    exc_info=True,
                )
            else:
                logger.exception(
                    "[%s] Failed processing observations id=%s: %s",
                    _ts(),
                    observations.id,
                    exc,
                )
            return
        # Success: reset the failure streak.
        self._consecutive_inference_failures = 0

    @staticmethod
    def _resolve_model_path(
        model_path: str,
        pretrained_model_path: str,
        cloud_config: Mapping[str, Any],
    ) -> str:
        model_cfg = cloud_config.get("model", {})
        if not isinstance(model_cfg, Mapping):
            model_cfg = {}

        nested_pretrained = model_cfg.get("pretrained_model")
        nested_candidates: list[str] = []
        if isinstance(nested_pretrained, str):
            nested_candidates.append(nested_pretrained)
        elif isinstance(nested_pretrained, Mapping):
            nested_candidates.extend(
                str(nested_pretrained.get(key, ""))
                for key in ("path", "pretrained_path", "name_or_path")
            )

        candidates = [
            pretrained_model_path,
            model_path,
            *nested_candidates,
            str(model_cfg.get("pretrained_path", "")),
            str(model_cfg.get("pretrained_model_path", "")),
            str(model_cfg.get("checkpoint_path", "")),
        ]
        selected = next((value for value in candidates if str(value).strip()), "")
        if not selected:
            return ""
        return str(Path(selected).expanduser())

    @staticmethod
    def _resolve_policy_type(cli_policy_type: str, model_cfg: Mapping[str, Any]) -> str:
        type_candidates = [
            cli_policy_type,
            str(model_cfg.get("policy_type", "")),
            str(model_cfg.get("lerobot_policy_type", "")),
            str(model_cfg.get("type", "")),
        ]
        raw = next((value for value in type_candidates if str(value).strip()), "")
        if not raw:
            return ""

        normalized = str(raw).strip().lower()
        if normalized.startswith("lerobot_"):
            return normalized.replace("lerobot_", "", 1)
        return normalized


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "R2C LeRobot policy server: subscribe Observations, run LeRobot model, publish Actions"
        )
    )
    parser.add_argument("--bundle", type=str, default=None)
    parser.add_argument("--client-config", default="config/client_config.yaml")
    parser.add_argument("--project-id", type=str, default=None)
    parser.add_argument("--device-id", type=str, default=None)
    parser.add_argument("--client-id", type=str, default=None)
    parser.add_argument("--endpoints", type=str, default="")
    parser.add_argument(
        "--mode",
        type=str,
        default=None,
        choices=["peer", "client"],
    )
    parser.add_argument("--model-path", default="")
    parser.add_argument("--pretrained-model-path", default="")
    parser.add_argument("--policy-type", default="")
    parser.add_argument(
        "--cloud-config",
        default="config/cloud_config.yaml",
        help="Cloud runtime + translator config path.",
    )
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--disable-pre-post-processors",
        action="store_true",
        help="Disable LeRobot pre/post processors loading.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Python logging level, e.g. DEBUG/INFO/WARNING.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    # Resolve config paths against the config shipped inside the installed
    # package, while still honoring explicit / source-checkout relative paths.
    from cloudrobo_r2c.common.config_path import resolve_config_path

    if args.client_config:
        args.client_config = resolve_config_path(args.client_config)
    if args.cloud_config:
        args.cloud_config = resolve_config_path(args.cloud_config)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    session = build_session_simple(args, default_client_id="r2c-lerobot-policy-server")
    server = R2CLeRobotPolicyServer(
        session=session,
        config=R2CLeRobotPolicyServerConfig(
            model_path=args.model_path,
            pretrained_model_path=args.pretrained_model_path,
            cloud_config_path=args.cloud_config,
            device=args.device,
            policy_type=args.policy_type,
            use_pre_post_processors=not bool(args.disable_pre_post_processors),
        ),
    )
    server.start(target_device_id=args.device_id)

    logger.info("[%s] R2CLeRobotPolicyServer is running. Press Ctrl+C to exit.", _ts())
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("[%s] Stopping R2CLeRobotPolicyServer...", _ts())
    finally:
        server.close()


if __name__ == "__main__":
    main()
