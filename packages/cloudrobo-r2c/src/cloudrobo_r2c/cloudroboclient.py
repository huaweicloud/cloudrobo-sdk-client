from __future__ import annotations

import os
import getpass
import argparse
import logging
import threading
import time
from dataclasses import replace
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

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
from cloudrobo_r2c.common.config_path import resolve_config_path
from cloudrobo_r2c.common.credential_bundle import (
    CredentialBundleError,
    bundle_requires_private_key_password,
)
from cloudrobo_r2c.common.utils.recorder import ObservationRecorder
from cloudrobo_r2c.client.event_manager import event_manager
from cloudrobo_r2c.client.topic_manager import start_topic_runtime
from cloudrobo_r2c.sync_client import SyncRobotClient, _maybe_start_heartbeats
from cloudrobo_r2c.forwarder import (
    open_forwarder_sessions,
    parse_forwarder_config,
    start_forwarder,
)

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
        "--forwarder-config",
        default=None,
        help=(
            "in-process forwarder config (forwarder_session + "
            "topic_defaults + routes). Only started when both --bundle and "
            "--forwarder-config are given; otherwise it degrades to pure local "
            "main line."
        ),
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

    parser.add_argument("--project-id", type=str, default="r2c", help="Project ID")
    parser.add_argument("--device-id", type=str, default="local", help="Device ID")
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
        default=None,
        choices=["peer", "client"],
        help=(
            "Connection mode. When --client-config is given and --mode is "
            "omitted, the mode is taken from the YAML."
        ),
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
        robot_config = dict(validated.model_dump(exclude_none=True))
    except ValidationError as exc:
        raise ValueError(f"Invalid robot config in {file_path}:\n{exc}") from exc

    # mapping rule validation (aggregated, reuses the runtime parser)
    # Fails fast before build_session() establishes any connection so
    # config mistakes (e.g. an extension rule without dtype/shape) exit
    # the program with a specific message instead of surfacing later as
    # misleading runtime errors (e.g. "Missing required fields timestamp,
    # task").
    from cloudrobo_r2c.core.config_mapper import collect_mapping_config_errors

    rule_errors = collect_mapping_config_errors(robot_config)
    if rule_errors:
        lines = "\n".join(f"  - {err}" for err in rule_errors)
        raise ValueError(
            f"Invalid robot config in {file_path} (mapping rules):\n{lines}"
        )

    return robot_config


def _private_key_requires_password(args: argparse.Namespace) -> bool:
    if args.bundle:
        return bundle_requires_private_key_password(args.bundle)

    if args.client_config:
        return ClientConfig.private_key_requires_password_from_yaml(args.client_config)

    return False


def build_session(args: argparse.Namespace, forwarder_active: bool = False):
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

    if args.bundle and not forwarder_active:
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
            endpoint_role=args.endpoint_role
        )
        # 回退：未显式指定 --mode / --endpoint-role 时（采用 YAML/默认的
        # connect 语义），若 connect 连接失败则降级为 peer 模式重试一次。
        if args.mode is None and args.endpoint_role is None:
            try:
                session = R2CClient.connect(
                    client_config,
                    private_key_password=password,
                )
                if forwarder_active:
                    return session, password
                return session
            except R2CConnectionError as exc:
                logger.warning(
                    "Main session connect failed (%s); retrying with "
                    "peer mode",
                    exc,
                )
                client_config = replace(client_config, mode="peer", endpoint_role="listen")
        session = R2CClient.connect(
            client_config,
            private_key_password=password,
        )
        if forwarder_active:
            return session, password
        return session

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
        mode=args.mode or "peer",
    )
    config.validate()
    session = R2CClient.connect(config, private_key_password=password)
    if forwarder_active:
        return session, password
    return session


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


def _forwarder_enabled(args) -> bool:
    return bool(args.bundle) and bool(args.forwarder_config)


def build_forwarder_config(args):
    config = parse_forwarder_config(args.forwarder_config)
    if config.forwarder_session is None:
        raise ValueError(
            "forwarder_config must declare forwarder_session"
        )
    return config


def start_inprocess_forwarder(*, config, main_session, bundle, connect=None,
                              private_key_password=None,
                              name='inprocess-forwarder'):
    sessions = open_forwarder_sessions(
        config, main_session=main_session, bundle=bundle, connect=connect,
        private_key_password=private_key_password,
    )
    try:
        return start_forwarder(config, sessions, name=name)
    except Exception:
        # start_forwarder 失败（如 proto 类名错误）时清理已打开的
        # forwarder_session（main 由调用方管理，不在此关闭），避免云连接泄漏
        logger.error(
            "in-process forwarder start failed, cleaning up sessions"
        )
        for sname, fs_session in sessions.items():
            if sname == "main":
                continue
            try:
                fs_session.close()
            except Exception:
                logger.debug(
                    "session %s close failed (ignored)", sname
                )
        raise


def _log_keyboard_control(runtime_cfg: Any) -> None:
    """记录 runtime.keyboard_control.enabled 状态（存在且开启时提示）。"""
    if not isinstance(runtime_cfg, Mapping):
        return
    keyboard_cfg = runtime_cfg.get("keyboard_control", {})
    if isinstance(keyboard_cfg, Mapping) and bool(keyboard_cfg.get("enabled", False)):
        logger.info("Runtime keyboard control is enabled by robot config.")


def _setup_forwarder(args: argparse.Namespace, session: Any, forwarder_active: bool,
                     private_key_password: Optional[Union[str, bytes]] = None):
    """按需启动 in-process forwarder，返回 (inprocess_forwarder, forwarder_config)。"""
    if not forwarder_active:
        return None, None
    forwarder_config = build_forwarder_config(args)
    inprocess_forwarder = start_inprocess_forwarder(
        config=forwarder_config,
        main_session=session,
        bundle=args.bundle,
        private_key_password=private_key_password,
    )
    logger.info(
        "In-process forwarder started (main -> forwarder_session, %d routes)",
        len(forwarder_config.routes),
    )
    return inprocess_forwarder, forwarder_config


def _build_recorder(args: argparse.Namespace) -> Optional[ObservationRecorder]:
    """按 --record 构建观察记录器；未指定时返回 None。"""
    if not args.record:
        return None
    recorder = ObservationRecorder(args.record)
    logger.info("Recording observations to %s", args.record)
    return recorder


def _start_topic_runtime(topics_cfg: Any, session: Any, robot_client: Any):
    """按 hardware.topics 配置启动 TopicRuntime（无配置则不启动）。"""
    if not topics_cfg:
        return None
    return start_topic_runtime(
        topics_cfg,
        session,
        callback_owner=robot_client.hardware_adapter,
        process_name="cloudroboclient",
    )


def _start_camera_streaming(
    session: Any, robot_client: Any, camera_streaming_cfg: Any
) -> bool:
    """按 camera_streaming 配置段启动 LiveKit 相机推流。

    依赖硬件适配器实现 ICameraFrameSource（共享帧槽），未实现则推流不可用。
    返回是否已启动推流服务（用于关闭流程清理）。
    """
    if camera_streaming_cfg is None:
        return False
    if not (isinstance(camera_streaming_cfg, Mapping) and bool(
        camera_streaming_cfg.get("enabled", True)
    )):
        logger.info("camera_streaming configured but disabled; skipped")
        return False
    try:
        session.start_camera_streaming(
            adapter=robot_client.hardware_adapter,
            camera_streaming_cfg=camera_streaming_cfg,
        )
        return True
    except Exception:
        logger.exception(
            "Failed to start camera streaming service; continuing without it"
        )
        return False


def _run_main_loop(robot_client: Any, stop_event: threading.Event, args: Any) -> None:
    """按 duration 等待主循环结束（有限时长或直至 running 停止）。"""
    if args.duration and args.duration > 0:
        stop_event.wait(timeout=float(args.duration))
        return
    while robot_client.running and not stop_event.wait(timeout=1.0):
        pass


def _stop_topic_runtime(topic_runtime: Any) -> None:
    """停止 TopicRuntime（未启动则忽略）。"""
    if topic_runtime is None:
        return
    try:
        topic_runtime.stop()
    except Exception:
        logger.exception("topic runtime stop failed")


def _stop_camera_streaming(session: Any, camera_streaming_active: bool) -> None:
    """停止相机推流服务（未启动则忽略）。"""
    if not camera_streaming_active:
        return
    try:
        session.stop_camera_streaming()
    except Exception:
        logger.exception("camera streaming cleanup failed")


def _close_forwarder_sessions(inprocess_forwarder: Any) -> None:
    """关闭 in-process forwarder 的全部子会话（未启动则忽略）。"""
    if inprocess_forwarder is None:
        return
    try:
        for fs_session in inprocess_forwarder._sessions.values():
            fs_session.close()
    except Exception:
        logger.exception("forwarder session close failed")


def _shutdown(
    topic_runtime: Any,
    robot_client: Any,
    session: Any,
    camera_streaming_active: bool,
    inprocess_forwarder: Any,
) -> None:
    """停机清理：停止 topic runtime、机器人、相机推流与各会话。"""
    _stop_topic_runtime(topic_runtime)
    robot_client.stop()
    try:
        robot_client.hardware_adapter.disconnect()
    finally:
        # 相机推流清理（停止取帧/断开 Room/停止续签；不释放相机设备——
        # 设备已在 adapter.disconnect() 释放）
        _stop_camera_streaming(session, camera_streaming_active)
        # EventManager.close 丢弃未发缓存并打汇总日志（先于 session.close）
        event_manager.close()
        session.close()
        _close_forwarder_sessions(inprocess_forwarder)


def run_edge_client(args: argparse.Namespace) -> None:
    # 解析配置文件路径：当前目录找不到时回退到 cloudrobo_r2c 包安装目录下查找
    args.robot_config = resolve_config_path(args.robot_config)
    args.bundle = resolve_config_path(args.bundle)
    args.client_config = resolve_config_path(args.client_config)
    args.forwarder_config = resolve_config_path(args.forwarder_config)

    robot_config = dict(load_yaml(args.robot_config))
    robot_config["_config_dir"] = os.path.dirname(os.path.abspath(args.robot_config))
    _log_keyboard_control(robot_config.get("runtime", {}))

    forwarder_active = _forwarder_enabled(args)
    session = build_session(args, forwarder_active=forwarder_active)
    forwarder_password = None
    if forwarder_active and isinstance(session, tuple):
        session, forwarder_password = session
    try:
        inprocess_forwarder, _ = _setup_forwarder(
            args, session, forwarder_active,
            private_key_password=forwarder_password,
        )
    except Exception:
        # forwarder 启动失败时关闭已建立的 session，避免泄漏
        logger.error("forwarder setup failed, closing session")
        try:
            session.close()
        except Exception:
            logger.debug("session close failed (ignored)")
        raise

    event_manager.configure(session)
    _maybe_start_heartbeats(session=session, robot_config=robot_config)

    recorder = _build_recorder(args)

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
    topics_cfg = (robot_config.get("hardware") or {}).get("topics") or {}
    # 相机推流配置（可选能力；未配置 camera_streaming 段则跳过启动）
    camera_streaming_cfg = robot_config.get("camera_streaming") or None
    topic_runtime = None
    camera_streaming_active=None
    try:
        robot_client.hardware_adapter.connect()
        # adapter.connect() 先初始化模块单例（状态缓存/采集器/告警等），
        # TopicRuntime 发布线程才能拿到有效 collector（robot_app 时代
        # 同序约束）；零配置零启动——官方既有机型路径不受影响
        topic_runtime = _start_topic_runtime(topics_cfg, session, robot_client)
        camera_streaming_active = _start_camera_streaming(
            session, robot_client, camera_streaming_cfg
        )
        robot_client.start()
        _run_main_loop(robot_client, stop_event, args)
    finally:
        _shutdown(
            topic_runtime=topic_runtime,
            robot_client=robot_client,
            session=session,
            camera_streaming_active=camera_streaming_active,
            inprocess_forwarder=inprocess_forwarder,
        )


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
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

    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=handlers,
    )

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
