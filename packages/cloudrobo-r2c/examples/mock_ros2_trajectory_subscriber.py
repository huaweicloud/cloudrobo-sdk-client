#!/usr/bin/env python3
"""Mock ROS2 轨迹订阅节点。

订阅 /track/cloud_trajectory 话题，接收 nav_msgs/Path 消息，
解析并打印轨迹数据。

与 q25_hardware_adapter.py 中的 publish_trajectory_path 格式对齐：
- topic: /track/cloud_trajectory
- type: nav_msgs/Path
- 每个 waypoint: pose.position.x/y + pose.orientation.z/w (sin_yaw/cos_yaw)

用法：
    python examples/mock_ros2_trajectory_subscriber.py
"""

from __future__ import annotations

from typing import Optional

import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from nav_msgs.msg import Path
from geometry_msgs.msg import PoseStamped


class MockTrajectorySubscriber(Node):
    """订阅 /track/cloud_trajectory 并打印轨迹数据的 ROS2 节点。"""

    def __init__(self) -> None:
        super().__init__("mock_trajectory_subscriber")

        self._topic: str = "/track/cloud_trajectory"

        # QoS（与 q25_hardware_adapter 发布端对齐）
        qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )

        self._sub = self.create_subscription(
            Path, self._topic, self._callback, qos
        )

        self._msg_count: int = 0

        self.get_logger().info(
            f"MockTrajectorySubscriber 已启动: topic={self._topic}, waiting for Path messages..."
        )

    # ------------------------------------------------------------------
    # 回调
    # ------------------------------------------------------------------

    def _callback(self, msg: Path) -> None:
        """收到 nav_msgs/Path 轨迹消息时解析并打印。"""
        import math

        self._msg_count += 1

        # --- 打印分隔线 ---
        self.get_logger().info("=" * 60)
        self.get_logger().info(f"[#{self._msg_count}] 收到轨迹 Path 消息")

        # --- header 信息 ---
        stamp = msg.header.stamp
        frame_id = msg.header.frame_id
        self.get_logger().info(
            f"  frame_id: {frame_id}, stamp: {stamp.sec}.{stamp.nanosec:09d}"
        )

        # --- 解析 poses (waypoints) ---
        poses: list[PoseStamped] = msg.poses
        num_waypoints: int = len(poses)

        self.get_logger().info(f"  waypoints: {num_waypoints} 个航点")

        if num_waypoints == 0:
            self.get_logger().warn("  ⚠ 轨迹为空，没有航点数据")
            return

        for i, ps in enumerate(poses):
            pos = ps.pose.position
            orient = ps.pose.orientation

            x, y = pos.x, pos.y
            sin_yaw, cos_yaw = orient.z, orient.w

            # 从 sin/cos 反算角度（弧度 -> 度）
            yaw_rad: float = math.atan2(sin_yaw, cos_yaw)
            yaw_deg: float = math.degrees(yaw_rad)

            self.get_logger().info(
                f"  Waypoint[{i:2d}]: "
                f"x={x:8.3f}  y={y:8.3f}  "
                f"sin_yaw={sin_yaw:7.4f}  cos_yaw={cos_yaw:7.4f}  "
                f"→ yaw={yaw_deg:+7.2f}°"
            )

        self.get_logger().info("=" * 60)


# ---------------------------------------------------------------------------
# 启动入口
# ---------------------------------------------------------------------------

def main(args: Optional[list[str]] = None) -> None:
    rclpy.init(args=args)
    node = MockTrajectorySubscriber()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
