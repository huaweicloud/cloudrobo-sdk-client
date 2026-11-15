"""Tests for teleop.proto integration into the custom-topic tools.

teleop.proto（未跟踪新文件，用户手放的 teleop topic 唯一 proto）经
``TeleopFrame`` wrapper 接入 tools/custom_topic_listener（AUTO_WRAPPERS
自动判别）与 custom_topic_subscriber / custom_topic_publisher
（--topic-proto 指定）。pb2 由构建命令编译到 common/models/generated，
不入库；本测试在 pb2 缺失时整体 skip。
"""

from __future__ import annotations

import json

import pytest

from cloudrobo_r2c.common.models import TeleopFrame
from cloudrobo_r2c.common.proto_registry import resolve_proto_class

teleop_pb2 = None
try:
    from cloudrobo_r2c.common.models.generated import teleop_pb2  # type: ignore
except Exception:  # pragma: no cover - pb2 not built yet
    teleop_pb2 = None

pytestmark = pytest.mark.skipif(teleop_pb2 is None, reason="teleop_pb2 not generated")

ACTION_DATA = {
    "timestamp": 1700000000001,
    "operator_id": "op-1",
    "end_effector_poses": {
        "names": ["left", "right"],
        "pose": [
            [0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 1.0],
            [0.4, 0.5, 0.6, 0.0, 0.0, 0.0, 1.0],
        ],
    },
    "end_effector_states": {
        "names": ["left"],
        "position": [0.7],
        "force": [1.5],
    },
    "raw_inputs": {"primary_left": True, "thumbstick_y_left": -0.5},
}


# ── 统一解析（publisher/subscriber/listener 共用 resolve_proto_class）──


def test_resolve_wrapper_mode():
    cls = resolve_proto_class("cloudrobo_r2c.common.models:TeleopFrame", "t", mode="wrapper")
    assert cls is TeleopFrame


def test_resolve_decode_mode():
    cls = resolve_proto_class("cloudrobo_r2c.common.models:TeleopFrame", "t", mode="decode")
    assert cls is TeleopFrame


# ── publisher 链路：from_dict → serialize ────────────────────────────


def test_publisher_command_frame_roundtrip():
    data = {
        "timestamp": 1700000000000,
        "operator_id": "op-1",
        "session_id": "s-abc",
        "start": {"input_mode": "vr"},
    }
    payload = TeleopFrame.from_dict(data).serialize()
    assert payload

    dec = TeleopFrame.from_protobuf(payload)
    assert dec.command == "start"
    assert dec.input_mode == "vr"
    assert dec.operator_id == "op-1"
    assert dec.session_id == "s-abc"
    # byte-stable roundtrip（listener 判别依赖重序列化等长）
    assert TeleopFrame.from_protobuf(payload).serialize() == payload


def test_publisher_action_frame_roundtrip():
    payload = TeleopFrame.from_dict(ACTION_DATA).serialize()
    dec = TeleopFrame.from_protobuf(payload)
    assert dec.command is None
    assert dec.end_effector_poses.names == ["left", "right"]
    assert [list(p) for p in dec.end_effector_poses.pose] == ACTION_DATA["end_effector_poses"]["pose"]
    assert dec.end_effector_states.position == [0.7]
    assert dec.raw_inputs.primary_left is True
    assert dec.raw_inputs.thumbstick_y_left == -0.5
    assert TeleopFrame.from_protobuf(payload).serialize() == payload


@pytest.mark.parametrize("command", ["pause", "resume", "finish", "quit"])
def test_empty_command_frames(command):
    payload = TeleopFrame.from_dict({"timestamp": 1, "operator_id": "op", command: {}}).serialize()
    assert TeleopFrame.from_protobuf(payload).command == command


def test_input_mode_variants():
    p = TeleopFrame.from_dict({"timestamp": 1, "start": {"input_mode": "isomorphic"}}).serialize()
    assert TeleopFrame.from_protobuf(p).input_mode == "isomorphic"
    p = TeleopFrame.from_dict({"timestamp": 1, "start": {}}).serialize()
    assert TeleopFrame.from_protobuf(p).input_mode == "unspecified"


def test_invalid_input_mode_raises():
    with pytest.raises(ValueError, match="input_mode"):
        TeleopFrame.from_dict({"timestamp": 1, "start": {"input_mode": "bogus"}}).serialize()


# ── subscriber 链路：from_protobuf → to_dict → JSON ──────────────────


def test_subscriber_to_dict_json():
    payload = TeleopFrame.from_dict(ACTION_DATA).serialize()
    out = json.dumps(TeleopFrame.from_protobuf(payload).to_dict(), ensure_ascii=False)
    assert "end_effector_poses" in out
    assert json.loads(out)["command"] is None


def test_joint_states_uses_torque_field():
    # teleop.proto 的 JointStates 来自 observation.proto（names/position/
    # velocity/torque），wrapper 统一复用 EndEffectorStates dataclass，
    # torque 承载力矩列表。
    payload = TeleopFrame.from_dict(
        {"timestamp": 1, "joint_states": {"names": ["j1"], "position": [0.1], "torque": [2.5]}}
    ).serialize()
    dec = TeleopFrame.from_protobuf(payload)
    assert dec.joint_states.names == ["j1"]
    assert dec.joint_states.position == [0.1]
    assert dec.joint_states.force == [2.5]  # dataclass 复用：force 即 torque 载体


# ── listener 链路：AUTO_WRAPPERS 自动判别 ────────────────────────────


def test_auto_wrappers_include_teleop_frame():
    from cloudrobo_r2c.tools.custom_topic_listener import AUTO_WRAPPERS, resolve_wrappers

    assert any(p.endswith(":TeleopFrame") for p in AUTO_WRAPPERS)
    names = [n for n, _ in resolve_wrappers("auto")]
    assert "TeleopFrame" in names


def _best_wrapper_name(payload: bytes):
    from cloudrobo_r2c.tools.custom_topic_listener import _score, resolve_wrappers

    best = None
    for name, cls in resolve_wrappers("auto"):
        try:
            obj = cls.from_protobuf(payload)
            reser = obj.serialize()
        except Exception:
            continue
        cand = (abs(len(reser) - len(payload)), -_score(obj), name)
        if best is None or cand[:2] < best[:2]:
            best = cand
    return best[2] if best else None


def test_listener_auto_detects_teleop_frames():
    cmd_payload = TeleopFrame.from_dict(
        {"timestamp": 1700000000000, "operator_id": "op-1", "session_id": "s", "start": {"input_mode": "vr"}}
    ).serialize()
    action_payload = TeleopFrame.from_dict(ACTION_DATA).serialize()
    assert _best_wrapper_name(cmd_payload) == "TeleopFrame"
    assert _best_wrapper_name(action_payload) == "TeleopFrame"


def test_listener_still_detects_observations_and_actions():
    from cloudrobo_r2c.common.models import Actions, Observations

    obs_payload = Observations(timestamp=1700000000000, task="demo").serialize()
    act_payload = Actions(timestamp=1700000000000).serialize()
    assert _best_wrapper_name(obs_payload) == "Observations"
    assert _best_wrapper_name(act_payload) == "Actions"


# ── pb2 直引用（decode 模式自动适配 Pb2MessageAdapter）───────────────


def test_pb2_direct_reference_still_resolves():
    adapter = resolve_proto_class(
        "cloudrobo_r2c.common.models.generated.teleop_pb2:TeleopFrame", "t", mode="decode"
    )
    view = adapter.from_protobuf(
        TeleopFrame.from_dict({"timestamp": 42, "operator_id": "pb2-direct"}).serialize()
    )
    assert view.to_dict()["operator_id"] == "pb2-direct"
