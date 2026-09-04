"""
Mock Publisher Example

Publishes multiple SDK message types on the robot side according to configuration:
- observation
- action
- joint_state
- end_effector_state
- localization_state
- imu_state
- heartbeat

Examples:
    python examples/mock_pub.py --bundle /path/to/cert_xxx.zip
    python examples/mock_pub.py --bundle /path/to/cert_xxx.zip --duration 30
    python examples/mock_pub.py --bundle /path/to/cert_xxx.zip --config examples/mock_pub_config.json

Advanced explicit mode:
    python examples/mock_pub.py --project-id test-tenant --device-id robot-01 --client-id mock-pub-01
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import cv2
import numpy as np
from google.protobuf.timestamp_pb2 import Timestamp

from cloudrobo_r2c import ClientConfig, R2CClient
from cloudrobo_r2c.common.models import (Actions, BatteryStatus,
                                   EndEffectorPoseChunk, EndEffectorState,
                                   EndEffectorStateAction,
                                   Heartbeat, IMUState, JointAction,
                                   JointInstruction, JointObservation,
                                   JointStateMessage, LocalizationAction,
                                   LocalizationState, Pose7D,
                                   PoseWithCovariance, Quaternion, Vector3)

logger = logging.getLogger(__name__)


DEFAULT_MESSAGE_CONFIG: Dict[str, Dict[str, Any]] = {
    "observation": {"enabled": True, "rate_hz": 1.0, "image_encode": "raw"},
    "action": {"enabled": False, "rate_hz": 0.5},
    "joint_state": {"enabled": True, "rate_hz": 5.0},
    "end_effector_state": {"enabled": True, "rate_hz": 2.0},
    "localization_state": {"enabled": True, "rate_hz": 2.0},
    "imu_state": {"enabled": True, "rate_hz": 20.0},
    "heartbeat": {"enabled": True, "rate_hz": 1.0},
}


@dataclass
class PublisherTask:
    name: str
    enabled: bool
    period_s: float
    next_run_ts: float
    publish_fn: Callable[[int], None]
	
	
def now_timestamp() -> Timestamp:
    """protobuf Timestamp"""
    ts = Timestamp()
    ts.GetCurrentTime()
    return ts

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mock publisher for all protobuf-backed SDK messages"
    )

    # Recommended: bundle mode
    parser.add_argument(
        "--bundle",
        type=str,
        default=None,
        help="Path to the platform-issued credential bundle zip or unpacked directory (recommended)",
    )

    parser.add_argument(
        "--client-config",
        default="config/client_config.yaml",
        help="Path to the original R2C SDK client config YAML",
    )
    
    # Advanced: explicit ClientConfig mode
    parser.add_argument("--project-id", type=str, default=None, help="Project ID")
    parser.add_argument("--device-id", type=str, default=None, help="Device ID")
    parser.add_argument("--client-id", type=str, default=None, help="Client ID")
    parser.add_argument(
        "--endpoints",
        type=str,
        default="",
        help="Comma-separated endpoints for explicit mode, for example: tls/127.0.0.1:7447",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="peer",
        choices=["peer", "client"],
        help="Connection mode for explicit mode",
    )

    # Mock behavior config
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Run duration in seconds; 0 means run forever",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to a JSON file that overrides the default message publishing behavior",
    )

    return parser.parse_args()


def parse_endpoints(raw: Optional[str]) -> List[str]:
    """Parse comma-separated endpoints."""
    if not raw:
        return []
    return [ep.strip() for ep in raw.split(",") if ep.strip()]


def build_client(args: argparse.Namespace):
    """Build a connected client session from bundle or explicit ClientConfig."""
    if args.bundle:
        logger.info("Connecting with platform credential bundle: %s", args.bundle)
        if args.client_id:
            logger.info("Using client_id override: %s", args.client_id)
        return R2CClient.connect(args.bundle, client_id=args.client_id)

    if args.client_config:
        logger.info("Connecting with client_config: %s", args.client_config)
        client_cfg = ClientConfig.from_yaml(args.client_config)
        return R2CClient.connect(client_cfg)

    if not args.project_id:
        raise ValueError("project_id is required when --bundle is not provided")
    if not args.device_id:
        raise ValueError("device_id is required when --bundle is not provided")

    resolved_client_id = args.client_id or "mock-pub-01"
    endpoints = parse_endpoints(args.endpoints)

    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=resolved_client_id,
        endpoints=endpoints,
        mode=args.mode,
    )
    config.validate()

    logger.info(
        "Connecting with explicit ClientConfig: project=%s, device=%s, client=%s, mode=%s, endpoints=%s",
        config.project_id,
        config.device_id,
        config.client_id,
        config.mode,
        list(config.endpoints),
    )
    return R2CClient.connect(config)


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge dictionaries and return a new object."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def build_image_ndarray(seq: int, width: int = 320, height: int = 240) -> np.ndarray:
    """Generate a synthetic BGR image as a numpy ndarray."""
    x_grad = np.tile(np.linspace(0, 255, width, dtype=np.uint8), (height, 1))
    y_grad = np.tile(
        np.linspace(0, 255, height, dtype=np.uint8).reshape(height, 1), (1, width)
    )
    b = ((x_grad.astype(np.uint16) + (seq * 7)) % 256).astype(np.uint8)
    g = ((y_grad.astype(np.uint16) + (seq * 13)) % 256).astype(np.uint8)
    r = (
        ((x_grad.astype(np.uint16) + y_grad.astype(np.uint16)) + (seq * 3)) % 256
    ).astype(np.uint8)
    return np.dstack([b, g, r])


def build_png_bytes_from_ndarray(
    seq: int, width: int = 320, height: int = 240
) -> bytes:
    """������ ndarray������ cv2 ����Ϊ PNG bytes��"""
    image = build_image_ndarray(seq=seq, width=width, height=height)
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("Failed to encode ndarray as PNG with cv2")
    return encoded.tobytes()

def build_observation_joint_state_data() -> Dict[str, Any]:
    """Build the joint_states field used inside observation payloads."""
    joint_names = ["j1", "j2", "j3", "j4", "j5", "j6"]
    return {
        "names": joint_names,
        "position": [round(random.uniform(-1, 1), 3) for _ in joint_names],
        "velocity": [round(random.uniform(-0.2, 0.2), 3) for _ in joint_names],
        "torque": [round(random.uniform(-2, 2), 3) for _ in joint_names],
    }


def build_observation_payload(seq: int, now_ms: int) -> Dict[str, Any]:
    """Build an observation payload as a dict."""
    images = {
        "front_cam": build_image_ndarray(seq=seq, width=320, height=240),
    }

    return {
        "timestamp": now_ms,
        "task": f"mock_task_{seq}",
        "id": seq,
        "images": images,
        "joint_states": build_observation_joint_state_data(),
        "end_effector_poses": {"names": [], "pose": []},
        "end_effector_states": {
            "names": ["gripper"],
            "position": [round(random.random(), 3)],
            "force": [round(random.uniform(0, 5), 3)],
        },
        "localization": {
            "odom_pose": [float(seq) * 0.05, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
            "map_pose": [],
        },
        "pointclouds": [],
    }


def build_action_joint_state_data() -> JointAction:
    """Build the joint_states field used inside action payloads."""
    joint_names = ["j1", "j2", "j3", "j4", "j5", "j6"]
    return JointAction(
        names=joint_names,
        position=[[round(random.uniform(-1.0, 1.0), 3) for _ in joint_names]],
        velocity=[[round(random.uniform(-0.3, 0.3), 3) for _ in joint_names]],
        torque=[[round(random.uniform(-1.0, 1.0), 3) for _ in joint_names]],
    )


def build_action_end_effector_pose_data() -> Dict[str, EndEffectorPoseChunk]:
    """Build the end_effector_poses field used inside action payloads."""
    return {
        "gripper": EndEffectorPoseChunk(
            pose=[Pose7D([0.1, 0.0, 0.2, 0.0, 0.0, 0.0, 1.0])]
        )
    }


def build_action_localization_data() -> LocalizationAction:
    """Build the localization field used inside action payloads."""
    return LocalizationAction(
        odom_pose=[Pose7D([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])],
        map_pose=[Pose7D([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])],
    )


def build_action_payload(seq: int, now_ms: int) -> Actions:
    """Build an action payload as an Actions wrapper."""
    return Actions(
        timestamp=now_ms,
        id=seq,
        chunk_size=1,
        joint_states=build_action_joint_state_data(),
        end_effector_poses=build_action_end_effector_pose_data(),
        end_effector_states=EndEffectorStateAction(
            names=["gripper"],
            position=[[0.2]],
            velocity=[[0.0]],
            torque=[[0.1]],
        ),
        localization=build_action_localization_data(),
    )


def build_joint_observation_data(names: List[str]) -> JointObservation:
    """Build the observation field used inside JointStateMessage."""
    return JointObservation(
        names=names,
        status=["ok"] * len(names),
        position=[round(random.uniform(-1.57, 1.57), 4) for _ in names],
        velocity=[round(random.uniform(-0.6, 0.6), 4) for _ in names],
        acceleration=[round(random.uniform(-2.0, 2.0), 4) for _ in names],
        temperature=[round(random.uniform(25.0, 45.0), 2) for _ in names],
        effort=[round(random.uniform(-3.0, 3.0), 4) for _ in names],
        motor_current=[round(random.uniform(0.1, 2.0), 4) for _ in names],
    )


def build_joint_instruction_data(joint_count: int) -> JointInstruction:
    """Build the instruction field used inside JointStateMessage."""
    return JointInstruction(
        target_position=[0.0] * joint_count,
        target_velocity=[0.0] * joint_count,
        target_effort=[0.0] * joint_count,
    )


def build_joint_state_payload(now_ms: int) -> JointStateMessage:
    names = ["j1", "j2", "j3", "j4", "j5", "j6"]
    return JointStateMessage(
        timestamp=now_timestamp(),
        observation=build_joint_observation_data(names),
        instruction=build_joint_instruction_data(len(names)),
    )


def build_end_effector_state_payload(now_ms: int) -> EndEffectorState:
    return EndEffectorState(
        timestamp=now_timestamp(),
        name="gripper",
        pose=Pose7D([0.1, 0.0, 0.25, 0.0, 0.0, 0.0, 1.0]),
        state=[round(random.uniform(0.0, 1.0), 4)],
        velocity=[round(random.uniform(-0.3, 0.3), 4)],
        acceleration=[round(random.uniform(-1.0, 1.0), 4)],
        force=[round(random.uniform(0.0, 30.0), 4)],
        tactile=[round(random.uniform(0.0, 1.0), 4) for _ in range(4)],
        category="parallel_gripper",
    )


def build_localization_state_payload(seq: int, now_ms: int) -> LocalizationState:
    x = seq * 0.01
    odom_pose = PoseWithCovariance(
        pose=Pose7D([x, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]),
        covariance=[0.0] * 36,
    )
    map_pose = PoseWithCovariance(
        pose=Pose7D([x, 0.1, 0.0, 0.0, 0.0, 0.0, 1.0]),
        covariance=[0.01] * 36,
    )
    return LocalizationState(
        timestamp=now_timestamp(),
        status="tracking",
        frame_id="map",
        odom_pose=odom_pose,
        map_pose=map_pose,
        confidence=round(min(0.99, 0.6 + seq * 0.001), 3),
    )


def build_imu_state_payload(now_ms: int) -> IMUState:
    return IMUState(
        timestamp=now_timestamp(),
        name="imu_link",
        orientation=Quaternion(0.0, 0.0, 0.0, 1.0),
        angular_velocity=Vector3(
            x=round(random.uniform(-0.2, 0.2), 4),
            y=round(random.uniform(-0.2, 0.2), 4),
            z=round(random.uniform(-0.2, 0.2), 4),
        ),
        linear_acceleration=Vector3(
            x=round(random.uniform(-0.5, 0.5), 4),
            y=round(random.uniform(-0.5, 0.5), 4),
            z=round(random.uniform(9.6, 9.9), 4),
        ),
        magnetic_field=Vector3(
            x=round(random.uniform(-0.05, 0.05), 4),
            y=round(random.uniform(-0.05, 0.05), 4),
            z=round(random.uniform(-0.05, 0.05), 4),
        ),
        orientation_covariance=[0.01] * 9,
        angular_velocity_covariance=[0.02] * 9,
        linear_acceleration_covariance=[0.03] * 9,
        magnetic_field_covariance=[0.05] * 9,
    )


def build_heartbeat_payload(now_ms: int) -> Heartbeat:
    return Heartbeat(
        timestamp=now_timestamp(),
        status="ok",
        mode="auto",
        error_code=[],
        battery=BatteryStatus(
            percentage=round(random.uniform(30, 95), 2),
            voltage=round(random.uniform(23, 25.2), 2),
            current=round(random.uniform(-5, 5), 2),
        ),
    )



def build_tasks(client, message_cfg: Dict[str, Dict[str, Any]]) -> Dict[str, PublisherTask]:
    now = time.time()

    def hz_to_period(hz: float) -> float:
        return 1.0 / max(hz, 1e-3)

    tasks: Dict[str, PublisherTask] = {}

    obs_image_encode = message_cfg["observation"].get("image_encode", "raw")
    tasks["observation"] = PublisherTask(
        name="observation",
        enabled=bool(message_cfg["observation"].get("enabled", True)),
        period_s=hz_to_period(float(message_cfg["observation"].get("rate_hz", 1.0))),
        next_run_ts=now,
        publish_fn=lambda seq: client.publish_observations(
            build_observation_payload(seq, int(time.time() * 1000)),
            image_encode=obs_image_encode,
        ),
    )

    tasks["action"] = PublisherTask(
        name="action",
        enabled=bool(message_cfg["action"].get("enabled", False)),
        period_s=hz_to_period(float(message_cfg["action"].get("rate_hz", 0.5))),
        next_run_ts=now,
        publish_fn=lambda seq: client.publish_actions(
            build_action_payload(seq, int(time.time() * 1000))
        ),
    )

    tasks["joint_state"] = PublisherTask(
        name="joint_state",
        enabled=bool(message_cfg["joint_state"].get("enabled", True)),
        period_s=hz_to_period(float(message_cfg["joint_state"].get("rate_hz", 5.0))),
        next_run_ts=now,
        publish_fn=lambda _: client.publish_joint_states(
            build_joint_state_payload(int(time.time() * 1000))
        ),
    )

    tasks["end_effector_state"] = PublisherTask(
        name="end_effector_state",
        enabled=bool(message_cfg["end_effector_state"].get("enabled", True)),
        period_s=hz_to_period(float(message_cfg["end_effector_state"].get("rate_hz", 2.0))),
        next_run_ts=now,
        publish_fn=lambda _: client.publish_end_effector_states(
            build_end_effector_state_payload(int(time.time() * 1000))
        ),
    )

    tasks["localization_state"] = PublisherTask(
        name="localization_state",
        enabled=bool(message_cfg["localization_state"].get("enabled", True)),
        period_s=hz_to_period(float(message_cfg["localization_state"].get("rate_hz", 2.0))),
        next_run_ts=now,
        publish_fn=lambda seq: client.publish_localization_states(
            build_localization_state_payload(seq, int(time.time() * 1000))
        ),
    )

    tasks["imu_state"] = PublisherTask(
        name="imu_state",
        enabled=bool(message_cfg["imu_state"].get("enabled", True)),
        period_s=hz_to_period(float(message_cfg["imu_state"].get("rate_hz", 20.0))),
        next_run_ts=now,
        publish_fn=lambda _: client.publish_imu_states(
            build_imu_state_payload(int(time.time() * 1000))
        ),
    )

    tasks["heartbeat"] = PublisherTask(
        name="heartbeat",
        enabled=bool(message_cfg["heartbeat"].get("enabled", True)),
        period_s=hz_to_period(float(message_cfg["heartbeat"].get("rate_hz", 1.0))),
        next_run_ts=now,
        publish_fn=lambda _: client.publish_heartbeats(
            build_heartbeat_payload(int(time.time() * 1000))
        ),
    )

    return tasks


def main() -> None:
    args = parse_args()

    message_cfg = DEFAULT_MESSAGE_CONFIG
    if args.config:
        with open(args.config, "r", encoding="utf-8") as f:
            override_cfg = json.load(f)
        message_cfg = deep_merge(DEFAULT_MESSAGE_CONFIG, override_cfg)

    client = build_client(args)
    tasks = build_tasks(client, message_cfg)

    conn_info = client.connection_info()
    logger.info("connection_info=%s", conn_info)

    logger.info("Active message config:")
    for name, cfg in message_cfg.items():
        logger.info(
            "  - %s: enabled=%s, rate_hz=%s",
            name,
            cfg.get("enabled"),
            cfg.get("rate_hz"),
        )

    start_ts = time.time()
    seq = 0

    try:
        while True:
            now = time.time()
            if args.duration > 0 and now - start_ts >= args.duration:
                logger.info("Duration reached, stopping.")
                break

            seq += 1
            nearest_next = now + 1.0

            for task in tasks.values():
                if not task.enabled:
                    continue

                if now >= task.next_run_ts:
                    task.publish_fn(seq)
                    task.next_run_ts = now + task.period_s
                    logger.info(
                        "published=%s seq=%d next_in=%.3fs",
                        task.name,
                        seq,
                        task.period_s,
                    )

                nearest_next = min(nearest_next, task.next_run_ts)

            sleep_s = max(0.001, nearest_next - time.time())
            time.sleep(min(sleep_s, 0.05))

    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    finally:
        client.close()
        logger.info("Closed.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    main()