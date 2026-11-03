"""Cloud adapter for Observations/Actions inference flow with multi-route support."""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

import yaml

from cloudrobo_r2c.common.cli_utils import resolve_api_key
from cloudrobo_r2c.common.models import Observations
from cloudrobo_r2c.core.interfaces import IModelTranslator
from cloudrobo_r2c.core.transformers import PAYLOAD_CODECS
from cloudrobo_r2c.inference.r2c_config_normalizer import normalize_config
from cloudrobo_r2c.inference.route_resolver import RouteResolver
from cloudrobo_r2c.translators.model_translator import ConfigurableModelTranslator

logger = logging.getLogger(__name__)

@dataclass
class R2CCloudAdapterConfig:
    """Runtime configuration for ``R2CCloudAdapter``."""

    openpi_host: str = ""
    openpi_port: Optional[int] = None
    cloud_config_path: Optional[str] = None
    route_name: Optional[str] = None
    host_url: str = ""
    # Runtime API key supplied at startup (e.g. masked stdin). In-memory only,
    # never persisted to a config file.
    api_key: Optional[str] = None


class R2CCloudAdapter:
    """Subscribe ``Observations``, call policy via route resolver, publish ``Actions``."""

    def __init__(
        self,
        session: Any,
        config: R2CCloudAdapterConfig,
        policy_client: Optional[Any] = None,
        model_translator: Optional[IModelTranslator] = None,
        route_resolver: Optional[RouteResolver] = None,
    ) -> None:
        self.config = config
        self._session = session

        # An explicitly injected translator (tests / advanced use) is always kept
        # as-is; otherwise the translator is built/refreshed from config.
        self._explicit_model_translator = model_translator

        cloud_config = self._load_cloud_config(config.cloud_config_path)

        if route_resolver is not None:
            # New mode: use RouteResolver (per-route policy client + per-route
            # model translator, synced on each observation).
            self._route_resolver = route_resolver
            self._policy_client = None
            self._model_translator = model_translator
            self._model_translator_route: Optional[str] = None
        else:
            # Legacy mode: single policy client + model translator built from
            # the top-level r2c_to_model / model_to_r2c mappings in cloud config.
            self._route_resolver = None
            self._policy_client = policy_client or self._create_openpi_policy_client()
            self._model_translator = model_translator or ConfigurableModelTranslator(
                cloud_config
            )
            self._model_translator_route: Optional[str] = None

    def close(self) -> None:
        if self._route_resolver is not None:
            self._route_resolver.close()
        # Legacy mode may hold a standalone policy client (e.g. openpi's
        # WebsocketClientPolicy with a live WebSocket connection); release it to
        # avoid leaking the underlying connection. Defensive getattr for clients
        # that do not expose close().
        if self._policy_client is not None:
            client_close_method = getattr(self._policy_client, "close", None)
            if client_close_method:
                client_close_method()
            self._policy_client = None

    def _sync_model_translator(self, route: Optional[Any]) -> None:
        """Keep ``self._model_translator`` aligned with the active route.

        In multi-route mode each route carries its own ``r2c_to_model`` /
        ``model_to_r2c`` mapping rules, so the translator is (re)built from the
        active route's config whenever the active route changes. An explicitly
        injected translator (tests / advanced use) is always kept as-is.
        """
        if self._explicit_model_translator is not None:
            return
        if route is None:
            # Legacy mode: translator was built from cloud config at construction.
            return
        if self._model_translator_route == route.config.name:
            return
        route_config = {
            "r2c_to_model": {"mappings": route.config.r2c_to_model or []},
            "model_to_r2c": {"mappings": route.config.model_to_r2c or []},
        }
        self._model_translator = ConfigurableModelTranslator(route_config)
        self._model_translator_route = route.config.name

    def r2c_to_model_input(self, observations): 
        return self._model_translator.r2c_to_model_input(observations)

    def model_input_serialize(self, route, model_input):
        serial_transform = (
            (route.config.serial_transform or route.default_serial_transform)
            if route is not None
            else None
        )
        if serial_transform:
            model_input = self._apply_payload_transforms(
                model_input, serial_transform
            )
            
        return model_input

    def model_inference(self, route, model_input):
        policy_client = (
            route.policy_client if route is not None else self._policy_client
        )
        return policy_client.infer(model_input)

    def model_output_deserialize(self, route, policy_output):
        deserial_transform = (
            (route.config.deserial_transform or route.default_deserial_transform)
            if route is not None
            else None
        )
        if deserial_transform:
            policy_output = self._apply_payload_transforms(
                policy_output, deserial_transform
            )
            
        return policy_output

    def model_output_to_r2c(self, id, model_output):
        actions = self._model_translator.model_output_to_r2c(model_output)
        actions.id = id
        return actions

    def on_observations_routed(self, observations: Observations) -> None:
        """Process one observations message.

        Unified entry for both the multi-route (RouteResolver) and legacy
        (policy_client) modes. Model <-> R2C conversion is always delegated to
        ``self._model_translator``; route-specific serial/deserial transforms and
        stop-condition handling only apply in multi-route mode.
        """

        logger.info("Translating Observation.")
        route = (
            self._route_resolver.active_route
            if self._route_resolver is not None
            else None
        )
        # Ensure the model translator matches the active route's mapping config
        # (each route may carry its own r2c_to_model / model_to_r2c rules).
        self._sync_model_translator(route)

        model_input = self.r2c_to_model_input(observations)

        logger.info("R2C observation translated, serializing.")
        serialized_msg = self.model_input_serialize(route, model_input)

        logger.info("Model input serialized, calling infer service.")
        serialized_msg = self.model_inference(route, serialized_msg)

        logger.info("Model output received, deserializing.")
        model_output = self.model_output_deserialize(route, serialized_msg)

        logger.info("Model output deserialized, translating to R2C action.")
        actions = self.model_output_to_r2c(observations.id, model_output)

        route_suffix = (
            f" (route={self._route_resolver.active_route_name})"
            if self._route_resolver is not None
            else ""
        )
        logger.info(
            "Publishing actions for id=%s with chunk_size=%s%s",
            observations.id,
            actions.chunk_size,
            route_suffix,
        )
        self._session.publish_actions(actions)
        logger.info("Action published.")

    @staticmethod
    def _apply_payload_transforms(
        payload: Any, transforms: Sequence[Any]
    ) -> Any:
        """Apply a chain of payload-level transforms."""
        result = payload
        for spec in transforms:
            func = PAYLOAD_CODECS.get(spec.name)
            if func is None:
                raise ValueError(f"Unknown payload transform: {spec.name!r}")
            result = func(result, spec.config)
        return result

    @staticmethod
    def _load_cloud_config(config_path: Optional[str]) -> Mapping[str, Any]:
        if not config_path:
            return {}
        with Path(config_path).open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, Mapping):
            raise ValueError(f"Cloud config must be a mapping: {config_path}")
        return data

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

        kwargs: Dict[str, Any] = {
            "host": self.config.openpi_host,
            "port": self.config.openpi_port,
        }
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        return WebsocketClientPolicy(**kwargs)


def parse_endpoints(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [endpoint.strip() for endpoint in raw.split(",") if endpoint.strip()]


def build_session(args: argparse.Namespace):
    from cloudrobo_r2c import ClientConfig, R2CClient

    if args.bundle:
        logger.info("Connecting with platform credential bundle: %s", args.bundle)
        return R2CClient.connect(args.bundle, client_id=args.client_id)

    if args.client_config:
        logger.info("Connecting with client config: %s", args.client_config)
        client_config = ClientConfig.from_yaml(
            args.client_config,
            args.project_id,
            args.device_id,
            args.client_id,
            endpoints=parse_endpoints(args.endpoints) or None,
            mode=args.mode,
        )
        return R2CClient.connect(
            client_config,
        )

    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id or "r2c-cloud-adapter",
        endpoints=parse_endpoints(args.endpoints),
        mode=args.mode,
    )
    config.validate()
    return R2CClient.connect(config)


def _resolve_host_url(args: argparse.Namespace) -> str:
    """Resolve the host URL for route mode (CLI > env var > openpi fallback)."""
    host_url = args.host_url or os.environ.get("MODEL_HOST_URL", "")
    if host_url or not args.openpi_host:
        return host_url
    if args.openpi_port is not None:
        return f"{args.openpi_host}:{args.openpi_port}"
    return args.openpi_host


def _build_route_adapter(
    args: argparse.Namespace,
    session: Any,
    cloud_config: Mapping[str, Any],
    host_url: str,
    api_key: str,
) -> R2CCloudAdapter:
    """Build a multi-route adapter, sync its model translator, select the route."""
    normalized = normalize_config(cloud_config, host_url=host_url)
    resolver = RouteResolver(normalized, api_key=api_key)
    adapter = R2CCloudAdapter(
        session=session,
        config=R2CCloudAdapterConfig(
            cloud_config_path=args.cloud_config,
            route_name=args.route,
            host_url=host_url,
        ),
        route_resolver=resolver,
    )
    resolver.select_route(adapter.config, route_name=args.route)
    return adapter


def _build_legacy_adapter(
    args: argparse.Namespace, cloud_config: Mapping[str, Any], api_key: str
) -> R2CCloudAdapter:
    """Build a legacy single-route adapter from the top-level openpi section."""
    openpi_config = cloud_config.get("openpi", {})
    if not isinstance(openpi_config, Mapping):
        openpi_config = {}
    return R2CCloudAdapter(
        config=R2CCloudAdapterConfig(
            openpi_host=args.openpi_host or str(openpi_config.get("host", "")),
            openpi_port=(
                args.openpi_port
                if args.openpi_port is not None
                else openpi_config.get("port")
            ),
            cloud_config_path=args.cloud_config,
            api_key=api_key,
        ),
    )


def _is_route_schema(schema_version: str) -> bool:
    """True when the schema version uses the new multi-route format.

    v2.2~v3.2 legacy single-route versions are normalised into route mode,
    as are the v3.3~v3.9 route versions.
    """
    legacy_single_route_versions = {"v2.2", "v3.0", "v3.1", "v3.2"}
    route_versions = {
        "v3.3", "v3.4", "v3.5", "v3.6", "v3.7", "v3.8", "v3.9",
    }
    return (
        schema_version in legacy_single_route_versions
        or schema_version in route_versions
    )


def _drain_observation_queue(
    obs_queue: "queue.Queue[Observations]",
    adapter: R2CCloudAdapter,
    session: Any,
) -> None:
    """Run the latest-wins observation loop until interrupted or an error."""
    logger.info("Adapter running. Press Ctrl+C to stop.")
    while True:
        try:
            observations = obs_queue.get(timeout=1)
        except queue.Empty:
            logger.warning("Still waiting for observation.")
            continue
        try:
            adapter.on_observations_routed(observations)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            logger.exception(
                "Failed processing observations id=%s: %s",
                observations.id,
                exc,
            )
    logger.info("Stopping adapter...")
    adapter.close()
    session.close()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="R2C cloud adapter: Observations -> Policy -> Actions (multi-route)"
    )

    # Connection args
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
    parser.add_argument("--project-id", type=str, default="r2c", help="Project ID")
    parser.add_argument("--device-id", type=str, default="local", help="Target Device ID")
    parser.add_argument(
        "--task",
        type=str,
        default=None,
        help="Override the task field of received observations before inference",
    )
    parser.add_argument("--client-id", type=str, default=None, help="Client ID")
    parser.add_argument(
        "--endpoints",
        type=str,
        default="",
        help="Comma-separated endpoints, for example: tls/127.0.0.1:7447",
    )
    parser.add_argument("--mode", default="peer", choices=["peer", "client"])

    # Route config args (new)
    parser.add_argument(
        "--route",
        default=None,
        help="Route name to select (overrides default_route in config).",
    )
    parser.add_argument(
        "--host-url",
        default="",
        help="Host URL for endpoint template variable {{HOST_URL}} replacement.",
    )
    parser.add_argument(
        "--stop-condition",
        default=None,
        help='Global stop condition in JSON, e.g. \'{"max_iter_num": 100}\'.',
    )
    parser.add_argument(
        "--cloud-config",
        default="config/r2c_config.yaml",
        help="Path to r2c config YAML (v3.4) or legacy cloud config (v2.2).",
    )

    parser.add_argument(
        "--api-key-stdin",
        action="store_true",
        help=(
            "Read the model API key from a masked stdin prompt (shows * while "
            "typing) and inject it for transport-layer auth at runtime. The key "
            "is kept in memory only and never written to any config file."
        ),
    )

    # Legacy args (deprecated)
    parser.add_argument(
        "--openpi-host",
        default="",
        help="[Deprecated] OpenPI websocket host or ws://host. Use --host-url + r2c_config.yaml instead.",
    )
    parser.add_argument(
        "--openpi-port",
        type=int,
        default=None,
        help="[Deprecated] OpenPI websocket port.",
    )

    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Python logging level, e.g. DEBUG/INFO/WARNING.",
    )
    return parser.parse_args(argv)

def subscribe_observation(args, session, obs_process_handler):
    """Start subscription loop (non-blocking); keep process alive in caller."""
    project_id = session.project_id
    target_device_id = args.device_id
    logger.info(
        "Subscribing observations from %s/%s/inference/observations",
        project_id,
        target_device_id,
    )
    session.subscribe_observations(
        obs_process_handler,
        target_device_id=target_device_id,
    )

def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    # Deprecation warnings for legacy args
    if args.openpi_host:
        logger.warning(
            "--openpi-host is deprecated. Use --host-url and r2c_config.yaml routes instead."
        )

    session = build_session(args)

    # Read the model API key from a masked stdin prompt when requested.
    # The value stays in memory only — never written to a config file.
    api_key = resolve_api_key(should_prompt=args.api_key_stdin)

    # Load and normalize cloud config
    cloud_config = R2CCloudAdapter._load_cloud_config(args.cloud_config)

    # Check if config has routes (v3.3~v3.9) or is legacy single-route (v2.2~v3.2)
    schema_version = str(cloud_config.get("schema_version", ""))

    # 版本划分规则（与 CloudConfigNormalizer.normalize 保持一致）：
    #   v2.2 ~ v3.2（含）→ 单路由旧格式（走 normalize_config 统一 route 模式）
    #   v3.3 ~ v3.9（不含 v4.0）→ 新版 route 格式
    #   其余版本 → 走传统 openpi 兼容分支（或由正常流程处理）
    if _is_route_schema(schema_version):
        # Use new route-based mode.
        # Priority: --host-url CLI > MODEL_HOST_URL env var > default.
        host_url = _resolve_host_url(args)
        adapter = _build_route_adapter(args, session, cloud_config, host_url, api_key)
    else:
        # Legacy mode: use openpi args directly
        adapter = _build_legacy_adapter(args, cloud_config, api_key)

    # Capacity-1 "latest-wins" observation queue. The subscription callback
    # (``on_observations``) only enqueues the most recent unprocessed
    # observation; the heavy map->infer->publish pipeline runs in the main
    # loop via :meth:`process_once`, decoupling subscription from inference
    # and never blocking the transport callback thread.
    obs_queue: "queue.Queue[Observations]" = queue.Queue(maxsize=1)

    def on_observations(observations: Observations) -> None:
        logger.info("Recvive Observation.")
        if args.task:
            observations.task = args.task
        try:
            obs_queue.put_nowait(observations)
        except queue.Full:
            # Slot full: a newer observation arrived before the main loop drained
            # the previous one. Drop the stale observation, keep the latest.
            try:
                obs_queue.get_nowait()
            except queue.Empty:
                pass
            obs_queue.put_nowait(observations)

    subscribe_observation(args, session, on_observations)

    _drain_observation_queue(obs_queue, adapter, session)


if __name__ == "__main__":
    main()
