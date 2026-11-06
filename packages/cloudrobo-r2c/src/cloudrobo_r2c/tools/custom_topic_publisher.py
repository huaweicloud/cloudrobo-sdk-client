#!/usr/bin/env python3
"""自定义 topic 单次发送工具。

根据 ``--topic-proto`` 查找对应的转换函数（wrapper 的 from_dict →
protobuf 序列化），将 ``--topic-data``（JSON）转换后发送到
``--topic-name``，发送一次即退出。

- topic 名支持 ``{project_id}/{device_id}`` 占位符，由参数自动展开。
- ``--topic-proto`` 为 wrapper 类路径：``module:Class`` 或 ``module.Class``，
  必须实现 from_dict / serialize（如 ``cloudrobo_r2c.common.models:CommandMessage``）。
- ``--topic-data`` 为 JSON 字符串，或 ``@path/to/file.json`` 从文件读取。

连接方式与其他联调示例一致：--bundle 可选，缺省用显式参数
（--endpoints / --endpoint-role / --mode）。

示例：
    # 发送 command（配合适配器 on_command 联调）
    python -m cloudrobo_r2c.tools.custom_topic_publisher \
        --topic-name "{project_id}/{device_id}/command" \
        --topic-proto cloudrobo_r2c.common.models:CommandMessage \
        --topic-data '{"timestamp": 1700000000000, "command_type": "noop"}'
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from typing import List, Optional

from cloudrobo_r2c import ClientConfig, R2CClient
from cloudrobo_r2c.client.topic_resolver import TopicResolver

logger = logging.getLogger(__name__)


def parse_endpoints(raw: Optional[str]) -> List[str]:
    """Parse comma-separated endpoints."""
    if not raw:
        return []
    return [ep.strip() for ep in raw.split(",") if ep.strip()]


def resolve_wrapper_class(path: str):
    """Resolve the wrapper (converter) class（统一解析，registry-first）."""
    from cloudrobo_r2c.common.proto_registry import resolve_proto_class

    return resolve_proto_class(path, "--proto", mode="wrapper")


def build_client(args: argparse.Namespace):
    """Build client session from bundle or explicit ClientConfig."""
    if args.bundle:
        logger.info("Connecting with platform credential bundle: %s", args.bundle)
        return R2CClient.connect(args.bundle, client_id=args.client_id or None)

    if not args.project_id:
        raise ValueError("project_id is required when --bundle is not provided")
    if not args.device_id:
        raise ValueError("device_id is required when --bundle is not provided")

    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id or "custom-topic-pub",
        endpoints=parse_endpoints(args.endpoints),
        endpoint_role=args.endpoint_role,
        mode=args.mode,
    )
    logger.info(
        "Connecting with explicit ClientConfig: client_id=%s, project_id=%s, "
        "device_id=%s, mode=%s, endpoints=%s",
        config.client_id,
        config.project_id,
        config.device_id,
        config.mode,
        list(config.endpoints),
    )
    return R2CClient.connect(config)


def load_topic_data(raw: str) -> dict:
    """Load --topic-data: inline JSON or '@file' reference."""
    if raw.startswith("@"):
        path = raw[1:]
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("--topic-data JSON must be an object")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send one custom-topic message (proto wrapper converts JSON -> protobuf)"
    )
    # 连接（bundle 可选，同其他联调示例）
    parser.add_argument("--bundle", type=str, default=None,
                        help="Platform credential bundle (optional)")
    parser.add_argument("--project-id", type=str, default="r2c",
                        help="Project ID (also expands {project_id} in topic name)")
    parser.add_argument("--device-id", type=str, default="local",
                        help="Device ID (also expands {device_id} in topic name)")
    parser.add_argument("--client-id", type=str, default="custom-topic-pub",
                        help="Client ID (default: custom-topic-pub)")
    parser.add_argument("--endpoints", type=str, default="tcp/127.0.0.1:7447",
                        help="Comma-separated endpoints, e.g. tcp/127.0.0.1:7447")
    parser.add_argument("--endpoint-role", type=str, default="connect",
                        choices=["connect", "listen"],
                        help="Connection endpoint role")
    parser.add_argument("--mode", type=str, default="client",
                        choices=["peer", "client"], help="Connection mode")
    # 目标 topic
    parser.add_argument("--topic-name", type=str, required=True,
                        help="Topic name; {project_id}/{device_id} placeholders expanded")
    parser.add_argument("--topic-proto", type=str, required=True,
                        help="Wrapper class path, e.g. cloudrobo_r2c.common.models:CommandMessage")
    parser.add_argument("--topic-data", type=str, required=True,
                        help="JSON string, or '@path/to/file.json'")
    parser.add_argument("--settle-s", type=float, default=1.0,
                        help="Wait N seconds after connect for peer/router "
                        "discovery before publishing (default: 1.0)")

    args = parser.parse_args()

    wrapper_cls = resolve_wrapper_class(args.topic_proto)
    data = load_topic_data(args.topic_data)

    topic = TopicResolver(args.project_id, args.device_id).resolve(args.topic_name)
    message = wrapper_cls.from_dict(data)
    payload = message.serialize()

    logger.info(
        "Resolved wrapper=%s | topic=%s | payload=%d bytes | data=%s",
        wrapper_cls.__name__,
        topic,
        len(payload),
        message.to_dict(),
    )

    client = build_client(args)
    try:
        if args.settle_s > 0:
            logger.info("Settling %.1fs for peer/router discovery ...", args.settle_s)
            time.sleep(args.settle_s)
        client.transport.publish(topic, payload)
        logger.info("Sent 1 message to %s — done.", topic)
    finally:
        client.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    main()
