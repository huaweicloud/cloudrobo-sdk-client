"""Teleop session gating tests for the SDK execution path (plan B).

验证设计 §3.3.1.2 的会话门控：

- RUNNING 时 ``inference/actions`` 不被消费（不执行、不缓存）；
- ``teleop/actions`` 动作帧仅在 RUNNING 且 ``session_id`` 匹配时驱动设备；
- pause 全停、resume 恢复、finish/quit 回到 IDLE（策略动作恢复执行）；
- 未声明 ``teleop_to_device`` 段的机器人不订阅 ``teleop/actions``。

使用 AgiBot G01 mock 适配器（tests/conftest.py 注入的 fake a2d_sdk）与
真实 teleop 配置，避免依赖真机与 GDK。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml
from cloudrobo_r2c.common.models import Actions, TeleopFrame

pytestmark = pytest.mark.skipif(
    TeleopFrame is None, reason="teleop wrapper unavailable"
)

CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "cloudrobo_r2c"
    / "config"
    / "robot_agibot_g01_mock_config.yaml"
)

LEFT_POSE = [0.42, 0.33, 1.07, 0.24, 0.75, -0.57, 0.24]
RIGHT_POSE = [0.44, -0.33, 1.02, -0.61, -0.19, -0.46, 0.62]


class _FakeTransport:
    def __init__(self) -> None:
        self.published: List[tuple] = []
        self.subscriptions: List[tuple] = []

    def publish(self, topic: str, payload: bytes) -> None:
        self.published.append((topic, payload))

    def subscribe(self, topic: str, handler: Any) -> None:
        self.subscriptions.append((topic, handler))


def _load_config() -> Dict[str, Any]:
    assert CONFIG_PATH.exists(), f"missing shipped config: {CONFIG_PATH}"
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def _actions_chunk(chunk_size: int = 2) -> Actions:
    return Actions.from_dict(
        {
            "timestamp": 1,
            "id": 1,
            "chunk_size": chunk_size,
            "joint_states": {
                "names": [],
                "position": [0.1] * 14,
                "velocity": [],
                "torque": [],
            },
            "end_effector_states": {
                "names": [],
                "position": [0.4, 0.6],
                "velocity": [],
                "torque": [],
            },
            "end_effector_poses": {},
            "localization": {"odom_pose": [], "map_pose": []},
            "extensions": {},
        }
    )


def _client(teleop_enabled: bool = True):
    from cloudrobo_r2c.client.session import R2CSession
    from cloudrobo_r2c.robots.agibot_g01.agibot_g01_mock_adapter import (
        create_agibot_g01_mock_adapter,
    )
    from cloudrobo_r2c.sync_client import SyncRobotClient
    from cloudrobo_r2c.translators import ConfigurableDeviceTranslator

    config = _load_config()
    adapter = create_agibot_g01_mock_adapter(config["hardware"]["config"])
    adapter.connect()
    translator = ConfigurableDeviceTranslator.from_config(config)
    session = R2CSession(
        transport=_FakeTransport(), client_id="test", project_id="p", device_id="d"
    )
    client = SyncRobotClient(
        session=session,
        hardware_adapter=adapter,
        translator=translator,
        target_fps=30,
        teleop_enabled=teleop_enabled,
    )
    return client, adapter, session


def _pose_of(adapter, side_index: int = 0) -> List[float]:
    return list(adapter.get_observation()["ee_poses_data"]["pose"][side_index])


def test_inference_actions_ignored_while_teleop_running():
    client, adapter, _ = _client()
    client._on_teleop_frame(
        TeleopFrame.from_dict(
            {"timestamp": 1, "session_id": "s-1", "start": {}, "input_mode": "vr"}
        )
    )
    assert client._teleop_session_state() == "running"

    client._enqueue_action_chunk(_actions_chunk())
    # 策略动作既不执行也不缓存
    assert client._action_queue.qsize() == 0
    assert client._teleop_dropped_inference == 1


def test_teleop_action_executes_only_when_running_with_matching_session():
    client, adapter, _ = _client()
    frame = TeleopFrame.from_dict(
        {
            "timestamp": 2,
            "session_id": "s-1",
            "end_effector_poses": {"names": ["left"], "pose": [LEFT_POSE]},
            "end_effector_states": {"names": ["left"], "position": [0.3]},
        }
    )
    before = _pose_of(adapter)
    # 未 start：动作帧丢弃
    client._on_teleop_frame(frame)
    assert adapter.get_observation()["ee_poses_data"]["pose"][0] == pytest.approx(
        before
    )
    assert client._teleop_dropped_actions == 1

    # start 后同 session_id：执行
    client._on_teleop_frame(
        TeleopFrame.from_dict({"timestamp": 1, "session_id": "s-1", "start": {}})
    )
    client._on_teleop_frame(frame)
    assert _pose_of(adapter) == pytest.approx(LEFT_POSE)
    assert adapter.get_observation()["gripper_positions"][0] == pytest.approx(0.3)

    # session_id 不匹配：丢弃
    other = TeleopFrame.from_dict(
        {
            "timestamp": 3,
            "session_id": "s-2",
            "end_effector_poses": {"names": ["left"], "pose": [RIGHT_POSE]},
        }
    )
    client._on_teleop_frame(other)
    assert _pose_of(adapter) == pytest.approx(LEFT_POSE)
    assert client._teleop_dropped_actions == 2


def test_pause_stops_and_finish_returns_to_idle():
    client, adapter, _ = _client()
    client._on_teleop_frame(
        TeleopFrame.from_dict({"timestamp": 1, "session_id": "s-1", "start": {}})
    )
    client._on_teleop_frame(
        TeleopFrame.from_dict({"timestamp": 2, "session_id": "s-1", "pause": {}})
    )
    assert client._teleop_session_state() == "paused"

    # PAUSED：动作帧不执行
    client._on_teleop_frame(
        TeleopFrame.from_dict(
            {
                "timestamp": 3,
                "session_id": "s-1",
                "end_effector_poses": {"names": ["right"], "pose": [RIGHT_POSE]},
            }
        )
    )
    assert _pose_of(adapter, 1) != pytest.approx(RIGHT_POSE)

    # resume 回到 RUNNING
    client._on_teleop_frame(
        TeleopFrame.from_dict({"timestamp": 4, "session_id": "s-1", "resume": {}})
    )
    assert client._teleop_session_state() == "running"

    # finish：回 IDLE，策略动作恢复消费
    client._on_teleop_frame(
        TeleopFrame.from_dict({"timestamp": 5, "session_id": "s-1", "finish": {}})
    )
    assert client._teleop_session_state() == "idle"
    client._enqueue_action_chunk(_actions_chunk())
    assert client._action_queue.qsize() == 2


def test_teleop_disabled_does_not_subscribe():
    client, _, session = _client(teleop_enabled=False)
    client.start()
    try:
        topics = [topic for topic, _ in session.transport.subscriptions]
        assert "p/d/teleop/actions" not in topics
        assert "p/d/inference/actions" in topics
    finally:
        client.stop()


def test_teleop_enabled_subscribes_topic():
    client, _, session = _client()
    client.start()
    try:
        topics = [topic for topic, _ in session.transport.subscriptions]
        assert "p/d/teleop/actions" in topics
    finally:
        client.stop()
