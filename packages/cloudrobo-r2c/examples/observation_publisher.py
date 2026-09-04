"""
Observation Publisher Example

Simulates a robot publishing observation data (Observations) to the cloud.

Recommended usage:
    python examples/observation_publisher.py --bundle /path/to/cert_xxx.zip
    python examples/observation_publisher.py --bundle /path/to/unpacked_bundle_dir
    python examples/observation_publisher.py --bundle /path/to/cert_xxx.zip --image /path/to/image.jpg
    python examples/observation_publisher.py --bundle /path/to/cert_xxx.zip --cameras front_cam,left_cam,right_cam

Compatible advanced usage:
    python examples/observation_publisher.py \
        --project-id test-tenant \
        --device-id robot-01 \
        --client-id robot-pub-01 \
        --mode peer

Topic:
    {project_id}/{device_id}/inference/observations
"""

from __future__ import annotations

import argparse
import logging
import random
import time
from typing import List, Optional

import cv2
import numpy as np

from cloudrobo_r2c import ClientConfig, R2CClient

logger = logging.getLogger(__name__)


def load_image(image_path: str) -> np.ndarray:
    """Load local image and return numpy array in BGR format."""
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Failed to load image: {image_path}")
    return img


def parse_camera_names(raw: str) -> List[str]:
    """Parse comma-separated camera names."""
    names = [name.strip() for name in raw.split(",") if name.strip()]
    return names or ["front_cam"]


def parse_endpoints(raw: Optional[str]) -> List[str]:
    """Parse comma-separated endpoints."""
    if not raw:
        return []
    return [ep.strip() for ep in raw.split(",") if ep.strip()]


def build_client(args: argparse.Namespace):
    """Build client session from bundle or explicit ClientConfig."""
    if args.bundle:
        logger.info("[Robot] Connecting with platform credential bundle: %s", args.bundle)
        if args.client_id:
            logger.info("[Robot] client_id override: %s", args.client_id)
        return R2CClient.connect(args.bundle, client_id=args.client_id)

    if not args.project_id:
        raise ValueError("project_id is required when --bundle is not provided")
    if not args.device_id:
        raise ValueError("device_id is required when --bundle is not provided")

    resolved_client_id = args.client_id or "robot-pub-01"
    endpoints = parse_endpoints(args.endpoints)

    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=resolved_client_id,
        endpoints=endpoints,
        mode=args.mode,
    )

    logger.info(
        "[Robot] Connecting with explicit ClientConfig: client_id=%s, project_id=%s, device_id=%s, mode=%s, endpoints=%s",
        config.client_id,
        config.project_id,
        config.device_id,
        config.mode,
        list(config.endpoints),
    )
    return R2CClient.connect(config)


def build_observation_data(
    sequence: int,
    camera_names: List[str],
    image: np.ndarray,
) -> dict:
    """Build one observation payload as dict."""
    joint_pos = [round(random.random(), 3) for _ in range(6)]
    images_dict = {cam_name: image for cam_name in camera_names}

    return {
        "timestamp": int(time.time() * 1000),
        "task": f"routine_check_seq_{sequence}",
        "id": sequence,
        "images": images_dict,
        "joint_states": {
            "names": ["j1", "j2", "j3", "j4", "j5", "j6"],
            "position": joint_pos,
            "velocity": [],
        },
        "end_effector_poses": {"names": [], "pose": []},
        "end_effector_states": {"names": [], "position": []},
        "localization": {
            "odom_pose": [float(sequence), 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
            "map_pose": [],
        },
        "pointclouds": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Observation Publisher Example")

    # Recommended formal connection mode
    parser.add_argument(
        "--bundle",
        type=str,
        default=None,
        help="Path to platform credential bundle zip or unpacked bundle directory (recommended)",
    )

    # Compatible explicit config mode
    parser.add_argument(
        "--project-id",
        type=str,
        default=None,
        help="Project ID for explicit ClientConfig mode",
    )
    parser.add_argument(
        "--device-id",
        type=str,
        default=None,
        help="Device ID for explicit ClientConfig mode",
    )
    parser.add_argument(
        "--client-id",
        type=str,
        default=None,
        help="Optional client_id override; in bundle mode SDK can auto-generate if omitted",
    )
    parser.add_argument(
        "--endpoints",
        type=str,
        default="",
        help="Comma-separated endpoints for explicit ClientConfig mode, e.g. tls/127.0.0.1:7447",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="peer",
        choices=["peer", "client"],
        help="Connection mode for explicit ClientConfig mode",
    )

    # Observation publishing options
    parser.add_argument(
        "--image",
        "-i",
        type=str,
        default=None,
        help="Local image path; uses dummy image if not specified",
    )
    parser.add_argument(
        "--cameras",
        "-c",
        type=str,
        default="front_cam",
        help="Comma-separated camera names (default: front_cam)",
    )
    parser.add_argument(
        "--image-encode",
        type=str,
        default="h264",
        choices=["raw", "h264"],
        help="Image encoding mode for publication (default: h264)",
    )
    parser.add_argument(
        "--hz",
        type=float,
        default=1.0,
        help="Publishing frequency in Hz (default: 1.0)",
    )

    args = parser.parse_args()

    if args.hz <= 0:
        raise ValueError("--hz must be > 0")

    camera_names = parse_camera_names(args.cameras)
    logger.info("[Robot] Cameras: %s", camera_names)

    client = build_client(args)

    try:
        conn_info = client.connection_info()
        logger.info("[Robot] Connection info: %s", conn_info)

        if args.image:
            logger.info("[Robot] Loading image from: %s", args.image)
            img = load_image(args.image)
            logger.info("[Robot] Image loaded: %s", img.shape)
        else:
            logger.info("[Robot] Using dummy image (480x640 black)")
            img = np.zeros((480, 640, 3), dtype=np.uint8)

        interval_s = 1.0 / args.hz
        logger.info(
            "[Robot] Start publishing observations at %.3f Hz with %d camera(s), image_encode=%s",
            args.hz,
            len(camera_names),
            args.image_encode,
        )

        sequence = 0
        while True:
            start_time = time.time()
            sequence += 1

            obs_data = build_observation_data(
                sequence=sequence,
                camera_names=camera_names,
                image=img,
            )

            client.publish_observations(obs_data, image_encode=args.image_encode)
            logger.info(
                "[Robot] Sent obs #%s | ts=%s | cameras=%s | encode=%s",
                sequence,
                obs_data["timestamp"],
                len(camera_names),
                args.image_encode,
            )

            elapsed = time.time() - start_time
            sleep_time = max(0.0, interval_s - elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info("[Robot] Stopping...")
    finally:
        client.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    main()