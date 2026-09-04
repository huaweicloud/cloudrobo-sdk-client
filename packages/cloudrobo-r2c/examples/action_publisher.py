"""
Action Publisher Example

Simulates a cloud-side publisher sending action commands to a robot.

Topic:
    {project_id}/{device_id}/inference/actions

Bundle-only usage:
    python examples/action_publisher.py --bundle /path/to/cert_xxx.zip
    python examples/action_publisher.py --bundle /path/to/unpacked_bundle_dir
"""

from __future__ import annotations

import argparse
import logging
import random
import time

from cloudrobo_r2c import R2CClient

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Action Publisher Example")
    parser.add_argument(
        "--bundle",
        type=str,
        required=True,
        help="Path to the platform-issued credential bundle zip or unpacked directory",
    )
    parser.add_argument(
        "--client-id",
        type=str,
        default=None,
        help="Optional client_id override",
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

    logger.info("Connecting with platform credential bundle: %s", args.bundle)
    if args.client_id:
        logger.info("Using client_id override: %s", args.client_id)

    client = R2CClient.connect(args.bundle, client_id=args.client_id)

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