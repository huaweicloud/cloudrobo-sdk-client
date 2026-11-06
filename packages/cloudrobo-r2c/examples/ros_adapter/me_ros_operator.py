#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from typing import Dict, Optional, List
import logging
from io import BytesIO

import cv2
import numpy as np
import rclpy
import tf2_ros
from cv_bridge import CvBridge
from geometry_msgs.msg import PoseStamped
from rclpy.node import Node
from rclpy.qos import QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy
from rclpy.callback_groups import ReentrantCallbackGroup
from sensor_msgs.msg import Image, JointState
from std_msgs.msg import Float64MultiArray, Header
from tf2_ros import TransformException
from tf2_ros.buffer import Buffer
from tf2_ros.transform_listener import TransformListener
from cloudrobo_r2c.common import Actions
import threading

from .config import (ActionPublisherConfig, CameraConfig,
                     EndEffectorPoseConfig, EndEffectorStateConfig,
                     JointStateConfig, R2CClientConfig)

logger = logging.getLogger(__file__)

def find_all_missing_key_paths(dict1: dict, dict2: dict, current_path: str = "") -> list:
    """
    查找 dict1 中存在但 dict2 中缺失的所有 key 的完整路径
    :param dict1: 参照字典（包含完整 key）
    :param dict2: 待检查字典
    :param current_path: 当前递归路径（用于记录嵌套层级）
    :return: list，包含所有缺失 key 的完整路径列表
    """
    logger.info("find_all_missing_key_paths")
    missing_keys = []
    
    # 类型检查：确保当前值是字典类型
    if not isinstance(dict1, dict) or not isinstance(dict2, dict):
        return missing_keys
    
    for key in dict1:
        new_path = f"{current_path}.{key}" if current_path else key
        
        # 当前层 key 存在性检查
        if key not in dict2:
            missing_keys.append(new_path)
            continue
        
        # 递归检查嵌套字典
        if isinstance(dict1[key], dict):
            if not isinstance(dict2[key], dict):
                missing_keys.append(new_path)  # 类型不一致视为缺失
            else:
                missing_keys.extend(
                    find_all_missing_key_paths(dict1[key], dict2[key], new_path)
                )
    return missing_keys

def build_png_bytes_from_ndarray(
    image: np.ndarray
) -> bytes:
    """先生成 ndarray，再用 cv2 编码为 PNG bytes。"""
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("Failed to encode ndarray as PNG with cv2")
    return encoded.tobytes()

def get_timestamp_from_ros_msg(rosMsg):
    return f"{rosMsg.header.stamp.sec}.{rosMsg.header.stamp.nanosec:09d}"

class MeRosOperator(Node):
    """
    1) 订阅 Panda joint_states（可选订阅 RGB）
    2) 用 TF 获取 panda_link0 -> panda_hand 位姿，作为 EE pose（ee_position + ee_orientation）
    3) 组装 pi0_libero 最小必要 obs（含 rgb/proprio/ee_pose/instruction）
    4) 订阅 OpenPI action（7D EE action），收到后调用 openpi_to_jointgroup_position_controller 发布 9D joint position
    """

    def __init__(self, node_name: str, config: R2CClientConfig) -> None:
        super().__init__(node_name)
        self.config = config
        self._bridge = CvBridge()
        self.init_obs()
        self.tf_timeout_sec = 0.05
        self._joint_states_lock = threading.Lock()
        self._camera_lock = threading.Lock()

        self._subscriptions: List[rclpy.subscription.Subscription] = []
        self._action_publishers: Dict[str, rclpy.publisher.Publisher] = {}
        # ---------- TF2 ----------
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

    def init_obs(self):
        self._camera_cache: Dict[str, Dict[str, bytes]] = {}
        self._camera_timestamp_cache: Dict[str, Dict[str, bytes]] = {}
        self._joint_state: Optional[JointState] = None
        self._joint_state_timestamp: Optional[str] = None
        self._end_effector_poses: Dict[str, PoseStamped] = {}
        self._end_effector_states: Optional[JointState] = None

    def init_node(self) -> None:
        """
        初始化ros节点
        """
        self.subscriber_group = ReentrantCallbackGroup()
        self._reset_subscriptions()
        self._setup_subscriptions()
        self._setup_action_publishers()

    def publish_action(
        self, action_data: Actions, step_index: int = 0
    ) -> None:
        logging.info(f"Publish_action action_data: {action_data}, step_index: {step_index}")
        for action_publish_cfg in self.config.action_publishers:
            logger.info(f"Publish {action_publish_cfg.name} topic {action_publish_cfg.topic}")
            action_publisher = self._action_publishers.get(action_publish_cfg.name)
            path_items = action_publish_cfg.r2c_data_path.split(".")
            action_data_item_names = getattr(getattr(action_data, path_items[1]), "names")
            # 使用step_index参数来获取对应时间步的数据
            action_data_item_values = getattr(getattr(action_data, path_items[1]), path_items[2])[step_index]
            action_data_item_dict = {name: value for name, value in zip(action_data_item_names, action_data_item_values)}
            action_data_pub = [action_data_item_dict[joint_name] for joint_name in action_publish_cfg.joint_names]
            msg = Float64MultiArray()
            msg.data = action_data_pub
            logging.info(f"publish_action: {msg}")
            action_publisher.publish(msg)

    def get_frame(self):
        logger.info(f"config cameras: {[cam.name for cam in self.config.cameras]}")
        logger.info(f"sub cameras: {list(self._camera_cache.get('color', {}).keys()) + list(self._camera_cache.get('depth', {}).keys())}")

        if self.config.cameras and \
           len(self.config.cameras) != len(self._camera_cache.get("color", {})) + len(self._camera_cache.get("depth", {})):
            logger.info(f"Waiting for cameras info, got {len(self._camera_cache.get('color', {})) + len(self._camera_cache.get('depth', {}))}/{len(self.config.cameras)}")
            return None
        logger.info(f"self.config.joint_states {self.config.joint_states} {self._joint_state}")
        if self.config.joint_states and not self._joint_state:
            logger.info("Waiting for joint states info")
            return None
        if self.config.end_effector_poses and not self._end_effector_poses:
            logger.info("Waiting for end effector pose info")
            return None
        if self.config.end_effector_states and not self._end_effector_states:
            logger.info("Waiting for end effector states info")
            return None
        # 查看每个数据的时间戳
        timestamp_info = f"joint_states timestamp {self._joint_state_timestamp}"
        logger.info(f"joint_states timestamp {self._joint_state_timestamp}")
        for cams_type, cams in self._camera_timestamp_cache.items():
            for cam_name, cam_val in cams.items():
                logger.info(f"{cams_type} {cam_name} timestamp {cam_val}")
                timestamp_info += f"{cams_type} {cam_name} timestamp {cam_val}"
        frame_data = {
            "images": self._camera_cache,
            "joint_states": self._joint_state,
            "end_effector_poses": self._end_effector_poses,
            "end_effector_states": self._end_effector_states,
            "timestamp_info": timestamp_info
        }

        return frame_data

    def _reset_subscriptions(self) -> None:
        """
        重置订阅
        """
        for sub in self._subscriptions:
            self.destroy_subscription(sub)
        self._subscriptions = []
        self._camera_cache.clear()
        self._joint_state = None
        self._end_effector_poses.clear()
        self._end_effector_states = None

    def _setup_subscriptions(self) -> None:
        """
        设置订阅
        """
        logging.info(f"_setup_subscriptions")
        for cam in self.config.cameras:
            logger.info(f"Create {cam.name} subscriber: {cam.topic}")
            sub = self.create_subscription(
                Image,
                cam.topic,
                lambda msg, cam=cam: self._camera_cb(msg, cam),
                10,
                callback_group=self.subscriber_group
            )
            self._subscriptions.append(sub)

        if self.config.joint_states:
            logger.info(f"Create {self.config.joint_states.name} subscriber: {self.config.joint_states.topic}")
            sub = self.create_subscription(
                JointState,
                self.config.joint_states.topic,
                self._joint_states_cb,
                10,
                callback_group=self.subscriber_group
            )
            self._subscriptions.append(sub)

    def _setup_action_publishers(self) -> None:
        """
        重置action发布器
        """
        logging.info(f"_setup_action_publishers")
        self._action_publishers.clear()

        for ee_cfg in self.config.action_publishers:
            self._action_publishers[ee_cfg.name] = self.create_publisher(
                Float64MultiArray,
                ee_cfg.topic,
                ee_cfg.queue_size,
            )

    def _camera_cb(self, msg: Image, cam: CameraConfig) -> None:
        with self._camera_lock:
            cv_image = self._bridge.imgmsg_to_cv2(msg, desired_encoding="rgb8")
            img_bytes = build_png_bytes_from_ndarray(cv_image)
            if cam.is_depth:
                if "depth" in self._camera_cache:
                    self._camera_cache["depth"][cam.name] = img_bytes
                    self._camera_timestamp_cache["depth"][cam.name] = get_timestamp_from_ros_msg(msg)
                else:
                    self._camera_cache["depth"] = {cam.name: img_bytes}
                    self._camera_timestamp_cache["depth"] = {
                        cam.name: get_timestamp_from_ros_msg(msg)
                    }
            else:
                if "color" in self._camera_cache:
                    self._camera_cache["color"][cam.name] = img_bytes
                    self._camera_timestamp_cache["color"][cam.name] = get_timestamp_from_ros_msg(msg)
                else:
                    self._camera_cache["color"] = {cam.name: img_bytes}
                    self._camera_timestamp_cache["color"] = {
                        cam.name: get_timestamp_from_ros_msg(msg)
                    }

    # ---------- Callbacks ----------
    def _joint_states_cb(self, msg: JointState):
        logger.info(f"Got joint_states ros msg")
        with self._joint_states_lock:
            name_to_pos = dict(zip(msg.name, msg.position))
            logger.info(f"{name_to_pos}")

            try:
                if self.config.joint_states:
                    _joint_state = np.array(
                        [name_to_pos[n] for n in self.config.joint_states.joint_names],
                        dtype=np.float64,
                    )
                else:
                    _joint_state = None

                if self.config.end_effector_states:
                    _end_effector_states = np.array(
                        [name_to_pos[n] for n in self.config.end_effector_states.joint_names],
                        dtype=np.float64,
                    )
                else:
                    _end_effector_states = None
            except KeyError as e:
                logger.warning(f"joint_state : {e}")

                return

            self._joint_state = _joint_state.tolist() if _joint_state is not None else None
            self._joint_state_timestamp = get_timestamp_from_ros_msg(msg)
            self._end_effector_states = _end_effector_states.tolist() if _end_effector_states is not None else None

            # joint_states 更新时顺便刷新一次 EE pose（更及时）
            self._update_ee_pose_from_tf()

    # ---------- TF EE pose ----------
    def _update_ee_pose_from_tf(self) -> bool:
        """
        通过 TF 获取 base_frame -> ee_frame 的变换，填充 last_ee_pos / last_ee_quat
        """

        for pose_cfg in self.config.end_effector_poses:
            try:
                trans = self.tf_buffer.lookup_transform(
                    pose_cfg.base_frame, pose_cfg.ee_frame, rclpy.time.Time()
                )
            except TransformException as e:
                return False

            t = trans.transform.translation
            r = trans.transform.rotation

            ee_pos = np.array([t.x, t.y, t.z], dtype=np.float32)
            # geometry_msgs/Quaternion: x,y,z,w
            ee_quat = np.array([r.x, r.y, r.z, r.w], dtype=np.float32)
            ee_7d = np.concatenate([ee_pos, ee_quat])
            self._end_effector_poses[pose_cfg.name] = ee_7d.tolist()

        return True


if __name__ == "__main__":
    dict_a = {
        "level1": {
            "level2": {
                "key1": 1,
                "key2": 2
            },
            "level3": 3  # 非字典类型
        }
    }

    dict_b = {
        "level1": {
            "level2": {
                "key1": 3,
                "key4": 4  # 缺少 key2
            },
            "level3": 6    # 值不同但 key 存在
        }
    }

    missing_keys = find_all_missing_keys(dict_a, dict_b)
    print(missing_keys)  # 输出: ['level1.level2.key2']
