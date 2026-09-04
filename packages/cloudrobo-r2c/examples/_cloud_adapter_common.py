"""Shared helpers for cloud adapter examples."""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional, Sequence

try:
    import numpy as np
except Exception:
    np = None

from cloudrobo_r2c import ClientConfig, R2CClient
from cloudrobo_r2c.common.models import Actions

logger = logging.getLogger(__name__)


def normalize_for_logging(data: Any) -> Any:
    """Normalize nested data for readable logging output."""
    if is_dataclass(data) and not isinstance(data, type):
        return normalize_for_logging(asdict(data))

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


def build_zero_action(
    now_ms: int,
    joint_names: Sequence[str],
    default_joint_names: Sequence[str],
    chunk_size: int = 1,
    end_effector_names : Sequence[str] = None,
) -> Actions:
    """
    Build a zero-action payload.

    If joint_names is empty, default_joint_names will be used.
    """
    names = list(joint_names) if joint_names else list(default_joint_names)
    end_effector_names = list(end_effector_names) if end_effector_names else  []
    zeros = [0.0 for _ in names]

    payload: Dict[str, Any] = {
        "timestamp": now_ms,
        "chunk_size": chunk_size,
        "joint_states": {
            "names": names,
            # 模拟一个随chunk size变化的position
            "position": [[0.1 * (i+1)] * len(names) for i in range(chunk_size)],
            "velocity": [zeros for _ in range(chunk_size)],
            "torque": [zeros for _ in range(chunk_size)],
        },
        "end_effector_poses": {},
        "end_effector_states": {
            "names": end_effector_names,
            # 让夹爪交替开合，便于判断状态确实传入
            "position": [[50.0 if i % 2 == 0 else 100.0] * len(end_effector_names) for i in range(chunk_size)] if end_effector_names else[],
            "velocity": [[0.]*len(end_effector_names)] * chunk_size if end_effector_names else [],
            "torque": [[0.]*len(end_effector_names)] * chunk_size if end_effector_names else [],
        },
        "localization": {
            "odom_pose": [],
            "map_pose": [],
        },
    }
    return Actions.from_dict(payload)


def build_parser(
    description: str,
    default_project_id: str,
    default_device_id: str,
    default_client_id: str,
    help_project_id: str,
    help_device_id: str,
    help_client_id: str,
    help_duration: str,
) -> argparse.ArgumentParser:
    """Build a standard CLI parser for cloud adapter examples."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--project-id", type=str, default=default_project_id, help=help_project_id)
    parser.add_argument("--device-id", type=str, default=default_device_id, help=help_device_id)
    parser.add_argument("--client-id", type=str, default=default_client_id, help=help_client_id)
    parser.add_argument("--duration", type=float, default=0.0, help=help_duration)
    return parser


def run_cloud_adapter(
    args: argparse.Namespace,
    adapter_name: str,
    action_default_joint_names: Sequence[str],
    observation_fallback_joint_names: Optional[Sequence[str]] = None,
    waiting_message_suffix: str = "observations",
    chunk_size: int = 1,
    end_effector_names: Sequence[str] = None,
) -> None:
    """
    Run a cloud adapter example.

    Parameters
    ----------
    args:
        Parsed CLI arguments.
    adapter_name:
        Adapter name used in log messages.
    action_default_joint_names:
        Default joint names used when building a zero-action and the received
        observation does not provide joint names.
    observation_fallback_joint_names:
        Joint names used only when extracting observation.joint_states.names
        raises an exception. If not provided, an empty list is used.
        This parameter exists to preserve current behavior in each example.
    waiting_message_suffix:
        Suffix used in the waiting log line, for example:
        "observations" or "SO101 observations".
    chunk_size:
        chunk size for action payload
    end_effector_names:
        End effector joint names
    """
    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id,
        endpoint_role="listen",
        endpoints=["tcp/0.0.0.0:7447"],
        mode="peer",
    )

    logger.info(
        "[%s] Connecting: project=%s, device=%s, client=%s",
        adapter_name,
        args.project_id,
        args.device_id,
        args.client_id,
    )
    client = R2CClient.connect(config)

    fallback_names = list(observation_fallback_joint_names or [])

    def on_observation(observation: Any) -> None:
        normalized = normalize_for_logging(observation)
        logger.info(
            "\n=== Received observation ===\n%s\n============================",
            json.dumps(normalized, ensure_ascii=False, indent=2),
        )

        try:
            joint_names = list(observation.joint_states.names)
        except Exception:
            joint_names = list(fallback_names)

        action = build_zero_action(
            now_ms=int(time.time() * 1000),
            joint_names=joint_names,
            default_joint_names=action_default_joint_names,
            chunk_size=chunk_size,
            end_effector_names=end_effector_names,
        )
        client.publish_actions(action)
        logger.info("[%s] Sent zero-action: joints=%d", adapter_name, len(action.joint_states.names))

    client.subscribe_observations(on_observation, target_device_id=args.device_id)
    logger.info(
        "[%s] Subscribed: %s/%s/inference/observations",
        adapter_name,
        args.project_id,
        args.device_id,
    )
    logger.info("[%s] Waiting for %s (Ctrl+C to stop)...", adapter_name, waiting_message_suffix)

    start = time.time()
    try:
        while True:
            if args.duration > 0 and time.time() - start >= args.duration:
                logger.info("[%s] Duration reached, stopping.", adapter_name)
                break
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("[%s] Interrupted by user.", adapter_name)
    finally:
        client.close()
        logger.info("[%s] Closed.", adapter_name)
