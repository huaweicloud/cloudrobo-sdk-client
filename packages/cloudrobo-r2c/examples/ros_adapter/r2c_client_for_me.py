"""ROS2 机器人侧 R2CClient 示例模块。

该模块负责：
- 按配置订阅相机、关节、末端等数据并组装 observation。
- 使用 r2c sdk 发布 observation。
- 订阅云端 actions 并转发到机器人侧 topic。
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
import rclpy
from cv_bridge import CvBridge
from geometry_msgs.msg import PoseStamped
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import Image, JointState
from std_msgs.msg import Float64MultiArray
from cloudrobo_r2c.client import R2CClient
from cloudrobo_r2c.common.builder import ObservationBuilder
from cloudrobo_r2c.common.config import ClientConfig as R2CSDKConfig
from cloudrobo_r2c.common.models import Actions, Observations, ObservationsH264
from cloudrobo_r2c.common.utils.h264_encoder import H264ImageEncoder

from .me_ros_operator import MeRosOperator

# 配置日志
logger = logging.getLogger(__file__)


# --- 1. 检查策略抽象类 ---
class CompletionStrategy(ABC):
    @abstractmethod
    def is_finished(self) -> bool:
        pass


# --- 2. 具体策略：基于时间的超时策略 ---
class TimeoutStrategy(CompletionStrategy):
    def __init__(self, limit_seconds):
        self.limit_seconds = limit_seconds
        self.start_time = time.time()

    def is_finished(self) -> bool:
        # 当前时间与启动时间差超过限制则认为完成
        return (time.time() - self.start_time) >= self.limit_seconds


class R2CClientForME(ABC):
    """ROS2 节点：采集机器人 observation 并转发 actions。"""

    def __init__(self, node_name: str) -> None:
        self._config = None
        self._sdk_config = None
        self._ros_operator = None
        self._executor = None
        self._executor_thread = None
        self._node_name: str = node_name
        self._task: str = "pick up the block"  # no use
        self._timer: Optional[rclpy.timer.Timer] = None
        self._session = None
        self.encoder = H264ImageEncoder()
        self._sequence = 0
        # 按指定频率发布action数据
        self._cached_actions = None
        self._action_lock = threading.Lock()  # 线程锁
        # 用于跟踪当前执行的chunk中的step索引
        self._current_step_index = 0
        self._total_steps = 0
        # 用于统计action接收情况
        self._received_action_count = 0
        self._dropped_action_count = 0

    def configure(self, config: R2CClientConfig, sdk_config: R2CSDKConfig) -> None:
        """应用配置并启动订阅/发布。可以在初始化前后调用。"""
        self._config = config
        self._sdk_config = sdk_config
        self._init_ros()
        self._session = R2CClient.connect(self._sdk_config)
        self._session.subscribe_actions(self._on_actions)
        # 起线程发布Actions
        self._action_thread = threading.Thread(
            target=self._action_publisher_loop,
            name="ActionPublisher",
            daemon=True
        )
        self._action_thread.start()
    
    def _action_publisher_loop(self) -> None:
        """独立线程：按频率发布 actions"""
        logger.info("Action publisher loop started.")
        logger.info(f"Action publish rate: {self._config.action_rate_hz} Hz")
        rate = self._ros_operator.create_rate(self._config.action_rate_hz)  # 动作发布频率
        while True:
            with self._action_lock:
                if self._cached_actions:
                    logger.info(f"Publishing actions - step {self._current_step_index + 1}/{self._total_steps}")
                    # 发布当前step的动作
                    self._ros_operator.publish_action(self._cached_actions, self._current_step_index)

                    # 移动到下一个step
                    self._current_step_index += 1

                    # 如果所有step都执行完了，清空缓存
                    if self._current_step_index >= self._total_steps:
                        logger.info("Completed all steps in current action chunk")
                        self._cached_actions = None
                        self._current_step_index = 0
                        self._total_steps = 0
            rate.sleep()

    def execute(
        self, task: str, strategy: Optional[CompletionStrategy] = None
    ) -> None:
        # 创建发布频率控制器
        rate = self._ros_operator.create_rate(self._config.observation_rate_hz)  # 10
        logger.info(f"task start, publish obs rate: {self._config.observation_rate_hz} Hz")
        self._task = task  # no use

        try:
            while True:
                # 检查任务是否完成（由传入的策略决定）
                if strategy is not None and strategy.is_finished():
                    logger.info("checked task finished.")
                    break

                # 执行具体操作
                # 获取观测数据
                frame_data = self._ros_operator.get_frame()
                if not frame_data:
                    logger.info("Waiting for observations")
                    # 按频率休眠
                    rate.sleep()
                    continue
                # 转为r2c协议观测消息
                observation = self.convert_to_observation(frame_data)
                # 发送数据
                logger.info(f"observation {observation.timestamp} {observation.id} {observation.joint_states} {observation.end_effector_states}")
                self._session.publish_observations(observation)
                self._ros_operator.init_obs()
                self._sequence += 1

                # 按频率休眠
                rate.sleep()
        except KeyboardInterrupt:
            logger.info("task stop")
        finally:
            self._ros_operator.destroy_node()
            rclpy.shutdown()

    @abstractmethod
    def convert_to_observation(self, frame_data):
        pass

    @abstractmethod
    def convert_actions(self, actions: Actions) -> dict[str, Float64MultiArray]:
        """
        需要用户把actions转为具体的动作序列接口的数据
        """
        pass

    def close(self) -> None:
        if self._session:
            self._session.close()

    def _init_ros(self) -> None:
        """初始化ros node"""
        rclpy.init()
        self._ros_operator = MeRosOperator(self._node_name, config=self._config)
        self._ros_operator.init_node()
        self._executor = MultiThreadedExecutor()
        self._executor.add_node(self._ros_operator)
        self._executor_thread = threading.Thread(
            target=self._executor.spin, name="MeRosOperator"
        )
        self._executor_thread.start()

    def _publish_observation(self) -> None:
        builder = ObservationBuilder().set_timestamp(int(time.time() * 1000))

        for cam in self._config.cameras:
            payload = self._camera_cache.get(cam.name)
            if payload:
                builder.add_image(cam.name, payload, is_depth=cam.is_depth)

        if self._joint_state:
            builder.set_joint_states(
                names=self._joint_state.name,
                positions=self._joint_state.position,
                velocities=self._joint_state.velocity or None,
                torques=self._joint_state.effort or None,
            )

        for name, pose_msg in self._end_effector_poses.items():
            pose = pose_msg.pose
            pose_7d = [
                pose.position.x,
                pose.position.y,
                pose.position.z,
                pose.orientation.x,
                pose.orientation.y,
                pose.orientation.z,
                pose.orientation.w,
            ]
            builder.add_end_effector_pose(name, pose_7d)

        for name, state_msg in self._end_effector_states.items():
            if state_msg.data:
                position = state_msg.data[0]
                force = state_msg.data[1] if len(state_msg.data) > 1 else 0.0
                builder.add_end_effector_state(name, position=position, force=force)

        observation = builder.build()

    def _on_actions(self, actions: Actions) -> None:
        with self._action_lock:
            self._received_action_count += 1
            logger.info(f"Get actions msgs #{self._received_action_count}: {actions}")

            # 检查是否正在执行之前的action
            if self._cached_actions is not None:
                self._dropped_action_count += 1
                logger.warning(
                    f"Previous action still executing (step {self._current_step_index + 1}/{self._total_steps}). "
                    f"Dropping new action. Total dropped: {self._dropped_action_count}"
                )
                # 可以选择：1. 丢弃新action（当前实现），2. 中断旧action执行新action
                return

            # 计算预计执行时间
            estimated_execution_time = actions.chunk_size / self._config.action_rate_hz
            logger.info(
                f"Received action chunk with {actions.chunk_size} steps. "
                f"Estimated execution time: {estimated_execution_time:.2f}s "
                f"(at {self._config.action_rate_hz} Hz)"
            )

            # 检查执行时间是否过长
            if estimated_execution_time > 1.0:  # 如果执行时间超过1秒
                logger.warning(
                    f"Action chunk will take {estimated_execution_time:.2f}s to execute. "
                    f"Consider reducing chunk_size or increasing action_rate_hz."
                )

            self._cached_actions = self.convert_actions(actions)
            # 初始化step计数
            self._current_step_index = 0
            self._total_steps = actions.chunk_size
