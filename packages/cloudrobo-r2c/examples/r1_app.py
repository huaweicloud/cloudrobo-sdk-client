"""MetaEngine仿真环境R1 机器人侧 R2CClient 示例模块。

该模块负责：
- 按配置订阅相机、关节、末端等数据并组装 observation。
- 使用 r2c sdk 发布 observation。
- 订阅云端 actions 并转发到机器人侧 topic。
"""

from __future__ import annotations

import logging

from cloudrobo_r2c.common import Actions

from .ros_adapter.config import (ActionPublisherConfig, CameraConfig,
                                 JointStateConfig, R2CClientConfig,
                                 R2CSDKConfig)
from .ros_adapter.r2c_client_for_me import R2CClientForME

logger = logging.getLogger(__name__)


def build_default_config() -> R2CClientConfig:
    config = R2CClientConfig()
    config.add_camera(name="left_wrist", topic="/camera_left/camera_image_color")
    config.add_camera(name="right_wrist", topic="/camera_right/camera_image_color")
    config.add_camera(name="high", topic="/camera_head_left/camera_image_color")
    config.set_joint_states(
        "right_arm_joints",
        "/joint_states",
        [
            "right_arm_joint1",
            "right_arm_joint2",
            "right_arm_joint3",
            "right_arm_joint4",
            "right_arm_joint5",
            "right_arm_joint6",
        ],
    )
    config.add_end_effector_pose(name="right_gripper_pose", ee_frame="right_gripper_link", base_frame="base_link")
    config.set_end_effector_state(
        "right_gripper_joints",
        "/joint_states",
        ["right_gripper_finger_joint1", "right_gripper_finger_joint2"],
    )
    
    config.add_action_publisher(
        name="right_arm_joints",
        topic="/right_arm_position_controller/commands",
        rate=10,
    )
    config.add_action_publisher(
        name="right_gripper_joints",
        topic="/right_gripper_position_controller/commands",
        rate=10,
    )
    return config


def build_sdk_config() -> R2CSDKConfig:
    sdk_config = R2CSDKConfig(
        project_id="test-tenant",
        device_id="robot-01",
        client_id="ros2-client-01",
        endpoints=["tcp/0.0.0.0:7447"],
        mode="peer",
        protocol="zenoh"
    )
    return sdk_config


class R1Client(R2CClientForME):
    def convert_actions(self, actions: Actions) -> dict[str, list]:
        print(f"riclient convert_actions")
        action_data = []
        if actions.joint_states.position:
            for position in actions.joint_states.position:
                action_data_item = {
                    'right_arm_joints': position[:6]
                }
                action_data.append(action_data_item)
            print(f"action_data {action_data}")

        return action_data


def main() -> None:
    try:
        client = R1Client("r1_app_node")
        client.configure(build_default_config(), build_sdk_config())
        task = "pick up the block"

        client.execute(task)
    except KeyboardInterrupt:
        pass
    finally:
        client.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    main()
