import os
import json
import logging
from typing import Any, Dict

import yaml

from ros_adapter.config import R2CClientConfig

# 配置日志, 加入代码所在行
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s  - %(lineno)d - %(message)s"
)
logger = logging.getLogger(__file__)


ROBOT_R2C_MAPPING_FILE = "./examples/configs/robot_to_r2c_mapping.yaml"


def get_robot_type(path: str) -> str:
    if not os.path.exists(path) or not os.path.isdir(path):
        logger.info(f"path not exist. path:{path}")
        return ""

    try:
        subdirs = [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]
        
        if len(subdirs) != 1:
            logger.info(f"Subdirs error. subdirs count:{len(subdirs)}")
            return ""
        
        subdir_path = os.path.join(path, subdirs[0])
        metadata_file = os.path.join(subdir_path, "metadata.json")
        
        if not os.path.isfile(metadata_file):
            logger.info(f"Metadata file not exist.")
            return ""
        
        try:
            with open(metadata_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            type = data.get("robot_type", "")
            logger.info(f"Get robot type success. type:{type} ")
            return type
        except (OSError, ValueError):
            logger.info(f"Get robot type failed.")
            return ""
    
    except Exception:
        return ""

def build_r2c_config(ros_r2c_mapping_info: Dict[str, Any]) -> R2CClientConfig:
    config = R2CClientConfig()
    ros_to_r2c_info = ros_r2c_mapping_info.get("ros_to_r2c", None)
    if ros_to_r2c_info is None:
        raise Exception("ros_to_r2c not in mapping file!")
    for r2c_data_path, ros_sub_info in ros_to_r2c_info.items():
        path_items = r2c_data_path.split(".")
        if "images" in r2c_data_path:
            config.add_camera(
                name=path_items[-1],
                topic=ros_sub_info["sub_topic_name"],
                is_depth="depth" in r2c_data_path
            )
        elif "joint_states" in r2c_data_path:
            config.set_joint_states(
                name=path_items[-2],
                topic=ros_sub_info["sub_topic_name"],
                joint_names=ros_sub_info["joint_names"]
            )
        elif "end_effector_states" in r2c_data_path:
            config.set_end_effector_state(
                name=path_items[-2],
                topic=ros_sub_info["sub_topic_name"],
                joint_names=ros_sub_info["joint_names"]
            )
        else:
            raise Exception(f"{r2c_data_path} can not be parsed !")
    r2c_to_ros_info = ros_r2c_mapping_info.get("r2c_to_ros", None)
    if r2c_to_ros_info is None:
        raise Exception("r2c_to_ros not in mapping file!")
    for r2c_data_path, ros_pub_info_list in r2c_to_ros_info.items():
        path_items = r2c_data_path.split(".")
        if len(path_items) < 3:
            raise Exception(f"{r2c_data_path} can not be parsed!")
        for pub_idx, ros_pub_info in enumerate(ros_pub_info_list):
            config.add_action_publisher(
                name=path_items[1] + f"_{pub_idx}",
                topic=ros_pub_info["pub_topic_name"],
                queue_size=10,
                joint_names=ros_pub_info["joint_names"],
                r2c_data_path=r2c_data_path
            )

    config.set_robot_to_model_joint_names_mapping(ros_r2c_mapping_info["robot_to_model_joint_names_mapping"])
    return config

def load_mapping_file(file: str) -> Dict[str, Any]:
    with open(file, "r") as f:
        robot_r2c_mapping_info = yaml.safe_load(f)
    return robot_r2c_mapping_info
