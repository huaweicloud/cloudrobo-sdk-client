"""Dummy cloud adapter example.

订阅 Observations，并基于其中的 joint position 生成轻微增量的 Actions。
生成的动作轨迹 chunk_size 固定为 50（即 50 个轨迹步）。

用法示例：
    python examples/dummy_cloud_adapter.py --client-config config/client_config.yaml
    python examples/dummy_cloud_adapter.py --bundle /path/to/bundle.zip --target-device-id robot-01
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any, Optional, Sequence

from cloudrobo_r2c.client import R2CClient
from cloudrobo_r2c.common.config import ClientConfig
from cloudrobo_r2c.common.models import Actions, Observations, ExtensionValue

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 50
DEFAULT_INCREMENT = 0.001


def parse_endpoints(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [endpoint.strip() for endpoint in raw.split(",") if endpoint.strip()]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Dummy cloud adapter: subscribe observations and publish incremental actions"
        )
    )

    # 与 src/cloudrobo_r2c/cloudroboclient.py 保持一致的连接参数
    parser.add_argument(
        "--bundle",
        type=str,
        default=None,
        help=(
            "Path to the platform-issued credential bundle zip or unpacked "
            "directory (recommended)"
        ),
    )
    parser.add_argument(
        "--client-config",
        default="config/client_config.yaml",
        help="Path to the R2C client config YAML.",
    )
    parser.add_argument("--project-id", type=str, default=None, help="Project ID")
    parser.add_argument("--device-id", type=str, default=None, help="Device ID")
    parser.add_argument("--client-id", type=str, default=None, help="Client ID")
    parser.add_argument(
        "--endpoints",
        type=str,
        default="",
        help="Comma-separated endpoints, for example: tls/127.0.0.1:7447",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="peer",
        choices=["peer", "client"],
        help="Connection mode for explicit mode",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Run duration in seconds; 0 means run forever",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Python logging level, e.g. DEBUG/INFO/WARNING.",
    )

    parser.add_argument(
        "--target-device-id",
        type=str,
        default=None,
        help="Target robot device ID for subscribing observations. Default: --device-id",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Action chunk size (default: {DEFAULT_CHUNK_SIZE})",
    )
    parser.add_argument(
        "--increment",
        type=float,
        default=DEFAULT_INCREMENT,
        help=(
            "Base micro increment applied to each chunk step. "
            f"Default: {DEFAULT_INCREMENT}"
        ),
    )
    parser.add_argument(
        "--delay-return",
        type=float,
        default=0.0,
        help="Delay in seconds before returning the action chunk (simulates network/inference latency). Default: 0",
    )

    return parser.parse_args(argv)


def build_session(args: argparse.Namespace):
    if args.bundle:
        logger.info("Connecting with platform credential bundle: %s", args.bundle)
        return R2CClient.connect(args.bundle, client_id=args.client_id)

    if args.client_config:
        logger.info("Connecting with client config: %s", args.client_config)
        client_config = ClientConfig.from_yaml(
            args.client_config,
            project_id=args.project_id or None,
            device_id=args.device_id or None,
            client_id=args.client_id or "dummy-cloud-adapter",
            endpoints=parse_endpoints(args.endpoints) or None,
            mode=args.mode,
        )
        return R2CClient.connect(client_config)

    if not args.project_id:
        raise ValueError("project_id is required when --bundle is not provided")
    if not args.device_id:
        raise ValueError("device_id is required when --bundle is not provided")

    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id or "dummy-cloud-adapter",
        endpoints=parse_endpoints(args.endpoints),
        mode=args.mode,
    )
    config.validate()
    return R2CClient.connect(config)


def build_incremental_action(
    observation: Observations,
    *,
    action_id: int,
    chunk_size: int,
    increment: float,
) -> Actions:
    joint_names = list(observation.joint_states.names)
    base_positions = list(observation.joint_states.position)

    if not joint_names and base_positions:
        joint_names = [f"joint_{idx}" for idx in range(len(base_positions))]

    steps = [
        [float(value) + (step_idx + 1) * increment for value in base_positions]
        for step_idx in range(chunk_size)
    ]

    zeros = [[0.0 for _ in base_positions] for _ in range(chunk_size)]

    # ── 扩展字段：将观测中的扩展字段回显，并添加 action 侧扩展数据 ──
    action_extensions = dict(observation.extensions)  # 回显观测扩展
    action_extensions["action_increment"] = ExtensionValue.from_scalar(increment)
    action_extensions["action_chunk_size"] = ExtensionValue.from_scalar(chunk_size)

    return Actions.from_dict(
        {
            "timestamp": int(time.time() * 1000),
            "id": observation.id,  # must eq observation.id
            "chunk_size": chunk_size,
            "joint_states": {
                "names": joint_names,
                "position": steps,
                "velocity": zeros,
                "torque": zeros,
            },
            "end_effector_poses": {},
            "end_effector_states": {
                "names": [],
                "position": [],
                "velocity": [],
                "torque": [],
            },
            "localization": {
                "odom_pose": [],
                "map_pose": [],
            },
            "extensions": action_extensions,
        }
    )


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    session = build_session(args)
    target_device_id = args.target_device_id or args.device_id
    next_action_id = 0

    logger.info(
        "Dummy cloud adapter started: target_device=%s chunk_size=%d "
        "increment=%.6f delay_return=%.1fs",
        target_device_id,
        int(args.chunk_size),
        float(args.increment),
        float(args.delay_return),
    )

    def on_observation(observation: Observations) -> None:
        nonlocal next_action_id

        logger.debug("recv observation: %s", observation)

        # ── 打印观测中的扩展字段 ──────────────────────────────────
        if observation.extensions:
            logger.info("── Observation extensions (%d fields) ──", len(observation.extensions))
            for key, ev in observation.extensions.items():
                if ev.dtype == "STRING":
                    logger.info("  extensions.%s = %r (STRING)", key, ev.to_string())
                elif ev.dtype in ("FLOAT32", "FLOAT64", "INT32", "INT64"):
                    logger.info("  extensions.%s = %s (%s)", key, ev.to_scalar(), ev.dtype)
                elif ev.dtype == "BYTES":
                    logger.info(
                        "  extensions.%s = <bytes len=%d mime=%r> (%s)",
                        key, len(ev.data), ev.mime_type, ev.dtype,
                    )
                else:
                    logger.info(
                        "  extensions.%s = <data len=%d> (%s shape=%s)",
                        key, len(ev.data), ev.dtype, ev.shape,
                    )
        else:
            logger.info("── No observation extensions ──")

        if not observation.joint_states.position:
            logger.warning(
                "Observation has no joint positions, skip publishing action."
            )
            return

        next_action_id += 1
        action = build_incremental_action(
            observation,
            action_id=next_action_id,
            chunk_size=int(args.chunk_size),
            increment=float(args.increment),
        )
        if args.delay_return > 0:
            time.sleep(args.delay_return)
        session.publish_actions(action)

        # ── 打印即将发布的 action 中的扩展字段 ───────────────────
        action_ext_info = ", ".join(
            f"{k}={ev.to_scalar()}" if ev.shape == [] and ev.dtype != "STRING"
            else f"{k}={ev.to_string()!r}" if ev.dtype == "STRING"
            else f"{k}=<{ev.dtype}>"
            for k, ev in action.extensions.items()
        )
        logger.info(
            "Published action id=%d chunk_size=%d joints=%d extensions={%s}",
            next_action_id,
            action.chunk_size,
            len(action.joint_states.names),
            action_ext_info,
        )

    session.subscribe_observations(on_observation, target_device_id=target_device_id)

    start = time.time()
    try:
        while True:
            if args.duration > 0 and time.time() - start >= args.duration:
                logger.info("Duration reached, stopping.")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Interrupted by keyboard, stopping.")
    finally:
        session.close()
        logger.info("Session closed.")


if __name__ == "__main__":
    main()