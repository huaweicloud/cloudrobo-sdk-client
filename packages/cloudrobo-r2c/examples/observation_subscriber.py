"""
Observation Subscriber Example

Core usage:
    python examples/observation_subscriber.py --bundle /path/to/cert_xxx.zip
    python examples/observation_subscriber.py --bundle /path/to/unpacked_bundle_dir
    python examples/observation_subscriber.py --bundle /path/to/cert_xxx.zip --visualize

Optional override:
    python examples/observation_subscriber.py --bundle /path/to/cert_xxx.zip --target-device-id robot-02

Compatible advanced usage:
    python examples/observation_subscriber.py \
        --project-id test-tenant \
        --device-id subscriber \
        --client-id cloud-monitor-01 \
        --target-device-id robot-01 \
        --mode peer

Topic:
    {project_id}/{target_device_id}/inference/observations
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import List, Optional

import cv2
import numpy as np

from cloudrobo_r2c import ClientConfig, R2CClient
from cloudrobo_r2c.common.models import Observations

logger = logging.getLogger(__name__)

ENABLE_VISUALIZATION = False


def parse_endpoints(raw: Optional[str]) -> List[str]:
    """Parse comma-separated endpoints."""
    if not raw:
        return []
    return [ep.strip() for ep in raw.split(",") if ep.strip()]


def build_client(args: argparse.Namespace):
    """Build client session from bundle or explicit ClientConfig."""
    if args.bundle:
        logger.info("[Cloud] Connecting with platform credential bundle: %s", args.bundle)
        if args.client_id:
            logger.info("[Cloud] client_id override: %s", args.client_id)
        return R2CClient.connect(args.bundle, client_id=args.client_id)

    if not args.project_id:
        raise ValueError("project_id is required when --bundle is not provided")

    resolved_device_id = args.device_id or "subscriber"
    resolved_client_id = args.client_id or "cloud-monitor-01"
    endpoints = parse_endpoints(args.endpoints)

    config = ClientConfig(
        project_id=args.project_id,
        device_id=resolved_device_id,
        client_id=resolved_client_id,
        endpoints=endpoints,
        endpoint_role=args.endpoint_role,
        mode=args.mode,
    )

    logger.info(
        "[Cloud] Connecting with explicit ClientConfig: client_id=%s, project_id=%s, device_id=%s, mode=%s, endpoints=%s",
        config.client_id,
        config.project_id,
        config.device_id,
        config.mode,
        list(config.endpoints),
    )
    return R2CClient.connect(config)


def resolve_target_device_id(args: argparse.Namespace, client) -> str:
    """Resolve subscription target device ID.

    Priority:
      1. --target-device-id
      2. bundle/default connected session.device_id
    """
    if args.target_device_id:
        return args.target_device_id

    if getattr(client, "device_id", None):
        return client.device_id

    raise ValueError(
        "target device id cannot be resolved automatically; "
        "please specify --target-device-id explicitly"
    )


def on_observation(obs: Observations) -> None:
    """
    Callback function for processing received Observations.

    SDK automatically handles:
    1. Protobuf deserialization
    2. H264 decoding when needed
    3. Returning unified Observations objects with images.color as numpy arrays
    """
    try:
        logger.info("=== Received Observation ===")
        logger.info("Timestamp: %s", obs.timestamp)
        logger.info("Task     : %s", obs.task)
        logger.info("ID       : %s", obs.id)

        if getattr(obs, "joint_states", None) and obs.joint_states.names:
            logger.info("Joints   : %s joints", len(obs.joint_states.names))
            logger.info(
                "  Pos    : %s",
                [round(p, 3) for p in obs.joint_states.position],
            )

        if getattr(obs, "images", None) and obs.images.color:
            logger.info("Images   : %s color streams", len(obs.images.color))
            for cam_name, data in obs.images.color.items():
                if isinstance(data, np.ndarray):
                    logger.info(
                        "  - %s: numpy array %s, dtype=%s",
                        cam_name,
                        data.shape,
                        data.dtype,
                    )
                    if ENABLE_VISUALIZATION:
                        cv2.imshow(f"Camera: {cam_name}", data)
                else:
                    size_info = len(data) if hasattr(data, "__len__") else "unknown"
                    logger.info(
                        "  - %s: %s, size=%s",
                        cam_name,
                        type(data).__name__,
                        size_info,
                    )

            if ENABLE_VISUALIZATION:
                cv2.waitKey(1)

        if (
            getattr(obs, "localization", None)
            and getattr(obs.localization, "odom_pose", None)
            and len(obs.localization.odom_pose) >= 3
        ):
            pose = obs.localization.odom_pose
            logger.info(
                "Odom Pose: x=%.2f, y=%.2f, z=%.2f",
                pose[0],
                pose[1],
                pose[2],
            )

        logger.info("==========================")

    except Exception as e:
        logger.exception("[Cloud] Failed to process observation: %s", e)


def main() -> None:
    global ENABLE_VISUALIZATION

    parser = argparse.ArgumentParser(description="Observation Subscriber Example")

    parser.add_argument(
        "--bundle",
        type=str,
        default=None,
        help="Path to platform credential bundle zip or unpacked bundle directory (recommended)",
    )

    parser.add_argument(
        "--project-id",
        type=str,
        default=None,
        help="Project ID for explicit ClientConfig mode",
    )
    parser.add_argument(
        "--device-id",
        type=str,
        default="subscriber",
        help="Subscriber self device_id for explicit ClientConfig mode",
    )
    parser.add_argument(
        "--client-id",
        type=str,
        default=None,
        help="Optional client_id override; in bundle mode SDK can auto-generate if omitted",
    )
    parser.add_argument(
        "--endpoints",
        type=str,
        default="",
        help="Comma-separated endpoints for explicit ClientConfig mode, e.g. tls/127.0.0.1:7447",
    )
    parser.add_argument(
        "--endpoint-role",
        type=str,
        default="connect",
        choices=["connect", "listen"],
        help="Connection endpoint role for explicit ClientConfig mode, e.g. connect",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="peer",
        choices=["peer", "client"],
        help="Connection mode for explicit ClientConfig mode",
    )

    parser.add_argument(
        "--target-device-id",
        type=str,
        default=None,
        help="Optional subscription target device ID; in bundle mode defaults to bundle robot_id",
    )
    parser.add_argument(
        "--visualize",
        "-v",
        action="store_true",
        help="Enable image visualization",
    )
    parser.add_argument(
        "--decode-images",
        action="store_true",
        default=True,
        help="Decode H264 images into numpy arrays before callback (default: enabled)",
    )
    parser.add_argument(
        "--no-decode-images",
        dest="decode_images",
        action="store_false",
        help="Disable image decoding",
    )

    args = parser.parse_args()
    ENABLE_VISUALIZATION = args.visualize

    logger.info("[Cloud] Visualization: %s", "ON" if ENABLE_VISUALIZATION else "OFF")
    logger.info("[Cloud] decode_images : %s", args.decode_images)

    client = build_client(args)

    try:
        conn_info = client.connection_info()
        logger.info("[Cloud] Connection info: %s", conn_info)

        target_device_id = resolve_target_device_id(args, client)
        logger.info("[Cloud] target device : %s", target_device_id)

        client.subscribe_observations(
            on_observation,
            target_device_id=target_device_id,
            decode_images=args.decode_images,
        )

        logger.info("[Cloud] Waiting for data (Ctrl+C to stop)...")
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("[Cloud] Stopping...")
    finally:
        if ENABLE_VISUALIZATION:
            cv2.destroyAllWindows()
        client.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    main()