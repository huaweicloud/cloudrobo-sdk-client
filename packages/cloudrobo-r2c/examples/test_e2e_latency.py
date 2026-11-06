#!/usr/bin/env python3
"""端到端全流程时延测试脚本。

启动顺序与架构:
1. mock_ros2_image_publisher     — 1Hz 发布 224x224 rgb8 图片到 /camera/image_raw
2. mock_ros2_trajectory_subscriber — 订阅 /track/cloud_trajectory
3. q25_cloud_adapter              — 通过 R2C(zenoh) 订阅机器人观测，回复轨迹动作
4. cloudroboclient (robot端)      — Q25HardwareAdapter 启动，通过 R2C 发布观测/接收动作

数据流:
    MockImagePub ---ROS2---> Q25HardwareAdapter ---R2C/zenoh---> Q25CloudAdapter ---R2C/zenoh---> SyncRobotClient ---ROS2---> MockTrajectorySub

时延标记点:
    T_img_pub     : mock_ros2_image_publisher 发布图片的时间 (ROS2 header stamp)
    T_hw_obs      : Q25HardwareAdapter.get_observation() 获取到图片的时间
    T_cloud_recv  : Q25CloudAdapter.on_observation() 收到观测的时间
    T_cloud_act   : Q25CloudAdapter 发布动作的时间
    T_traj_recv   : MockTrajectorySubscriber 收到轨迹消息的时间

时延计算:
    L1 (image trans)   = T_hw_obs - T_img_pub      # ROS2 图片传输时延
    L2 (obs uplink)    = T_cloud_recv - T_hw_obs    # 观测上行时延
    L3 (cloud process) = T_cloud_act - T_cloud_recv # 云端处理时延
    L4 (act downlink)  = T_traj_recv - T_cloud_act  # 动作下行时延
    L_total            = T_traj_recv - T_img_pub    # 全流程端到端时延

用法:
    python examples/test_e2e_latency.py [--duration 60] [--output latency_results.csv]
"""

from __future__ import annotations

import argparse
import csv
import logging
import multiprocessing
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("e2e_latency_test")

# ============================================================================
# 全局共享状态（通过 multiprocessing Manager）
# ============================================================================

_SHARED_TS: dict[str, float] = {}
_RECORDS: list[dict] = []
_LOCK: object = None

# ============================================================================
# 时延记录结构
# ============================================================================


@dataclass
class LatencyRecord:
    """一次完整往返的时延记录。"""
    cycle_id: int = 0
    T_img_pub: float = 0.0      # mock image pub 发布时刻 (monotonic ns)
    T_traj_recv: float = 0.0    # mock trajectory sub 接收时刻 (monotonic ns)
    img_pub_wall: str = ""      # 墙上时钟字符串
    traj_recv_wall: str = ""
    raw_payload: str = ""       # 原始轨迹 JSON 摘要


# ============================================================================
# 子进程 1: mock_ros2_image_publisher (图片发布 + 时间戳打点)
# ============================================================================

def _run_image_publisher(shared_ts: dict) -> None:
    """1Hz 发布 224x224 rgb8 图片，将时间戳记录到 shared_ts。"""
    # 延迟导入以避免在父进程初始化 rclpy
    time.sleep(0.5)

    import rclpy
    from rclpy.node import Node
    from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
    from sensor_msgs.msg import Image
    from std_msgs.msg import Header

    rclpy.init(args=[])

    node = Node("e2e_mock_image_publisher")
    qos = QoSProfile(
        reliability=ReliabilityPolicy.RELIABLE,
        history=HistoryPolicy.KEEP_LAST,
        depth=10,
    )
    pub = node.create_publisher(Image, "/camera/image_raw", qos)

    width, height = 224, 224
    cycle_id = 0

    # 给其他节点足够时间启动
    time.sleep(2.0)

    image_send_log_path = _log_path(shared_ts, "image_send.csv")

    with open(image_send_log_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["cycle_id", "T_img_pub_mono_ns", "wall_time"])

        import numpy as np
        while True:
            cycle_id += 1
            # 创建随机彩色图片
            rgb_array = np.random.randint(0, 256, (height, width, 3), dtype=np.uint8)

            msg = Image()
            msg.header = Header()
            now_stamp = node.get_clock().now().to_msg()
            msg.header.stamp = now_stamp
            msg.header.frame_id = "camera_link"
            msg.height = height
            msg.width = width
            msg.encoding = "rgb8"
            msg.is_bigendian = False
            msg.step = width * 3
            msg.data = rgb_array.tobytes()

            # 记录时间戳
            mono_now = time.monotonic_ns()
            wall_now = datetime.now()

            pub.publish(msg)

            # 写入 CSV
            writer.writerow([cycle_id, mono_now, wall_now.isoformat(timespec="milliseconds")])
            f.flush()
            os.fsync(f.fileno())

            # 共享最新时间戳 (供 trajectory subscriber 进程读取)
            shared_ts["latest_img_pub_cycle"] = cycle_id
            shared_ts[f"T_img_pub_{cycle_id}"] = mono_now
            shared_ts[f"img_pub_wall_{cycle_id}"] = wall_now.isoformat(timespec="milliseconds")

            node.get_logger().info(
                f"[IMG_PUB] cycle={cycle_id} stamp={now_stamp.sec}.{now_stamp.nanosec:09d} "
                f"mono={mono_now}"
            )

            time.sleep(0.9)  # 略小于 1s 避免漂移


# ============================================================================
# 子进程 2: mock_ros2_trajectory_subscriber + 时延匹配记录
# ============================================================================

def _run_trajectory_subscriber(shared_ts: dict, records: list) -> None:
    """订阅轨迹 + 从 shared_ts 匹配时延记录。"""
    time.sleep(1.0)

    import math
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
    from nav_msgs.msg import Path as RosPath

    rclpy.init(args=[])

    node = Node("e2e_mock_traj_subscriber")
    qos = QoSProfile(
        reliability=ReliabilityPolicy.RELIABLE,
        history=HistoryPolicy.KEEP_LAST,
        depth=10,
    )

    latency_log_path = _log_path(shared_ts, "latency_results.csv")

    with open(latency_log_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "cycle_id",
            "T_img_pub_mono_ns",
            "T_img_pub_wall",
            "T_traj_recv_mono_ns",
            "T_traj_recv_wall",
            "L_total_ms",
            "action_preview",
        ])
        f.flush()

        def callback(msg: RosPath) -> None:
            traj_recv_mono = time.monotonic_ns()
            traj_recv_wall = datetime.now()

            poses = msg.poses
            # 构造 action 摘要（前几个 waypoint 的 x, y, sin_yaw, cos_yaw）
            action_preview_parts = []
            for ps in poses[:5]:
                p = ps.pose.position
                o = ps.pose.orientation
                action_preview_parts.append(
                    f"[{p.x:.2f},{p.y:.2f},{o.z:.3f},{o.w:.3f}]"
                )
            action_summary = ",".join(action_preview_parts)
            if len(poses) > 5:
                action_summary += f"...({len(poses)} waypoints)"

            # 获取当前 cycle_id（从 image publisher 共享）
            cycle_id = shared_ts.get("latest_img_pub_cycle", 0)

            # 尝试获取对应的时间戳 — 如果还没写入，用上一个可用
            img_pub_mono = shared_ts.get(f"T_img_pub_{cycle_id}", 0)
            img_pub_wall = shared_ts.get(f"img_pub_wall_{cycle_id}", "")

            # 计算时延
            if img_pub_mono > 0:
                L_total_ms = (traj_recv_mono - img_pub_mono) / 1_000_000
            else:
                L_total_ms = 0.0

            writer.writerow([
                cycle_id,
                img_pub_mono,
                img_pub_wall,
                traj_recv_mono,
                traj_recv_wall.isoformat(timespec="milliseconds"),
                f"{L_total_ms:.2f}",
                action_summary,
            ])
            f.flush()
            os.fsync(f.fileno())

            # 也写入共享 list 供主进程汇总
            records.append({
                "cycle_id": cycle_id,
                "T_img_pub_mono_ns": img_pub_mono,
                "T_img_pub_wall": img_pub_wall,
                "T_traj_recv_mono_ns": traj_recv_mono,
                "T_traj_recv_wall": traj_recv_wall.isoformat(timespec="milliseconds"),
                "L_total_ms": round(L_total_ms, 2),
                "action_preview": action_summary,
            })

            node.get_logger().info(
                f"[TRAJ_RECV] cycle={cycle_id} L_total={L_total_ms:.1f}ms "
                f"action={action_summary[:60]}"
            )

        sub = node.create_subscription(String, "/track/cloud_trajectory", callback, qos)
        node.get_logger().info("Trajectory subscriber started, waiting...")

        rclpy.spin(node)


# ============================================================================
# 子进程 3: q25_cloud_adapter
# ============================================================================

def _run_cloud_adapter(shared_ts: dict, args_obj: argparse.Namespace) -> None:
    """启动 cloud adapter（独立进程，有自己的 zenoh 连接）。

    不传入 --endpoints，让 cloud adapter 使用默认值
    tcp/0.0.0.0:7447 (endpoint_role=listen) 等待 robot client 连接。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    sys.argv = [
        "q25_cloud_adapter.py",
        "--project-id", args_obj.project_id,
        "--device-id", args_obj.device_id,
        "--client-id", args_obj.client_id,
    ]

    import q25_cloud_adapter
    q25_cloud_adapter.main()


# ============================================================================
# 子进程 4: cloudroboclient (robot 端)
# ============================================================================

def _run_robot_client(shared_ts: dict, args_obj: argparse.Namespace) -> None:
    """启动 cloudroboclient（读 robot_config.yaml + Q25HardwareAdapter）。"""
    from cloudrobo_r2c.cloudroboclient import main as robot_main

    # 等待 1~3 就绪
    time.sleep(3.0)

    sys.argv = [
        "cloudroboclient",
        "--robot-config", args_obj.robot_config,
        "--project-id", args_obj.robot_project_id,
        "--device-id", args_obj.robot_device_id,
        "--client-id", args_obj.robot_client_id,
        "--endpoints", args_obj.robot_endpoints,
        "--mode", "peer",
        "--log-level", "INFO",
    ]
    if args_obj.duration > 0:
        sys.argv.extend(["--duration", str(args_obj.duration)])

    robot_main()


# ============================================================================
# 日志文件路径
# ============================================================================

def _log_path(shared_ts: dict, filename: str) -> str:
    """在 results 目录下创建日志文件路径。"""
    out_dir = shared_ts.get("output_dir", "latency_results")
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, filename)


# ============================================================================
# 主流程
# ============================================================================

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="E2E latency test: MockImage -> Q25HardwareAdapter -> Q25CloudAdapter -> MockTrajectorySub",
    )
    parser.add_argument("--duration", type=float, default=60.0, help="测试运行时长（秒），0 表示一直运行")
    parser.add_argument("--output", type=str, default="latency_results", help="输出目录")

    # Cloud adapter 参数（不传 --endpoints，使用默认 tcp/0.0.0.0:7447 listen）
    parser.add_argument("--project-id", type=str, default="r2c",
                        help="Cloud adapter 的 project-id")
    parser.add_argument("--device-id", type=str, default="local",
                        help="Cloud adapter 订阅的设备 ID")
    parser.add_argument("--client-id", type=str, default="q25-cloud-adapter",
                        help="Cloud adapter 的 client-id")

    # Robot client 参数
    parser.add_argument("--robot-config", type=str, default="config/robot_q25_config.yaml",
                        help="Robot client 的配置路径")
    parser.add_argument("--robot-project-id", type=str, default="r2c",
                        help="Robot client 的 project-id")
    parser.add_argument("--robot-device-id", type=str, default="local",
                        help="Robot client 的 device-id")
    parser.add_argument("--robot-client-id", type=str, default="robot",
                        help="Robot client 的 client-id")
    parser.add_argument("--robot-endpoints", type=str, default="tcp/127.0.0.1:7447",
                        help="Robot client 的 zenoh endpoint（逗号分隔）")

    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    out_dir = args.output
    os.makedirs(out_dir, exist_ok=True)

    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(os.path.join(out_dir, "test_main.log"), mode="w"),
        ],
    )

    logger.info("=" * 60)
    logger.info("E2E Latency Test Starting")
    logger.info(f"  Output dir: {out_dir}")
    logger.info(f"  Duration:   {args.duration}s")
    logger.info("=" * 60)

    # 共享状态
    manager = multiprocessing.Manager()
    shared_ts = manager.dict()
    records = manager.list()
    shared_ts["output_dir"] = out_dir
    shared_ts["latest_img_pub_cycle"] = 0

    # 启动子进程
    procs = []

    p1 = multiprocessing.Process(
        target=_run_image_publisher,
        args=(shared_ts,),
        name="image-publisher",
    )
    procs.append(p1)

    p2 = multiprocessing.Process(
        target=_run_trajectory_subscriber,
        args=(shared_ts, records),
        name="trajectory-subscriber",
    )
    procs.append(p2)

    p3 = multiprocessing.Process(
        target=_run_cloud_adapter,
        args=(shared_ts, args),
        name="cloud-adapter",
    )
    procs.append(p3)

    p4 = multiprocessing.Process(
        target=_run_robot_client,
        args=(shared_ts, args),
        name="robot-client",
    )
    procs.append(p4)

    # 1~3 先启动
    logger.info("Starting image publisher, trajectory subscriber, and cloud adapter...")
    p1.start()
    p2.start()
    p3.start()

    # 等待 1~3 就绪（延后启动 cloudroboclient）
    logger.info("Waiting 5s for 1~3 to initialize...")
    time.sleep(5.0)

    logger.info("Starting robot client (cloudroboclient)...")
    p4.start()

    # 等待结束
    start_time = time.time()
    try:
        if args.duration > 0:
            while time.time() - start_time < args.duration:
                # 检查是否有进程意外退出
                alive = [p.is_alive() for p in procs]
                if not all(alive):
                    dead_names = [procs[i].name for i, a in enumerate(alive) if not a]
                    logger.error(f"Process(es) died prematurely: {dead_names}")
                    break
                time.sleep(1.0)
                elapsed = time.time() - start_time
                n_records = len(records)
                logger.info(f"Running... {elapsed:.0f}s / {args.duration:.0f}s, records={n_records}")
        else:
            while True:
                alive = [p.is_alive() for p in procs]
                if not all(alive):
                    break
                time.sleep(1.0)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        logger.info("Shutting down all processes...")
        for p in procs:
            if p.is_alive():
                p.terminate()
                p.join(timeout=5.0)

    # ========================================================================
    # 输出汇总报告
    # ========================================================================
    logger.info("=" * 60)
    logger.info("LATENCY TEST SUMMARY")
    logger.info("=" * 60)

    final_records = list(records)

    if not final_records:
        logger.warning("No trajectory records received!")
    else:
        latencies = [r["L_total_ms"] for r in final_records]
        logger.info(f"Total cycles completed: {len(final_records)}")
        logger.info(f"E2E Latency (T_traj_recv - T_img_pub):")
        logger.info(f"  Min:    {min(latencies):.2f} ms")
        logger.info(f"  Max:    {max(latencies):.2f} ms")
        logger.info(f"  Avg:    {sum(latencies)/len(latencies):.2f} ms")
        logger.info(f"  Median: {sorted(latencies)[len(latencies)//2]:.2f} ms")

        # 写入汇总 CSV
        summary_path = os.path.join(out_dir, "latency_summary.csv")
        import csv as csv_module
        with open(summary_path, "w", newline="") as f:
            writer = csv_module.writer(f)
            writer.writerow(["metric", "value_ms"])
            writer.writerow(["min", round(min(latencies), 2)])
            writer.writerow(["max", round(max(latencies), 2)])
            writer.writerow(["avg", round(sum(latencies) / len(latencies), 2)])
            median = sorted(latencies)[len(latencies) // 2]
            writer.writerow(["median", round(median, 2)])
            writer.writerow(["count", len(latencies)])

        logger.info(f"Summary saved to: {summary_path}")

        # 打印最近的记录
        logger.info("Recent records (last 5):")
        for r in final_records[-5:]:
            logger.info(
                f"  cycle={r['cycle_id']} "
                f"L_total={r['L_total_ms']:.1f}ms "
                f"img_pub={r['T_img_pub_wall']} "
                f"traj_recv={r['T_traj_recv_wall']}"
            )

    logger.info("Detailed results: %s", os.path.join(out_dir, "latency_results.csv"))
    logger.info("Image send log:  %s", os.path.join(out_dir, "image_send.csv"))
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
