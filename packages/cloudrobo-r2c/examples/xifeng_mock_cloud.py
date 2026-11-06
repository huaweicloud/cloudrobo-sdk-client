"""本地 mock 云端：listen on 7447，订阅 observations 并回 zero-action。

启动后再启动 bridge（mode=peer, endpoint_role=connect, endpoints=tcp/127.0.0.1:7447），
两者通过 zenoh peer 自动 P2P 互联。

用法：
    python examples/xifeng_mock_cloud.py \\
        --project r2c \\
        --device local
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any

from cloudrobo_r2c import ClientConfig, R2CClient
from cloudrobo_r2c.common.models import Actions

LEFT_JOINT_NAMES = [f"left_arm_{i}_exp" for i in range(1, 8)]
RIGHT_JOINT_NAMES = [f"right_arm_{i}_exp" for i in range(1, 8)]
ALL_JOINTS = LEFT_JOINT_NAMES + RIGHT_JOINT_NAMES
EE_NAMES = ["left_gripper_exp", "right_gripper_exp"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mock cloud for xifeng (cloudrobo_r2c.robots.xifeng) local integration test"
    )
    parser.add_argument("--project", default="r2c")
    parser.add_argument("--device", default="local")
    parser.add_argument("--client", default="xifeng-mock-cloud")
    parser.add_argument("--listen", default="tcp/0.0.0.0:7447")
    parser.add_argument("--chunk", type=int, default=5, help="action chunk size")
    parser.add_argument("--duration", type=float, default=0.0, help="0 = forever")
    return parser.parse_args()


def build_zero_action(now_ms: int, chunk_size: int) -> Actions:
    return Actions.from_dict(
        {
            "timestamp": now_ms,
            "chunk_size": chunk_size,
            "joint_states": {
                "names": ALL_JOINTS,
                "position": [[0.05 * (i + 1)] * len(ALL_JOINTS) for i in range(chunk_size)],
                "velocity": [[0.0] * len(ALL_JOINTS) for _ in range(chunk_size)],
                "torque": [[0.0] * len(ALL_JOINTS) for _ in range(chunk_size)],
            },
            "end_effector_poses": {},
            "end_effector_states": {
                "names": EE_NAMES,
                "position": [
                    [50.0 if i % 2 == 0 else 100.0] * len(EE_NAMES)
                    for i in range(chunk_size)
                ],
                "velocity": [[0.0] * len(EE_NAMES) for _ in range(chunk_size)],
                "torque": [[0.0] * len(EE_NAMES) for _ in range(chunk_size)],
            },
            "localization": {"odom_pose": [], "map_pose": []},
        }
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    log = logging.getLogger("mock_cloud")
    args = parse_args()

    cfg = ClientConfig(
        project_id=args.project,
        device_id=args.device,
        client_id=args.client,
        endpoint_role="listen",
        endpoints=[args.listen],
        mode="peer",
    )
    session = R2CClient.connect(cfg)
    log.info("mock cloud listening on %s", args.listen)
    log.info("connection_info=%s", session.connection_info())

    def on_obs(obs: Any) -> None:
        joints = list(getattr(obs.joint_states, "names", []) or [])
        # images.color.* 可能是 numpy ndarray（raw）、bytes（jpeg/png）或 None
        imgs_summary: dict[str, Any] = {}
        for name, img in (getattr(obs.images, "color", {}) or {}).items():
            if hasattr(img, "shape"):
                imgs_summary[name] = f"ndarray {tuple(img.shape)}"
            elif isinstance(img, (bytes, bytearray)):
                imgs_summary[name] = f"{len(img)}B"
            else:
                imgs_summary[name] = type(img).__name__
        log.info(
            "[obs] id=%s ts=%s joints=%d images=%s",
            obs.id,
            obs.timestamp,
            len(joints),
            imgs_summary,
        )
        action = build_zero_action(int(time.time() * 1000), args.chunk)
        session.publish_actions(action)
        log.info("[action] sent zero-action chunk_size=%d", args.chunk)

    session.subscribe_observations(on_obs, target_device_id=args.device)
    log.info("subscribed to %s/%s/inference/observations", args.project, args.device)

    start = time.time()
    try:
        while True:
            if args.duration > 0 and time.time() - start >= args.duration:
                break
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
