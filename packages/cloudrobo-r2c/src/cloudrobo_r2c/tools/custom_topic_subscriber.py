#!/usr/bin/env python3
"""自定义 topic 单次订阅工具。

订阅 ``--topic-name``，阻塞最多 ``--timeout`` 秒；收到第一条消息后按
``--topic-proto`` 对应的 wrapper（from_protobuf → to_dict）解析打印，
随即退出（收到 = 退出码 0；超时未收到 = 退出码 1）。

- topic 名支持 ``{project_id}/{device_id}`` 占位符，由参数自动展开。
- 订阅方默认 ``--endpoint-role listen``（作为稳定接收入口，配合
  custom_topic_publisher 的 connect 模式可无 router 互测）。

示例（与 custom_topic_publisher 互测）：
    # 终端 A：先启动订阅
    python -m cloudrobo_r2c.tools.custom_topic_subscriber \
        --topic-name "{project_id}/{device_id}/command" \
        --topic-proto cloudrobo_r2c.common.models:CommandMessage --timeout 15
    # 终端 B：再发送
    python -m cloudrobo_r2c.tools.custom_topic_publisher \
        --topic-name "{project_id}/{device_id}/command" \
        --topic-proto cloudrobo_r2c.common.models:CommandMessage \
        --topic-data '{"timestamp": 1700000000000, "command_type": "noop"}'
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
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
    """Resolve the wrapper (converter) class（统一解析，registry-first）.

    ``mode="decode"``：只需 ``from_protobuf`` 即可订阅解码——wrapper 四方法
    校验（含 ``to_dict``）不再强制，部分顶层消息（Observations/Actions/
    Heartbeat 等）未实现 ``to_dict`` 也能用于订阅（打印走 to_dict 或
    pb2 MessageToDict 兜底，见 _handler）。
    """
    from cloudrobo_r2c.common.proto_registry import resolve_proto_class

    return resolve_proto_class(path, "--proto", mode="decode")


def _to_dict(message: Any) -> Any:
    """Convert a parsed wrapper message to a JSON-serializable dict.

    优先 wrapper 的 ``to_dict``；未实现 ``to_dict`` 的顶层消息（如
    Observations/Actions）经 ``to_protobuf()`` + ``MessageToDict`` 兜底，
    字段名保持 proto 原名（preserving_proto_field_name）。
    """
    to_dict = getattr(message, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    to_protobuf = getattr(message, "to_protobuf", None)
    if callable(to_protobuf):
        from google.protobuf.json_format import MessageToDict

        return MessageToDict(
            to_protobuf(),
            preserving_proto_field_name=True,
            always_print_fields_with_no_presence=True,
        )
    raise TypeError(
        f"message type {type(message).__name__} has neither to_dict() "
        f"nor to_protobuf()"
    )


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
        client_id=args.client_id or "custom-topic-sub",
        endpoints=parse_endpoints(args.endpoints),
        endpoint_role=args.endpoint_role,
        mode=args.mode,
    )
    logger.info(
        "Connecting with explicit ClientConfig: client_id=%s, project_id=%s, "
        "device_id=%s, mode=%s, endpoint_role=%s, endpoints=%s",
        config.client_id,
        config.project_id,
        config.device_id,
        config.mode,
        config.endpoint_role,
        list(config.endpoints),
    )
    return R2CClient.connect(config)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Subscribe one custom-topic message (proto wrapper parses protobuf -> dict)"
    )
    # 连接（bundle 可选）
    parser.add_argument("--bundle", type=str, default=None,
                        help="Platform credential bundle (optional)")
    parser.add_argument("--project-id", type=str, default="r2c",
                        help="Project ID (also expands {project_id} in topic name)")
    parser.add_argument("--device-id", type=str, default="local",
                        help="Device ID (also expands {device_id} in topic name)")
    parser.add_argument("--client-id", type=str, default="custom-topic-sub",
                        help="Client ID (default: custom-topic-sub)")
    parser.add_argument("--endpoints", type=str, default="tcp/127.0.0.1:7447",
                        help="Comma-separated endpoints, e.g. tcp/127.0.0.1:7447")
    parser.add_argument("--endpoint-role", type=str, default="connect",
                        choices=["connect", "listen"],
                        help="Connection endpoint role (default: connect — "
                        "stable receiver entry for publisher connect mode)")
    parser.add_argument("--mode", type=str, default="client",
                        choices=["peer", "client"], help="Connection mode")
    # 目标 topic
    parser.add_argument("--topic-name", type=str, required=True,
                        help="Topic name; {project_id}/{device_id} placeholders expanded")
    parser.add_argument("--topic-proto", type=str, required=True,
                        help="Wrapper class path, e.g. cloudrobo_r2c.common.models:CommandMessage")
    parser.add_argument("--timeout", type=float, default=10.0,
                        help="Max seconds to wait for one message (default: 10)")

    args = parser.parse_args()
    if args.timeout <= 0:
        raise ValueError("--timeout must be > 0")

    wrapper_cls = resolve_wrapper_class(args.topic_proto)
    topic = TopicResolver(args.project_id, args.device_id).resolve(args.topic_name)

    received = {"payload": None}
    got = threading.Event()

    def _handler(payload: bytes) -> None:
        if got.is_set():
            return
        try:
            message = wrapper_cls.from_protobuf(bytes(payload))
            received["payload"] = _to_dict(message)
            got.set()
        except Exception:
            logger.exception("failed to parse payload (%d bytes)", len(payload))

    client = build_client(args)
    exit_code = 1
    try:
        logger.info("Subscribing %s (wrapper=%s, timeout=%.1fs) ...",
                    topic, wrapper_cls.__name__, args.timeout)
        client.transport.subscribe(topic, _handler)
        logger.info("Waiting for one message (Ctrl+C to stop)...")

        success = got.wait(timeout=args.timeout)

        if success:
            print(json.dumps(received["payload"], ensure_ascii=False, indent=2))
            logger.info("Received 1 message — done.")
            exit_code = 0
        else:
            logger.error("Timeout: no message received within %.1fs", args.timeout)
    finally:
        client.close()
    sys_exit(exit_code)


def sys_exit(code: int) -> None:
    import sys

    sys.exit(code)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    main()
