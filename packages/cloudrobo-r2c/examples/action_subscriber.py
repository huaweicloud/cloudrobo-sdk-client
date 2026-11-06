"""
Action Subscriber Example

Simulates a robot-side subscriber receiving action commands from the cloud.

Topic:
    {project_id}/{target_device_id}/inference/actions

Bundle usage:
    python examples/action_subscriber.py --bundle /path/to/cert_xxx.zip
    python examples/action_subscriber.py --bundle /path/to/unpacked_bundle_dir

Compatible advanced usage (no bundle):
    python examples/action_subscriber.py
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import List, Optional

from cloudrobo_r2c import ClientConfig, R2CClient
from cloudrobo_r2c.common.models import Actions

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

    if not args.project_id:
        raise ValueError("project_id is required when --bundle is not provided")

    resolved_device_id = args.device_id or "local"
    resolved_client_id = args.client_id or "action-sub-01"
    config = ClientConfig(
        project_id=args.project_id,
        device_id=resolved_device_id,
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Action Subscriber Example")
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
        help="Subscriber self device_id for explicit ClientConfig mode",
    )
    parser.add_argument(
        "--client-id",
        type=str,
        default=None,
        help="Optional client_id override; defaults to action-sub-01 in explicit mode",
    )
    parser.add_argument(
        "--endpoints",
        type=str,
        default="tcp/127.0.0.1:7447",
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
        "--target-device-id",
        type=str,
        default="local",
        help="Optional subscription target device ID; in bundle mode defaults to bundle robot_id",
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

    client = build_client(args)

    try:
        logger.info("Connected. connection_info=%s", client.connection_info())

        target_device_id = resolve_target_device_id(args, client)
        logger.info("Subscribing to actions for device_id=%s", target_device_id)

        client.subscribe_actions(
            on_action,
            target_device_id=target_device_id,
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