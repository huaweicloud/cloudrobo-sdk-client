"""
Jaka Simulation Cloud Adapter — 重放录制轨迹作为 action chunk。

与 ``jaka_cloud_adapter.py`` 不同,此 adapter 不连接真实推理模型,
而是从本地 .npz 轨迹文件加载预录的关节角 + 夹爪数据,按固定 chunk_size
(默认 100) 逐块返回。

每次收到一个 observation,就返回轨迹的下一个 chunk;轨迹数据全部发送
完成后,后续 observation 不再产生 action (打一条 INFO 告知已消费完毕)。

用法:
    python examples/jaka_sim_cloud_adapter.py \\
        --project-id test-tenant --device-id jaka-001

数据:
    /home/robot/suhanwu/project/java_mini2/ur5e_data/data/my_traj.npz
    /home/robot/suhanwu/project/java_mini2/ur5e_data/data/my_traj_meta.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from cloudrobo_r2c import ClientConfig, R2CClient
from cloudrobo_r2c.common.models import Actions

logger = logging.getLogger(__name__)

# ── 默认路径 ──────────────────────────────────────────────────────────
_DEFAULT_TRAJ_NPZ = (
    "/home/robot/suhanwu/project/java_mini2/ur5e_data/data/my_traj.npz"
)
_DEFAULT_TRAJ_META = (
    "/home/robot/suhanwu/project/java_mini2/ur5e_data/data/my_traj_meta.json"
)

ADAPTER_NAME = "JakaSimCloudAdapter"
DEFAULT_PROJECT_ID = "test-tenant"
DEFAULT_DEVICE_ID = "jaka-001"
DEFAULT_CLIENT_ID = "jaka-sim-cloud-adapter"
DEFAULT_CHUNK_SIZE = 100


# ── 轨迹加载 ──────────────────────────────────────────────────────────


def _load_trajectory(
    npz_path: str, meta_path: str
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Return ``(joints, gripper_percent, meta)``.

    - joints: (N, 6) float32 — 6 个关节角 (rad)
    - gripper_percent: (N,) float32 — 夹爪百分比 0–1
    - meta: dict — JSON 元信息
    """
    if not npz_path.endswith(".npz"):
        raise ValueError(f"仅支持 .npz 文件，实际路径: {npz_path}")
    data = np.load(npz_path, allow_pickle=False)
    joints: np.ndarray = data["joint_positions"]  # (N, 6)
    gripper_percent: np.ndarray = data["gripper_percent"]  # (N,)
    data.close()

    with open(meta_path, "r", encoding="utf-8") as fh:
        meta = json.load(fh)

    logger.info(
        "Loaded trajectory: %d frames, %.1f s @ %d Hz (robot=%s, ip=%s)",
        joints.shape[0],
        meta.get("duration_s", 0),
        meta.get("record_frequency", 0),
        meta.get("robot_type", "?"),
        meta.get("robot_ip", "?"),
    )
    return joints, gripper_percent, meta


# ── Chunk generator ────────────────────────────────────────────────────


def _build_chunks(
    joints: np.ndarray,
    gripper_percent: np.ndarray,
    chunk_size: int,
) -> List[np.ndarray]:
    """Cut the trajectory into chunks, each of shape ``(C, 7)``.

    Column layout: [j1, j2, j3, j4, j5, j6, gripper_percent].
    Gripper 百分比从 0–1 转为 0–100 (Jaka SDK set_percent 的约定)。
    The last chunk may be smaller than *chunk_size*.
    """
    # Build (N, 7) array: 6 joints + gripper percent (0–100)
    grip = (gripper_percent.astype(np.float64) * 100.0).reshape(-1, 1)
    joints_f64 = joints.astype(np.float64)
    full = np.column_stack([joints_f64, grip])
    n = full.shape[0]
    chunks: List[np.ndarray] = []
    start = 0
    while start < n:
        end = min(start + chunk_size, n)
        chunks.append(full[start:end].copy())
        start = end
    logger.info(
        "Split %d frames into %d chunk(s) (chunk_size=%d, last_chunk_size=%d)",
        n,
        len(chunks),
        chunk_size,
        chunks[-1].shape[0] if chunks else 0,
    )
    return chunks


# ── Action 构造 ────────────────────────────────────────────────────────


def _build_action_from_chunk(
    chunk: np.ndarray,
    now_ms: int,
    joint_names: Sequence[str],
) -> Actions:
    """Build an ``Actions`` object from a (C, 7) chunk.

    Positions = ``[[j1..j6, grip_w_m], ...]`` (C rows).
    """
    c = chunk.shape[0]
    # Each step: [j1..j6, gripper_width]
    positions: list = chunk.tolist()
    zeros: list = [[0.0] * 7 for _ in range(c)]

    payload: Dict[str, Any] = {
        "timestamp": now_ms,
        "chunk_size": c,
        "joint_states": {
            "names": list(joint_names) + ["gripper"],
            "position": positions,
            "velocity": zeros,
            "torque": zeros,
        },
        "end_effector_poses": {},
        "end_effector_states": {},
        "localization": {"odom_pose": [], "map_pose": []},
    }
    return Actions.from_dict(payload)


# ── CLI / Main ─────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Jaka simulation cloud adapter — replay recorded trajectory chunks"
    )
    # ── 连接方式: --client-config 优先, 否则用独立参数 ──
    conn = parser.add_argument_group("connection (use --client-config OR individual args)")
    conn.add_argument(
        "--client-config",
        type=str,
        default=None,
        help="Path to client config YAML (e.g. config/client_config.yaml). "
        "Overrides --project-id / --device-id / --client-id / --mode / --endpoints.",
    )
    conn.add_argument("--project-id", type=str, default=DEFAULT_PROJECT_ID)
    conn.add_argument("--device-id", type=str, default=DEFAULT_DEVICE_ID)
    conn.add_argument("--client-id", type=str, default=DEFAULT_CLIENT_ID)

    # ── 轨迹 ──
    traj = parser.add_argument_group("trajectory")
    traj.add_argument("--traj-npz", type=str, default=_DEFAULT_TRAJ_NPZ)
    traj.add_argument("--traj-meta", type=str, default=_DEFAULT_TRAJ_META)

    # ── 行为 ──
    ctrl = parser.add_argument_group("replay control")
    ctrl.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    ctrl.add_argument("--duration", type=float, default=0.0)
    ctrl.add_argument("--loop", action="store_true", default=False)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Check data files exist
    for p, name in [
        (args.traj_npz, ".npz"),
        (args.traj_meta, "meta JSON"),
    ]:
        if not os.path.isfile(p):
            raise FileNotFoundError(f"Trajectory {name} not found: {p}")

    joints, gripper_percent, meta = _load_trajectory(args.traj_npz, args.traj_meta)
    chunks = _build_chunks(joints, gripper_percent, args.chunk_size)

    chunk_index: int = 0
    exhausted: bool = False

    if args.client_config:
        from cloudrobo_r2c.common.config import ClientConfig as Cfg
        yaml_cfg = Cfg.from_yaml(args.client_config, None, None, None)
        project_id = yaml_cfg.project_id
        device_id = yaml_cfg.device_id
        client_id = "jaka-sim-cloud-adapter"  # 避免与边缘端的 client_id 冲突
        mode = yaml_cfg.mode
        endpoints = yaml_cfg.endpoints
    else:
        project_id = args.project_id
        device_id = args.device_id
        client_id = args.client_id
        endpoints = None
        mode = "peer"

    config = ClientConfig(
        project_id=project_id,
        device_id=device_id,
        client_id=client_id,
        endpoints=endpoints or (["tcp/127.0.0.1:7447"] if mode == "client" else None),
        mode=mode,
    )

    logger.info(
        "[%s] Connecting: project=%s, device=%s, client=%s",
        ADAPTER_NAME,
        project_id,
        device_id,
        client_id,
    )
    client = R2CClient.connect(config)

    def on_observation(observation: Any) -> None:
        nonlocal chunk_index, exhausted

        if exhausted:
            return  # trajectory fully consumed, no more actions

        if chunk_index >= len(chunks):
            exhausted = True
            logger.info(
                "[%s] Trajectory fully consumed (%d chunks, %d total frames). "
                "No further actions will be published.",
                ADAPTER_NAME,
                len(chunks),
                joints.shape[0],
            )
            if args.loop:
                chunk_index = 0
                exhausted = False
                logger.info("[%s] Looping: restarting from chunk 0.", ADAPTER_NAME)
            return

        try:
            joint_names = list(observation.joint_states.names)
        except Exception:
            joint_names = [
                "joint_1", "joint_2", "joint_3",
                "joint_4", "joint_5", "joint_6",
            ]

        chunk = chunks[chunk_index]
        action = _build_action_from_chunk(
            chunk,
            now_ms=int(time.time() * 1000),
            joint_names=joint_names,
        )
        client.publish_actions(action)
        logger.info(
            "[%s] Published chunk %d/%d (size=%d, joints=%d)",
            ADAPTER_NAME,
            chunk_index + 1,
            len(chunks),
            chunk.shape[0],
            len(joint_names),
        )
        chunk_index += 1

    client.subscribe_observations(on_observation, target_device_id=device_id)
    logger.info(
        "[%s] Subscribed: %s/%s/inference/observations",
        ADAPTER_NAME,
        project_id,
        device_id,
    )
    logger.info(
        "[%s] Waiting for observations (%d chunks ready, Ctrl+C to stop)...",
        ADAPTER_NAME,
        len(chunks),
    )

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


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    main()
