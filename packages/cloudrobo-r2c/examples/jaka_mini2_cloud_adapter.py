"""JAKA Mini2 cloud adapter example.

订阅 Observations，并生成 50 步 Actions：
1) 第 1 个 joint action 基于 observation.joint_states.position 加微小增量。
2) 后续 joint action 基于前一 action 继续加微小增量。
3) 夹爪值范围使用 0~100.0，模拟“闭合 -> 张开 -> 再闭合”的过程。

用法示例：
    python examples/jaka_mini2_cloud_adapter.py --client-config config/client_config.yaml
    python examples/jaka_mini2_cloud_adapter.py --bundle /path/to/bundle.zip --target-device-id robot-01
"""

from __future__ import annotations

import argparse
import logging
from threading import Lock
import time
from typing import Optional, Sequence

import numpy as np

from cloudrobo_r2c.client import R2CClient
from cloudrobo_r2c.common.config import ClientConfig
from cloudrobo_r2c.common.models import Actions, Observations

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 50
DEFAULT_INCREMENT = 0.001
GRIPPER_MIN = 0.0
GRIPPER_MAX = 100.0
GRIPPER_INCREMENT = 1.0
GRIPPER_HOLD_SECONDS = 5
ACTION_STEPS_PER_SECOND = 30
GRIPPER_HOLD_STEPS = GRIPPER_HOLD_SECONDS * ACTION_STEPS_PER_SECOND
DEFAULT_GRIPPER_NAME = "gripper"

_gripper_lock = Lock()
_gripper_value = GRIPPER_MIN
_gripper_direction = 1
_gripper_hold_steps_remaining = 0
_gripper_initialized = False


def parse_endpoints(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [endpoint.strip() for endpoint in raw.split(",") if endpoint.strip()]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "JAKA Mini2 cloud adapter: subscribe observations and publish 50-step actions"
        )
    )

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
        "--action-state-npy",
        type=str,
        default="episode_1_state.npy",
        help=(
            "Path to .npy action state file loaded by numpy. "
            "Action joint_states.position will be sourced from this file."
        ),
    )

    return parser.parse_args(argv)


def build_session(args: argparse.Namespace):
    if args.bundle:
        logger.info("Connecting with platform credential bundle: %s", args.bundle)
        return R2CClient.connect(args.bundle, client_id=args.client_id)

    if args.client_config:
        logger.info("Connecting with client config: %s", args.client_config)
        client_config = ClientConfig.from_yaml(args.client_config)
        return R2CClient.connect(client_config)

    if not args.project_id:
        raise ValueError("project_id is required when --bundle is not provided")
    if not args.device_id:
        raise ValueError("device_id is required when --bundle is not provided")

    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id or "jaka-mini2-cloud-adapter",
        endpoints=parse_endpoints(args.endpoints),
        mode=args.mode,
    )
    config.validate()
    return R2CClient.connect(config)


def build_gripper_trajectory(chunk_size: int) -> list[float]:
    if chunk_size <= 1:
        return [GRIPPER_MIN]

    trajectory: list[float] = []
    for step_idx in range(chunk_size):
        phase = step_idx / (chunk_size - 1)
        if phase <= 0.5:
            value = GRIPPER_MIN + (phase / 0.5) * (GRIPPER_MAX - GRIPPER_MIN)
        else:
            value = GRIPPER_MAX - ((phase - 0.5) / 0.5) * (GRIPPER_MAX - GRIPPER_MIN)
        trajectory.append(max(GRIPPER_MIN, min(GRIPPER_MAX, value)))
    return trajectory


def build_incremental_action(
    observation: Observations,
    *,
    chunk_joint_steps: list[list[float]],
    chunk_size: int,
    increment: float,
) -> Actions:
    del increment  # Not used when trajectory is sourced from --action-state-npy
    joint_names = list(observation.joint_states.names)
    base_positions = chunk_joint_steps[0] if chunk_joint_steps else []

    if not joint_names and base_positions:
        joint_names = [f"joint_{idx}" for idx in range(len(base_positions))]

    joint_steps = [list(step) for step in chunk_joint_steps]

    zeros = [[0.0 for _ in base_positions] for _ in range(chunk_size)]

    return Actions.from_dict(
        {
            "timestamp": int(time.time() * 1000),
            "id": observation.id,
            "chunk_size": chunk_size,
            "joint_states": {
                "names": joint_names,
                "position": joint_steps,
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
        }
    )


def load_joint_steps_from_npy(path: str) -> list[list[float]]:
    if not path.endswith(".npy"):
        raise ValueError(f"仅支持 .npy 文件，实际路径: {path}")
    data = np.load(path, allow_pickle=False)
    array = np.asarray(data, dtype=np.float64)
    if array.ndim == 1:
        array = array.reshape(1, -1)
    if array.ndim != 2:
        raise ValueError(
            f"Expected a 1D or 2D .npy array for joint positions, got ndim={array.ndim}"
        )
    if array.shape[1] == 0:
        raise ValueError("The .npy file has zero joint columns.")
    return array.tolist()


def _build_joint_steps_from_loaded_states(
    *, loaded_joint_steps: list[list[float]], chunk_size: int
) -> list[list[float]]:
    if not loaded_joint_steps:
        raise ValueError("Loaded .npy joint states are empty.")

    steps_count = len(loaded_joint_steps)
    if steps_count >= chunk_size:
        return [list(step) for step in loaded_joint_steps[:chunk_size]]

    padded = [list(step) for step in loaded_joint_steps]
    while len(padded) < chunk_size:
        padded.append(list(loaded_joint_steps[-1]))
    return padded


def pop_next_chunk_joint_steps(
    *, loaded_joint_steps: list[list[float]], offset: int, chunk_size: int
) -> tuple[list[list[float]], int]:
    if offset >= len(loaded_joint_steps):
        return [], offset

    next_offset = min(offset + chunk_size, len(loaded_joint_steps))
    chunk = loaded_joint_steps[offset:next_offset]
    return [list(step) for step in chunk], next_offset


def _build_gripper_steps(*, chunk_size: int, current_value: float) -> list[float]:
    global _gripper_value
    global _gripper_direction
    global _gripper_hold_steps_remaining
    global _gripper_initialized

    with _gripper_lock:
        if not _gripper_initialized:
            _gripper_value = max(GRIPPER_MIN, min(GRIPPER_MAX, float(current_value)))
            _gripper_direction = 1
            _gripper_hold_steps_remaining = 0
            _gripper_initialized = True

        trajectory: list[float] = []
        for _ in range(chunk_size):
            if _gripper_hold_steps_remaining > 0:
                trajectory.append(_gripper_value)
                _gripper_hold_steps_remaining -= 1
                if _gripper_hold_steps_remaining == 0:
                    _gripper_direction *= -1
                continue

            _gripper_value += _gripper_direction * GRIPPER_INCREMENT
            if _gripper_value >= GRIPPER_MAX:
                _gripper_value = GRIPPER_MAX
                _gripper_hold_steps_remaining = GRIPPER_HOLD_STEPS
            elif _gripper_value <= GRIPPER_MIN:
                _gripper_value = GRIPPER_MIN
                _gripper_hold_steps_remaining = GRIPPER_HOLD_STEPS

            trajectory.append(_gripper_value)

        return trajectory


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if int(args.chunk_size) <= 0:
        raise ValueError("--chunk-size must be > 0")

    loaded_joint_steps = load_joint_steps_from_npy(args.action_state_npy)
    logger.info(
        "Loaded action state file: %s (steps=%d, joints=%d)",
        args.action_state_npy,
        len(loaded_joint_steps),
        len(loaded_joint_steps[0]) if loaded_joint_steps else 0,
    )

    session = build_session(args)
    target_device_id = args.target_device_id or args.device_id

    logger.info(
        "JAKA Mini2 cloud adapter started: target_device=%s chunk_size=%d increment=%.6f",
        target_device_id,
        int(args.chunk_size),
        float(args.increment),
    )

    cursor_lock = Lock()
    joint_steps_cursor = 0

    def on_observation(observation: Observations) -> None:
        nonlocal joint_steps_cursor
        with cursor_lock:
            chunk_joint_steps, next_cursor = pop_next_chunk_joint_steps(
                loaded_joint_steps=loaded_joint_steps,
                offset=joint_steps_cursor,
                chunk_size=int(args.chunk_size),
            )

            if not chunk_joint_steps:
                logger.info(
                    "No action published for obs_id=%d because .npy action data is exhausted.",
                    observation.id,
                )
                return

            joint_steps_cursor = next_cursor

        action = build_incremental_action(
            observation,
            chunk_joint_steps=chunk_joint_steps,
            chunk_size=len(chunk_joint_steps),
            increment=float(args.increment),
        )
        session.publish_actions(action)

        logger.info(
            "Published action obs_id=%d chunk_size=%d joints=%d consumed=%d/%d",
            observation.id,
            action.chunk_size,
            len(action.joint_states.names),
            joint_steps_cursor,
            len(loaded_joint_steps),
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
