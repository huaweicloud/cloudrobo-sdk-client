#!/usr/bin/env python3
"""隙锋人形机器人 cloud adapter 示例（交付物 #2）。

充当**云端**：通过 R2C SDK 订阅机器人侧 Observations，每收到一帧就用
**round-robin** 方式回发一条**预制安全位姿** Action。用于在没有真实 AI 模型时
验证「云 → 桥 → 机器人」下行链路与关节名/数据格式。

遵循 `skills/r2c/references/r2c-custom-robot.md` §8 强制要求：
- 必须 ≥3 个安全位置预制动作（少于 3 报错）
- 每次收到观测时 round-robin 发送这些预制动作
- 禁止使用定时器循环（动作由观测到达驱动，不是固定频率）

安全位姿来自隙锋提供的『打包、运动以及零位机械臂角度.xlsx』（外部输入 C，2026-06-15，
   单位度，单臂 7 关节 J1..J7）。见下方 ``_SAFE_POSES_DEG_SINGLE_ARM``。
   ⚠️ **在真机上执行前务必先 dry_run 比对**。工程约定（已确认）：双臂左右相同角度；安全位姿不控制夹爪。

单位：R2C 协议关节用**弧度**；隙锋 armMoveJoints 用**度**，换算由桥
（`cloudrobo_r2c.robots.xifeng.robot_real`）负责。产品侧给度时，这里填 `math.radians(度)`。

用法（配合场景 B 本地联调，先启动本脚本当云端，再启动桥连上来）：
    # 终端 A：本脚本（云端，listen 7447）
    python examples/xifeng_cloud_adapter.py --project-id r2c --device-id local
    # 终端 B：桥（robot 侧，connect 7447）
    python -m cloudrobo_r2c.cloudroboclient \
        --client-config config/client_xifeng_local.yaml \
        --robot-config config/robot_xifeng_config.yaml
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import time
from typing import Any, List, Sequence

from _cloud_adapter_common import build_parser, normalize_for_logging

logger = logging.getLogger(__name__)

ADAPTER_NAME = "XifengCloudAdapter"
DEFAULT_PROJECT_ID = "r2c"
DEFAULT_DEVICE_ID = "local"
DEFAULT_CLIENT_ID = "xifeng-cloud-adapter"

# 隙锋双臂关节名（左臂 7 + 右臂 7 = 14），必须与 robot_xifeng_config.yaml
# device_to_r2c.joint_states.names 完全一致。
XIFENG_ARM_JOINT_NAMES: List[str] = [f"left_arm_{i}" for i in range(1, 8)] + [
    f"right_arm_{i}" for i in range(1, 8)
]
# 末端二指夹爪（0=闭，100=开）。
XIFENG_GRIPPER_NAMES: List[str] = ["left_gripper", "right_gripper"]

N_ARM = len(XIFENG_ARM_JOINT_NAMES)      # 14
N_GRIPPER = len(XIFENG_GRIPPER_NAMES)    # 2

# =============================================================================
# 预制安全位姿（≥3 组，round-robin 轮发）—— 数据来源：外部输入 C
# =============================================================================
# 来源：huawei_cloudrobo/打包、运动以及零位机械臂角度.xlsx（2026-06-15）
#   单位：度（°），单臂 7 关节 J1..J7（根→末端，对应 left/right_arm_1..7）。
#   姿态一=打包/stow、姿态二=零位/zero、姿态三=运动/ready；均在手册关节限位内。
#
# 两处工程约定（xlsx 未给，已与用户确认）：
#   1) 双臂：xlsx 为单臂，**左右臂用相同角度**（14 = 左7 + 右7）。
#      若日后右臂需镜像（部分关节反号），在 _build_predefined_safe_poses() 内调整。
#   2) 夹爪：**安全位姿不控制夹爪**——只动手臂，end_effector_states 留空。
#
# 单位换算：R2C 协议关节用弧度，这里 math.radians(度)；桥 robot_real 再 rad→deg 调 pypilot。
_SAFE_POSES_DEG_SINGLE_ARM: List[dict] = [
    {"name": "pose#1 打包/stow",  "arm_deg": [20.0, -20.0, 0.0, -100.0, 0.0, 0.0, 0.0]},
    {"name": "pose#2 零位/zero",  "arm_deg": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]},
    {"name": "pose#3 运动/ready", "arm_deg": [0.0, 25.0, 20.0, 90.0, 0.0, 0.0, 85.0]},
]


def _build_predefined_safe_poses() -> List[dict]:
    """把单臂度数表展开为 R2C 双臂弧度预制动作（左右臂相同角度；安全位姿不控制夹爪）。"""
    poses: List[dict] = []
    for p in _SAFE_POSES_DEG_SINGLE_ARM:
        arm_single_rad = [math.radians(float(d)) for d in p["arm_deg"]]
        # 14 关节 = 左臂 7 + 右臂 7（左右相同）
        arm_dual_rad = arm_single_rad + arm_single_rad
        poses.append(
            {
                "name": p["name"],
                "joint_states": {"position": arm_dual_rad},
                # 安全位姿只动手臂、不控制夹爪 → end_effector_states 留空
                "end_effector_states": {"position": []},
            }
        )
    return poses


PREDEFINED_SAFE_POSES: List[dict] = _build_predefined_safe_poses()


def validate_predefined_actions(predefined_actions: Sequence[dict]) -> None:
    """规范 §8：少于 3 个安全位置应报错；并校验每个 pose 维度正确。"""
    if len(predefined_actions) < 3:
        raise ValueError(
            f"至少需要 3 个预制安全位姿（r2c-custom-robot.md §8），当前只有 "
            f"{len(predefined_actions)} 个"
        )
    for i, pose in enumerate(predefined_actions):
        arm = pose["joint_states"]["position"]
        if len(arm) != N_ARM:
            raise ValueError(
                f"pose#{i + 1} 臂关节数应为 {N_ARM}，实际 {len(arm)}"
            )
        # 夹爪可选：0=安全位姿不控制夹爪；否则须为 N_GRIPPER 个
        grip = pose.get("end_effector_states", {}).get("position", [])
        if len(grip) not in (0, N_GRIPPER):
            raise ValueError(
                f"pose#{i + 1} 夹爪数应为 0(不控制) 或 {N_GRIPPER}，实际 {len(grip)}"
            )


def build_predefined_action(
    now_ms: int,
    arm_joint_names: Sequence[str],
    gripper_names: Sequence[str],
    action_index: int,
    predefined_actions: Sequence[dict],
    chunk_size: int,
) -> Any:
    """构建一条预制安全位姿 Action（round-robin 选 pose，position 扩展为 chunk_size 步）。

    joint_states.position → 桥的 arm_positions（14 臂关节，弧度）
    end_effector_states.position → 桥的 gripper_positions（2 夹爪，0-100）
    """
    from cloudrobo_r2c.common.models import Actions

    pose = predefined_actions[action_index % len(predefined_actions)]
    arm_pos = list(pose["joint_states"]["position"])
    grip_pos = list(pose.get("end_effector_states", {}).get("position", []))
    has_grip = len(grip_pos) > 0

    payload = {
        "timestamp": now_ms,
        "chunk_size": chunk_size,
        "joint_states": {
            "names": list(arm_joint_names),
            # 同一目标位姿重复 chunk_size 步（机器人侧逐步消费）
            "position": [arm_pos] * chunk_size,
        },
        "end_effector_poses": {},
        # 安全位姿不控制夹爪时 end_effector_states 留空（只下发手臂动作）
        "end_effector_states": {
            "names": list(gripper_names) if has_grip else [],
            "position": ([grip_pos] * chunk_size) if has_grip else [],
            "velocity": [],
            "torque": [],
        },
        "localization": {"odom_pose": [], "map_pose": []},
    }
    return Actions.from_dict(payload)


def run_xifeng_cloud_adapter(args: argparse.Namespace) -> None:
    """订阅观测，round-robin 回发预制安全位姿（观测驱动，无定时器）。"""
    from cloudrobo_r2c import ClientConfig, R2CClient

    validate_predefined_actions(PREDEFINED_SAFE_POSES)

    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id,
        endpoint_role="listen",
        endpoints=["tcp/0.0.0.0:7447"],
        mode="peer",
    )
    logger.info(
        "[%s] Connecting: project=%s, device=%s, client=%s, chunk_size=%d, poses=%d",
        ADAPTER_NAME,
        args.project_id,
        args.device_id,
        args.client_id,
        args.chunk_size,
        len(PREDEFINED_SAFE_POSES),
    )
    client = R2CClient.connect(config)

    action_index = 0

    def on_observation(observation: Any) -> None:
        nonlocal action_index

        normalized = normalize_for_logging(observation)
        logger.info(
            "\n=== Received observation ===\n%s\n============================",
            json.dumps(normalized, ensure_ascii=False, indent=2),
        )

        pose = PREDEFINED_SAFE_POSES[action_index % len(PREDEFINED_SAFE_POSES)]
        action = build_predefined_action(
            now_ms=int(time.time() * 1000),
            arm_joint_names=XIFENG_ARM_JOINT_NAMES,
            gripper_names=XIFENG_GRIPPER_NAMES,
            action_index=action_index,
            predefined_actions=PREDEFINED_SAFE_POSES,
            chunk_size=args.chunk_size,
        )
        client.publish_actions(action)
        logger.info(
            "[%s] Sent predefined pose #%d (%s): arm[0]=%.3f grippers=%s",
            ADAPTER_NAME,
            action_index % len(PREDEFINED_SAFE_POSES) + 1,
            pose["name"],
            pose["joint_states"]["position"][0],
            pose["end_effector_states"]["position"],
        )
        action_index += 1

    client.subscribe_observations(on_observation, target_device_id=args.device_id)
    logger.info(
        "[%s] Subscribed: %s/%s/inference/observations",
        ADAPTER_NAME,
        args.project_id,
        args.device_id,
    )
    logger.info("[%s] Waiting for observations (Ctrl+C to stop)...", ADAPTER_NAME)

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


def parse_args() -> argparse.Namespace:
    parser = build_parser(
        description="Xifeng cloud adapter: subscribe observations and publish predefined safe poses",
        default_project_id=DEFAULT_PROJECT_ID,
        default_device_id=DEFAULT_DEVICE_ID,
        default_client_id=DEFAULT_CLIENT_ID,
        help_project_id="Project ID",
        help_device_id="Target device ID",
        help_client_id="Client ID",
        help_duration="Running duration in seconds; 0 means continuous",
    )
    # chunk_size 默认 publish_hz(10) * 2 = 20。若桥收不到 action，调小（见 HANDOFF 坑：
    # 大 chunk + 多关节可能被 zenoh/protobuf 吞）。
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=20,
        help="action chunk size (default publish_hz*2=20); reduce if bridge misses actions",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_xifeng_cloud_adapter(args)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    main()
