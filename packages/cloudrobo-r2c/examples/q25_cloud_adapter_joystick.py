"""
Q25 Ultra 四足机器人云端适配器示例.

订阅机器人端的 Observations 并打印内容。每次收到 Observation 后，
发送模拟的 Action（包含预定义的姿态）。

用法示例:
    python examples/q25_cloud_adapter.py
    python examples/q25_cloud_adapter.py --bundle /path/to/cert_xxx.zip
    python examples/q25_cloud_adapter.py --bundle /path/to/unpacked_bundle_dir
    python examples/q25_cloud_adapter.py --project-id test-tenant --device-id q25-robot-01
    python examples/q25_cloud_adapter.py --duration 30
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any, Optional, Sequence

from cloudrobo_r2c import R2CClient, ClientConfig
from cloudrobo_r2c.common.models import Actions, Observations

logger = logging.getLogger(__name__)

# 默认配置
ADAPTER_NAME = "Q25CloudAdapter"
DEFAULT_PROJECT_ID = "test-tenant"
DEFAULT_DEVICE_ID = "q25-robot-01"
DEFAULT_CLIENT_ID = "q25-cloud-adapter"

# Q25 关节名称 (12个关节: 4条腿 x 3个关节)
Q25_JOINT_NAMES = [
    "FL_hip_joint", "FL_thigh_joint", "FL_calf_joint",
    "FR_hip_joint", "FR_thigh_joint", "FR_calf_joint",
    "BL_hip_joint", "BL_thigh_joint", "BL_calf_joint",
    "BR_hip_joint", "BR_thigh_joint", "BR_calf_joint",
]

# 预制动作: 至少 3 个安全位置
# Q25 是四足机器人，使用 end_effector_states.position 传递高层控制命令
# 位置映射：
#   position[0]: stand (True=站立)
#   position[1]: lie (True=趴下)
#   position[2]: emergency_stop (True=急停)
#   position[3]: gait (0=walk, 1=run)
#   position[4]: height (0=低, 1=中, 2=高)
#   position[5-8]: 摇杆控制 (left_x, left_y, right_x, right_y)

# 控制命令字段名称
CONTROL_FIELD_NAMES = [
    "stand", "lie", "emergency_stop", "gait", "height",
    "left_x", "left_y", "right_x", "right_y"
]

# 参考 Q25SDKDemo/axis_control_demo.cpp
# 流程: 站立10秒 -> 前进2秒 -> 后退2秒 -> 左转2秒 -> 右转2秒 -> 左移2秒 -> 右移2秒 -> 趴下 -> 停止
# 命令码:
#   CMD_STAND_UP = 0x21010202 （站立）
#   CMD_LIE_DOWN = 0x21010222 （趴下）
#   CMD_LEFT_YAXIS = 0x21010130 (左摇杆Y轴 - 前后)
#   CMD_LEFT_XAXIS = 0x21010131 (左摇杆X轴 - 左右)
#   CMD_RIGHT_XAXIS = 0x21010135 (右摇杆X轴 - 旋转)
#
# 轴值定义 (死区外有效):
#   前进: left_y = 20000
#   后退: left_y = -20000
#   左移: left_x = -30000
#   右移: left_x = 30000
#   左转: right_x = -30000
#   右转: right_x = 30000
#   停止: 所有轴 = 0

# 控制值定义
# 参考 axis_control_demo.cpp: 轴值范围 [-1000, 1000]，无死区
# C++ demo 中轴值控制频率为 100Hz (每10ms发送一次)
ACTION_FREQUENCY = 100  # 100Hz - 与 C++ demo 一致

# 轴值定义 (与 axis_control_demo_new.cpp 一致)
AXIS_FORWARD = 500     # 前进 (left_y > 0)
AXIS_BACKWARD = -500   # 后退 (left_y < 0)
AXIS_MOVE_LEFT = -500  # 左移 (left_x < 0)
AXIS_MOVE_RIGHT = 500  # 右移 (left_x > 0)
AXIS_TURN_LEFT = -500  # 左转 (right_x < 0)
AXIS_TURN_RIGHT = 500  # 右转 (right_x > 0)
AXIS_STOP = 0          # 停止

# 动作序列定义 (不循环)
# 参考 axis_control_demo_new.cpp
# 注意：运动时必须保持 stand=1，否则 hardware adapter 会误判为停止
ACTION_SEQUENCE = [
    # 动作1: 站立 10秒 (axis_control_demo_new.cpp 先站立10秒)
    {
        "name": "站立",
        "duration_sec": 10,
        "end_effector_states": {
            "names": CONTROL_FIELD_NAMES,
            "position": [1.0, 0.0, 0.0, 0.0, 2.0, 0, 0, 0, 0],  # stand=1, height=2 (高)
        }
    },
    # 动作2: 前进 2秒 (left_y = 500)
    # 注意：保持 stand=1 以避免触发停止逻辑
    {
        "name": "前进",
        "duration_sec": 2,
        "end_effector_states": {
            "names": CONTROL_FIELD_NAMES,
            "position": [1.0, 0.0, 0.0, 0.0, 2.0, 0, AXIS_FORWARD, 0, 0],  # stand=1, left_y=500
        }
    },
    # 动作3: 后退 2秒 (left_y = -500)
    {
        "name": "后退",
        "duration_sec": 2,
        "end_effector_states": {
            "names": CONTROL_FIELD_NAMES,
            "position": [1.0, 0.0, 0.0, 0.0, 2.0, 0, AXIS_BACKWARD, 0, 0],  # stand=1, left_y=-500
        }
    },
    # 动作4: 左转 2秒 (right_x = -500)
    {
        "name": "左转",
        "duration_sec": 2,
        "end_effector_states": {
            "names": CONTROL_FIELD_NAMES,
            "position": [1.0, 0.0, 0.0, 0.0, 2.0, 0, 0, AXIS_TURN_LEFT, 0],  # stand=1, right_x=-500
        }
    },
    # 动作5: 右转 2秒 (right_x = 500)
    {
        "name": "右转",
        "duration_sec": 2,
        "end_effector_states": {
            "names": CONTROL_FIELD_NAMES,
            "position": [1.0, 0.0, 0.0, 0.0, 2.0, 0, 0, AXIS_TURN_RIGHT, 0],  # stand=1, right_x=500
        }
    },
    # 动作6: 左移 2秒 (left_x = -500)
    {
        "name": "左移",
        "duration_sec": 2,
        "end_effector_states": {
            "names": CONTROL_FIELD_NAMES,
            "position": [1.0, 0.0, 0.0, 0.0, 2.0, AXIS_MOVE_LEFT, 0, 0, 0],  # stand=1, left_x=-500
        }
    },
    # 动作7: 右移 2秒 (left_x = 500)
    {
        "name": "右移",
        "duration_sec": 2,
        "end_effector_states": {
            "names": CONTROL_FIELD_NAMES,
            "position": [1.0, 0.0, 0.0, 0.0, 2.0, AXIS_MOVE_RIGHT, 0, 0, 0],  # stand=1, left_x=500
        }
    },
    # 动作8: 趴下 (CMD_LIE_DOWN) - 执行完后程序自动结束
    {
        "name": "趴下",
        "duration_sec": 1,
        "end_effector_states": {
            "names": CONTROL_FIELD_NAMES,
            "position": [0.0, 1.0, 0.0, 0.0, 0.0, 0, 0, 0, 0],  # lie=1
        }
    },
]


def calculate_chunk_size(duration_sec: float, frequency: int = ACTION_FREQUENCY) -> int:
    """根据动作持续时间计算 chunk_size."""
    return int(duration_sec * frequency)


def parse_endpoints(raw: Optional[str]) -> list[str]:
    """解析逗号分隔的 endpoints 列表."""
    if not raw:
        return []
    return [endpoint.strip() for endpoint in raw.split(",") if endpoint.strip()]


def build_session(args: argparse.Namespace):
    """从 bundle 或显式配置构建会话.

    支持三种连接方式（按优先级）:
    1. --bundle: 使用平台颁发的证书包（推荐）
    2. --client-config: 使用 YAML 配置文件
    3. 显式参数: --project-id, --device-id, --endpoint-role, --endpoints 等

    云端适配器应使用 --endpoint-role listen (默认值):
    - listen 模式: 监听连接，适合云端服务
    - connect 模式: 主动连接，适合边缘设备
    """
    # 方式1: Bundle 模式（推荐）
    if args.bundle:
        logger.info("[%s] 使用平台证书包连接: %s", ADAPTER_NAME, args.bundle)
        return R2CClient.connect(args.bundle, client_id=args.client_id)

    # 方式2: YAML 配置文件模式
    if args.client_config:
        logger.info("[%s] 使用客户端配置连接: %s", ADAPTER_NAME, args.client_config)
        client_config = ClientConfig.from_yaml(args.client_config)
        return R2CClient.connect(client_config)

    # 方式3: 显式参数模式
    if not args.project_id:
        raise ValueError("project_id is required when --bundle is not provided")
    if not args.device_id:
        raise ValueError("device_id is required when --bundle is not provided")

    # 解析 endpoint role
    endpoint_role = getattr(args, "endpoint_role", "listen")
    parsed_endpoints = parse_endpoints(args.endpoints) if args.endpoints else []

    # Listen 模式下不需要 endpoints（自动监听）
    # Connect 模式下需要 endpoints
    if endpoint_role == "connect" and not parsed_endpoints:
        logger.warning(
            "[%s] endpoint_role=connect 但未指定 --endpoints, 将使用空 endpoints (可能依赖自动发现)",
            ADAPTER_NAME,
        )

    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id or DEFAULT_CLIENT_ID,
        endpoint_role=endpoint_role,
        endpoints=parsed_endpoints,
        mode=args.mode,
    )
    config.validate()

    logger.info(
        "[%s] 连接配置: project=%s, device=%s, endpoint_role=%s, mode=%s, endpoints=%s",
        ADAPTER_NAME,
        args.project_id,
        args.device_id,
        endpoint_role,
        args.mode,
        parsed_endpoints if parsed_endpoints else "(自动)",
    )

    return R2CClient.connect(config)


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
    """构建标准 CLI 解析器."""
    parser = argparse.ArgumentParser(description=description)

    # 连接方式选择（推荐 bundle 模式）
    parser.add_argument(
        "--bundle",
        type=str,
        default=None,
        help="平台颁发的证书包路径 (zip 文件或解压目录, 推荐)",
    )
    parser.add_argument(
        "--client-config",
        type=str,
        default=None,
        help="R2C 客户端配置 YAML 文件路径",
    )

    # 显式参数（当不使用 bundle 或 client-config 时使用）
    parser.add_argument("--project-id", type=str, default=default_project_id, help=help_project_id)
    parser.add_argument("--device-id", type=str, default=default_device_id, help=help_device_id)
    parser.add_argument("--client-id", type=str, default=default_client_id, help=help_client_id)

    # Endpoint role 配置 (云端适配器使用 "listen" 模式)
    parser.add_argument(
        "--endpoint-role",
        type=str,
        default="listen",
        choices=["connect", "listen"],
        help="端点角色: connect (主动连接) 或 listen (监听模式, 云端适配器用)",
    )
    parser.add_argument(
        "--endpoints",
        type=str,
        default="",
        help="逗号分隔的 endpoints, 例如: tcp/127.0.0.1:7447 (listen 模式下可选)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="peer",
        choices=["peer", "client"],
        help="连接模式: peer (P2P) 或 client (需路由器)",
    )

    # 运行参数
    parser.add_argument("--duration", type=float, default=0.0, help=help_duration)
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=200,
        help="Action chunk 大小 (默认 200 = 100Hz * 2s)"
    )

    # 订阅目标
    parser.add_argument(
        "--target-device-id",
        type=str,
        default=None,
        help="订阅目标设备 ID, 默认使用 --device-id",
    )

    # 机器人配置文件 (可选)
    parser.add_argument(
        "--robot-config",
        type=str,
        default=None,
        help="机器人配置文件路径 (robot_q25_config.yaml 格式)",
    )

    return parser


def normalize_for_logging(data: Any) -> Any:
    """规范化数据以便日志输出."""
    from dataclasses import is_dataclass, asdict

    try:
        import numpy as np
    except ImportError:
        np = None

    if is_dataclass(data) and not isinstance(data, type):
        return normalize_for_logging(asdict(data))

    if np is not None and isinstance(data, np.ndarray):
        return {
            "type": "ndarray",
            "shape": list(data.shape),
            "dtype": str(data.dtype),
        }

    if isinstance(data, bytes):
        return {"type": "bytes", "size": len(data)}

    if isinstance(data, dict):
        return {k: normalize_for_logging(v) for k, v in data.items()}

    if isinstance(data, list):
        return [normalize_for_logging(v) for v in data[:10]]  # 限制长度

    return data


def build_action_from_sequence(
    now_ms: int,
    joint_names: Sequence[str],
    default_joint_names: Sequence[str],
    sequence_index: int,
) -> tuple[Actions, int]:
    """根据动作序列构建 Action.

    使用动作序列循环发送: 高位站立5秒 -> 右转90度 -> 左转90度 -> 趴下
    Q25 使用 end_effector_states.position 传递高层控制命令。

    Args:
        now_ms: 当前时间戳 (毫秒)
        joint_names: 关节名称列表 (来自 observation)
        default_joint_names: 默认关节名称列表
        sequence_index: 动作序列索引

    Returns:
        (Actions 对象, chunk_size)
    """
    # 使用实际关节名称或默认
    names = list(joint_names) if joint_names else list(default_joint_names)

    # 从动作序列中选择动作
    action_def = ACTION_SEQUENCE[sequence_index % len(ACTION_SEQUENCE)]
    duration_sec = action_def["duration_sec"]
    chunk_size = calculate_chunk_size(duration_sec)

    # 获取控制命令数据 (使用 end_effector_states)
    control_data = action_def["end_effector_states"]
    control_names = control_data["names"]
    control_position = control_data["position"]

    # 扩展为 chunk_size 个相同的步
    control_positions = [list(control_position) for _ in range(chunk_size)]

    # 构建 Actions
    velocities = [[0.0] * len(names) for _ in range(chunk_size)]
    torques = [[0.0] * len(names) for _ in range(chunk_size)]

    # end_effector_states 也需要扩展
    ee_velocities = [[0.0] * len(control_names) for _ in range(chunk_size)]
    ee_torques = [[0.0] * len(control_names) for _ in range(chunk_size)]

    return Actions.from_dict({
        "timestamp": now_ms,
        "chunk_size": chunk_size,
        "joint_states": {
            "names": list(names),
            "position": [[0.0] * len(names)] * chunk_size,  # Q25 不支持直接关节位置
            "velocity": velocities,
            "torque": torques,
        },
        "end_effector_poses": {},
        "end_effector_states": {
            "names": list(control_names),
            "position": control_positions,
            "velocity": ee_velocities,
            "torque": ee_torques,
        },
        "localization": {
            "odom_pose": [],
            "map_pose": [],
        },
    }), chunk_size


def run_cloud_adapter(
    args: argparse.Namespace,
    adapter_name: str,
    action_default_joint_names: Sequence[str],
    observation_fallback_joint_names: Optional[Sequence[str]] = None,
    waiting_message_suffix: str = "Q25 observations",
    chunk_size: int = 20,
) -> None:
    """
    运行 Q25 云端适配器.

    Parameters
    ----------
    args:
        解析后的 CLI 参数.
    adapter_name:
        适配器名称，用于日志消息.
    action_default_joint_names:
        构建 Action 时使用的默认关节名称.
    observation_fallback_joint_names:
        从 observation 提取关节名称失败时使用的备用名称.
    waiting_message_suffix:
        等待日志的后缀.
    chunk_size:
        Action 的 chunk 大小.
    """
    # 确定订阅目标设备 ID
    target_device_id = args.target_device_id or args.device_id

    # 获取 endpoint_role 配置
    endpoint_role = getattr(args, "endpoint_role", "listen")

    logger.info(
        "[%s] 启动云端适配器, 目标设备: %s, endpoint_role: %s",
        adapter_name,
        target_device_id,
        endpoint_role,
    )

    # 构建会话（支持 bundle/client-config/显式参数三种模式）
    client = build_session(args)

    fallback_names = list(observation_fallback_joint_names or [])

    # 动作序列索引
    sequence_index = 0

    # 当前动作的累积计时
    current_action_start_time = None

    # 序列执行完毕标志
    sequence_finished = False

    def on_observation(observation: Observations) -> None:
        nonlocal sequence_index, current_action_start_time, sequence_finished

        # 如果序列已执行完毕，不再发送任何指令
        if sequence_finished:
            return

        # 打印收到的 observation
        normalized = normalize_for_logging(observation)
        logger.info(
            "\n=== 收到 Observation ===\n%s\n============================",
            normalized,
        )

        # 打印 end_effector_states 中的额外观测数据
        ee_states = observation.end_effector_states
        if ee_states and ee_states.names:
            try:
                extra_data = dict(zip(ee_states.names, ee_states.position))
                logger.info("额外观测数据: %s", extra_data)
            except Exception as e:
                logger.debug("提取额外观测数据失败: %s", e)

        # 获取关节名称
        joint_names = list(fallback_names)
        try:
            if observation.joint_states and observation.joint_states.names:
                joint_names = list(observation.joint_states.names)
        except Exception as e:
            logger.debug("提取关节名称失败: %s", e)

        # 初始化起始时间
        if current_action_start_time is None:
            current_action_start_time = time.time()

        # 获取当前动作定义
        current_action = ACTION_SEQUENCE[sequence_index % len(ACTION_SEQUENCE)]
        action_duration = current_action["duration_sec"]
        action_name = current_action["name"]

        # 检查是否需要切换到下一个动作
        elapsed = time.time() - current_action_start_time

        # 检查是否是最后一个动作
        if sequence_index >= len(ACTION_SEQUENCE) - 1:
            # 最后动作：持续发送直到超过duration + 缓冲时间
            buffer_time = 0.5  # 额外0.5秒缓冲
            if elapsed >= action_duration + buffer_time:
                # 动作序列执行完毕，但不结束程序，只是停止发送动作
                if not sequence_finished:
                    logger.info("[%s] 动作序列执行完毕，继续运行但不发送动作", adapter_name)
                    sequence_finished = True
                # 不再发送动作，直接返回
                return

            # 继续发送最后一个动作（不需要切换）
            pass
        elif elapsed >= action_duration:
            # 非最后动作：检查是否需要切换
            next_sequence_index = sequence_index + 1
            if next_sequence_index >= len(ACTION_SEQUENCE):
                # 理论上不应该到达这里（最后动作已经在上面处理）
                logger.warning("[%s] 意外到达序列末尾，强制结束", adapter_name)
                sequence_finished = True
                return
            else:
                # 切换到下一个动作
                sequence_index = next_sequence_index
                current_action_start_time = time.time()
                current_action = ACTION_SEQUENCE[sequence_index % len(ACTION_SEQUENCE)]
                action_duration = current_action["duration_sec"]
                action_name = current_action["name"]
                logger.info("[%s] 切换到动作: %s", adapter_name, action_name)

        # 如果动作序列已执行完毕，不再发送动作
        if sequence_finished:
            return

        # 构建并发送动作
        action, actual_chunk_size = build_action_from_sequence(
            now_ms=int(time.time() * 1000),
            joint_names=joint_names,
            default_joint_names=action_default_joint_names,
            sequence_index=sequence_index,
        )

        # 发布 action 到目标设备的 topic
        # 注意：必须使用 target_device_id 而不是 session.device_id
        # 因为 session.device_id 可能是 cloud adapter 自己的 ID
        action_topic = f"{client.project_id}/{target_device_id}/inference/actions"
        pb_msg = action.to_protobuf()
        payload = pb_msg.SerializeToString()
        client.transport.publish(action_topic, payload)

        logger.info(
            "[%s] 发送 Action 到 %s: action=%s, chunk_size=%d, joints=%d",
            adapter_name,
            action_topic,
            action_name,
            actual_chunk_size,
            len(action.joint_states.names),
        )

    client.subscribe_observations(on_observation, target_device_id=target_device_id)
    logger.info(
        "[%s] 已订阅: %s/%s/inference/observations",
        adapter_name,
        args.project_id if args.project_id else "<bundle>",
        target_device_id,
    )
    logger.info("[%s] 等待 %s (Ctrl+C 停止)...", adapter_name, waiting_message_suffix)

    start = time.time()
    try:
        while True:
            # 检查是否超过指定的运行时间（只在动作序列未执行完毕时检查）
            if not sequence_finished and args.duration > 0 and time.time() - start >= args.duration:
                logger.info("[%s] 运行时间到达，停止", adapter_name)
                break
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("[%s] 被用户中断", adapter_name)
    finally:
        client.close()
        logger.info("[%s] 已关闭", adapter_name)


def parse_args() -> argparse.Namespace:
    """解析命令行参数."""
    parser = build_parser(
        description="Q25 Ultra 云端适配器: 订阅 observations 并发布预制 actions",
        default_project_id=DEFAULT_PROJECT_ID,
        default_device_id=DEFAULT_DEVICE_ID,
        default_client_id=DEFAULT_CLIENT_ID,
        help_project_id="项目 ID",
        help_device_id="目标设备 ID",
        help_client_id="客户端 ID",
        help_duration="运行时间 (秒), 0 表示永久运行",
    )
    return parser.parse_args()


def main() -> None:
    """主函数."""
    args = parse_args()
    run_cloud_adapter(
        args=args,
        adapter_name=ADAPTER_NAME,
        action_default_joint_names=Q25_JOINT_NAMES,
        observation_fallback_joint_names=[],
        waiting_message_suffix="Q25 observations",
        chunk_size=args.chunk_size,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    main()
