#!/usr/bin/env python3
"""新松（DucoCobot 双臂）cloud adapter 示例。

充当**云端**：通过 R2C SDK 订阅机器人侧 Observations，每收到一帧就用
**round-robin** 方式回发一条**预制安全位姿** Action（双臂独立控制）。
用于在没有真实 AI 模型时验证「云 → 机器人」下行链路与数据格式。

遵循 `skills/r2c/references/r2c-custom-robot.md` §8 强制要求：
- 必须 ≥3 个安全位置预制动作（少于 3 报错）
- 每次收到观测时 round-robin 发送这些预制动作
- 禁止使用定时器循环（动作由观测到达驱动，不是固定频率）

支持两种控制模式（--control-mode 选择），均带双臂夹爪：

joint 模式（关节 + 夹爪）:
    joint_states.position        = [左臂 J1-J7, 右臂 J8-J14] 弧度
    end_effector_states.position = [left_ee, right_ee] 夹爪 0-10（0 夹紧 10 松开）

cartesian 模式（末端位姿 + 夹爪）:
    end_effector_poses.left_ee/right_ee.pose = [x,y,z,qw,qx,qy,qz] 米 + 四元数
      （6 维 xyzrpy，米/弧度，起原样直发不经转换）
    end_effector_states.position             = 同上夹爪

关节位姿#1 取自新松 demo.py 已验证姿态；笛卡尔位姿取自 docs/data.json 实测值
+小偏移。⚠️ **上真机前务必先 dry_run 比对，并按现场验证结果替换数值**。

用法：
    python examples/songyi/cloud_adapter.py \
        [--control-mode joint|cartesian] [--chunk-size 60]
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from typing import Any, Dict, List, Sequence

from _cloud_adapter_common import build_parser, normalize_for_logging

logger = logging.getLogger(__name__)

ADAPTER_NAME = "SongyiCloudAdapter"
DEFAULT_PROJECT_ID = "r2c"
DEFAULT_DEVICE_ID = "local"
DEFAULT_CLIENT_ID = "songyi-cloud-adapter"

MODE_JOINT = "joint"
MODE_CARTESIAN = "cartesian"

# 双臂关节名（14 = 左臂 J1-J7 + 右臂 J8-J14），与机器人侧观测一致
SONGYI_ARM_JOINT_NAMES: List[str] = ["左肩俯仰", "左肩横滚", "左肩偏航", "左肘俯仰", "左腕横滚", "左腕偏航", "左腕俯仰", "右肩俯仰", "右肩横滚", "右肩偏航", "右肘俯仰", "右腕横滚", "右腕偏航", "右腕俯仰"]
N_ARM = len(SONGYI_ARM_JOINT_NAMES)          # 14
N_ARM_PER_SIDE = 7                            # 单臂 7 关节
# 夹爪名（0=夹紧，10=松开）
SONGYI_EE_NAMES: List[str] = ["左夹爪", "右夹爪"]
N_EE = len(SONGYI_EE_NAMES)                  # 2
DEFAULT_GRIPPER = 10.0                        # 安全默认：张开

# =============================================================================
# 预制安全位姿（≥3 组，round-robin 轮发）—— 双臂独立定义（左右展开）
# =============================================================================
# joint 模式：左/右臂各 7 关节（弧度）分别定义。右臂 J1/J3/J5/J7 与左臂镜像
# 反号（与 docs/data.json 录制数据一致）；#1 取自新松 demo.py 已验证姿态。
_SAFE_POSES_JOINT_RAD: List[Dict[str, Any]] = [
    {
        "name": "pose#1 零位/demo-home",
        "left": [-1.0320814488493082,-1.518377327058867,1.5707963267948966,2.328474977986255,-0.36408075262473183,0.1747299991200988,0.1772946232498458],
        "right": [1.0320814488493082,-1.518377327058867,-1.5707963267948966,2.328474977986255,0.36408075262473183,0.1747299991200988,-0.1772946232498458],
    },
    {
        "name": "pose#2 抬臂/ready",
        "left": [-1.0320814488493082,-1.518377327058867,1.5707963267948966,2.328474977986255,-0.36408075262473183,0.1747299991200988,-0.1772946232498458],
        "right": [1.0320814488493082,-1.518377327058867,-1.5707963267948966,2.328474977986255,0.36408075262473183,0.1747299991200988,0.1772946232498458],
    },
    {
        "name": "pose#3 展臂/side",
        "left": [-1.0320814488493082,-1.518377327058867,1.5707963267948966,2.328474977986255,-0.36408075262473183,0.1747299991200988,0],
        "right": [1.0320814488493082,-1.518377327058867,-1.5707963267948966,2.328474977986255,0.36408075262473183,0.1747299991200988,0],
    },
]

# cartesian 模式：左/右臂末端位姿 [x,y,z,Rx,Ry,Rz]（米/弧度）分别定义；
# 数值取自 docs/data.json 实测首帧（右臂 Rz 与左臂反号）+ 小偏移。
# ⚠️ 现场验证后按需替换。
_SAFE_POSES_CART_RPY: List[Dict[str, Any]] = [
    {
        "name": "cart#1 实测位姿",
        "left_xyzrpy": [-0.33222841322422028, -0.18081063032150269, 0.09880013018846512, -1.9018911123275757, 0.1496405005455017, 2.6977906227111816],
        "right_xyzrpy": [-0.33171572387218475, 0.18128830194473267, 0.09835295379161835, 1.8944250345230103, 0.1474941521883011, -2.7049787044525146],
    },
    {
        "name": "cart#2 双臂抬升+2cm",
        "left_xyzrpy": [-0.23222841322422028, -0.08081063032150269, 0.09880013018846512, -1.9018911123275757, 0.1496405005455017, 2.6977906227111816],
        "right_xyzrpy": [-0.23171572387218475, 0.08128830194473267, 0.09835295379161835, 1.8944250345230103, 0.1474941521883011, -2.7049787044525146],
    },
    {
        "name": "cart#3 双臂外展-/+2cm",
        "left_xyzrpy": [-0.33222841322422028, -0.08081063032150269, 0.09880013018846512, -1.9018911123275757, 0.1496405005455017, 2.6977906227111816],
        "right_xyzrpy": [-0.33171572387218475, 0.08128830194473267, 0.09835295379161835, 1.8944250345230103, 0.1474941521883011, -2.7049787044525146],
    },
]


def _build_predefined(mode: str) -> List[Dict[str, Any]]:
    """展开预制位姿为双臂动作（joint: 左右 7 关节；cartesian: 左右 Pose7D）。"""
    poses: List[Dict[str, Any]] = []
    source = _SAFE_POSES_JOINT_RAD if mode == MODE_JOINT else _SAFE_POSES_CART_RPY
    for p in source:
        entry: Dict[str, Any] = {"name": p["name"], "grippers": [DEFAULT_GRIPPER] * N_EE}
        if mode == MODE_JOINT:
            entry["left_joints"] = [float(v) for v in p["left"]]
            entry["right_joints"] = [float(v) for v in p["right"]]
        else:
            entry["left_pose"] = p["left_xyzrpy"]
            entry["right_pose"] = p["right_xyzrpy"]
        poses.append(entry)
    return poses


def validate_predefined_actions(predefined_actions: List[Dict[str, Any]], mode: str) -> None:
    """规范 §8：少于 3 个安全位置应报错；并校验每个 pose 维度正确。"""
    if len(predefined_actions) < 3:
        raise ValueError(
            f"至少需要 3 个预制安全位姿（r2c-custom-robot.md §8），当前只有 "
            f"{len(predefined_actions)} 个"
        )
    for i, pose in enumerate(predefined_actions):
        grippers = pose["grippers"]
        if len(grippers) != N_EE:
            raise ValueError(f"pose#{i + 1} 夹爪数应为 {N_EE}，实际 {len(grippers)}")
        for g in grippers:
            if not (0.0 <= g <= 10.0):
                raise ValueError(f"pose#{i + 1} 夹爪值 {g} 超出 0-10")
        if mode == MODE_JOINT:
            for side in ("left_joints", "right_joints"):
                if len(pose[side]) != N_ARM_PER_SIDE:
                    raise ValueError(
                        f"pose#{i + 1} {side} 应为 {N_ARM_PER_SIDE} 关节，"
                        f"实际 {len(pose[side])}"
                    )
        else:
            for key in ("left_pose", "right_pose"):
                if len(pose[key]) != 6:
                    raise ValueError(f"pose#{i + 1} {key} 应为 6 维 Pose7D")


def build_predefined_action(
    now_ms: int,
    mode: str,
    arm_joint_names: Sequence[str],
    ee_names: Sequence[str],
    action_index: int,
    predefined_actions: List[Dict[str, Any]],
    chunk_size: int,
) -> Any:
    """构建一条预制 Action（round-robin 选 pose，数据扩展为 chunk_size 步）。"""
    from cloudrobo_r2c.common.models import Actions

    pose = predefined_actions[action_index % len(predefined_actions)]
    grippers = [float(v) for v in pose["grippers"]]

    payload: Dict[str, Any] = {
        "timestamp": now_ms,
        "chunk_size": chunk_size,
        "joint_states": {
            "names": [],
            "position": [],
            "velocity": [],
            "torque": [],
        },
        "end_effector_poses": {},
        "end_effector_states": {
            "names": list(ee_names),
            "position": [grippers] * chunk_size,
            "velocity": [[0.0] * N_EE] * chunk_size,
            "torque": [[0.0] * N_EE] * chunk_size,
        },
        "localization": {"odom_pose": [], "map_pose": []},
    }

    if mode == MODE_JOINT:
        arm = [float(v) for v in pose["left_joints"]] + [
            float(v) for v in pose["right_joints"]
        ]
        payload["joint_states"] = {
            "names": list(arm_joint_names),
            "position": [arm] * chunk_size,
            "velocity": [[0.0] * N_ARM] * chunk_size,
            "torque": [[0.0] * N_ARM] * chunk_size,
        }
    else:
        payload["end_effector_poses"] = {
            "左末端": {"pose": [list(pose["left_pose"])] * chunk_size},
            "右末端": {"pose": [list(pose["right_pose"])] * chunk_size},
        }

    return Actions.from_dict(payload)


def run_songyi_cloud_adapter(args: argparse.Namespace) -> None:
    """订阅观测，round-robin 回发预制安全位姿（观测驱动，无定时器）。"""
    from cloudrobo_r2c import ClientConfig, R2CClient

    mode = args.control_mode
    predefined = _build_predefined(mode)
    validate_predefined_actions(predefined, mode)

    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id,
        endpoint_role="connect",
        endpoints=["tcp/127.0.0.1:7447"],
        mode="client",
    )
    logger.info(
        "[%s] Connecting: project=%s, device=%s, client=%s, mode=%s, "
        "chunk_size=%d, poses=%d",
        ADAPTER_NAME,
        args.project_id,
        args.device_id,
        args.client_id,
        mode,
        args.chunk_size,
        len(predefined),
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

        # 关节名以观测为准（cartesian 模式不依赖）
        try:
            joint_names = list(observation.joint_states.names)[:N_ARM]
        except Exception:
            joint_names = []
        if mode == MODE_JOINT and len(joint_names) != N_ARM:
            joint_names = list(SONGYI_ARM_JOINT_NAMES)

        pose = predefined[action_index % len(predefined)]
        action = build_predefined_action(
            now_ms=int(time.time() * 1000),
            mode=mode,
            arm_joint_names=joint_names,
            ee_names=SONGYI_EE_NAMES,
            action_index=action_index,
            predefined_actions=predefined,
            chunk_size=args.chunk_size,
        )
        client.publish_actions(action)
        if mode == MODE_JOINT:
            detail = f"J1(L/R)={pose['left_joints'][0]:.3f}/{pose['right_joints'][0]:.3f}"
        else:
            detail = (
                f"xyz(L/R)={tuple(round(v, 3) for v in pose['left_pose'][:3])}/"
                f"{tuple(round(v, 3) for v in pose['right_pose'][:3])}"
            )
        logger.info(
            "[%s] Sent pose #%d (%s, %s): %s grippers=%s",
            ADAPTER_NAME,
            action_index % len(predefined) + 1,
            pose["name"],
            mode,
            detail,
            pose["grippers"],
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
        description="Songyi cloud adapter: dual-arm predefined safe poses "
        "(joint+gripper or cartesian+gripper)",
        default_project_id=DEFAULT_PROJECT_ID,
        default_device_id=DEFAULT_DEVICE_ID,
        default_client_id=DEFAULT_CLIENT_ID,
        help_project_id="Project ID",
        help_device_id="Target device ID",
        help_client_id="Client ID",
        help_duration="Running duration in seconds; 0 means continuous",
    )
    parser.add_argument(
        "--control-mode",
        choices=[MODE_JOINT, MODE_CARTESIAN],
        default=MODE_JOINT,
        help="joint = 关节角度+夹爪; cartesian = 末端位姿+夹爪 (default: joint)",
    )
    # chunk_size 默认 publish_hz(30) * 2 = 60，即缓冲 2 秒动作；
    # 若机器人侧收不到 action 或执行跟不上，调小（如 30）。
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=60,
        help="action chunk size (default publish_hz*2=60); reduce if robot misses actions",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_songyi_cloud_adapter(args)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    main()
