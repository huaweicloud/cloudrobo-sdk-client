"""Shared CLI utility functions used across entry points and inference modules."""

from __future__ import annotations

import argparse
import getpass
import logging
import os
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from cloudrobo_r2c.client import R2CClient
from cloudrobo_r2c.common.config import ClientConfig, ENDPOINT_ROLE

logger = logging.getLogger(__name__)


def parse_endpoints(raw: Optional[str]) -> list[str]:
    """Split a comma-separated endpoint string into a cleaned list."""
    if not raw:
        return []
    return [endpoint.strip() for endpoint in raw.split(",") if endpoint.strip()]


def parse_log_level(value: str) -> str:
    """Validate and normalize a log level string for use with argparse."""
    normalized = str(value).upper()
    if normalized not in logging._nameToLevel:
        raise argparse.ArgumentTypeError(
            f"Invalid log level: {value}. Expected one of: "
            "CRITICAL, ERROR, WARNING, INFO, DEBUG, NOTSET."
        )
    return normalized


def resolve_password(
    *,
    direct_password: Optional[str],
    password_env: Optional[str],
    should_prompt: bool,
) -> Optional[str]:
    """Resolve a private key password from direct value, env var, or prompt."""
    if direct_password:
        return direct_password

    if password_env:
        value = os.environ.get(password_env)
        if not value:
            raise ValueError(
                f"Environment variable {password_env!r} is not set or is empty"
            )
        return value

    if should_prompt:
        return getpass.getpass("Encrypted private key password: ")

    return None


def load_yaml_mapping(path: Optional[str | Path]) -> Mapping[str, Any]:
    """Load a YAML file and return it as a Mapping, or {} if path is empty."""
    if not path:
        return {}
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, Mapping):
        raise ValueError(f"Config file must contain a mapping: {file_path}")
    return data


def build_session_simple(
    args: argparse.Namespace,
    *,
    default_client_id: str,
):
    """Build an R2C session from CLI args (bundle, client-config, or explicit).

    This is the shared session builder for inference modules.  It does *not*
    handle private-key password resolution — use ``cloudroboclient.build_session``
    for the edge-client path that needs it.
    """
    _endpoint_role = getattr(args, "endpoint_role", None)

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
        # Override endpoints/mode/endpoint_role if provided via CLI
        resolved_eps = parse_endpoints(args.endpoints)
        if resolved_eps or args.mode or _endpoint_role:
            client_config = ClientConfig(
                project_id=client_config.project_id,
                device_id=client_config.device_id,
                client_id=client_config.client_id,
                endpoint_role=_endpoint_role or client_config.endpoint_role,
                endpoints=tuple(resolved_eps) if resolved_eps else client_config.endpoints,
                protocol=client_config.protocol,
                mode=args.mode or client_config.mode,
                authentication=client_config.authentication,
                connect=client_config.connect,
                tls=client_config.tls,
            )
        return R2CClient.connect(client_config)

    if not args.project_id:
        raise ValueError("project_id is required when --bundle is not provided")
    if not args.device_id:
        raise ValueError("device_id is required when --bundle is not provided")

    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id or default_client_id,
        endpoint_role=_endpoint_role or ENDPOINT_ROLE,
        endpoints=parse_endpoints(args.endpoints),
        mode=args.mode,
    )
    config.validate()
    return R2CClient.connect(config)
