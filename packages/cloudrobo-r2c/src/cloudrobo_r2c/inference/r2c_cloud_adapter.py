"""Cloud adapter for Observations/Actions inference flow via OpenPI websocket policy."""

from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np

from cloudrobo_r2c.common.cli_utils import build_session_simple, load_yaml_mapping
from cloudrobo_r2c.common.models import (
    Actions,
    Observations,
)
from cloudrobo_r2c.core.interfaces import IModelTranslator
from cloudrobo_r2c.translators.translator_factory import ModelTranslatorFactory

logger = logging.getLogger(__name__)


@dataclass
class R2CCloudAdapterConfig:
    """Runtime configuration for ``R2CCloudAdapter``."""

    openpi_host: str = ""
    openpi_port: Optional[int] = None
    openpi_api_key: Optional[str] = None
    cloud_config_path: Optional[str] = None


class R2CCloudAdapter:
    """Subscribe ``Observations``, call OpenPI policy, publish ``Actions``."""

    def __init__(
        self,
        session: Any,
        config: R2CCloudAdapterConfig,
        policy_client: Optional[Any] = None,
        model_translator: Optional[IModelTranslator] = None,
    ) -> None:
        self.config = config
        self._session = session
        self._policy_client = policy_client or self._create_openpi_policy_client()
        cloud_config = load_yaml_mapping(config.cloud_config_path)
        self._model_translator = ModelTranslatorFactory.create_model_translator(
            cloud_config,
            model_translator=model_translator,
        )

    def start(self) -> None:
        """Start subscription loop (non-blocking); keep process alive in caller."""
        project_id = self._session.project_id
        device_id = self._session.device_id
        logger.info(
            "Subscribing observations from %s/%s/inference/observations",
            project_id,
            device_id,
        )
        self._session.subscribe_observations(
            self.on_observations,
            target_device_id=device_id,
        )

    def close(self) -> None:
        self._session.close()

    def on_observations(self, observations: Observations) -> None:
        """Handle one observations message: decode -> infer -> publish actions."""
        try:
            policy_input = self._model_translator.r2c_to_model_input(observations)
        except Exception as exc:
            logger.exception(
                "Failed r2c_to_model_input for id=%s: %s",
                observations.id,
                exc,
            )
            return

        # Model translator returns None when a required source field is not
        # yet ready - skip this tick.
        if policy_input is None:
            return

        try:
            policy_output = self._policy_client.infer(policy_input)
            actions = self._model_translator.model_output_to_r2c(policy_output)
            logger.debug("actions %s", actions)
            actions.id = observations.id
            self._session.publish_actions(actions)
            logger.info(
                "Published actions for id=%s with chunk_size=%s",
                observations.id,
                actions.chunk_size,
            )
        except Exception as exc:
            logger.exception(
                "Failed processing observations id=%s: %s",
                observations.id,
                exc,
            )

    def _create_openpi_policy_client(self) -> Any:
        if not self.config.openpi_host:
            raise ValueError(
                "openpi_host is required (CLI --openpi-host or openpi.host in cloud config)."
            )
        try:
            from openpi_client.websocket_client_policy import WebsocketClientPolicy
        except Exception as exc:
            raise ImportError(
                "openpi_client is required. Please install openpi-client first."
            ) from exc

        return WebsocketClientPolicy(
            host=self.config.openpi_host,
            port=self.config.openpi_port,
            api_key=self.config.openpi_api_key,
        )

    def _normalize_policy_output(self, policy_output: Any) -> Dict[str, np.ndarray]:
        if isinstance(policy_output, Mapping):
            for key in ("action_chunk", "actions", "action"):
                nested = policy_output.get(key)
                if isinstance(nested, Mapping):
                    return self._normalize_action_mapping(nested)
            return self._normalize_action_mapping(policy_output)

        if isinstance(policy_output, (list, tuple, np.ndarray)):
            return {"action": np.asarray(policy_output, dtype=np.float32)}

        raise ValueError(
            f"Unsupported OpenPI inference output type: {type(policy_output)!r}"
        )

    def _normalize_action_mapping(
        self, action_mapping: Mapping[str, Any]
    ) -> Dict[str, np.ndarray]:
        normalized: Dict[str, np.ndarray] = {}
        for key, value in action_mapping.items():
            if isinstance(value, Mapping) and "data" in value:
                value = value["data"]
            if isinstance(value, (bytes, bytearray)):
                normalized[str(key)] = np.frombuffer(value, dtype=np.float32)
            else:
                normalized[str(key)] = np.asarray(value, dtype=np.float32)
        if not normalized:
            raise ValueError("Policy output does not contain any action features.")
        return normalized


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="R2C cloud adapter: Observations -> OpenPI websocket -> Actions"
    )

    parser.add_argument(
        "--bundle",
        type=str,
        default=None,
        help=(
            "Path to the platform-issued credential bundle zip or unpacked "
            "directory (recommended)"
        ),
    )
    parser.add_argument(
        "--client-config",
        default="config/client_config.yaml",
        help="Path to the original R2C SDK client config YAML",
    )
    parser.add_argument("--project-id", type=str, default=None, help="Project ID")
    parser.add_argument("--device-id", type=str, default=None, help="Device ID")
    parser.add_argument("--client-id", type=str, default=None, help="Client ID")
    parser.add_argument(
        "--endpoints",
        type=str,
        default="",
        help="Comma-separated endpoints, for example: tls/127.0.0.1:7447",
    )

    parser.add_argument(
        "--openpi-host",
        default="",
        help="OpenPI websocket host or ws://host",
    )
    parser.add_argument("--openpi-port", type=int, default=None)
    parser.add_argument("--openpi-api-key", default=None)
    parser.add_argument("--mode", default="peer", choices=["peer", "client"])
    parser.add_argument(
        "--cloud-config",
        default="config/cloud_config.yaml",
        help="Cloud runtime + translator config path.",
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
    session = build_session_simple(args, default_client_id="r2c-cloud-adapter")

    cloud_config = load_yaml_mapping(args.cloud_config)
    openpi_config = cloud_config.get("openpi", {})
    if not isinstance(openpi_config, Mapping):
        openpi_config = {}

    adapter = R2CCloudAdapter(
        session=session,
        config=R2CCloudAdapterConfig(
            openpi_host=args.openpi_host or str(openpi_config.get("host", "")),
            openpi_port=(
                args.openpi_port
                if args.openpi_port is not None
                else openpi_config.get("port")
            ),
            openpi_api_key=args.openpi_api_key or openpi_config.get("api_key"),
            cloud_config_path=args.cloud_config,
        ),
    )
    adapter.start()

    logger.info("Adapter running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping adapter...")
    finally:
        adapter.close()


if __name__ == "__main__":
    main()
