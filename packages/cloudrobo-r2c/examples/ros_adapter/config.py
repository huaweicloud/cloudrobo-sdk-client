import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from cloudrobo_r2c.common.config import ClientConfig as R2CSDKConfig


@dataclass(slots=True)
class CameraConfig:
    name: str
    topic: str
    is_depth: bool = False


@dataclass(slots=True)
class JointStateConfig:
    name: str
    topic: str
    joint_names: list[str]


@dataclass(slots=True)
class EndEffectorPoseConfig:
    name: str
    ee_frame: str
    base_frame: str


@dataclass(slots=True)
class EndEffectorStateConfig:
    name: str
    topic: str
    joint_names: list[str]


@dataclass(slots=True)
class ActionPublisherConfig:
    name: str
    topic: str
    queue_size: int
    joint_names: List[str]
    r2c_data_path: str


@dataclass(slots=True)
class R2CClientConfig:
    cameras: List[CameraConfig] = field(default_factory=list)
    joint_states: Optional[JointStateConfig] = None
    end_effector_poses: List[EndEffectorPoseConfig] = field(default_factory=list)
    end_effector_states: Optional[EndEffectorStateConfig] = None
    observation_rate_hz: float = 10.0
    action_rate_hz: float = 10.0
    action_publishers: List[ActionPublisherConfig] = field(default_factory=list)
    image_encode: str = "raw"
    robot_to_model_joint_names_mapping: Dict[str, str] = field(default_factory=dict)
    model_to_robot_joint_names_mapping: Dict[str, str] = field(default_factory=dict)

    def add_camera(self, name: str, topic: str, is_depth: bool = False) -> None:
        self.cameras.append(CameraConfig(name=name, topic=topic, is_depth=is_depth))

    def set_joint_states(self, name: str, topic: str, joint_names: list[str]) -> None:
        self.joint_states = JointStateConfig(
            name=name, topic=topic, joint_names=joint_names
        )

    def add_end_effector_pose(self, name: str, ee_frame: str, base_frame: str) -> None:
        self.end_effector_poses.append(
            EndEffectorPoseConfig(name=name, ee_frame=ee_frame, base_frame=base_frame)
        )

    def set_end_effector_state(
        self, name: str, topic: str, joint_names: list[str]
    ) -> None:
        self.end_effector_states = EndEffectorStateConfig(
            name=name, topic=topic, joint_names=joint_names
        )

    def set_observation_rate(self, rate_hz: float) -> None:
        self.observation_rate_hz = rate_hz
    
    def set_action_rate(self, rate_hz: float) -> None:
        self.action_rate_hz = rate_hz

    def add_action_publisher(self, name: str, topic: str, queue_size: int, joint_names: List[str], r2c_data_path: str) -> None:
        self.action_publishers.append(
            ActionPublisherConfig(name=name, topic=topic, queue_size=queue_size, joint_names=joint_names, r2c_data_path=r2c_data_path)
        )

    def set_robot_to_model_joint_names_mapping(self, mapping_info: Dict[str, str]):
        self.robot_to_model_joint_names_mapping = mapping_info
        self.model_to_robot_joint_names_mapping = {value: key for key, value in mapping_info.items()}
