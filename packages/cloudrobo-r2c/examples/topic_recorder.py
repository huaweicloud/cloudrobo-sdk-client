"""话题记录器：订阅 {project_id}/{device_id}/**（默认，多层通配）并保存为带时间戳的 pkl。

Core usage:
    python examples/topic_recorder.py --client-config config/client_config.yaml \
        --project-id r2c --device-id local

    python examples/topic_recorder.py --bundle /path/to/cert_xxx.zip \
        --topics '{project_id}/{device_id}/current_state'

    python examples/topic_recorder.py \
        --project-id r2c --device-id local --client-id recorder-01 \
        --endpoints tcp/127.0.0.1:7447 --mode peer --endpoint-role connect

Output:
    recordings/record_YYYYmmdd_HHMMSS.pkl（--output 可自定义）
Ctrl+C 停止并保存；--max-duration-s 到时自动保存退出。
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import datetime
from typing import List, Optional

sys.path.insert(0, "src")

from cloudrobo_r2c import ClientConfig, R2CClient
from cloudrobo_r2c.tools.record_replay import (
    TopicRecorder,
    open_zenoh_session,
    zenoh_session_from_client,
)

logger = logging.getLogger("topic_recorder")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    conn = parser.add_mutually_exclusive_group()
    conn.add_argument("--bundle", help="platform credential bundle (.zip/dir)")
    conn.add_argument(
        "--client-config",
        help="client config YAML (project/device/client_id, endpoints, mode)",
    )
    parser.add_argument("--project-id", default="r2c", help="project id (topic prefix)")
    parser.add_argument("--device-id", default="local", help="device id (topic prefix)")
    parser.add_argument("--client-id", default="topic-recorder-01")
    parser.add_argument("--endpoints", help="comma-separated zenoh endpoints")
    parser.add_argument("--mode", default="peer", choices=["peer", "client", "router"])
    parser.add_argument(
        "--endpoint-role",
        default="connect",
        choices=["connect", "listen"],
        help="how endpoints are used (default: connect)",
    )
    parser.add_argument(
        "--topics",
        default="{project_id}/{device_id}/**",
        help=("comma-separated key expressions "
              "(default: {project_id}/{device_id}/**, multi-level glob)"),
    )
    parser.add_argument(
        "--output",
        help="output pkl path (default: recordings/record_<timestamp>.pkl)",
    )
    parser.add_argument(
        "--max-duration-s",
        type=float,
        default=0.0,
        help="stop and save after N seconds (0 = until Ctrl+C)",
    )
    return parser.parse_args()


def parse_endpoints(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [ep.strip() for ep in raw.split(",") if ep.strip()]


def expand_topics(patterns: str, project_id: str, device_id: str) -> List[str]:
    return [
        p.strip().format(project_id=project_id, device_id=device_id)
        for p in patterns.split(",")
        if p.strip()
    ]


def _connect_with_bundle(args: argparse.Namespace):
    logger.info("connecting with bundle: %s", args.bundle)
    client = R2CClient.connect(args.bundle, client_id=args.client_id)
    project_id = args.project_id or client.project_id
    device_id = args.device_id or client.device_id
    return zenoh_session_from_client(client), project_id, device_id


def _build_client_config(args: argparse.Namespace) -> ClientConfig:
    """Build a ``ClientConfig`` from the non-bundle CLI connection args."""
    if args.client_config:
        return ClientConfig.from_yaml(
            args.client_config,
            project_id=args.project_id,
            device_id=args.device_id,
            client_id=args.client_id,
            endpoints=parse_endpoints(args.endpoints) or None,
            mode=args.mode,
        )
    if not args.project_id:
        raise SystemExit(
            "project_id is required (via --project-id or --client-config)"
        )
    return ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id,
        endpoints=parse_endpoints(args.endpoints),
        endpoint_role=args.endpoint_role,
        mode=args.mode,
    )


def _connect_with_config(args: argparse.Namespace):
    config = _build_client_config(args)
    project_id = config.project_id
    device_id = config.device_id
    endpoints = list(config.endpoints)
    if not endpoints:
        raise SystemExit("no zenoh endpoints configured")
    logger.info(
        "zenoh session: mode=%s role=%s endpoints=%s",
        config.mode,
        config.endpoint_role,
        endpoints,
    )
    session = open_zenoh_session(
        endpoints=endpoints,
        mode=config.mode,
        endpoint_role=args.endpoint_role or config.endpoint_role,
    )
    return session, project_id, device_id


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    args = parse_args()

    if args.bundle:
        session, project_id, device_id = _connect_with_bundle(args)
    else:
        session, project_id, device_id = _connect_with_config(args)

    key_exprs = expand_topics(args.topics, project_id, device_id)
    output = args.output or (
        f"recordings/record_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pkl"
    )
    recorder = TopicRecorder(session, key_exprs, output)
    recorder.start()
    logger.info("recorder started: %s -> %s", key_exprs, output)

    stop_requested = {"flag": False}

    def _on_signal(signum, _frame):
        stop_requested["flag"] = True

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    deadline = (
        time.monotonic() + args.max_duration_s if args.max_duration_s > 0 else None
    )
    try:
        while not stop_requested["flag"]:
            if deadline is not None and time.monotonic() >= deadline:
                break
            time.sleep(0.2)
    finally:
        recorder.stop(save=True)
        if not args.bundle:
            try:
                session.close()
            except Exception:
                logger.debug("session close failed", exc_info=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
