"""
Action Publisher Example

Simulates a cloud-side publisher sending action commands to a robot.

Topic:
    {project_id}/{device_id}/inference/actions

Bundle usage:
    python examples/action_publisher.py --bundle /path/to/cert_xxx.zip
    python examples/action_publisher.py --bundle /path/to/unpacked_bundle_dir

Compatible advanced usage (no bundle):
    python examples/action_publisher.py \
        --client-id action-pub-01 \
        --endpoints tcp/127.0.0.1:7447 \
        --mode peer
"""

from __future__ import annotations

import argparse
import logging
import random
import time
from typing import List, Optional

from cloudrobo_r2c import ClientConfig, R2CClient

logger = logging.getLogger(__name__)


def parse_endpoints(raw: Optional[str]) -> List[str]:
    """Parse comma-separated endpoints."""
    if not raw:
        return []
    return [ep.strip() for ep in raw.split(",") if ep.strip()]


def build_client(args: argparse.Namespace):
    """Build client session from bundle or explicit ClientConfig."""
    if args.bundle:
        logger.info("Connecting with platform credential bundle: %s", args.bundle)
        if args.client_id:
            logger.info("Using client_id override: %s", args.client_id)
        return R2CClient.connect(args.bundle, client_id=args.client_id)

    resolved_client_id = args.client_id or "action-pub-01"
    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=resolved_client_id,
        endpoints=parse_endpoints(args.endpoints),
        endpoint_role=args.endpoint_role,
        mode=args.mode,
    )

    logger.info(
        "Connecting with explicit ClientConfig: client_id=%s, project_id=%s, device_id=%s, mode=%s, endpoints=%s",
        config.client_id,
        config.project_id,
        config.device_id,
        config.mode,
        list(config.endpoints),
    )
    return R2CClient.connect(config)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Action Publisher Example")
    parser.add_argument(
        "--bundle",
        type=str,
        default=None,
        help="Path to the platform-issued credential bundle zip or unpacked directory (recommended)",
    )
    parser.add_argument(
        "--project-id",
        type=str,
        default="r2c",
        help="Project ID for explicit ClientConfig mode",
    )
    parser.add_argument(
        "--device-id",
        type=str,
        default="local",
        help="Device ID for explicit ClientConfig mode",
    )
    parser.add_argument(
        "--client-id",
        type=str,
        default=None,
        help="Optional client_id override; defaults to action-pub-01 in explicit mode",
    )
    parser.add_argument(
        "--endpoints",
        type=str,
        default="",
        help="Comma-separated endpoints for explicit ClientConfig mode, e.g. tcp/127.0.0.1:7447",
    )
    parser.add_argument(
        "--endpoint-role",
        type=str,
        default="connect",
        choices=["connect", "listen"],
        help="Connection endpoint role for explicit ClientConfig mode",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="peer",
        choices=["peer", "client"],
        help="Connection mode for explicit ClientConfig mode",
    )
    parser.add_argument(
        "--hz",
        type=float,
        default=1.0,
        help="Publishing frequency in Hz (default: 1.0)",
    )
    return parser.parse_args()


def build_action_payload(sequence: int) -> dict:
    """Build one action payload as a dict."""
    chunk_size = 4
    joint_positions = [
        [round(random.random(), 3) for _ in range(6)]
        for _ in range(chunk_size)
    ]

    return {
        "timestamp": int(time.time() * 1000),
        "id": sequence,
        "chunk_size": chunk_size,
        "joint_states": {
            "names": ["j1", "j2", "j3", "j4", "j5", "j6"],
            "position": joint_positions,
            "velocity": [],
        },
        "end_effector_poses": {},
        "end_effector_states": {
            "names": [],
            "position": [],
            "velocity": [],
        },
        "localization": {
            "odom_pose": [],
            "map_pose": [],
        },
    }


def main() -> None:
    args = parse_args()

    if args.hz <= 0:
        raise ValueError("--hz must be > 0")

    client = build_client(args)

    try:
        logger.info("Connected. connection_info=%s", client.connection_info())
        logger.info(
            "Start publishing actions to device_id=%s at %.3f Hz",
            client.device_id,
            args.hz,
        )

        interval_s = 1.0 / args.hz
        sequence = 0

        while True:
            start_time = time.time()
            sequence += 1

            action_data = build_action_payload(sequence)
            client.publish_actions(action_data)

            logger.info(
                "Sent action #%s to device_id=%s | chunk_size=%s",
                sequence,
                client.device_id,
                action_data["chunk_size"],
            )

            elapsed = time.time() - start_time
            sleep_time = max(0.0, interval_s - elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info("Stopping...")
    finally:
        client.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    main()