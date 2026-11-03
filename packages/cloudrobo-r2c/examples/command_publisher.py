#!/usr/bin/env python3
"""发布 command 指令样例：发布 CommandMessage 到 {project_id}/{device_id}/command
（sync_client 直接订阅并经 ModeControl 消费：teleop 进出 / 心跳 mode
联动）。

进入遥操（心跳 mode → CONTROLLED）:
    python examples/command_publisher.py \
        --command-type teleop_enter

退出遥操（心跳 mode → AUTO）:
    python examples/command_publisher.py \
        --command-type teleop_exit

command_type 任意字符串均可发布（SDK 侧已知值：teleop_enter / teleop_exit /
assist_enter / assist_exit，未知值由 SDK 忽略并告警；
大小写不敏感，归一化）。
连接方式同其他示例：--bundle / --client-config / 显式参数。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import List, Optional

sys.path.insert(0, "src")

from cloudrobo_r2c import ClientConfig, R2CClient
from cloudrobo_r2c.common.models import CommandMessage

logger = logging.getLogger("command_publisher")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    conn = parser.add_mutually_exclusive_group()
    conn.add_argument("--bundle", help="platform credential bundle (.zip/dir)")
    conn.add_argument("--client-config", help="client config YAML")
    parser.add_argument("--project-id", default='r2c')
    parser.add_argument("--device-id", default='local')
    parser.add_argument("--client-id", default="command-publisher")
    parser.add_argument("--endpoints", default="tcp/127.0.0.1:7447",
                        help="comma-separated zenoh endpoints")
    parser.add_argument("--mode", default="peer", choices=["peer", "client", "router"])
    parser.add_argument("--endpoint-role", default="connect",
                        choices=["connect", "listen"])
    parser.add_argument("--command-type", default="teleop_enter",
                        help="command type (default: teleop_enter; "
                             "SDK 已知值 teleop_enter/teleop_exit/"
                             "assist_enter/assist_exit，大小写不敏感)")
    parser.add_argument("--skip-data-reflux", type=int, default=0, choices=[0, 1],
                        help="数据回流开关（0=正常回流，1=跳过回流）")
    parser.add_argument("--repeat", type=int, default=1,
                        help="number of commands to publish (default 1)")
    parser.add_argument("--interval-s", type=float, default=1.0,
                        help="seconds between repeats (default 1.0)")
    return parser.parse_args()


def parse_endpoints(raw: Optional[str]) -> List[str]:
    return [ep.strip() for ep in (raw or "").split(",") if ep.strip()]


def build_session(args: argparse.Namespace):
    """返回 (publish_fn, project_id, device_id)；publish_fn(topic, payload)。"""
    if args.bundle:
        client = R2CClient.connect(args.bundle, client_id=args.client_id)
        transport = client.transport
        return (transport.publish,
                args.project_id or client.project_id,
                args.device_id or client.device_id)

    if args.client_config:
        config = ClientConfig.from_yaml(
            args.client_config,
            project_id=args.project_id,
            device_id=args.device_id,
            client_id=args.client_id,
            endpoints=parse_endpoints(args.endpoints) or None,
            mode=args.mode,
        )
    else:
        config = ClientConfig(
            project_id=args.project_id,
            device_id=args.device_id,
            client_id=args.client_id,
            endpoints=parse_endpoints(args.endpoints),
            endpoint_role=args.endpoint_role,
            mode=args.mode,
        )
    client = R2CClient.connect(config)
    return (client.transport.publish,
            config.project_id,
            config.device_id)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    args = parse_args()

    publish, project_id, device_id = build_session(args)
    topic = f"{project_id}/{device_id}/job/command"
    logger.info("publishing commands to %s", topic)

    for i in range(max(args.repeat, 1)):
        command = CommandMessage.from_dict({
            "timestamp": int(time.time() * 1000),
            "command_type": args.command_type,
            "skip_data_reflux": args.skip_data_reflux,
        })
        publish(topic, command.serialize())
        logger.info(
            "command published: type=%s skip_data_reflux=%d ts=%d",
            command.command_type, command.skip_data_reflux, command.timestamp,
        )
        if i < args.repeat - 1:
            time.sleep(max(args.interval_s, 0.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
