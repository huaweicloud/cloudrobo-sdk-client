"""Q25 Cloud Adapter (Mock VLA Inference Service).

Subscribes to Q25 robot observations through the R2C SDK and sends wheeled
robot actions (trajectory commands) back.

Behavior
--------
- Subscribe-response mode: each observation triggers exactly one action.
- ``reset_status`` is always fixed to 0.
- 32-dim trajectory data from 3 predefined safe actions (round-robin).
- ``call_method`` in the action echoes the received observation value.

Usage::

    python examples/q25_cloud_adapter.py
    python examples/q25_cloud_adapter.py --project-id test-tenant --device-id q25-robot-01
    python examples/q25_cloud_adapter.py --duration 30
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any, Dict, List, Sequence

import numpy as np
from PIL import Image

from cloudrobo_r2c import ClientConfig, R2CClient
from cloudrobo_r2c.common.models import Actions

logger = logging.getLogger(__name__)

ADAPTER_NAME = "Q25CloudAdapter"
ENABLE_VISUALIZATION = False
DEFAULT_PROJECT_ID = "test-tenant"
DEFAULT_DEVICE_ID = "q25-robot-01"
DEFAULT_CLIENT_ID = "q25-cloud-adapter"
DEFAULT_ENDPOINTS = ["tcp/0.0.0.0:7447"]
DEFAULT_ENDPOINT_ROLE = "listen"

# ---------------------------------------------------------------------------
# Predefined safe actions (8 waypoints x 4 dims: x, y, yaw_sin, yaw_cos)
# ---------------------------------------------------------------------------

_ACTION_FORWARD: List[float] = [
    0.5, 0.0, 0.0, 1.0,
    0.5, 0.0, 0.0, 1.0,
    0.5, 0.0, 0.0, 1.0,
    0.5, 0.0, 0.0, 1.0,
    0.5, 0.0, 0.0, 1.0,
    0.5, 0.0, 0.0, 1.0,
    0.5, 0.0, 0.0, 1.0,
    0.5, 0.0, 0.0, 1.0,
]

_ACTION_TURN_LEFT: List[float] = [
    0.3, 0.0, 0.0, 1.0,
    0.3, 0.05, 0.05, 0.999,
    0.3, 0.1, 0.1, 0.995,
    0.3, 0.15, 0.149, 0.989,
    0.2, 0.2, 0.198, 0.98,
    0.2, 0.25, 0.247, 0.969,
    0.1, 0.25, 0.247, 0.969,
    0.0, 0.2, 0.198, 0.98,
]

_ACTION_TURN_RIGHT: List[float] = [
    0.3, 0.0, 0.0, 1.0,
    0.3, -0.05, -0.05, 0.999,
    0.3, -0.1, -0.1, 0.995,
    0.3, -0.15, -0.149, 0.989,
    0.2, -0.2, -0.198, 0.98,
    0.2, -0.25, -0.247, 0.969,
    0.1, -0.25, -0.247, 0.969,
    0.0, -0.2, -0.198, 0.98,
]

PREDEFINED_ACTIONS: List[Dict[str, Any]] = [
    {"name": "forward",    "position": _ACTION_FORWARD},
    {"name": "turn_left",  "position": _ACTION_TURN_LEFT},
    {"name": "turn_right", "position": _ACTION_TURN_RIGHT},
]


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Q25 cloud adapter: subscribe to observations and publish trajectory actions",
    )
    parser.add_argument("--project-id", type=str, default=DEFAULT_PROJECT_ID)
    parser.add_argument("--device-id", type=str, default=DEFAULT_DEVICE_ID)
    parser.add_argument("--client-id", type=str, default=DEFAULT_CLIENT_ID)
    parser.add_argument("--endpoints", type=str, nargs="+", default=DEFAULT_ENDPOINTS)
    parser.add_argument("--endpoint-role", type=str, default=DEFAULT_ENDPOINT_ROLE, choices=["listen", "auto"])
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--visualize", "-v", action="store_true", help="Display camera images in real-time")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main adapter logic
# ---------------------------------------------------------------------------


def main() -> None:
    args = parse_args()

    global ENABLE_VISUALIZATION
    ENABLE_VISUALIZATION = args.visualize

    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id,
        endpoint_role=args.endpoint_role,
        endpoints=list(args.endpoints) if args.endpoints else DEFAULT_ENDPOINTS,
        mode="peer",
    )

    logger.info(
        "[%s] Connecting: project=%s, device=%s, client=%s, endpoints=%s",
        ADAPTER_NAME, args.project_id, args.device_id, args.client_id, args.endpoints,
    )

    client = R2CClient.connect(config)

    action_index = 0
    joint_names_32 = _build_joint_names_32()

    def on_observation(observation: Any) -> None:
        nonlocal action_index

        # --- Log raw received observation data (images show byte count) ---
        _log_observation_raw(observation)

        # --- Visualize ---
        if ENABLE_VISUALIZATION:
            _visualize_observation(observation)

        # --- Parse end_effector_states ---
        call_method, call_id, task_id = 0.0, 0.0, 10.0

        try:
            ee_states = observation.end_effector_states
            ee_position = list(ee_states.position) if ee_states.position else []
            if ee_position:
                if isinstance(ee_position[0], (list, tuple)):
                    ee_position = list(ee_position[0])
                call_method = float(ee_position[0]) if len(ee_position) > 0 else 0.0
                call_id = float(ee_position[1]) if len(ee_position) > 1 else 0.0
                task_id = float(ee_position[2]) if len(ee_position) > 2 else 10.0
        except Exception as exc:
            logger.warning("Failed to parse end_effector_states: %s", exc)

        logger.info(
            "[%s] Received obs: call_method=%.4f (%s), call_id=%.6f",
            ADAPTER_NAME, call_method,
            "reset" if call_method < 0.5 else "infer",
            call_id,
        )

        # --- Build action ---
        if call_method < 0.5:
            action_index = 0
            position: List[float] = [0.0] * 32
            logger.info("  => RESET: zero trajectory, action_index reset to 0")
        else:
            action_data = PREDEFINED_ACTIONS[action_index % len(PREDEFINED_ACTIONS)]
            action_index += 1
            position = action_data["position"]
            logger.info("  => INFER: action=%s (index=%d)", action_data["name"], action_index - 1)

        action = _build_action(
            joint_names=joint_names_32,
            position=position,
            call_method=call_method,  # echo the received value
            call_id=call_id,
            task_id=task_id,
            reset_status=0.0,  # always 0
        )

        client.publish_actions(action)
        logger.info(
            "[%s] Published action: chunk_size=%d, joints=%d, reset_status=0",
            ADAPTER_NAME, action.chunk_size, len(action.joint_states.names),
        )

    client.subscribe_observations(on_observation, target_device_id=args.device_id)
    logger.info("[%s] Subscribed: %s/%s/inference/observations", ADAPTER_NAME, args.project_id, args.device_id)
    logger.info("[%s] Waiting for Q25 observations (Ctrl+C to stop)...", ADAPTER_NAME)

    start = time.time()
    try:
        while True:
            if args.duration > 0 and time.time() - start >= args.duration:
                logger.info("[%s] Duration reached, stopping.", ADAPTER_NAME)
                break
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("[%s] Interrupted by user.", ADAPTER_NAME)
    finally:
        client.close()
        logger.info("[%s] Closed.", ADAPTER_NAME)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_joint_names_32() -> List[str]:
    names = []
    for i in range(8):
        names.extend([f"x{i}", f"y{i}", f"yaw_sin{i}", f"yaw_cos{i}"])
    return names


def _log_observation_raw(observation: Any) -> None:
    """Log raw observation data; images show byte count only."""
    from dataclasses import asdict

    try:
        raw = asdict(observation)
    except Exception:
        raw = observation.__dict__ if hasattr(observation, "__dict__") else {}

    def _summarize(v: Any, _depth: int = 0) -> Any:
        if _depth > 5:
            return "..."
        if isinstance(v, bytes):
            return f"<{len(v)} bytes>"
        if isinstance(v, dict):
            return {k: _summarize(v, _depth + 1) for k, v in v.items()}
        if isinstance(v, (list, tuple)):
            if not v:
                return v
            if isinstance(v[0], (list, tuple)):
                return [_summarize(x, _depth + 1) for x in v]
            if len(v) > 10:
                return f"<{len(v)} floats>"
            return v
        return v

    summarized = _summarize(raw)
    logger.info("[OBS RAW] %s", summarized)


def _build_action(
    joint_names: Sequence[str],
    position: Sequence[float],
    call_method: float,
    call_id: float,
    task_id: float,
    reset_status: float,
) -> Actions:
    payload: Dict[str, Any] = {
        "timestamp": int(time.time() * 1000),
        "chunk_size": 1,
        "joint_states": {
            "names": list(joint_names),
            "position": [list(position)],
        },
        "end_effector_states": {
            "names": ["call_method", "reset_status"],
            "position": [[call_method, 0.0]],
        },
        "end_effector_poses": {},
        "localization": {"odom_pose": [], "map_pose": []},
    }
    return Actions.from_dict(payload)


def _visualize_observation(observation: Any) -> None:
    """Display camera images from the observation."""
    try:
        import matplotlib.pyplot as plt
        plt.ion()
        import io
        for cam_name, data in observation.images.color.items():
            if isinstance(data, bytes):
                try:
                    with Image.open(io.BytesIO(data)) as pil_img:
                        pil_img.load()
                        plt.close(cam_name)
                        fig = plt.figure(num=cam_name)
                        plt.imshow(pil_img)
                        plt.axis("off")
                        plt.title(f"Q25: {cam_name}")
                        fig.canvas.draw_idle()
                        plt.pause(0.001)
                except Exception as e:
                    logger.warning("[VIZ] Cannot decode %s: %s", cam_name, e)
            elif isinstance(data, np.ndarray):
                plt.close(cam_name)
                fig = plt.figure(num=cam_name)
                plt.imshow(data)
                plt.axis("off")
                plt.title(f"Q25: {cam_name}")
                fig.canvas.draw_idle()
                plt.pause(0.001)
    except Exception as exc:
        logger.warning("Failed to display visualization: %s", exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    main()
