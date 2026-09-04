import json
import logging
import os
import sys
import time
from typing import Any, Dict

import yaml
from azureloong_app import AzureLoongClient
from jaka_app import JakaClient
from moz1_app import Moz1Client
from robot_common_utils import (ROBOT_R2C_MAPPING_FILE, build_r2c_config,
                                get_robot_type, load_mapping_file)
from ros_adapter.config import R2CClientConfig
from ros_adapter.r2c_client_for_me import R2CClientForME
from so101_app import SO101Client

from cloudrobo_r2c.common import Actions
from cloudrobo_r2c.common.config import ClientConfig as R2CSDKConfig
from cloudrobo_r2c.common.models import Observations, ObservationsH264

# 配置日志, 加入代码所在行
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s  - %(lineno)d - %(message)s"
)
logger = logging.getLogger(__file__)
logger.info(f"work_path: {os.getcwd()}")

# input
WORKSPACE_ID = os.getenv("WORKSPACE_ID", "test-tenant")
DEVICE_ID = os.getenv("EXECUTION_ID", "robot-01")
ROBOT_METADATA_PATH = os.getenv("ROBOT_METADATA_PATH", "/data/workspace/eail/Assets/robots")
ROBOT_TYPE = get_robot_type(ROBOT_METADATA_PATH)
CONTAINER_IP=os.getenv("CONTAINER_IP", "127.0.0.1")
if not ROBOT_TYPE:
    raise Exception("robot type is empty")
PUBLISH_ACTION_RATE = float(os.getenv("PUBLISH_ACTION_RATE", "10.0"))
PUBLISH_OBSERVATION_RATE = float(os.getenv("PUBLISH_OBSERVATION_RATE", "10.0"))

def build_sdk_config() -> R2CSDKConfig:
    sdk_config = R2CSDKConfig(
        project_id=WORKSPACE_ID,
        device_id=f"robot-{DEVICE_ID}",
        client_id="ros2-client-01",
        endpoint_role="listen",
        endpoints=[f"tcp/{CONTAINER_IP}:7447"],
        mode="peer",
        protocol="zenoh"
    )
    logger.info(f"Connecting: project={sdk_config.project_id}, target_device={sdk_config.device_id}, client={sdk_config.client_id}")
    return sdk_config

class R1Client(R2CClientForME):
    def convert_to_observation(self, frame_data):
        end_effector_poses = frame_data.get("end_effector_poses", {})
        poses_names = end_effector_poses.keys()
        poses_values = end_effector_poses.values()

        end_effector_states = frame_data.get("end_effector_states", [])
        end_effector_states_dict = {name: value for name, value in zip(self._config.end_effector_states.joint_names, end_effector_states)}
        logger.info(f"end_effector_states_dict: {end_effector_states_dict}")

        obs_data = {
            "timestamp": int(time.time() * 1000),
            "task": f"{self._task} {frame_data.pop('timestamp_info')}",
            "id": self._sequence,
            "images": frame_data.get("images", {}),
            "joint_states": {
                "names": (
                    [self._config.robot_to_model_joint_names_mapping[joint_name] for joint_name in self._config.joint_states.joint_names]
                    if self._config.joint_states
                    else []
                ),
                "position": frame_data.get("joint_states", []),
                "velocity": [],
                "torque": [],
            },
            "end_effector_poses": {"names": poses_names, "pose": poses_values},
            "end_effector_states": {
                "names": (
                    ["left_gripper", "right_gripper"]
                    if self._config.end_effector_states
                    else []
                ),
                "position": [
                    (end_effector_states_dict["left_gripper_finger_joint1"] + end_effector_states_dict["left_gripper_finger_joint2"]) * 1000,
                    (end_effector_states_dict["right_gripper_finger_joint1"] + end_effector_states_dict["right_gripper_finger_joint2"]) * 1000
                ],
                "force": [],
            },
        }
        logger.info(f"task {obs_data['task']}")
        logger.info(f"obs_data['end_effector_states'] {obs_data['end_effector_states']}")
        image_sources = frame_data.get("images", {})

        if not image_sources:
            raise ValueError("No images found in observation")

        if self._config.image_encode == "h264":
            encoded_payload, _ = self.encoder.encode_images({"images": image_sources})
            images_section = encoded_payload.get("images", {})
            obs_data["images"] = {
                "h264_data": images_section.get("h264_data", b""),
                "metadata": images_section.get("metadata", {}),
            }
            obs = ObservationsH264.from_dict(obs_data)
        else:
            obs_data["images"] = image_sources
            obs = Observations.from_dict(obs_data)

        return obs

    def convert_actions(self, actions: Actions) -> dict[str, list]:
        actions.joint_states.names = [self._config.model_to_robot_joint_names_mapping[joint_name.replace("_exp", "")] for joint_name in actions.joint_states.names]
        end_effector_states_dict = {name: value for name, value in zip(actions.end_effector_states.names, actions.end_effector_states.position[0])}
        actions.end_effector_states.names = [
            "left_gripper_finger_joint1",
            "left_gripper_finger_joint2",
            "right_gripper_finger_joint1",
            "right_gripper_finger_joint2"
        ]
        actions.end_effector_states.position = [[
            end_effector_states_dict["left_gripper_exp"]/200., 
            end_effector_states_dict["left_gripper_exp"]/200.,
            end_effector_states_dict["right_gripper_exp"]/200., 
            end_effector_states_dict["right_gripper_exp"]/200.,
        ]]
        return actions

def run():
    robot_r2c_mapping_info = load_mapping_file(ROBOT_R2C_MAPPING_FILE)
    if ROBOT_TYPE not in robot_r2c_mapping_info:
        raise Exception(f"{ROBOT_TYPE} has no ROS topics config ...")
    if ROBOT_TYPE == "r1":
        client = R1Client("r1_app_node")
    elif ROBOT_TYPE == "qinglong":
        client = AzureLoongClient("qinglong_app_node")
    elif ROBOT_TYPE == "moz1":
        client = Moz1Client("moz1_app_node")
    elif ROBOT_TYPE == "so101":
        client = SO101Client("so101_app_node")
    elif ROBOT_TYPE == "jaka_minicobo":
        client = JakaClient("jaka_app_node")
    else:
        logger.error(f"Unsupported robot type for r2c connection: {ROBOT_TYPE}")
    logger.info(f"{type(client)} created")
    try:
        r1_r2c_mapping_info = robot_r2c_mapping_info[ROBOT_TYPE]
        r2c_config = build_r2c_config(r1_r2c_mapping_info)
        r2c_config.set_observation_rate(PUBLISH_OBSERVATION_RATE)
        r2c_config.set_action_rate(PUBLISH_ACTION_RATE)
        client.configure(
            r2c_config,
            build_sdk_config()
        )
        task = "no use"
        client.execute(task)
    except KeyboardInterrupt:
        pass
    finally:
        client.close()

if __name__ == "__main__":
    run()