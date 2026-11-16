#!/usr/bin/env python3
"""自定义 topic 监听器。

监听 ``{project_id}/{device_id}/**`` 下的全部 topic，按候选 proto wrapper
逐一试解析（取非空字段得分最高者），解码后打印；图像等二进制字段按
``normalize_for_logging`` 缩略输出（shape/dtype/size）。

- ``--topic-proto`` 可指定 wrapper（逗号分隔多个），缺省 ``auto`` 尝试
  全部已知模型（RunTime/Command/Event/TaskProcess/Observations/Actions/
  TeleopFrame）。
- ``auto`` 模式先做**话题名后缀预匹配**：话题末两级命中预匹配表（如
  ``teleop/actions`` → TeleopFrame）时直接按该 wrapper 解码，不再试
  解析——载荷字段撞车也不会误判；未命中（如 ``/inference/observations``
  之外的自定义话题）回退到试解析判别（roundtrip 长度差 + 非空字段得分）。
  显式 ``--topic-proto`` 时不干预，完全按用户指定解码。
- ``--timeout`` 秒后自动退出（0 = 一直运行，Ctrl+C 停止）。
- 连接方式与其他联调示例一致（--bundle 可选 / --endpoints / --mode）。

示例（监听全部自定义 topic / 云侧 topic）：
    python -m cloudrobo_r2c.tools.custom_topic_listener \
        --project-id r2c --device-id local \
        --endpoints tcp/127.0.0.1:7447 --endpoint-role connect \
        --timeout 30

示例（只监听 teleop topic）：
    python -m cloudrobo_r2c.tools.custom_topic_listener \
        --project-id r2c --device-id local \
        --topic-name "{project_id}/{device_id}/teleop/**" \
        --topic-proto cloudrobo_r2c.common.models:TeleopFrame \
        --endpoints tcp/127.0.0.1:7447 --endpoint-role connect \
        --timeout 30
"""

from __future__ import annotations

import argparse
import logging
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional

from cloudrobo_r2c import ClientConfig, R2CClient
from cloudrobo_r2c.client.topic_resolver import TopicResolver

logger = logging.getLogger(__name__)


def normalize_for_logging(data: Any) -> Any:
    """Normalize nested data for readable logging output.

    Inlined from ``examples/_cloud_adapter_common.py`` to keep this tool
    self-contained (no dependency on the ``examples/`` directory).
    """
    if is_dataclass(data) and not isinstance(data, type):
        return normalize_for_logging(asdict(data))

    try:
        import numpy as np
    except Exception:
        np = None

    if np is not None and isinstance(data, np.ndarray):
        return {
            "type": "ndarray",
            "shape": list(data.shape),
            "dtype": str(data.dtype),
            "min": float(data.min()) if data.size else None,
            "max": float(data.max()) if data.size else None,
        }

    if isinstance(data, bytes):
        return {"type": "bytes", "size": len(data)}

    if isinstance(data, dict):
        return {k: normalize_for_logging(v) for k, v in data.items()}

    if isinstance(data, list):
        return [normalize_for_logging(v) for v in data]

    return data

WRAPPER_REQUIRED_ATTRS = ("from_protobuf",)

AUTO_WRAPPERS = [
    "cloudrobo_r2c.common.models.wrappers.heartbeat:Heartbeat",
    "cloudrobo_r2c.common.models:RunTimeMessage",
    "cloudrobo_r2c.common.models:CommandMessage",
    "cloudrobo_r2c.common.models:EventMessage",
    "cloudrobo_r2c.common.models:TaskProcessMessage",
    "cloudrobo_r2c.common.models:Observations",
    "cloudrobo_r2c.common.models:Actions",
    # teleop 操作端帧（teleop.proto，命令帧 + 动作帧）
    "cloudrobo_r2c.common.models:TeleopFrame",
    # 数据回流大屏（pb2 直引用，无 wrapper）
    "cloudrobo_r2c.common.models.generated.data_quality_overview_pb2:DataQualityOverview",
    "cloudrobo_r2c.common.models.generated.data_upload_progress_pb2:DataUploadProgress",
]

# 话题名后缀 → wrapper 类名（auto 模式的预匹配表）。
# 话题末段与次末段拼出的 key（如 teleop/actions → "actions/teleop"）命中
# 时直接锁定对应 wrapper 解码，跳过试解析（载荷字段撞车也不再误判）；
# 未命中再按原试解析逻辑（roundtrip 差值 + 得分）判别。新增条目时保持
# "次末段/末段" 的顺序（末段在前更直观，含通配的多级话题写末两级）。
TOPIC_SUFFIX_WRAPPERS = {
    "actions/teleop": "TeleopFrame",
    "commands/teleop": "TeleopFrame",
    "observations": "Observations",
    "actions": "Actions",
    "command": "CommandMessage",
    "event": "EventMessage",
    "heartbeat": "Heartbeat",
    "task_process": "TaskProcessMessage",
    "joint_states": "RunTimeMessage",
    "end_effector_states": "RunTimeMessage",
    "end_effector_poses": "RunTimeMessage",
}


def topic_preferred_wrapper_name(topic: str) -> Optional[str]:
    """按话题名后缀给出预匹配 wrapper 类名（无匹配返回 None）。

    取话题末段与次末段拼 ``"末段/次末段"``：既命中 ``observations``
    这类单级话题，也让 ``teleop/actions``（与 r2c 的 ``/actions``）
    以 "actions/teleop" 区分开。
    """
    parts = [p for p in topic.strip("/").split("/") if p]
    if not parts:
        return None
    last = parts[-1]
    prev = parts[-2] if len(parts) >= 2 else ""
    if prev:
        hit = TOPIC_SUFFIX_WRAPPERS.get(f"{last}/{prev}")
        if hit:
            return hit
    return TOPIC_SUFFIX_WRAPPERS.get(last)


def parse_endpoints(raw: Optional[str]) -> List[str]:
    """Parse comma-separated endpoints."""
    if not raw:
        return []
    return [ep.strip() for ep in raw.split(",") if ep.strip()]


def resolve_wrapper_class(path: str):
    """Resolve a wrapper class exposing from_protobuf(payload)（统一解析）.

    pb2 直引用的消息类自动适配为
    :class:`~cloudrobo_r2c.common.proto_registry.Pb2MessageAdapter`
    （提供 from_protobuf/serialize 与 __name__，接口对齐 wrapper）。
    """
    from cloudrobo_r2c.common.proto_registry import resolve_proto_class

    return resolve_proto_class(path, "--topic-proto", mode="decode")


def resolve_wrappers(spec: str) -> List[tuple]:
    """Resolve 'auto' or a comma-separated wrapper list to (name, class)."""
    if spec.strip().lower() == "auto":
        spec = ",".join(AUTO_WRAPPERS)
    out: List[tuple] = []
    for path in [p.strip() for p in spec.split(",") if p.strip()]:
        cls = resolve_wrapper_class(path)
        out.append((cls.__name__, cls))
    if not out:
        raise ValueError("no wrappers resolved")
    return out


def _score(obj: Any) -> int:
    """Count non-default fields recursively (higher = better decode)."""
    if hasattr(obj, "ListFields"):
        # protobuf 消息（含 _Pb2MessageView 转发）：按已设置字段计分
        try:
            return sum(_score(v) for _, v in obj.ListFields())
        except Exception:
            return 0
    if is_dataclass(obj) and not isinstance(obj, type):
        return _score({
            f: getattr(obj, f)
            for f in obj.__dataclass_fields__  # type: ignore[attr-defined]
        })
    if isinstance(obj, dict):
        return sum(_score(v) for v in obj.values())
    if isinstance(obj, (list, tuple, set)):
        return sum(_score(v) for v in obj)
    if isinstance(obj, bytes):
        return 1 if obj else 0
    if isinstance(obj, str):
        return 1 if obj else 0
    if isinstance(obj, (int, float)):
        return 1 if obj else 0
    return 0


def build_client(args: argparse.Namespace):
    """Build client session from bundle or explicit ClientConfig."""
    if args.bundle:
        logger.info("Connecting with platform credential bundle: %s", args.bundle)
        return R2CClient.connect(args.bundle, client_id=args.client_id or None)

    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id or "custom-topic-listener",
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
        description="Listen to {project_id}/{device_id}/* topics, auto-decode and print"
    )
    parser.add_argument("--bundle", type=str, default=None,
                        help="Platform credential bundle (optional)")
    parser.add_argument("--project-id", type=str, default="r2c",
                        help="Project ID (expands {project_id})")
    parser.add_argument("--device-id", type=str, default="local",
                        help="Device ID (expands {device_id})")
    parser.add_argument("--client-id", type=str, default="custom-topic-listener",
                        help="Client ID (default: custom-topic-listener)")
    parser.add_argument("--endpoints", type=str, default="tcp/127.0.0.1:7447",
                        help="Comma-separated endpoints, e.g. tcp/127.0.0.1:7447")
    parser.add_argument("--endpoint-role", type=str, default="connect",
                        choices=["connect", "listen"],
                        help="Connection endpoint role (default: connect)")
    parser.add_argument("--mode", type=str, default="client",
                        choices=["peer", "client"], help="Connection mode")
    parser.add_argument("--topic-name", type=str, default="{project_id}/{device_id}/**",
                        help="Topic selector (default: {project_id}/{device_id}/**)")
    parser.add_argument("--topic-proto", type=str, default="auto",
                        help="'auto' or comma-separated wrapper paths to try")
    parser.add_argument("--timeout", type=float, default=0.0,
                        help="Stop after N seconds (0 = run until Ctrl+C)")

    args = parser.parse_args()
    if args.timeout < 0:
        raise ValueError("--timeout must be >= 0")

    wrappers = resolve_wrappers(args.topic_proto)
    selector = TopicResolver(args.project_id, args.device_id).resolve(args.topic_name)
    logger.info("Listening %s | wrappers: %s", selector, [n for n, _ in wrappers])
    by_name = dict(wrappers)
    # 话题后缀预匹配（仅 auto 模式且表内命中时生效）；--topic-proto 显式
    # 指定时尊重用户选择，不走预匹配。
    preferred_name = (
        topic_preferred_wrapper_name(selector)
        if args.topic_proto.strip().lower() == "auto"
        else None
    )
    if preferred_name and preferred_name in by_name:
        logger.info(
            "Topic suffix pre-match: %s → %s (payload decode skips trial-"
            "parse; falls back to trial-parse only if decoding fails)",
            selector,
            preferred_name,
        )

    client = build_client(args)
    count = {"n": 0}

    def _handler(payload: bytes) -> None:
        payload = bytes(payload)
        count["n"] += 1
        # 判别：话题后缀预匹配优先（auto 且命中预匹配表时）；匹配上的
        # wrapper 能解码就直接用，不再试解析——载荷字段撞车也不会误判。
        # 未命中或解码失败时退回试解析：roundtrip 长度差（误解析的
        # 重序列化长度必偏离）为主依据，非空字段得分为次。
        preferred = by_name.get(preferred_name) if preferred_name else None
        if preferred is not None:
            try:
                obj = preferred.from_protobuf(payload)
                best = (0, 0, preferred_name, obj)
            except Exception:
                logger.debug(
                    "pre-matched %s failed to decode; falling back to trial-parse",
                    preferred_name,
                    exc_info=True,
                )
                best = None
        else:
            best = None
        if best is None:  # (roundtrip_diff, -score, name, obj)
            for name, cls in wrappers:
                try:
                    obj = cls.from_protobuf(payload)
                    reser = obj.serialize()
                except Exception:
                    logger.debug("payload parse failed for %s", name, exc_info=True)
                    continue
                cand = (abs(len(reser) - len(payload)), -_score(obj), name, obj)
                if best is None or cand[:2] < best[:2]:
                    best = cand
        print(f"\n=== #{count['n']} key={selector} | {len(payload)} bytes ===")
        if best is None:
            print(f"<undecodable payload: tried {len(wrappers)} wrappers>")
        else:
            print(f"decoded as: {best[2]}")
            print(normalize_for_logging(best[3]))
        print("=" * 40)

    client.transport.subscribe(selector, _handler)
    logger.info("Waiting for messages (Ctrl+C to stop)...")

    start = time.monotonic()
    try:
        while True:
            if args.timeout > 0 and time.monotonic() - start >= args.timeout:
                logger.info("Timeout reached, stopping.")
                break
            time.sleep(0.2)
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    finally:
        client.close()
        logger.info("Listener stopped. Total messages: %d", count["n"])


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    main()
