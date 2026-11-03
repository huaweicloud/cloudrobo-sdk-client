#!/usr/bin/env python3
"""S05 (华沿/Huayan) cloud adapter example.

Subscribes to robot-side Observations through the R2C SDK and prints the
observation content. After receiving each Observation, it sends a pre-defined
Action with joint positions and gripper values in round-robin order.

Usage examples:
    python examples/s05_cloud_adapter.py
    python examples/s05_cloud_adapter.py
    python examples/s05_cloud_adapter.py --duration 30
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from typing import Any, List, Optional, Sequence

from _cloud_adapter_common import build_parser

logger = logging.getLogger(__name__)

ADAPTER_NAME = "S05CloudAdapter"
DEFAULT_PROJECT_ID = "r2c"
DEFAULT_DEVICE_ID = "local"
DEFAULT_CLIENT_ID = "s05-cloud-adapter"

# S05 joint names (6 arm joints + 1 gripper)
S05_DEFAULT_JOINT_NAMES = [
    "joint_1",
    "joint_2",
    "joint_3",
    "joint_4",
    "joint_5",
    "joint_6",
]

S05_END_EFFECTOR_NAMES = [
    "gripper_1",
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
    chunk_size: int = 10,
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

    payload = {
        "timestamp": now_ms,
        "chunk_size": chunk_size,
        "joint_states": {
            "names": list(joint_names),
            "position": [position] * chunk_size,
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


def run_s05_cloud_adapter(
    args: argparse.Namespace,
    adapter_name: str,
    action_default_joint_names: Sequence[str],
    predefined_actions: List[dict],
    observation_fallback_joint_names: Sequence[str] = None,
    waiting_message_suffix: str = "observations",
) -> None:
    """
    Run the S05 cloud adapter example.

    Subscribes to observations and publishes pre-defined actions in round-robin.
    """
    from cloudrobo_r2c import ClientConfig, R2CClient

    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id,
        endpoint_role=args.endpoint_role,
        endpoints=parse_endpoints(args.endpoints),
        mode="peer",
    )

    logger.info(
        "[%s] Connecting: project=%s, device=%s, client=%s, endpoints=%s, role=%s",
        adapter_name,
        args.project_id,
        args.device_id,
        args.client_id,
        args.endpoints,
        args.endpoint_role,
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
        except AttributeError:
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


def parse_endpoints(raw: Optional[str]) -> list[str]:
    """Parse comma-separated endpoints string into a list."""
    if not raw:
        return []
    return [ep.strip() for ep in raw.split(",") if ep.strip()]


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = build_parser(
        description="S05 cloud adapter: subscribe to observations and publish predefined actions",
        default_project_id=DEFAULT_PROJECT_ID,
        default_device_id=DEFAULT_DEVICE_ID,
        default_client_id=DEFAULT_CLIENT_ID,
        help_project_id="Project ID",
        help_device_id="Target device ID",
        help_client_id="Client ID",
        help_duration="Running duration in seconds; 0 means continuous",
    )
    parser.add_argument(
        "--endpoints",
        type=str,
        default="tcp/0.0.0.0:7447",
        help="Comma-separated endpoints, e.g. tls/127.0.0.1:7447",
    )
    parser.add_argument(
        "--endpoint-role",
        type=str,
        default="listen",
        choices=["listen", "connect"],
        help="Endpoint role: listen or connect",
    )
    return parser.parse_args()


def main() -> None:
    """Run the S05 cloud adapter example."""
    args = parse_args()

    # Pre-defined action sequence (6 joint positions + 1 gripper value)
    # Format: [joint_1, joint_2, joint_3, joint_4, joint_5, joint_6]
    # Note: positions are in radians, gripper is [0.0]=open / [1.0]=close
    predefined_actions = [
        {
            "joint_states": {
                "names": S05_DEFAULT_JOINT_NAMES,
                # HOME: 待机位置 (对应度: 0, -90, 90, 0, 90, 0)
                "position": [0.0, -1.5708, 1.5708, 0.0, 1.5708, 0.0],
            }
        },
        {
            "joint_states": {
                "names": S05_DEFAULT_JOINT_NAMES,
                # OBSERVE: 高位相机观察点 (对应度: 15, -75, 100, -10, 90, 0)
                "position": [0.2618, -1.3090, 1.7453, -0.1745, 1.5708, 0.0],
            }
        },
        {
            "joint_states": {
                "names": S05_DEFAULT_JOINT_NAMES,
                # RETRACT: 避障安全撤回点 (对应度: 0, -60, 60, 0, 90, 0)
                "position": [0.0, -1.0472, 1.0472, 0.0, 1.5708, 0.0],
            }
        },
    ]

    # Validate: must have at least 3 predefined actions
    if len(predefined_actions) < 3:
        raise ValueError(
            f"[{ADAPTER_NAME}] At least 3 predefined actions are required, "
            f"got {len(predefined_actions)}"
        )

    run_s05_cloud_adapter(
        args=args,
        adapter_name=ADAPTER_NAME,
        action_default_joint_names=S05_DEFAULT_JOINT_NAMES,
        predefined_actions=predefined_actions,
        observation_fallback_joint_names=[],
        waiting_message_suffix="S05 observations",
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    main()
