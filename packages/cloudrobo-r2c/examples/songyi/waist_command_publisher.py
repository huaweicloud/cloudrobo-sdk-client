#!/usr/bin/env python3
"""腰部点动发布脚本：发布 WaistCommand 到 {project_id}/{device_id}/waist_command，
控制腰部最后一个可控关节（第 4 关节，弧度）。

- fire-and-forget：发布即返回，**不阻塞等待**关节到达位置（机器人侧按
  max_step_rad 步长夹紧渐进执行，大幅值目标需连续多次发布逐步逼近）。
- 腰部前 3 关节固定不可控，position 仅 1 个值（第 4 关节弧度）。
- topic 名支持 ``{project_id}/{device_id}`` 占位符；默认发本地
  waist_command，也可 ``--topic-name "{project_id}/{device_id}/job/waist"``
  走云侧名（经 forwarder 下行透传，联调用）。

示例：
    # 发一次（0.2 rad）
    python examples/songyi/waist_command_publisher.py \
        --position 0.2

    # 大幅值目标：每 0.2s 重发一次目标，共 30 次（机器人侧逐步夹紧逼近）
    python examples/songyi/waist_command_publisher.py \
        --position 0.8 --repeat 30 --interval-s 0.2

安全提醒：真机操作请保持 dry_run 双层保护与急停值守；硬件层 dry_run
只打印目标角不下发。
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from typing import List, Optional

sys.path.insert(0, "src")

from cloudrobo_r2c import ClientConfig, R2CClient
from songyi_robot.models.generated import waist_command_pb2

logger = logging.getLogger("songyi-waist-publisher")


def parse_endpoints(raw: Optional[str]) -> List[str]:
    return [ep.strip() for ep in (raw or "").split(",") if ep.strip()]


def build_client(args: argparse.Namespace):
    """bundle 可选，缺省用显式参数（同其他联调示例）。"""
    if args.bundle:
        logger.info("Connecting with platform credential bundle: %s", args.bundle)
        return R2CClient.connect(args.bundle, client_id=args.client_id or None)
    if not args.project_id or not args.device_id:
        raise ValueError("project_id/device_id required when --bundle is not provided")
    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id or "songyi-waist-publisher",
        endpoints=parse_endpoints(args.endpoints),
        endpoint_role=args.endpoint_role,
        mode=args.mode,
    )
    logger.info(
        "Connecting with explicit ClientConfig: project_id=%s, device_id=%s, "
        "mode=%s, endpoints=%s",
        config.project_id, config.device_id, config.mode, list(config.endpoints),
    )
    return R2CClient.connect(config)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish WaistCommand (last controllable waist joint), non-blocking"
    )
    # 连接（bundle 可选，同其他联调示例）
    parser.add_argument("--bundle", type=str, default=None,
                        help="Platform credential bundle (optional)")
    parser.add_argument("--project-id", type=str, default="r2c",
                        help="Project ID (also expands {project_id} in topic name)")
    parser.add_argument("--device-id", type=str, default="local",
                        help="Device ID (also expands {device_id} in topic name)")
    parser.add_argument("--client-id", type=str, default="songyi-waist-publisher",
                        help="Client ID")
    parser.add_argument("--endpoints", type=str, default="tcp/127.0.0.1:7447",
                        help="Comma-separated endpoints, e.g. tcp/127.0.0.1:7447")
    parser.add_argument("--endpoint-role", type=str, default="connect",
                        choices=["connect", "listen"])
    parser.add_argument("--mode", type=str, default="client",
                        choices=["peer", "client"])
    # 目标 topic（默认本地 waist_command；联调可改 job/waist 走 forwarder 下行）
    parser.add_argument("--topic-name", type=str,
                        default="{project_id}/{device_id}/waist_command",
                        help="Topic name; {project_id}/{device_id} placeholders expanded")
    parser.add_argument("--settle-s", type=float, default=1.0,
                        help="Wait N seconds after connect for discovery "
                             "(default: 1.0, 0 to skip)")
    # 目标角度（弧度/度 二选一）
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--position", type=float,
                       help="Target angle of the last waist joint (radians)")
    group.add_argument("--position-deg", type=float,
                       help="Target angle of the last waist joint (degrees)")
    # 重发（大幅值目标需多次发布逐步逼近：机器人侧按 max_step_rad 夹紧）
    parser.add_argument("--repeat", type=int, default=1,
                        help="Number of publishes (default: 1)")
    parser.add_argument("--interval-s", type=float, default=0.2,
                        help="Seconds between publishes (default: 0.2)")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    args = parse_args()

    if args.position is not None:
        angle_rad = args.position
    else:
        angle_rad = math.radians(args.position_deg)

    topic = args.topic_name.replace("{project_id}", args.project_id) \
                          .replace("{device_id}", args.device_id)

    client = build_client(args)
    try:
        if args.settle_s > 0:
            logger.info("Settling %.1fs for peer/router discovery ...", args.settle_s)
            time.sleep(args.settle_s)

        repeat = max(args.repeat, 1)
        for i in range(repeat):
            # 发布即返回，不等待关节到位（非阻塞）
            message = waist_command_pb2.WaistCommand(
                source=f"{args.project_id}/{args.device_id}",
                timestamp=int(time.time() * 1000),
                position=[angle_rad],
            )
            client.transport.publish(topic, message.SerializeToString())
            logger.info(
                "[%d/%d] WaistCommand published: topic=%s position=[%.4f rad "
                "(%.2f deg)] (fire-and-forget)",
                i + 1, repeat, topic, angle_rad, math.degrees(angle_rad),
            )
            if i < repeat - 1:
                time.sleep(max(args.interval_s, 0.0))
    finally:
        client.close()


if __name__ == "__main__":
    main()
