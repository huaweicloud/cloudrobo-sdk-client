"""话题回放器：加载 pkl 记录，按白/黑名单过滤并按原始时间戳发布。

Core usage:
    python examples/topic_replayer.py --client-config config/client_config.yaml \
        --source recordings/record_20260902_150000.pkl

    python examples/topic_replayer.py --bundle /path/to/cert_xxx.zip \
        --source recordings/record_xxx.pkl

    python examples/topic_replayer.py \
        --project-id r2c --device-id local --client-id replayer-01 \
        --endpoints tcp/127.0.0.1:7447 --mode peer \
        --source recordings/record_xxx.pkl \
        --whitelist '*current_state,*observations' --speed 2.0

Filter:
    --whitelist a,b   fnmatch 通配，逗号分隔；为空 = 全部保留
    --blacklist a,b   先按黑名单排除
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from typing import List, Optional

sys.path.insert(0, "src")

from cloudrobo_r2c import ClientConfig, R2CClient
from cloudrobo_r2c.tools.record_replay import (
    TopicReplayer,
    open_zenoh_session,
    zenoh_session_from_client,
)

logger = logging.getLogger("topic_replayer")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    conn = parser.add_mutually_exclusive_group()
    conn.add_argument("--bundle", help="platform credential bundle (.zip/dir)")
    conn.add_argument(
        "--client-config",
        help="client config YAML (project/device/client_id, endpoints, mode)",
    )
    parser.add_argument("--project-id", default="r2c")
    parser.add_argument("--device-id", default="local")
    parser.add_argument("--client-id", default="topic-replayer-01")
    parser.add_argument("--endpoints", help="comma-separated zenoh endpoints")
    parser.add_argument("--mode", default="peer", choices=["peer", "client", "router"])
    parser.add_argument(
        "--endpoint-role",
        default="connect",
        choices=["connect", "listen"],
    )
    parser.add_argument("--source", required=True, help="recording pkl path")
    parser.add_argument(
        "--whitelist",
        help="comma-separated fnmatch patterns; empty = keep all",
    )
    parser.add_argument(
        "--blacklist",
        help="comma-separated fnmatch patterns to drop",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="playback speed multiplier (default 1.0)",
    )
    parser.add_argument(
        "--skip-lead-in",
        action="store_true",
        help="drop the wait before the first (filtered) topic; publish it immediately",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="replay repeatedly until --max-duration-s",
    )
    parser.add_argument(
        "--max-duration-s",
        type=float,
        default=0.0,
        help="stop after N seconds (0 = play once / until Ctrl+C with --loop)",
    )
    return parser.parse_args()


def parse_patterns(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def parse_endpoints(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [ep.strip() for ep in raw.split(",") if ep.strip()]


def _connect_with_bundle(args: argparse.Namespace):
    logger.info("connecting with bundle: %s", args.bundle)
    client = R2CClient.connect(args.bundle, client_id=args.client_id)
    return zenoh_session_from_client(client)


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
    return ClientConfig(
        project_id=args.project_id or "r2c",
        device_id=args.device_id or "local",
        client_id=args.client_id,
        endpoints=parse_endpoints(args.endpoints),
        endpoint_role=args.endpoint_role,
        mode=args.mode,
    )


def _connect_with_config(args: argparse.Namespace):
    config = _build_client_config(args)
    endpoints = list(config.endpoints)
    if not endpoints:
        raise SystemExit("no zenoh endpoints configured")
    logger.info(
        "zenoh session: mode=%s role=%s endpoints=%s",
        config.mode,
        config.endpoint_role,
        endpoints,
    )
    return open_zenoh_session(
        endpoints=endpoints,
        mode=config.mode,
        endpoint_role=args.endpoint_role or config.endpoint_role,
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    args = parse_args()

    if args.bundle:
        session = _connect_with_bundle(args)
    else:
        session = _connect_with_config(args)

    replayer = TopicReplayer(
        session,
        args.source,
        whitelist=parse_patterns(args.whitelist),
        blacklist=parse_patterns(args.blacklist),
        speed=args.speed,
        loop=args.loop,
        skip_lead_in=args.skip_lead_in,
    )
    logger.info(
        "replayer ready: %d frames, speed=%.2fx, loop=%s",
        len(replayer.frames),
        args.speed,
        args.loop,
    )

    stop_requested = {"flag": False}

    def _on_signal(signum, _frame):
        stop_requested["flag"] = True

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    deadline = (
        time.monotonic() + args.max_duration_s if args.max_duration_s > 0 else None
    )

    def _should_stop() -> bool:
        if stop_requested["flag"]:
            return True
        return deadline is not None and time.monotonic() >= deadline

    published = 0
    try:
        while not _should_stop():
            published += replayer.run(
                max_duration_s=0.0,
                should_stop=_should_stop,
            )
            if not args.loop:
                break
    finally:
        if not args.bundle:
            try:
                session.close()
            except Exception:
                logger.debug("session close failed", exc_info=True)
    logger.info("replay done: %d frames published", published)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
