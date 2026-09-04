"""
Mock Subscriber Example

Simulates a cloud-side receiver subscribing to multiple robot-side message types
and printing the parsed content.

Covered message types:
- observation
- action
- joint_state
- end_effector_state
- localization_state
- imu_state
- heartbeat
- schema_observation
- action_chunk
- robot_meta
Recommended usage:
    python examples/mock_sub.py --bundle /path/to/cert_xxx.zip
    python examples/mock_sub.py --bundle /path/to/unpacked_bundle_dir
    python examples/mock_sub.py --bundle /path/to/cert_xxx.zip --duration 30

Optional override:
    python examples/mock_sub.py --bundle /path/to/cert_xxx.zip --target-device-id robot-02

Advanced explicit mode:
    python examples/mock_sub.py --project-id test-tenant --target-device-id robot-01 --client-id mock-sub-01
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Callable, Dict, List, Optional

import cv2
import numpy as np
from google.protobuf.timestamp_pb2 import Timestamp

from cloudrobo_r2c import ClientConfig, R2CClient
from cloudrobo_r2c.common.models import (EndEffectorState, Heartbeat, IMUState,
                                   JointStateMessage, LocalizationState)

logger = logging.getLogger(__name__)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mock cloud subscriber for all protobuf-backed SDK messages"
    )

    # Recommended: bundle mode
    parser.add_argument(
        "--bundle",
        type=str,
        default=None,
        help="Path to the platform-issued credential bundle zip or unpacked directory (recommended)",
    )

    parser.add_argument(
        "--client-config",
        default="config/client_config.yaml",
        help="Path to the original R2C SDK client config YAML",
    )

    # Advanced: explicit ClientConfig mode
    parser.add_argument("--project-id", type=str, default=None, help="Project ID")
    parser.add_argument(
        "--device-id",
        type=str,
        default="subscriber",
        help="Subscriber self device_id in explicit mode",
    )
    parser.add_argument("--client-id", type=str, default=None, help="Client ID")
    parser.add_argument(
        "--endpoints",
        type=str,
        default="",
        help="Comma-separated endpoints for explicit mode, for example: tls/127.0.0.1:7447",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="peer",
        choices=["peer", "client"],
        help="Connection mode for explicit mode",
    )

    # Subscription behavior
    parser.add_argument(
        "--target-device-id",
        type=str,
        default=None,
        help="Target robot device ID to subscribe to; in bundle mode defaults to bundle robot_id",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Run duration in seconds; 0 means run forever",
    )

    return parser.parse_args()


def parse_endpoints(raw: Optional[str]) -> List[str]:
    """Parse comma-separated endpoints."""
    if not raw:
        return []
    return [ep.strip() for ep in raw.split(",") if ep.strip()]


def build_client(args: argparse.Namespace):
    """Build a connected client session from bundle or explicit ClientConfig."""
    if args.bundle:
        logger.info("Connecting with platform credential bundle: %s", args.bundle)
        if args.client_id:
            logger.info("Using client_id override: %s", args.client_id)
        return R2CClient.connect(args.bundle, client_id=args.client_id)

    if args.client_config:
        logger.info("Connecting with client_config: %s", args.client_config)
        client_cfg = ClientConfig.from_yaml(args.client_config)
        return R2CClient.connect(client_cfg)
        
    if not args.project_id:
        raise ValueError("project_id is required when --bundle is not provided")

    resolved_client_id = args.client_id or "mock-sub-01"
    endpoints = parse_endpoints(args.endpoints)

    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=resolved_client_id,
        endpoints=endpoints,
        mode=args.mode,
    )
    config.validate()

    logger.info(
        "Connecting with explicit ClientConfig: project=%s, device=%s, client=%s, mode=%s, endpoints=%s",
        config.project_id,
        config.device_id,
        config.client_id,
        config.mode,
        list(config.endpoints),
    )
    return R2CClient.connect(config)


def resolve_target_device_id(args: argparse.Namespace, client) -> str:
    """Resolve the target device ID for subscription.

    Priority:
      1. --target-device-id
      2. connected session.device_id (bundle robot_id in bundle mode)
    """
    if args.target_device_id:
        return args.target_device_id

    if getattr(client, "device_id", None):
        return client.device_id

    raise ValueError(
        "target device id cannot be resolved automatically; "
        "please specify --target-device-id explicitly"
    )


def _decode_png_bytes(data: bytes) -> Any:
    if np is None:
        return {
            "type": "png",
            "decoded": False,
            "size": len(data),
            "error": "numpy not installed",
        }
    try:
        encoded = np.frombuffer(data, dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    except Exception as e:
        return {"type": "png", "decoded": False, "size": len(data), "error": str(e)}

    if image is None:
        return {
            "type": "png",
            "decoded": False,
            "size": len(data),
            "error": "cv2.imdecode returned None",
        }

    return {"type": "ndarray", "size": image.shape}


def _normalize(data: Any) -> Any:
    """Convert objects into printable structures and avoid dumping large binary blobs directly."""
    if is_dataclass(data) and not isinstance(data, type):
        return _normalize(asdict(data))

    if isinstance(data, Timestamp):
        return {"seconds": data.seconds, "nanos": data.nanos}

    if np is not None and isinstance(data, np.ndarray):
        return {
            "type": "ndarray",
            "shape": list(data.shape),
            "dtype": str(data.dtype),
            "min": float(data.min()) if data.size else None,
            "max": float(data.max()) if data.size else None,
        }

    if isinstance(data, bytes):
        if data.startswith(PNG_SIGNATURE):
            return _decode_png_bytes(data)
        return {"type": "bytes", "size": len(data)}

    if isinstance(data, dict):
        return {k: _normalize(v) for k, v in data.items()}

    if isinstance(data, list):
        return [_normalize(v) for v in data]

    return data


def _log_message(name: str, message: Any) -> None:
    payload = _normalize(message)
    logger.info(
        "\n=== Received %s ===\n%s\n====================",
        name,
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def main() -> None:
    args = parse_args()

    client = build_client(args)
    target_device_id = resolve_target_device_id(args, client)
    topic_prefix = f"{client.project_id}/{target_device_id}"

    logger.info(
        "Connected. project_id=%s, subscriber_device_id=%s, target_device_id=%s",
        client.project_id,
        client.device_id,
        target_device_id,
    )
    logger.info("connection_info=%s", client.connection_info())
    logger.info("Subscribing to topics:")

    client.subscribe_observations(
        lambda m: _log_message("observation", m),
        target_device_id=target_device_id,
    )
    logger.info("  - %s/inference/observations", topic_prefix)

    client.subscribe_actions(
        lambda m: _log_message("action", m),
        target_device_id=target_device_id,
    )
    logger.info("  - %s/inference/actions", topic_prefix)

    state_handlers: Dict[str, tuple[Callable[[bytes], Any], str]] = {
        f"{topic_prefix}/state/joint_states": (
            JointStateMessage.from_protobuf,
            "joint_state",
        ),
        f"{topic_prefix}/state/end_effector_states": (
            EndEffectorState.from_protobuf,
            "end_effector_state",
        ),
        f"{topic_prefix}/state/localization_states": (
            LocalizationState.from_protobuf,
            "localization_state",
        ),
        f"{topic_prefix}/state/imu_states": (
            IMUState.from_protobuf,
            "imu_state",
        ),
        f"{topic_prefix}/state/heartbeats": (
            Heartbeat.from_protobuf,
            "heartbeat",
        ),
    }

    for topic, (parser_fn, name) in state_handlers.items():

        def _wrapper(
            payload: bytes,
            _parser=parser_fn,
            _name=name,
            _topic=topic,
        ) -> None:
            try:
                msg = _parser(payload)
                _log_message(_name, msg)
            except Exception as e:
                logger.warning(
                    "Parse error: topic=%s, error=%s, payload_size=%d",
                    _topic,
                    e,
                    len(payload),
                )

        client.transport.subscribe(topic, _wrapper)
        logger.info("  - %s", topic)

    logger.info("Waiting for messages (Ctrl+C to stop)...")

    start = time.time()
    try:
        while True:
            if args.duration > 0 and time.time() - start >= args.duration:
                logger.info("Duration reached, stopping.")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    finally:
        client.close()
        logger.info("Closed.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    main()