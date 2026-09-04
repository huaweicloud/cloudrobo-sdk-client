"""
Moz1 Cloud Adapter example.

Subscribes to robot-side Observations through the R2C SDK and prints the
observation content. After receiving each Observation, it sends a simulated
Action with all joint values set to 0.0.

Usage examples:
    python examples/moz1_cloud_adapter.py
    python examples/moz1_cloud_adapter.py --project-id test-tenant --device-id moz1-robot-01
    python examples/moz1_cloud_adapter.py --duration 30
"""

from __future__ import annotations

import argparse
import logging

from _cloud_adapter_common import build_parser, run_cloud_adapter

logger = logging.getLogger(__name__)

ADAPTER_NAME = "Moz1CloudAdapter"
DEFAULT_PROJECT_ID = "test-tenant"
DEFAULT_DEVICE_ID = "moz1-robot-01"
DEFAULT_CLIENT_ID = "moz1-cloud-adapter"

MOZL_DEFAULT_JOINT_NAMES = [
    # 左臂（7个）
    "left_arm_exp_1",
    "left_arm_exp_2",
    "left_arm_exp_3",
    "left_arm_exp_4",
    "left_arm_exp_5",
    "left_arm_exp_6",
    "left_arm_exp_7",
    # 右臂（7个）
    "right_arm_exp_1",
    "right_arm_exp_2",
    "right_arm_exp_3",
    "right_arm_exp_4",
    "right_arm_exp_5",
    "right_arm_exp_6",
    "right_arm_exp_7",

]

MOZL_END_EFFECTOR_NAMES = [
    # 左夹爪
    "left_gripper_exp",
    # 右夹爪
    "right_gripper_exp",
]


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = build_parser(
        description="Moz1 cloud adapter: subscribe to observations and publish zero-action",
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
    """Run the Moz1 cloud adapter example."""
    args = parse_args()
    run_cloud_adapter(
        args=args,
        adapter_name=ADAPTER_NAME,
        action_default_joint_names=MOZL_DEFAULT_JOINT_NAMES,
        observation_fallback_joint_names=[],
        waiting_message_suffix="observations",
        chunk_size=3,
        end_effector_names=MOZL_END_EFFECTOR_NAMES,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    main()
