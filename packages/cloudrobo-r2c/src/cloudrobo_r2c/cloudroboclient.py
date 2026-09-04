from __future__ import annotations

import os
import getpass
import argparse
import logging
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import yaml
from pydantic import ValidationError

from cloudrobo_r2c.client import R2CClient
from cloudrobo_r2c.common.exceptions import R2CConnectionError, R2CSDKError
from cloudrobo_r2c.common.cli_utils import (
    parse_endpoints,
    parse_log_level,
    resolve_password,
)
from cloudrobo_r2c.common.config import ClientConfig, ENDPOINT_ROLE
from cloudrobo_r2c.common.credential_bundle import (
    CredentialBundleError,
    bundle_requires_private_key_password,
)
from cloudrobo_r2c.common.utils.recorder import ObservationRecorder
from cloudrobo_r2c.sync_client import SyncRobotClient

logger = logging.getLogger(__name__)


def parse_log_level(value: str) -> str:
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


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run R2C robot edge client with config-driven hardware adapter, "
            "mapper bridge and threading-based control loop."
        )
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
        help="Path to the R2C client config YAML.",
    )

    parser.add_argument(
        "--private-key-password",
        default=None,
        help=(
            "Password for encrypted server_key.pem. "
            "Avoid using this on shared shells if shell history is a concern."
        ),
    )
    parser.add_argument(
        "--private-key-password-env",
        default=None,
        help="Read private key password from environment variable name",
    )

    prompt_group = parser.add_mutually_exclusive_group()
    prompt_group.add_argument(
        "--prompt-password",
        dest="prompt_password",
        action="store_true",
        default=True,
        help=(
            "Prompt for private key password only when an encrypted private key is detected "
            "(default: enabled)"
        ),
    )
    prompt_group.add_argument(
        "--no-prompt-password",
        dest="prompt_password",
        action="store_false",
        help="Never prompt for private key password; fail if one is required",
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
        "--mode",
        type=str,
        default="peer",
        choices=["peer", "client"],
        help="Connection mode for explicit mode",
    )
    parser.add_argument(
        "--endpoint-role",
        type=str,
        default=None,
        choices=["connect", "listen"],
        help="Zenoh endpoint role: 'connect' (active, default) or 'listen' (passive).",
    )

    parser.add_argument(
        "--robot-config",
        default="config/robot_dummy_config.yaml",
        help="Path to edge mapping/runtime/hardware config.",
    )
    parser.add_argument(
        "--hardware-class",
        type=str,
        default=None,
        help=(
            "Optional dotted class path for custom IRobotHardwareAdapter. "
            "Example: my_pkg.my_module.MyHardwareAdapter"
        ),
    )
    parser.add_argument(
        "--translator-class",
        type=str,
        default=None,
        help=(
            "Optional dotted class path for custom IDeviceTranslator. "
            "Example: my_pkg.my_module.MyTranslator"
        ),
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Run duration in seconds; 0 or negative value means run forever",
    )
    parser.add_argument(
        "--log-level",
        type=parse_log_level,
        default="INFO",
        help="Python logging level, e.g. DEBUG/INFO/WARNING.",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Optional path to a rotating log file (100 MB max, 5 backups).",
    )
    parser.add_argument(
        "--record",
        type=str,
        default=None,
        help="Path to save recorded observations (.pkl) for later playback.",
    )

    return parser.parse_args(argv)


def load_yaml(path: str | Path) -> Mapping[str, Any]:
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, Mapping):
        raise ValueError(f"Config file must contain a mapping: {file_path}")
    try:
        from cloudrobo_r2c.common.robot_config_schema import RobotConfig

        validated = RobotConfig.model_validate(data)
        return dict(validated.model_dump(exclude_none=True))
    except ValidationError as exc:
        raise ValueError(
            f"Invalid robot config in {file_path}:\n{exc}"
        ) from exc


def _private_key_requires_password(args: argparse.Namespace) -> bool:
    if args.bundle:
        return bundle_requires_private_key_password(args.bundle)

    if args.client_config:
        return ClientConfig.private_key_requires_password_from_yaml(args.client_config)

    return False


def build_session(args: argparse.Namespace):
    has_explicit_password = bool(
        args.private_key_password or args.private_key_password_env
    )

    requires_password = False
    if not has_explicit_password:
        requires_password = _private_key_requires_password(args)

    if requires_password:
        if args.prompt_password:
            logger.debug("Encrypted private key detected; prompting for password.")
        else:
            raise ValueError(
                "Encrypted private key detected, but password prompting is disabled. "
                "Provide --private-key-password or --private-key-password-env, "
                "or enable prompting."
            )
    elif args.bundle or args.client_config:
        logger.debug("Unencrypted private key detected; password prompt skipped.")

    password = resolve_password(
        direct_password=args.private_key_password,
        password_env=args.private_key_password_env,
        should_prompt=bool(args.prompt_password and requires_password),
    )

    if args.bundle:
        logger.info("Connecting with platform credential bundle: %s", args.bundle)
        return R2CClient.connect(
            args.bundle,
            client_id=args.client_id,
            private_key_password=password,
        )

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
            private_key_password=password,
        )

    if not args.project_id:
        raise ValueError("project_id is required when --bundle is not provided")
    if not args.device_id:
        raise ValueError("device_id is required when --bundle is not provided")

    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id or "sync-robot-client",
        endpoint_role=args.endpoint_role or ENDPOINT_ROLE,
        endpoints=parse_endpoints(args.endpoints),
        mode=args.mode,
    )
    config.validate()
    return R2CClient.connect(config, private_key_password=password)


def build_sync_robot_client(
    *,
    robot_config: Mapping[str, Any],
    session: Any,
    hardware_class: Optional[str] = None,
    translator_class: Optional[str] = None,
    recorder: Optional[ObservationRecorder] = None,
) -> SyncRobotClient:
    return SyncRobotClient.from_config(
        session=session,
        robot_config=robot_config,
        hardware_class=hardware_class,
        translator_class=translator_class,
        recorder=recorder,
    )


def _maybe_start_heartbeats(*, session: Any, robot_config: Mapping[str, Any]) -> None:
    runtime_cfg = robot_config.get("runtime", {})
    if not isinstance(runtime_cfg, Mapping):
        return

    raw_hb_cfg = runtime_cfg.get("heartbeat", {})
    if not isinstance(raw_hb_cfg, Mapping):
        enabled = bool(raw_hb_cfg)
        hb_cfg: Mapping[str, Any] = {}
    else:
        hb_cfg = raw_hb_cfg
        enabled = bool(hb_cfg.get("enabled", True))

    if not enabled:
        logger.info("Heartbeat auto publish is disabled by config.")
        return

    interval_ms = int(hb_cfg.get("interval_ms", 5000))
    jitter_ms = int(hb_cfg.get("jitter_ms", 500))
    status = str(hb_cfg.get("status", "ONLINE"))
    mode = str(hb_cfg.get("mode", "AUTO"))

    if interval_ms <= 0:
        raise ValueError("runtime.heartbeat.interval_ms must be > 0")
    if jitter_ms < 0:
        raise ValueError("runtime.heartbeat.jitter_ms must be >= 0")

    session.start_heartbeats(
        provider=lambda: {
            "timestamp": int(time.time() * 1000),
            "status": status,
            "mode": mode,
        },
        interval_ms=interval_ms,
        jitter_ms=jitter_ms,
    )
    logger.info(
        "Heartbeat auto publish started (interval_ms=%s, jitter_ms=%s, status=%s, mode=%s).",
        interval_ms,
        jitter_ms,
        status,
        mode,
    )


def _log_translator_summary(translator: Any, current_logger: Any) -> None:
    """Log a summary of the device translator configuration."""
    try:
        lines = ["----- Translator -------------------------------------------------------------------------"]

        # ConfigurableDeviceTranslator stores the device----r2c mapper as _obs_mapper
        d2r = getattr(translator, "_obs_mapper", None) or getattr(translator, "_device_to_r2c_mapper", None)
        if d2r and getattr(d2r, "rules", None):
            lines.append("  device_to_r2c mappings:")
            for rule in d2r.rules:
                src = rule.source or (",".join(rule.source_paths) if rule.source_paths else None) or "(default)"
                required = "" if rule.required else " (optional)"
                transforms = _fmt_transforms(rule.transforms)
                lines.append(f"    {src} ---- {rule.target}{required}{transforms}")

        r2d = getattr(translator, "_r2c_to_device_mapper", None)
        if r2d and getattr(r2d, "rules", None):
            lines.append("  r2c_to_device mappings:")
            for rule in r2d.rules:
                idx = f"[{rule.source_index}]" if rule.source_index is not None else ""
                transforms = _fmt_transforms(rule.transforms)
                lines.append(f"    {rule.source}{idx} ---- {rule.target}{transforms}")

        lines.append("---------------------------------------------------------------------------------------")
        for line in lines:
            current_logger.info(line)
    except Exception:
        current_logger.debug("Failed to log translator summary", exc_info=True)


def _fmt_transforms(transforms: Any) -> str:
    """Format a list of TransformSpec into a compact string."""
    if not transforms:
        return ""
    parts: list[str] = []
    for t in transforms:
        name = getattr(t, "name", str(t))
        cfg = getattr(t, "config", None)
        if cfg and isinstance(cfg, dict) and len(cfg) == 1:
            # single-key config like {quality: 70} ---- "name(quality=70)"
            k, v = next(iter(cfg.items()))
            parts.append(f"{name}({k}={v})")
        elif cfg:
            parts.append(f"{name}({cfg})")
        else:
            parts.append(name)
    return "  [" + ", ".join(parts) + "]"


def run_edge_client(args: argparse.Namespace) -> None:
    robot_config = load_yaml(args.robot_config)
    robot_config["_config_dir"] = os.path.dirname(os.path.abspath(args.robot_config))
    runtime_cfg = robot_config.get("runtime", {})
    if isinstance(runtime_cfg, Mapping):
        keyboard_cfg = runtime_cfg.get("keyboard_control", {})
        if isinstance(keyboard_cfg, Mapping) and bool(
            keyboard_cfg.get("enabled", False)
        ):
            logger.info("Runtime keyboard control is enabled by robot config.")
    session = build_session(args)
    _maybe_start_heartbeats(session=session, robot_config=robot_config)

    recorder = None
    if args.record:
        recorder = ObservationRecorder(args.record)
        logger.info("Recording observations to %s", args.record)

    robot_client = build_sync_robot_client(
        robot_config=robot_config,
        session=session,
        hardware_class=args.hardware_class,
        translator_class=args.translator_class,
        recorder=recorder,
    )

    _log_translator_summary(robot_client.translator, logger)

    logger.info(
        "Starting cloudroboclient with robot_config=%s duration=%.3fs",
        args.robot_config,
        float(args.duration),
    )

    stop_event = threading.Event()
    try:
        robot_client.hardware_adapter.connect()
        robot_client.start()

        if args.duration and args.duration > 0:
            stop_event.wait(timeout=float(args.duration))
        else:
            while robot_client.running and not stop_event.wait(timeout=1.0):
                pass
    finally:
        robot_client.stop()
        try:
            robot_client.hardware_adapter.disconnect()
        finally:
            session.close()


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    # Resolve config paths against the config shipped inside the installed
    # package, while still honoring explicit paths / source-checkout relative
    # paths (see cloudrobo_r2c.common.config_path).
    from cloudrobo_r2c.common.config_path import resolve_config_path

    if args.client_config:
        args.client_config = resolve_config_path(args.client_config)
    if args.robot_config:
        args.robot_config = resolve_config_path(args.robot_config)

    log_level = getattr(logging, str(args.log_level).upper())
    log_format = "%(asctime)s %(levelname)s [%(name)s] %(message)s"

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        log_path = Path(args.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_path, maxBytes=100 * 1024 * 1024, backupCount=5
            )
        )

    # The parent `cloudrobo` group already calls logging.basicConfig() when it
    # starts, which installs handlers on the root logger. A subsequent
    # basicConfig() is therefore a documented no-op, so --log-level would have
    # no effect. Explicitly (re)configure the root logger instead.
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    formatter = logging.Formatter(log_format)
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.setLevel(log_level)
        root_logger.addHandler(handler)

    try:
        run_edge_client(args)
    except KeyboardInterrupt:
        logger.info("Stopped by keyboard interrupt")
    except (R2CSDKError, CredentialBundleError, FileNotFoundError, ValueError) as e:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "cloudroboclient exited with recoverable error",
                exc_info=True,
            )
        logger.error("%s", e)
        raise SystemExit(1)


if __name__ == "__main__":
    main()