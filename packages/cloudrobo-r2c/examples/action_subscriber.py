"""
Action Subscriber Example

Simulates a robot-side subscriber receiving action commands from the cloud.

Topic:
    {project_id}/{target_device_id}/inference/actions

Bundle-only usage:
    python examples/action_subscriber.py --bundle /path/to/cert_xxx.zip
    python examples/action_subscriber.py --bundle /path/to/unpacked_bundle_dir
"""

from __future__ import annotations

import argparse
import logging
import time

from cloudrobo_r2c import R2CClient
from cloudrobo_r2c.common.models import Actions

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Action Subscriber Example")
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
    return parser.parse_args()


def on_action(action: Actions) -> None:
    """Handle received action messages."""
    try:
        logger.info("=== Received Action ===")
        logger.info("Timestamp : %s", action.timestamp)
        logger.info("ID        : %s", action.id)
        logger.info("Chunk Size: %s", action.chunk_size)

        if action.joint_states.names:
            logger.info("Joints    : %s joints", len(action.joint_states.names))
            logger.info("Names     : %s", action.joint_states.names)

            if action.joint_states.position:
                logger.info("Steps     : %s", len(action.joint_states.position))
                for i, step in enumerate(action.joint_states.position):
                    logger.info("  Step %s: %s", i, [round(p, 3) for p in step])

        if action.end_effector_poses:
            logger.info("EE Poses  : %s effectors", len(action.end_effector_poses))
            for name, chunk in action.end_effector_poses.items():
                logger.info("  %s: %s poses", name, len(chunk.pose))

        if action.end_effector_states.names:
            logger.info(
                "EE States : %s effectors",
                len(action.end_effector_states.names),
            )

        logger.info("======================")

    except Exception as e:
        logger.exception("Failed to process action: %s", e)


def main() -> None:
    args = parse_args()

    logger.info("Connecting with platform credential bundle: %s", args.bundle)
    if args.client_id:
        logger.info("Using client_id override: %s", args.client_id)

    client = R2CClient.connect(args.bundle, client_id=args.client_id)

    try:
        logger.info("Connected. connection_info=%s", client.connection_info())
        logger.info("Subscribing to actions for device_id=%s", client.device_id)

        client.subscribe_actions(
            on_action,
            target_device_id=client.device_id,
        )

        logger.info("Waiting for actions (Ctrl+C to stop)...")
        while True:
            time.sleep(1)

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