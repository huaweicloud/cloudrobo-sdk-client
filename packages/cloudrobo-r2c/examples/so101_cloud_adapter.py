"""
SO101 Cloud Adapter example.

Subscribes to robot-side Observations through the R2C SDK and prints the
observation content. After receiving each Observation, it sends a simulated
Action with all joint values set to 0.0.

Usage examples:
    python examples/so101_cloud_adapter.py
    python examples/so101_cloud_adapter.py --project-id test-tenant --device-id so101-robot-01
    python examples/so101_cloud_adapter.py --duration 30
"""

from __future__ import annotations

import argparse
import logging

from _cloud_adapter_common import build_parser, run_cloud_adapter

logger = logging.getLogger(__name__)

ADAPTER_NAME = "SO101CloudAdapter"
DEFAULT_PROJECT_ID = "test-tenant"
DEFAULT_DEVICE_ID = "so101-robot-01"
DEFAULT_CLIENT_ID = "so101-cloud-adapter"

SO101_ACTION_DEFAULT_JOINT_NAMES = [
    # 手臂关节（5个）
    "joint_1",
    "joint_2",
    "joint_3",
    "joint_4",
    "joint_5",
    # 末端执行器名称（夹爪）
    "joint_6",
]


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = build_parser(
        description="SO101 cloud adapter: subscribe to observations and publish zero-action",
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
    """Run the SO101 cloud adapter example."""
    args = parse_args()
    run_cloud_adapter(
        args=args,
        adapter_name=ADAPTER_NAME,
        action_default_joint_names=SO101_ACTION_DEFAULT_JOINT_NAMES,
        waiting_message_suffix="SO101 observations",
        chunk_size=1,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    main()