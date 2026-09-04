#!/usr/bin/env python3
"""Mock ROS2 图片发布节点。

以 1Hz 频率向 /camera/image_raw 话题发布 224x224 的 JPEG 图片，
图片内容为随机彩色噪声（模拟真实相机帧），编码格式为 rgb8，
消息类型为 sensor_msgs/Image。

与 q25_hardware_adapter.py 中的 _image_callback 期望格式对齐：
- topic: /camera/image_raw
- encoding: rgb8（或 bgr8，这里使用 rgb8）
- width/height: 224x224
- step: width * 3（无填充）
- data: 原始像素数据（非压缩 JPEG，因为 ROS sensor_msgs/Image 传的是原始像素）

用法：
    ros2 run mock_ros2_image_publisher mock_ros2_image_publisher.py
    # 或
    python examples/mock_ros2_image_publisher.py
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image
from std_msgs.msg import Header


class MockImagePublisher(Node):
    """以 1Hz 发布 224x224 rgb8 测试图片的 ROS2 节点。"""

    def __init__(self) -> None:
        super().__init__("mock_image_publisher")

        # --- 配置参数 ---
        self._topic: str = "/camera/image_raw"
        self._width: int = 224
        self._height: int = 224
        self._publish_hz: float = 1.0  # 1Hz

        # --- QoS（与 q25_hardware_adapter 订阅端对齐）---
        qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )

        # --- Publisher ---
        self._pub = self.create_publisher(Image, self._topic, qos)

        # --- 定时器 ---
        self._timer = self.create_timer(1.0 / self._publish_hz, self._timer_callback)

        self.get_logger().info(
            f"MockImagePublisher 已启动: topic={self._topic}, "
            f"size={self._width}x{self._height}, "
            f"rate={self._publish_hz}Hz, encoding=rgb8"
        )

    # ------------------------------------------------------------------
    # 图片生成
    # ------------------------------------------------------------------

    def _generate_random_image(self) -> np.ndarray:
        """生成一张 224x224 的随机彩色 RGB 图片 (dtype=uint8, range=[0,255])。

        每次生成不同内容，模拟真实相机画面变化。
        """
        return np.random.randint(0, 256, (self._height, self._width, 3), dtype=np.uint8)

    # ------------------------------------------------------------------
    # 消息构造
    # ------------------------------------------------------------------

    def _build_image_msg(self, rgb_array: np.ndarray) -> Image:
        """将 numpy RGB 数组打包为 sensor_msgs/Image 消息。

        格式对齐 q25_hardware_adapter 中 _image_callback 的期望：
        - encoding: rgb8
        - height/width: 224x224
        - step: width * 3（无行填充）
        - data: bytes
        """
        msg = Image()
        msg.header = Header()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "camera_link"

        msg.height = self._height
        msg.width = self._width
        msg.encoding = "rgb8"
        msg.is_bigendian = False
        msg.step = self._width * 3  # 3 字节每像素（RGB）
        msg.data = rgb_array.tobytes()

        return msg

    # ------------------------------------------------------------------
    # 定时回调
    # ------------------------------------------------------------------

    def _timer_callback(self) -> None:
        """定时器回调：生成图片并发布。"""
        rgb_array = self._generate_random_image()
        msg = self._build_image_msg(rgb_array)

        # 记录一些统计信息用于调试
        mean_val = float(np.mean(rgb_array))
        std_val = float(np.std(rgb_array))

        self._pub.publish(msg)

        self.get_logger().info(
            f"[PUB] /camera/image_raw | "
            f"timestamp={msg.header.stamp.sec}.{msg.header.stamp.nanosec:09d} | "
            f"size={self._width}x{self._height} | "
            f"pixel_mean={mean_val:.1f} pixel_std={std_val:.1f} | "
            f"{len(msg.data)} bytes"
        )


# ---------------------------------------------------------------------------
# 启动入口
# ---------------------------------------------------------------------------

def main(args: Optional[list[str]] = None) -> None:
    rclpy.init(args=args)
    node = MockImagePublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
