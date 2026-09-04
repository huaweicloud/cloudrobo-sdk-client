"""
Heartbeat publisher example.

Recommended usage:
  # Periodic heartbeats with platform credential bundle (default 1Hz, run 20s)
  python examples/heartbeat_publisher.py --bundle /path/to/cert_xxx.zip
  python examples/heartbeat_publisher.py --bundle /path/to/unpacked_bundle_dir

  # Periodic heartbeats with custom interval/jitter/duration
  python examples/heartbeat_publisher.py --bundle /path/to/cert_xxx.zip --interval-ms 1000 --jitter-ms 50 --duration-s 60

  # Publish once then exit
  python examples/heartbeat_publisher.py --bundle /path/to/cert_xxx.zip --once

Advanced explicit ClientConfig usage:
  python examples/heartbeat_publisher.py \
      --project-id test-tenant \
      --device-id robot-01 \
      --client-id robot-pub-01 \
      --mode peer
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from typing import Any, Dict, List, Optional

from cloudrobo_r2c.client import R2CClient
from cloudrobo_r2c.common.config import ClientConfig


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="R2C SDK Heartbeat Publisher Example")

    # Recommended formal connection mode
    p.add_argument(
        "--bundle",
        default=None,
        help="Path to platform credential bundle zip or unpacked bundle directory (recommended)",
    )

    # Explicit ClientConfig mode
    p.add_argument("--project-id", default=None, help="Project ID for explicit ClientConfig mode")
    p.add_argument("--device-id", default=None, help="Device ID for explicit ClientConfig mode")
    p.add_argument(
        "--client-id",
        default=None,
        help="Optional client_id override; in bundle mode SDK can auto-generate if omitted",
    )
    p.add_argument(
        "--endpoints",
        default="",
        help="Comma-separated endpoints for explicit ClientConfig mode",
    )
    p.add_argument(
        "--mode",
        default="peer",
        choices=["peer", "client"],
        help="Connection mode for explicit ClientConfig mode",
    )

    # Heartbeat behavior
    p.add_argument("--interval-ms", type=int, default=1000, help="Heartbeat interval in ms (1Hz=1000)")
    p.add_argument("--jitter-ms", type=int, default=0, help="Optional jitter in ms")
    p.add_argument("--duration-s", type=int, default=20, help="Run duration in seconds (periodic mode)")
    p.add_argument("--print-every-s", type=int, default=5, help="Print stats every N seconds")
    p.add_argument("--once", action="store_true", help="Publish one heartbeat then exit")
    return p


def parse_endpoints(raw: Optional[str]) -> List[str]:
    """Parse comma-separated endpoints."""
    if not raw:
        return []
    return [ep.strip() for ep in raw.split(",") if ep.strip()]


def build_session(args: argparse.Namespace):
    """Build session from bundle or explicit ClientConfig."""
    if args.bundle:
        print(f"[Robot] Connecting with platform credential bundle: {args.bundle}")
        if args.client_id:
            print(f"[Robot] client_id override: {args.client_id}")
        return R2CClient.connect(args.bundle, client_id=args.client_id)

    if not args.project_id:
        raise ValueError("project_id is required when --bundle is not provided")
    if not args.device_id:
        raise ValueError("device_id is required when --bundle is not provided")

    resolved_client_id = args.client_id or "robot-heartbeat-01"
    endpoints = parse_endpoints(args.endpoints)

    cfg = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=resolved_client_id,
        endpoints=endpoints,
        mode=args.mode,
    )
    cfg.validate()

    print(
        f"[Robot] Connecting with explicit ClientConfig: client_id={cfg.client_id}, "
        f"project_id={cfg.project_id}, device_id={cfg.device_id}, mode={cfg.mode}, endpoints={list(cfg.endpoints)}"
    )
    return R2CClient.connect(cfg)


def main() -> int:
    args = build_arg_parser().parse_args()

    sess = None
    main_exc: Optional[BaseException] = None

    # Graceful stop on Ctrl+C / SIGTERM
    stop_flag = {"stop": False}

    def _handle_stop_signal(signum, frame):
        stop_flag["stop"] = True
        print(f"\n[Robot] Signal received ({signum}), stopping...")

    signal.signal(signal.SIGINT, _handle_stop_signal)
    signal.signal(signal.SIGTERM, _handle_stop_signal)

    try:
        if args.interval_ms <= 0:
            raise ValueError("--interval-ms must be > 0")
        if args.jitter_ms < 0:
            raise ValueError("--jitter-ms must be >= 0")
        if args.duration_s < 0:
            raise ValueError("--duration-s must be >= 0")
        if args.print_every_s <= 0:
            raise ValueError("--print-every-s must be > 0")

        # 1) Connect
        sess = build_session(args)
        print("[Robot] Connected. connection_info():")
        print(sess.connection_info())

        # 2) Prepare a simple provider
        state = {
            "pct": 100.0,
            "tick": 0,
            "error_code": [],
            "mode": "auto",
            "status": "online",
        }

        def provider() -> Dict[str, Any]:
            """
            Return dict heartbeat payload.
            Intentionally omit 'timestamp' to verify SDK auto-fills timestamp.
            """
            state["tick"] += 1

            # Simple battery drain simulation
            state["pct"] = max(0.0, state["pct"] - 0.1)

            # Demonstrate a transient error code occasionally
            if state["tick"] % 30 == 0:
                state["error_code"] = [1001]
            else:
                state["error_code"] = []

            return {
                "status": state["status"],
                "mode": state["mode"],
                "error_code": state["error_code"],
                "battery": {
                    "percentage": float(state["pct"]),
                    "voltage": 24.2,
                    "current": 1.0,
                },
            }

        if args.once:
            sess.publish_heartbeats(provider())
            print("[Robot] Published one heartbeat.")
            print("[Robot] Stats:", sess.get_state_report_stats(reset=False))
            return 0

        # 3) Periodic mode
        sess.start_heartbeats(
            provider=provider,
            interval_ms=args.interval_ms,
            jitter_ms=args.jitter_ms,
        )
        print(
            f"[Robot] Heartbeats started: interval={args.interval_ms}ms (≈{1000 / args.interval_ms:.2f}Hz), "
            f"jitter={args.jitter_ms}ms, duration={args.duration_s}s"
        )

        t0 = time.time()
        last_print = t0

        while True:
            now = time.time()

            if stop_flag["stop"]:
                break
            if now - t0 >= args.duration_s:
                break

            if now - last_print >= args.print_every_s:
                stats = sess.get_state_report_stats(reset=False)
                hb = stats.get("heartbeats", {})
                print(
                    f"[Robot] Stats @ +{int(now - t0)}s | "
                    f"sent={hb.get('sent_messages')} msgs, bytes={hb.get('sent_bytes')}, "
                    f"failed={hb.get('failed_messages')}, provider_err={hb.get('provider_errors')}, "
                    f"running={hb.get('running')}"
                )
                last_print = now

            time.sleep(0.2)

        sess.stop_heartbeats()
        print("[Robot] Heartbeats stopped.")
        print("[Robot] Final stats:", sess.get_state_report_stats(reset=False))
        return 0

    except KeyboardInterrupt:
        main_exc = KeyboardInterrupt()
        print("[Robot] Interrupted by user (Ctrl+C).", file=sys.stderr)
        return 130

    except Exception as e:
        main_exc = e
        print(f"[Robot] ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    finally:
        if sess is not None:
            try:
                sess.close()
            except BaseException as e:
                print(f"[Robot] ERROR during session.close(): {type(e).__name__}: {e}", file=sys.stderr)
                if main_exc is None:
                    raise SystemExit(2)
        print("[Robot] Session closed.")


if __name__ == "__main__":
    sys.exit(main())