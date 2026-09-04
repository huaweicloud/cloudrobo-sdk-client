"""
R1 Cloud Adapter example.

Subscribes to robot-side Observations through the R2C SDK and prints the
observation content. After receiving each Observation, it sends a simulated
Action with all joint values set to 0.0.

Usage examples:
    python examples/r1_cloud_adapter.py
    python examples/r1_cloud_adapter.py --project-id test-tenant --device-id robot-01
    python examples/r1_cloud_adapter.py --duration 30
"""

from __future__ import annotations

import argparse
import logging

from _cloud_adapter_common import build_parser, run_cloud_adapter

logger = logging.getLogger(__name__)

ADAPTER_NAME = "R1CloudAdapter"
DEFAULT_PROJECT_ID = "test-tenant"
DEFAULT_DEVICE_ID = "robot-01"
DEFAULT_CLIENT_ID = "r1-cloud-adapter"

R1_DEFAULT_JOINT_NAMES = [
    "j1",
    "j2",
    "j3",
    "j4",
    "j5",
    "j6",
]


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = build_parser(
        description="Cloud adapter: subscribe to observations and publish zero-action",
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
    """Run the R1 cloud adapter example."""
    args = parse_args()
    run_cloud_adapter(
        args=args,
        adapter_name=ADAPTER_NAME,
        action_default_joint_names=R1_DEFAULT_JOINT_NAMES,
        observation_fallback_joint_names=[],
        waiting_message_suffix="observations",
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    main()