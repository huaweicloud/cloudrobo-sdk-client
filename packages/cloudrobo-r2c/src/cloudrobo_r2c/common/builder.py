"""Observation 构建器，提供流式 API。"""

from __future__ import annotations

import time
from typing import List, Sequence

from cloudrobo_r2c.common.models import (EndEffectorPoses, EndEffectorStates,
                                   ImageGroups, JointStates, Localization,
                                   Observations, PointCloud, Pose7D)


class ObservationBuilder:
    """
    用于构建 Observations 对象的辅助类。
    """

    def __init__(self) -> None:
        self._timestamp = int(time.time() * 1000)
        self._task = ""
        self._images = ImageGroups()
        self._joint_states = JointStates()
        self._ee_poses = EndEffectorPoses()
        self._ee_states = EndEffectorStates()
        self._localization = Localization()
        self._pointclouds: List[PointCloud] = []

    def set_timestamp(self, timestamp_ms: int) -> ObservationBuilder:
        self._timestamp = timestamp_ms
        return self

    def set_task(self, task: str) -> ObservationBuilder:
        self._task = task
        return self

    def add_image(self, name: str, data: bytes, is_depth: bool = False) -> ObservationBuilder:
        """添加图像数据。"""
        target = self._images.depth if is_depth else self._images.color
        target[name] = data
        return self

    def set_joint_states(
        self, 
        names: Sequence[str], 
        positions: Sequence[float], 
        velocities: Sequence[float] | None = None, 
        torques: Sequence[float] | None = None
    ) -> ObservationBuilder:
        """设置关节状态。"""
        if len(names) != len(positions):
            raise ValueError("names and positions length must be equals")
        if velocities and len(velocities) != len(names):
            raise ValueError("velocities length and names length must be equals")
        if torques and len(torques) != len(names):
            raise ValueError("torques length and names length must be equals")

        self._joint_states.names = list(names)
        self._joint_states.position = list(positions)
        if velocities:
            self._joint_states.velocity = list(velocities)
        if torques:
            self._joint_states.torque = list(torques)
        return self

    def add_end_effector_pose(self, name: str, pose_7d: Sequence[float]) -> ObservationBuilder:
        """添加末端位姿 [x, y, z, qx, qy, qz, qw]。"""
        if len(pose_7d) != 7:
            raise ValueError(f"Pose must have 7 elements, got {len(pose_7d)}")
        
        self._ee_poses.names.append(name)
        self._ee_poses.pose.append(Pose7D(list(pose_7d)))
        return self

    def add_end_effector_state(
        self, name: str, position: float, force: float = 0.0
    ) -> ObservationBuilder:
        self._ee_states.names.append(name)
        self._ee_states.position.append(position)
        self._ee_states.force.append(force)
        return self

    def set_localization(
        self, 
        odom_pose_7d: Sequence[float] | None = None, 
        map_pose_7d: Sequence[float] | None = None
    ) -> ObservationBuilder:
        if odom_pose_7d and len(odom_pose_7d) != 7:
            raise ValueError(f"odom_pose_7d must have 7 elements, got {len(odom_pose_7d)}")
            
        if map_pose_7d and len(map_pose_7d) != 7:
            raise ValueError(f"map_pose_7d must have 7 elements, got {len(map_pose_7d)}")

        if odom_pose_7d:
            self._localization.odom_pose = list(odom_pose_7d)
        
        if map_pose_7d:
            self._localization.map_pose = list(map_pose_7d)
        return self

    def add_pointcloud(self, name: str, data: bytes) -> ObservationBuilder:
        self._pointclouds.append(PointCloud(data=data, name=name))
        return self

    def build(self) -> Observations:
        """构建并返回 Observations 对象。"""
        return Observations(
            timestamp=self._timestamp,
            task=self._task,
            images=self._images,
            joint_states=self._joint_states,
            end_effector_poses=self._ee_poses,
            end_effector_states=self._ee_states,
            localization=self._localization,
            pointclouds=self._pointclouds,
        )
