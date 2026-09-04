#!/usr/bin/env python3
"""Flexiv cloud adapter example.

Subscribes to robot-side Observations through the R2C SDK and prints the
observation content. After receiving each Observation, it sends a pre-defined
Action with TCP position + quaternion + gripper values.

Usage examples:
    python examples/flexiv_cloud_adapter.py
    python examples/flexiv_cloud_adapter.py --project-id test-tenant --device-id flexiv-robot-01
    python examples/flexiv_cloud_adapter.py --duration 30
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from typing import Any, List, Sequence

from _cloud_adapter_common import build_parser

logger = logging.getLogger(__name__)

ADAPTER_NAME = "FlexivCloudAdapter"
DEFAULT_PROJECT_ID = "test-tenant"
DEFAULT_DEVICE_ID = "flexiv-robot-01"
DEFAULT_CLIENT_ID = "flexiv-cloud-adapter"

# Flexiv joint names for action (8 DoF: TCP position 3 + TCP quaternion 4 + gripper 1)
FLEXIV_DEFAULT_JOINT_NAMES = [
    "tcp_x",
    "tcp_y",
    "tcp_z",
    "tcp_qw",
    "tcp_qx",
    "tcp_qy",
    "tcp_qz",
    "gripper",
]

# Flexiv joint names for euler action (7 DoF: TCP position 3 + TCP euler 3 + gripper 1)
FLEXIV_EULER_JOINT_NAMES = [
    "tcp_x",
    "tcp_y",
    "tcp_z",
    "tcp_roll",
    "tcp_pitch",
    "tcp_yaw",
    "gripper",
]


def normalize_for_logging(data: Any) -> Any:
    """Normalize nested data for readable logging output."""
    from dataclasses import asdict, is_dataclass

    try:
        import numpy as np
    except Exception:
        np = None

    if is_dataclass(data) and not isinstance(data, type):
        return normalize_for_logging(asdict(data))

    if np is not None and isinstance(data, np.ndarray):
        return {
            "type": "ndarray",
            "shape": list(data.shape),
            "dtype": str(data.dtype),
        }

    if isinstance(data, bytes):
        return {"type": "bytes", "size": len(data)}

    if isinstance(data, dict):
        return {k: normalize_for_logging(v) for k, v in data.items()}

    if isinstance(data, list):
        return [normalize_for_logging(v) for v in data]

    return data


def build_predefined_action(
    now_ms: int,
    joint_names: Sequence[str],
    action_index: int,
    predefined_actions: List[dict],
) -> Any:
    """
    Build a pre-defined action payload.

    Args:
        now_ms: Current timestamp in milliseconds
        joint_names: Joint names for the action
        action_index: Index of the pre-defined action to use
        predefined_actions: List of pre-defined action dictionaries

    Returns:
        Actions object
    """
    from cloudrobo_r2c.common.models import Actions

    # Round-robin through predefined actions
    action_data = predefined_actions[action_index % len(predefined_actions)]
    position = action_data["joint_states"]["position"]

    # chunk_size = publish_hz * 2 = 5 * 2 = 10
    # publish_hz=5 表示机器人发布观测的频率（5Hz，即每0.2秒发布一次）
    # chunk_size=10 表示缓冲10个动作，足够覆盖2秒，确保机器人始终有动作可执行
    # 注意：如果动作在2秒内执行不完，请增大chunk_size
    chunk_size = 10
    payload = {
        "timestamp": now_ms,
        "chunk_size": chunk_size,
        "joint_states": {
            "names": list(joint_names),
            "position": [position] * chunk_size,  # 扩展为 chunk_size 个相同的动作
        },
        "end_effector_poses": {},
        "end_effector_states": {
            "names": [],
            "position": [],
            "velocity": [],
            "torque": [],
        },
        "localization": {
            "odom_pose": [],
            "map_pose": [],
        },
    }
    return Actions.from_dict(payload)


def run_flexiv_cloud_adapter(
    args: argparse.Namespace,
    adapter_name: str,
    action_default_joint_names: Sequence[str],
    predefined_actions: List[dict],
    observation_fallback_joint_names: Sequence[str] = None,
    waiting_message_suffix: str = "observations",
) -> None:
    """
    Run the Flexiv cloud adapter example.

    Subscribes to observations and publishes pre-defined actions in round-robin.
    """
    from cloudrobo_r2c import ClientConfig, R2CClient

    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id,
        endpoint_role="listen",
        endpoints=["tcp/0.0.0.0:7447"],
        mode="peer",
    )

    logger.info(
        "[%s] Connecting: project=%s, device=%s, client=%s",
        adapter_name,
        args.project_id,
        args.device_id,
        args.client_id,
    )
    client = R2CClient.connect(config)

    fallback_names = list(observation_fallback_joint_names or [])
    action_index = 0

    def on_observation(observation: Any) -> None:
        nonlocal action_index

        normalized = normalize_for_logging(observation)
        logger.info(
            "\n=== Received observation ===\n%s\n============================",
            json.dumps(normalized, ensure_ascii=False, indent=2),
        )

        try:
            joint_names = list(observation.joint_states.names)
        except Exception:
            joint_names = list(fallback_names)

        # Build and publish pre-defined action
        action = build_predefined_action(
            now_ms=int(time.time() * 1000),
            joint_names=joint_names if joint_names else action_default_joint_names,
            action_index=action_index,
            predefined_actions=predefined_actions,
        )
        client.publish_actions(action)

        # Log action details
        action_pos = action.joint_states.position[0] if action.joint_states.position else []
        logger.info(
            "[%s] Sent predefined action %d: position=%s",
            adapter_name,
            action_index + 1,
            action_pos,
        )

        action_index += 1

    client.subscribe_observations(on_observation, target_device_id=args.device_id)
    logger.info(
        "[%s] Subscribed: %s/%s/inference/observations",
        adapter_name,
        args.project_id,
        args.device_id,
    )
    logger.info("[%s] Waiting for %s (Ctrl+C to stop)...", adapter_name, waiting_message_suffix)

    start = time.time()
    try:
        while True:
            if args.duration > 0 and time.time() - start >= args.duration:
                logger.info("[%s] Duration reached, stopping.", adapter_name)
                break
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("[%s] Interrupted by user.", adapter_name)
    finally:
        client.close()
        logger.info("[%s] Closed.", adapter_name)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = build_parser(
        description="Flexiv cloud adapter: subscribe to observations and publish predefined actions",
        default_project_id=DEFAULT_PROJECT_ID,
        default_device_id=DEFAULT_DEVICE_ID,
        default_client_id=DEFAULT_CLIENT_ID,
        help_project_id="Project ID",
        help_device_id="Target device ID",
        help_client_id="Client ID",
        help_duration="Running duration in seconds; 0 means continuous",
    )
    return parser.parse_args()


def main() -> None:
    """Run the Flexiv cloud adapter example."""
    args = parse_args()

    # Pre-defined action sequence (TCP position + euler + gripper)
    # Format: tcp_x, tcp_y, tcp_z, tcp_roll, tcp_pitch, tcp_yaw, gripper
    predefined_actions = [
        {
            "joint_states": {
                "names": FLEXIV_EULER_JOINT_NAMES,
                "position": [0.4, 0.0, 0.3, 3.1403186309991513, -0.00554339984668028, 2.1970929522741534, 0],
            }
        },
        {
            "joint_states": {
                "names": FLEXIV_EULER_JOINT_NAMES,
                "position": [0.5, 0.1, 0.35, 3.1403186309991513, -0.00554339984668028, 2.1970929522741534, 0],
            }
        },
        {
            "joint_states": {
                "names": FLEXIV_EULER_JOINT_NAMES,
                "position": [0.4, -0.1, 0.3, 3.1403186309991513, -0.00554339984668028, 2.1970929522741534, 0],
            }
        },
    ]

    run_flexiv_cloud_adapter(
        args=args,
        adapter_name=ADAPTER_NAME,
        action_default_joint_names=FLEXIV_DEFAULT_JOINT_NAMES,
        predefined_actions=predefined_actions,
        observation_fallback_joint_names=[],
        waiting_message_suffix="Flexiv observations",
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    main()