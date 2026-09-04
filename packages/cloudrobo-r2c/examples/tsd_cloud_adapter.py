"""TSD 云侧联调示例。

这个脚本提供一个最小闭环：
1. 订阅机器人客户端上报的 observation
2. 构造标准 R2C action 并回发
3. 提供一个简单交互终端，用来验证标准关节动作链路

常用启动命令：

1. 自动模式（默认）：
   `python examples/tsd_cloud_adapter.py --project-id test --device-id tsd`
2. 手动模式：
   `python examples/tsd_cloud_adapter.py --project-id test --device-id tsd --interactive`
   `python examples/tsd_cloud_adapter.py --project-id test --device-id tsd --it`

配套的机器人客户端通常在另一个终端启动：

1. 真实机器人：
   `python -m cloudrobo_r2c.cloudroboclient --project-id test --device-id tsd --client-config config/client_config.yaml --robot-config config/robot_tsd_config.yaml`
2. Dummy:
   `python -m cloudrobo_r2c.cloudroboclient --project-id test --device-id tsd --client-config config/client_config.yaml --robot-config config/robot_tsd_dummy_config.yaml`

当前约束需要明确：
1. 标准 `Actions` 主链路当前稳定用于关节动作 `movj`
2. 当前交互模式只开放已经端到端打通的关节动作入口
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from _cloud_adapter_common import build_parser, normalize_for_logging  # noqa: E402

from cloudrobo_r2c import ClientConfig, R2CClient
from cloudrobo_r2c.common.models import Actions

logger = logging.getLogger(__name__)

ADAPTER_NAME = "TSDCloudAdapter"
DEFAULT_PROJECT_ID = "test"
DEFAULT_DEVICE_ID = "tsd"
DEFAULT_CLIENT_ID = "tsd-cloud-adapter"

TSD_STATE_NAMES = [
    "joint_1",
    "joint_2",
    "joint_3",
    "joint_4",
    "joint_5",
    "joint_6",
    "tcp_x",
    "tcp_y",
    "tcp_z",
    "tcp_a",
    "tcp_b",
    "tcp_c",
    "tcp_user_x",
    "tcp_user_y",
    "tcp_user_z",
    "tcp_user_a",
    "tcp_user_b",
    "tcp_user_c",
    "link",
    "enable",
    "alarm",
    "mode",
    "run_state",
    "in_pos",
    "remote",
    "cmd_queue",
    "speed",
    "uf_no",
    "tf_no",
    "rgm_state",
]

TSD_PREDEFINED_ACTIONS = [
    {"joints": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]},
    {"joints": [10.0, 20.0, -30.0, 0.0, 45.0, 0.0]},
    {"joints": [-15.0, 10.0, -20.0, 10.0, 30.0, -10.0]},
    {"joints": [5.0, -10.0, 25.0, -5.0, 50.0, 15.0]},
]


def build_action_payload(
    now_ms: int,
    joint_names: Sequence[str],
    joint_positions: List[float],
    chunk_size: int = 10,
) -> Actions:
    """构造标准 R2C Actions 载荷。"""
    while len(joint_positions) < len(joint_names):
        joint_positions.append(0.0)

    payload = {
        "timestamp": now_ms,
        "chunk_size": chunk_size,
        "joint_states": {
            "names": list(joint_names),
            "position": [joint_positions[: len(joint_names)]] * chunk_size,
        },
        "end_effector_poses": {},
        "end_effector_states": {
            "names": [],
            "position": [],
            "velocity": [],
            "torque": [],
        },
        "localization": {"odom_pose": [], "map_pose": []},
    }
    return Actions.from_dict(payload)


def print_help() -> None:
    """打印交互模式支持的命令。"""
    print(
        """
╔══════════════════════════════════════════════════════════════╗
║  TSD Cloud Adapter — Commands                               ║
╠══════════════════════════════════════════════════════════════╣
║  movj <j1> <j2> <j3> <j4> <j5> <j6>       Joint motion      ║
║  status                                     Show observation  ║
║  help                                       Show this help    ║
║  quit / exit                                Exit              ║
╚══════════════════════════════════════════════════════════════╝
"""
    )


def parse_command(command_text: str) -> Optional[Dict[str, Any]]:
    """解析一条交互命令。"""
    parts = command_text.strip().split()
    if not parts:
        return None

    command_name = parts[0].lower()
    if command_name in {"help", "h", "?"}:
        print_help()
        return None
    if command_name in {"quit", "exit", "q"}:
        raise SystemExit(0)
    if command_name == "status":
        return {"_local": "status"}

    if command_name == "movj":
        if len(parts) < 7:
            print("Usage: movj <j1> <j2> <j3> <j4> <j5> <j6>")
            return None
        try:
            values = [float(value) for value in parts[1:7]]
        except ValueError:
            print("Usage: movj <j1> <j2> <j3> <j4> <j5> <j6>")
            return None
        positions = values + [0.0] * (len(TSD_STATE_NAMES) - 6)
        return {"_action": True, "positions": positions, "label": f"movj {values}"}

    print(f"Unknown command: {command_name}. Type 'help' for available commands.")
    return None


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = build_parser(
        description="TSD cloud adapter: auto mode by default, optional interactive mode",
        default_project_id=DEFAULT_PROJECT_ID,
        default_device_id=DEFAULT_DEVICE_ID,
        default_client_id=DEFAULT_CLIENT_ID,
        help_project_id="Project ID",
        help_device_id="Target device ID",
        help_client_id="Client ID",
        help_duration="Running duration in seconds; 0 means continuous",
    )
    parser.add_argument(
        "--interactive",
        "--it",
        action="store_true",
        help="Interactive mode: type commands in terminal",
    )
    return parser.parse_args()


def run_interactive(client: R2CClient, device_id: str) -> None:
    """运行交互模式。"""
    print_help()
    latest_observation: Dict[str, Any] = {}

    def on_observation(observation: Any) -> None:
        nonlocal latest_observation
        latest_observation = normalize_for_logging(observation)

    client.subscribe_observations(on_observation, target_device_id=device_id)
    logger.info("[%s] Subscribed to observations. Waiting for data...", ADAPTER_NAME)

    time.sleep(2)

    while True:
        try:
            command_text = input("\n[TSD] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting...")
            break

        if not command_text:
            continue

        try:
            parsed = parse_command(command_text)
        except SystemExit:
            break

        if parsed is None:
            continue

        if parsed.get("_local") == "status":
            if latest_observation:
                print(json.dumps(latest_observation, indent=2, ensure_ascii=False))
            else:
                print("No observation received yet.")
            continue

        if parsed.get("_action"):
            action = build_action_payload(
                now_ms=int(time.time() * 1000),
                joint_names=TSD_STATE_NAMES,
                joint_positions=parsed["positions"],
                chunk_size=10,
            )
            client.publish_actions(action)
            print(f"[{ADAPTER_NAME}] Sent: {parsed['label']}")


def run_auto(client: R2CClient, device_id: str, duration: float) -> None:
    """运行默认自动模式。"""
    action_index = 0

    def on_observation(observation: Any) -> None:
        nonlocal action_index

        normalized = normalize_for_logging(observation)
        logger.info(
            "\n=== Received observation ===\n%s\n============================",
            json.dumps(normalized, ensure_ascii=False, indent=2),
        )

        predefined = TSD_PREDEFINED_ACTIONS[action_index % len(TSD_PREDEFINED_ACTIONS)]
        positions = list(predefined["joints"]) + [0.0] * (len(TSD_STATE_NAMES) - 6)
        action = build_action_payload(
            now_ms=int(time.time() * 1000),
            joint_names=TSD_STATE_NAMES,
            joint_positions=positions,
            chunk_size=10,
        )
        client.publish_actions(action)
        logger.info(
            "[%s] Sent action %d: joints=%s",
            ADAPTER_NAME,
            action_index + 1,
            predefined["joints"],
        )
        action_index += 1

    client.subscribe_observations(on_observation, target_device_id=device_id)
    logger.info("[%s] Auto mode. Waiting for observations...", ADAPTER_NAME)

    start_time = time.time()
    try:
        while True:
            if duration > 0 and time.time() - start_time >= duration:
                logger.info("[%s] Duration reached.", ADAPTER_NAME)
                break
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("[%s] Interrupted.", ADAPTER_NAME)


def main() -> None:
    """脚本入口。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = parse_args()
    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id,
        endpoint_role="listen",
        endpoints=["tcp/0.0.0.0:7447"],
        mode="peer",
    )
    logger.info(
        "[%s] Connecting: project=%s, device=%s, client=%s",
        ADAPTER_NAME,
        args.project_id,
        args.device_id,
        args.client_id,
    )
    client = R2CClient.connect(config)

    try:
        if args.interactive:
            run_interactive(client, args.device_id)
        else:
            run_auto(client, args.device_id, args.duration)
    finally:
        client.close()
        logger.info("[%s] Closed.", ADAPTER_NAME)


if __name__ == "__main__":
    main()
