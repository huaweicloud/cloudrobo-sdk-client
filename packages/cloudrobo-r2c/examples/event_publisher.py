"""上传告警（event）样例：发布 Event 到本地 {project_id}/{device_id}/event，
经 r2c_forwarder 转发上云（source/timestamp 由转发侧覆盖为云身份/当前时间）。

单次告警:
    python examples/event_publisher.py \
        --endpoints tcp/127.0.0.1:7447 --mode peer \
        --abstract "质检得分 62，低于训练准入线" --severity Major \
        --content "质检得分 62，低于训练准入线 85"

完整周期演示（1 个 repeat = 告警上传 + 解除上传；--duration-ms 为
告警持续时间，--interval-s 为周期间隔）:
    python examples/event_publisher.py \
        --abstract "左臂关节2 电机过载" --severity Major \
        --repeat 5 --duration-ms 3000 --interval-s 2

只上传告警（--phase raise，ends_at=0 进行中）/ 只上传解除
（--phase clear，复用 --clear-event-id 或 --event-id，ends_at=当前）:
    python examples/event_publisher.py ... --phase raise
    python examples/event_publisher.py ... --phase clear \
        --clear-event-id <原告警event_id>

消除告警（复用原告警 event_id，ends_at=当前时刻）:
    python examples/event_publisher.py \
        --clear-event-id <原告警event_id> \
        --abstract "电机过载解除" --content "左臂关节2 电机过载已恢复"

severity 枚举: Critical / Major / Minor / Info
type 枚举: event（事件）/ alarm（告警）
连接方式同其他示例：--bundle / --client-config / 显式参数。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import List, Optional

sys.path.insert(0, "src")

from cloudrobo_r2c import ClientConfig, R2CClient
from cloudrobo_r2c.common.models import EventMessage
from cloudrobo_r2c.common.models.wrappers.event import EventMetadata

logger = logging.getLogger("r2c_event_publisher")

SEVERITIES = ["Critical", "Major", "Minor", "Info"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    conn = parser.add_mutually_exclusive_group()
    conn.add_argument("--bundle", help="platform credential bundle (.zip/dir)")
    conn.add_argument("--client-config", help="client config YAML")
    parser.add_argument("--project-id", default="r2c")
    parser.add_argument("--device-id", default="local")
    parser.add_argument("--client-id", default="r2c-event-publisher")
    parser.add_argument("--endpoints", default="tcp/127.0.0.1:7447",
                        help="comma-separated zenoh endpoints")
    parser.add_argument("--mode", default="peer", choices=["peer", "client", "router"])
    parser.add_argument("--endpoint-role", default="connect",
                        choices=["connect", "listen"])
    parser.add_argument("--abstract", default="r2c sample event",
                        help="event abstract/摘要 (default: r2c sample event)")
    parser.add_argument("--event-id", default="",
                        help="event_id（缺省自动生成 uuid4）")
    parser.add_argument("--type", dest="event_type", default="event",
                        choices=["event", "alarm"],
                        help="event type enum (default: event)")
    parser.add_argument("--severity", default="Minor",
                        choices=SEVERITIES, help="severity (default: Minor)")
    parser.add_argument("--content", default="r2c sample alarm",
                        help="event content text")
    parser.add_argument("--duration-ms", type=int, default=0,
                        help="告警持续时间 ms（0=上传后立即解除；解除事件 "
                             "starts_at=告警发起、ends_at=当前时刻）")
    parser.add_argument("--phase", default="cycle",
                        choices=["cycle", "raise", "clear"],
                        help="cycle=完整周期（默认，告警上传+解除上传为 "
                             "1 个 repeat）；raise=只上传告警（ends_at=0 "
                             "进行中）；clear=只上传解除（复用 "
                             "--clear-event-id/--event-id，ends_at=当前）")
    parser.add_argument("--repeat", type=int, default=1,
                        help="number of events to publish (default 1)")
    parser.add_argument("--interval-s", type=float, default=1.0,
                        help="seconds between repeats (default 1.0)")
    parser.add_argument("--clear-event-id", default="",
                        help="消除告警：复用原告警的 event_id（ends_at=当前"
                             "时刻），发布一条解除事件；配合 --severity/"
                             "--abstract/--content 定制解除文案；设置后 "
                             "--repeat/--duration-ms 被忽略")
    parser.add_argument("--starts-at", type=int, default=0,
                        help="clear 模式的 starts_at（epoch ms，缺省当前"
                             "时刻；可传原告警的 starts_at 表达完整持续"
                             "时间）")
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
    topic = f"{project_id}/{device_id}/event"
    logger.info("publishing events to %s", topic)

    now_ms = int(time.time() * 1000)

    # ── 消除告警模式：复用原告警 event_id，ends_at=当前时刻 ──────────
    if args.clear_event_id:
        clear_event = EventMessage.from_dict({
            "source": f"{project_id}/{device_id}",
            "timestamp": now_ms,
            "event_id": args.clear_event_id,
            "metadata": [{
                "abstract": args.abstract,
                "type": args.event_type,
                "severity": args.severity,
                "content": args.content,
                "starts_at": args.starts_at or now_ms,
                "ends_at": now_ms,
            }],
        })
        publish(topic, clear_event.serialize())
        logger.info(
            "clear event published: event_id=%s abstract=%s severity=%s "
            "ends_at=%d",
            clear_event.event_id, args.abstract, args.severity, now_ms,
        )
        return 0

    phase = args.phase
    for i in range(max(args.repeat, 1)):
        cycle_tag = ("" if args.repeat == 1
                     else f" ({i + 1}/{args.repeat})")
        event_id = args.event_id or str(uuid.uuid4())

        # 1) 告警上传（ends_at=0 表示进行中；phase=clear 跳过）
        raise_ms = int(time.time() * 1000)
        if phase in ("cycle", "raise"):
            raise_event = EventMessage.from_dict({
                "source": f"{project_id}/{device_id}",
                "timestamp": raise_ms,
                "event_id": event_id,
                "metadata": [{
                    "abstract": args.abstract,
                    "type": args.event_type,
                    "severity": args.severity,
                    "content": f"{args.content}{cycle_tag}",
                    "starts_at": raise_ms,
                    "ends_at": 0,
                }],
            })
            publish(topic, raise_event.serialize())
            logger.info(
                "raise published: event_id=%s abstract=%s severity=%s "
                "starts_at=%d",
                raise_event.event_id, args.abstract, args.severity,
                raise_ms,
            )

        # 2) 告警持续时间（仅完整周期）
        if phase == "cycle" and args.duration_ms > 0:
            time.sleep(args.duration_ms / 1000.0)

        # 3) 解除上传（复用同一 event_id，ends_at=当前时刻；
        # phase=raise 跳过；phase=clear 复用 --clear-event-id/--event-id）
        if phase in ("cycle", "clear"):
            clear_ms = int(time.time() * 1000)
            if phase == "clear":
                event_id = (args.clear_event_id or args.event_id
                            or str(uuid.uuid4()))
                clear_starts_at = args.starts_at or clear_ms
                clear_abstract = args.abstract
                clear_content = f"{args.content}{cycle_tag}"
                clear_severity = args.severity
            else:
                clear_starts_at = raise_ms
                clear_abstract = f"{args.abstract}解除"
                clear_content = f"{args.content} 已解除{cycle_tag}"
                clear_severity = "Info"
            clear_event = EventMessage.from_dict({
                "source": f"{project_id}/{device_id}",
                "timestamp": clear_ms,
                "event_id": event_id,
                "metadata": [{
                    "abstract": clear_abstract,
                    "type": args.event_type,
                    "severity": clear_severity,
                    "content": clear_content,
                    "starts_at": clear_starts_at,
                    "ends_at": clear_ms,
                }],
            })
            publish(topic, clear_event.serialize())
            logger.info(
                "clear published: event_id=%s abstract=%s severity=%s "
                "ends_at=%d",
                clear_event.event_id, clear_abstract, clear_severity,
                clear_ms,
            )

        if i < args.repeat - 1:
            time.sleep(max(args.interval_s, 0.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
