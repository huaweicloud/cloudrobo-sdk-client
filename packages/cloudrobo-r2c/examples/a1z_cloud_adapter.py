"""A1Z + G1Z cloud adapter example — 本地调试用模拟推理模块.

订阅 Observations，生成安全的模拟 Actions，无需真实 Policy Server 即可
验证 A1Z 适配器的完整数据链路（观测 → 动作 → 执行）。

两种动作生成模式:
1. **npy 回放模式** (--action-state-npy): 从预录制的 .npy 文件加载关节轨迹逐帧回放
2. **安全模拟模式** (--simulate): 基于当前观测生成微小增量动作，所有值严格
   裁剪在 A1Z 关节软限位范围内，确保安全

用法示例:
    # 模式 1: 从 .npy 回放
    python examples/a1z_cloud_adapter.py \
      --client-config config/client_config.yaml \
      --action-state-npy recordings/a1z_trajectory.npy

    # 模式 2: 安全模拟 (现场生成动作)
    python examples/a1z_cloud_adapter.py \
      --client-config config/client_config.yaml \
      --simulate

    # 模式 3: Bundle 凭证包
    python examples/a1z_cloud_adapter.py \
      --bundle /path/to/bundle.zip \
      --simulate --chunk-size 50 --increment 0.005
"""

from __future__ import annotations

import argparse
import logging
import math
import time
from threading import Lock
from typing import Optional, Sequence

import numpy as np

from cloudrobo_r2c.client import R2CClient
from cloudrobo_r2c.common.config import ClientConfig
from cloudrobo_r2c.common.models import Actions, Observations

logger = logging.getLogger(__name__)

# ── A1Z 关节软限位 (rad) ─────────────────────────────────────────────────
# 参考: A1Z SDK get_robot.py _JOINT_LIMITS, 官方文档软件 API
_A1Z_JOINT_LIMITS = np.array([
    [-2.094,  2.094],   # J1 (arm_joint1)  [-120°, 120°]
    [ 0.000,  3.142],   # J2 (arm_joint2)  [0°, 180°]
    [-3.142,  0.000],   # J3 (arm_joint3)  [-180°, 0°]
    [-1.484,  1.484],   # J4 (arm_joint4)  [-85°, 85°]
    [-1.484,  1.484],   # J5 (arm_joint5)  [-85°, 85°]
    [-2.007,  2.007],   # J6 (arm_joint6)  [-115°, 115°]
], dtype=np.float64)

# A1Z 关节名 (与 robot_a1z_config.yaml 中 device_to_r2c 的 names 一致)
_A1Z_JOINT_NAMES = [
    "arm_joint1", "arm_joint2", "arm_joint3",
    "arm_joint4", "arm_joint5", "arm_joint6",
    "gripper",
]

# 夹爪范围 (归一化)
_GRIPPER_MIN = 0.0    # 闭合
_GRIPPER_MAX = 1.0    # 全开
_GRIPPER_INCREMENT = 0.02
_GRIPPER_HOLD_STEPS = 90   # 到达边界后停留的步数 (30Hz × 3s)
_GRIPPER_HOLD_SECONDS = 3

# 安全余量: 关节角距限位边界的最小距离 (rad)，约 1°
_SAFETY_MARGIN_RAD = 0.017

# ── 默认参数 ──────────────────────────────────────────────────────────────
_DEFAULT_CHUNK_SIZE = 50
_DEFAULT_INCREMENT = 0.005  # rad/步 ≈ 0.3°/步，非常保守
_DEFAULT_MAX_JOINT_SPEED = 0.05  # rad/步 最大关节速度

# ── 夹爪状态 (全局, 跨 chunk 保持连续性) ───────────────────────────────────
_gripper_lock = Lock()
_gripper_value: float = _GRIPPER_MAX       # 从张开开始
_gripper_direction: int = -1                # -1: 向闭合, +1: 向张开
_gripper_hold_steps_remaining: int = 0
_gripper_initialized: bool = False


# ═══════════════════════════════════════════════════════════════════════════
# 参数解析
# ═══════════════════════════════════════════════════════════════════════════

def parse_endpoints(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [ep.strip() for ep in raw.split(",") if ep.strip()]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="A1Z cloud adapter: 订阅观测并生成安全的模拟动作"
    )

    # ── 连接方式 ──
    parser.add_argument(
        "--bundle", type=str, default=None,
        help="平台签发的凭证包路径 (zip 或目录)",
    )
    parser.add_argument(
        "--client-config", default="config/client_config.yaml",
        help="R2C 客户端 YAML 配置路径",
    )
    parser.add_argument("--project-id", type=str, default=None)
    parser.add_argument("--device-id", type=str, default=None)
    parser.add_argument("--client-id", type=str, default=None)
    parser.add_argument(
        "--endpoints", type=str, default="",
        help="逗号分隔的 Zenoh endpoints, 如 'tls/127.0.0.1:7447'",
    )
    parser.add_argument(
        "--mode", type=str, default="peer", choices=["peer", "client"],
        help="Zenoh 连接模式",
    )

    # ── 动作生成 ──
    parser.add_argument(
        "--action-state-npy", type=str, default=None,
        help="预录制的 .npy 关节轨迹文件 (每行 = 一帧关节角, rad)",
    )
    parser.add_argument(
        "--simulate", action="store_true", default=False,
        help="启用安全模拟模式: 基于当前观测生成微小增量动作",
    )
    parser.add_argument(
        "--chunk-size", type=int, default=_DEFAULT_CHUNK_SIZE,
        help=f"每 chunk 的动作步数 (默认: {_DEFAULT_CHUNK_SIZE})",
    )
    parser.add_argument(
        "--increment", type=float, default=_DEFAULT_INCREMENT,
        help=(
            "安全模拟模式下每步关节角增量 (rad)。"
            f"默认 {_DEFAULT_INCREMENT} rad ≈ {math.degrees(_DEFAULT_INCREMENT):.1f}°"
        ),
    )

    # ── 运行控制 ──
    parser.add_argument(
        "--target-device-id", type=str, default=None,
        help="目标机器人 device ID。默认使用 --device-id 的值",
    )
    parser.add_argument(
        "--duration", type=float, default=0.0,
        help="运行时长 (秒)，0=无限",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        help="日志级别: DEBUG/INFO/WARNING/ERROR",
    )

    return parser.parse_args(argv)


# ═══════════════════════════════════════════════════════════════════════════
# 连接建立
# ═══════════════════════════════════════════════════════════════════════════

def build_session(args: argparse.Namespace):
    if args.bundle:
        logger.info("使用凭证包连接: %s", args.bundle)
        return R2CClient.connect(args.bundle, client_id=args.client_id)

    if args.client_config:
        logger.info("使用客户端配置连接: %s", args.client_config)
        client_config = ClientConfig.from_yaml(args.client_config)
        return R2CClient.connect(client_config)

    if not args.project_id:
        raise ValueError("需要 --project-id (未提供 --bundle 或 --client-config 时)")
    if not args.device_id:
        raise ValueError("需要 --device-id (未提供 --bundle 或 --client-config 时)")

    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id or "a1z-cloud-adapter",
        endpoints=parse_endpoints(args.endpoints),
        mode=args.mode,
    )
    config.validate()
    return R2CClient.connect(config)


# ═══════════════════════════════════════════════════════════════════════════
# 安全模拟模式: 从当前观测 + A1Z 限位生成安全动作
# ═══════════════════════════════════════════════════════════════════════════

def _clamp_to_a1z_limits(joint_positions: np.ndarray) -> np.ndarray:
    """将关节位置裁剪到 A1Z 软限位内（保留安全余量）。"""
    clamped = joint_positions.copy()
    for i in range(6):
        lo = _A1Z_JOINT_LIMITS[i, 0] + _SAFETY_MARGIN_RAD
        hi = _A1Z_JOINT_LIMITS[i, 1] - _SAFETY_MARGIN_RAD
        clamped[i] = np.clip(clamped[i], lo, hi)
    return clamped


def _generate_safe_oscillation(
    base_positions: np.ndarray,
    chunk_size: int,
    increment: float,
) -> list[list[float]]:
    """生成围绕 base_positions 的安全正弦振荡轨迹。

    每个关节独立振荡，幅度 = increment × joint_idx_offset，
    始终裁剪在 A1Z 限位内。
    """
    joint_steps: list[list[float]] = []
    # 每关节不同频率和相位，避免机械臂做单调运动
    frequencies = np.array([0.3, 0.5, 0.7, 0.4, 0.6, 0.8])
    phases = np.array([0.0, 1.2, 2.4, 0.8, 1.6, 3.0])
    # 幅度限制: 不超过 increment × 50，且不超过安全范围
    amplitudes = np.clip(
        np.array([increment * 30, increment * 25, increment * 25,
                  increment * 20, increment * 15, increment * 15]),
        0.0, 0.1,  # 绝对上限 0.1 rad ≈ 5.7°
    )

    for step in range(chunk_size):
        t = step / max(chunk_size, 1)
        offsets = amplitudes * np.sin(2 * math.pi * frequencies * t + phases)
        target = base_positions + offsets
        target = _clamp_to_a1z_limits(target)
        joint_steps.append(target.tolist())

    return joint_steps


def _build_gripper_steps(chunk_size: int) -> list[float]:
    """生成夹爪轨迹: 张开 ↔ 闭合 循环，到达边界停 3 秒。

    范围 [0.0, 1.0]，与 A1Z 归一化夹爪接口一致。
    """
    global _gripper_value
    global _gripper_direction
    global _gripper_hold_steps_remaining
    global _gripper_initialized

    with _gripper_lock:
        if not _gripper_initialized:
            _gripper_value = _GRIPPER_MAX
            _gripper_direction = -1
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

            _gripper_value += _gripper_direction * _GRIPPER_INCREMENT
            if _gripper_value >= _GRIPPER_MAX:
                _gripper_value = _GRIPPER_MAX
                _gripper_hold_steps_remaining = _GRIPPER_HOLD_STEPS
            elif _gripper_value <= _GRIPPER_MIN:
                _gripper_value = _GRIPPER_MIN
                _gripper_hold_steps_remaining = _GRIPPER_HOLD_STEPS

            trajectory.append(_gripper_value)

        return trajectory


# ═══════════════════════════════════════════════════════════════════════════
# npy 回放模式: 从预录制轨迹加载
# ═══════════════════════════════════════════════════════════════════════════

def load_joint_steps_from_npy(path: str) -> list[list[float]]:
    """从 .npy 文件加载关节轨迹。

    支持 1D (单帧) 和 2D (多帧) 数组。
    自动验证每帧在 A1Z 限位内，超限值警告并裁剪。
    """
    if not path.endswith(".npy"):
        raise ValueError(f"仅支持 .npy 文件，实际路径: {path}")
    data = np.load(path, allow_pickle=False)
    array = np.asarray(data, dtype=np.float64)
    if array.ndim == 1:
        array = array.reshape(1, -1)
    if array.ndim != 2:
        raise ValueError(f".npy 文件需为 1D 或 2D 数组，实际 ndim={array.ndim}")
    if array.shape[1] < 6:
        raise ValueError(
            f".npy 文件至少需要 6 列 (关节角 rad)，实际列为 {array.shape[1]}"
        )

    # 验证每帧在限位内
    out_of_bounds = 0
    for row_idx in range(array.shape[0]):
        for j_idx in range(min(6, array.shape[1])):
            lo = _A1Z_JOINT_LIMITS[j_idx, 0]
            hi = _A1Z_JOINT_LIMITS[j_idx, 1]
            if array[row_idx, j_idx] < lo or array[row_idx, j_idx] > hi:
                logger.warning(
                    "轨迹[%d] J%d=%.4f rad 超出限位 [%.3f, %.3f]，已裁剪",
                    row_idx, j_idx + 1, array[row_idx, j_idx], lo, hi,
                )
                array[row_idx, j_idx] = np.clip(array[row_idx, j_idx], lo, hi)
                out_of_bounds += 1

    if out_of_bounds > 0:
        logger.warning("共 %d 个关节值超出限位，已裁剪到安全范围", out_of_bounds)
    else:
        logger.info("轨迹验证通过：所有 %d 帧均在 A1Z 关节限位内", array.shape[0])

    return array.tolist()


def _pop_next_chunk(
    *, loaded_steps: list[list[float]], offset: int, chunk_size: int
) -> tuple[list[list[float]], int]:
    """从加载的轨迹中取下一个 chunk。"""
    if offset >= len(loaded_steps):
        return [], offset
    next_offset = min(offset + chunk_size, len(loaded_steps))
    chunk = loaded_steps[offset:next_offset]
    return [list(step) for step in chunk], next_offset


# ═══════════════════════════════════════════════════════════════════════════
# Action 构建
# ═══════════════════════════════════════════════════════════════════════════

def build_action(
    observation: Observations,
    *,
    chunk_joint_steps: list[list[float]],
    chunk_size: int,
) -> Actions:
    """从关节步骤构建 Actions 消息。"""
    if not chunk_joint_steps:
        raise ValueError("chunk_joint_steps 不能为空")

    num_joints = len(chunk_joint_steps[0])
    joint_names = list(observation.joint_states.names) if observation.joint_states.names else []
    if len(joint_names) < num_joints:
        # 补齐到实际维度
        joint_names = list(_A1Z_JOINT_NAMES[:num_joints])

    joint_steps = [list(step) for step in chunk_joint_steps]
    zeros = [[0.0 for _ in range(num_joints)] for _ in range(chunk_size)]

    return Actions.from_dict({
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
            "names": [], "position": [], "velocity": [], "torque": [],
        },
        "localization": {
            "odom_pose": [], "map_pose": [],
        },
    })


# ═══════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════

def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    chunk_size = int(args.chunk_size)
    if chunk_size <= 0:
        raise ValueError("--chunk-size 必须 > 0")
    increment = float(args.increment)
    if increment <= 0:
        raise ValueError("--increment 必须 > 0")

    # ── 确定动作生成模式 ──────────────────────────────────────────────
    use_npy = args.action_state_npy is not None
    use_simulate = args.simulate

    if use_npy and use_simulate:
        logger.warning("同时指定 --action-state-npy 和 --simulate，优先使用 .npy 回放")
        use_simulate = False
    if not use_npy and not use_simulate:
        logger.info("未指定动作生成模式，默认启用安全模拟模式")
        use_simulate = True

    loaded_joint_steps: list[list[float]] = []
    if use_npy:
        loaded_joint_steps = load_joint_steps_from_npy(args.action_state_npy)
        logger.info(
            "npy 回放模式: 加载 %d 帧, %d 关节",
            len(loaded_joint_steps),
            len(loaded_joint_steps[0]) if loaded_joint_steps else 0,
        )
    else:
        logger.info(
            "安全模拟模式: chunk_size=%d, increment=%.4f rad (≈%.2f°)",
            chunk_size, increment, math.degrees(increment),
        )

    # ── 连接 ──────────────────────────────────────────────────────────
    session = build_session(args)
    target_device_id = args.target_device_id or args.device_id

    logger.info(
        "A1Z cloud adapter 已启动: target_device=%s chunk_size=%d",
        target_device_id, chunk_size,
    )

    # ── 状态 ──────────────────────────────────────────────────────────
    npy_cursor = 0
    cursor_lock = Lock()
    # 用于安全模拟模式：缓存最新观测的关节位置
    latest_joint_positions: Optional[np.ndarray] = None
    state_lock = Lock()

    def on_observation(observation: Observations) -> None:
        nonlocal npy_cursor
        nonlocal latest_joint_positions

        # 提取当前观测的关节位置 (用于安全模拟模式)
        obs_positions = None
        if observation.joint_states and observation.joint_states.position:
            pos_list = observation.joint_states.position
            if pos_list:
                obs_positions = np.asarray(pos_list[-1] if isinstance(pos_list[0], list) else pos_list, dtype=np.float64)

        # ── 生成关节步骤 ──────────────────────────────────────────
        if use_npy:
            with cursor_lock:
                chunk_joint_steps, next_cursor = _pop_next_chunk(
                    loaded_steps=loaded_joint_steps,
                    offset=npy_cursor,
                    chunk_size=chunk_size,
                )
                if not chunk_joint_steps:
                    logger.info(
                        "obs_id=%d: .npy 轨迹已耗尽 (%d/%d)，不再发布动作",
                        observation.id, npy_cursor, len(loaded_joint_steps),
                    )
                    return
                npy_cursor = next_cursor
            actual_chunk_size = len(chunk_joint_steps)

        else:
            # 安全模拟模式
            with state_lock:
                base = latest_joint_positions
            if base is None and obs_positions is not None:
                base = obs_positions[:6].copy()
                with state_lock:
                    latest_joint_positions = base

            if base is None:
                # 首次无观测: 使用安全的默认 home 位
                base = np.array([0.0, 1.047, -1.047, 0.0, 0.0, 0.0], dtype=np.float64)
                logger.info("obs_id=%d: 无历史观测，使用默认 home 位作为基准", observation.id)

            base = _clamp_to_a1z_limits(base)

            chunk_joint_steps = _generate_safe_oscillation(
                base_positions=base,
                chunk_size=chunk_size,
                increment=increment,
            )
            actual_chunk_size = chunk_size

            # 更新基准为最后一个安全位置（下一 chunk 从此继续）
            with state_lock:
                latest_joint_positions = np.array(chunk_joint_steps[-1][:6], dtype=np.float64)

        # ── 追加夹爪 ──────────────────────────────────────────────
        gripper_traj = _build_gripper_steps(actual_chunk_size)
        for i in range(actual_chunk_size):
            # 确保每步至少有 7 个元素 (6 关节 + 1 夹爪)
            if len(chunk_joint_steps[i]) < 7:
                chunk_joint_steps[i] = list(chunk_joint_steps[i]) + [gripper_traj[i]]
            else:
                chunk_joint_steps[i][6] = gripper_traj[i]

        # ── 构建并发布 Actions ────────────────────────────────────
        action = build_action(
            observation,
            chunk_joint_steps=chunk_joint_steps,
            chunk_size=actual_chunk_size,
        )
        session.publish_actions(action)

        # 详细日志 (每 chunk 打印一次)
        first_joints = ", ".join(
            f"J{i+1}={chunk_joint_steps[0][i]:.3f}"
            for i in range(min(6, len(chunk_joint_steps[0])))
        )
        last_joints = ", ".join(
            f"J{i+1}={chunk_joint_steps[-1][i]:.3f}"
            for i in range(min(6, len(chunk_joint_steps[-1])))
        )
        gripper_info = ""
        if len(chunk_joint_steps[0]) >= 7:
            gripper_info = f", gripper={chunk_joint_steps[0][6]:.2f}→{chunk_joint_steps[-1][6]:.2f}"
        logger.info(
            "obs_id=%d chunk_size=%d | 起始: [%s]%s | 结束: [%s]",
            observation.id, actual_chunk_size, first_joints, gripper_info, last_joints,
        )

    # ── 订阅观测 ────────────────────────────────────────────────────
    session.subscribe_observations(on_observation, target_device_id=target_device_id)

    start = time.time()
    try:
        while True:
            if args.duration > 0 and time.time() - start >= args.duration:
                logger.info("运行时长 %.1fs 已到，停止", args.duration)
                break
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到键盘中断，停止")
    finally:
        session.close()
        logger.info("会话已关闭")


if __name__ == "__main__":
    main()
