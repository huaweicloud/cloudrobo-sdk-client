"""Tests for teleop.proto integration into the custom-topic tools.

teleop.proto（teleop topic 唯一 proto）经 ``TeleopFrame`` wrapper 接入
tools/custom_topic_listener（AUTO_WRAPPERS 自动判别）与
custom_topic_subscriber / custom_topic_publisher（--topic-proto 指定）。
pb2 由构建命令编译到 common/models/generated，不入库；本测试在 pb2
缺失时整体 skip。

proto 要点（2026-11-16 最新版）：oneof 命令为 start/pause/resume/finish/quit
（quit 回归，四个生命周期载荷均为空消息）；input_mode 提升为帧级字段
（field 14），随 start 设置并在后续帧继续携带；原始手柄输入不再有平铺
raw_inputs（field 12 reserved），改为 ``map<string, ExtensionValue>
extensions = 13``，键名按 ``<hand>.btn.primary`` / ``<hand>.btn.thumbstick``
/ ``<hand>.stick.x/y`` 分组。位姿/触发值/关节用 teleop 专属消息
（TeleopEndEffectorPoses map / TeleopStep），不复用 observation.proto。
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
    "extensions": {
        "left.btn.primary": True,
        "left.stick.y": -0.5,
        "right.btn.thumbstick": True,
        "right.stick.x": 0.25,
    },
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
    # 标量便捷写法经 ExtensionValue 往返
    assert dec.extensions["left.btn.primary"].ext_decode() is True
    assert dec.extensions["left.stick.y"].ext_decode() == pytest.approx(-0.5)
    assert dec.extensions["right.btn.thumbstick"].ext_decode() is True
    assert dec.extensions["right.stick.x"].ext_decode() == pytest.approx(0.25)
    # 语义往返（extensions 是 proto3 map，键序不保证字节级一致）
    assert TeleopFrame.from_protobuf(payload).to_dict() == dec.to_dict()


def test_action_frame_without_extensions_byte_stable():
    # 不含 map 字段的帧保持 byte-stable（listener 判别依赖重序列化等长）
    data = {k: v for k, v in ACTION_DATA.items() if k != "extensions"}
    payload = TeleopFrame.from_dict(data).serialize()
    assert TeleopFrame.from_protobuf(payload).serialize() == payload


@pytest.mark.parametrize("command", ["pause", "resume", "finish"])
def test_empty_command_frames(command):
    payload = TeleopFrame.from_dict({"timestamp": 1, "operator_id": "op", command: {}}).serialize()
    assert TeleopFrame.from_protobuf(payload).command == command


def test_quit_command_present_again():
    # 2026-11-16 后期：TeleopQuit 回归（field 8 = quit，空载荷），
    # 与 start/pause/resume/finish 一样由 oneof 选择。
    from cloudrobo_r2c.common.models.wrappers.teleop import _COMMAND_FIELDS

    assert "quit" in _COMMAND_FIELDS
    assert hasattr(teleop_pb2.TeleopFrame(), "quit")
    payload = TeleopFrame.from_dict({"timestamp": 1, "operator_id": "op", "quit": {}}).serialize()
    assert TeleopFrame.from_protobuf(payload).command == "quit"


def test_input_mode_variants():
    # input_mode 为帧级字段（field 14），随 start 设置并在后续帧继续携带
    p = TeleopFrame.from_dict(
        {"timestamp": 1, "start": {}, "input_mode": "isomorphic"}
    ).serialize()
    assert TeleopFrame.from_protobuf(p).input_mode == "isomorphic"
    p = TeleopFrame.from_dict({"timestamp": 1, "start": {}}).serialize()
    assert TeleopFrame.from_protobuf(p).input_mode == "unspecified"
    # 后续帧（pause）同样携带 input_mode
    p = TeleopFrame.from_dict(
        {"timestamp": 1, "pause": {}, "input_mode": "vr"}
    ).serialize()
    assert TeleopFrame.from_protobuf(p).input_mode == "vr"


def test_invalid_input_mode_raises():
    with pytest.raises(ValueError, match="input_mode"):
        TeleopFrame.from_dict({"timestamp": 1, "start": {}, "input_mode": "bogus"}).serialize()


def test_removed_keys_fail_fast():
    # 平铺 raw_inputs 已从 proto 移除（field 12 reserved，改用 extensions）；
    # 输入中出现顶层 raw_inputs 即 fail-fast，提示按新协议构造。
    with pytest.raises(ValueError, match="removed"):
        TeleopFrame.from_dict(
            {"timestamp": 1, "operator_id": "op", "raw_inputs": {"primary_left": True}}
        ).serialize()


def test_extensions_scalar_and_explicit_forms():
    data = {
        "timestamp": 1,
        "extensions": {
            "left.btn.primary": True,                      # bool 便捷写法
            "left.stick.x": 0.5,                           # float 便捷写法
            "note": "hello",                               # str 便捷写法
            "blob": b"\x01\x02",                           # bytes 便捷写法
            "explicit": {"dtype": "INT64", "shape": [], "data": "0700000000000000"},
        },
    }
    payload = TeleopFrame.from_dict(data).serialize()
    dec = TeleopFrame.from_protobuf(payload)
    assert dec.extensions["left.btn.primary"].ext_decode() is True
    assert dec.extensions["left.stick.x"].ext_decode() == pytest.approx(0.5)
    assert dec.extensions["note"].ext_decode() == "hello"
    assert dec.extensions["blob"].dtype == "BYTES"
    assert dec.extensions["explicit"].ext_decode() == 7
    # 语义往返（map 键序不保证字节级一致）
    assert TeleopFrame.from_protobuf(payload).to_dict() == dec.to_dict()


def test_extensions_invalid_type_raises():
    with pytest.raises(ValueError, match="extensions"):
        TeleopFrame.from_dict({"timestamp": 1, "extensions": {"bad": [1, 2]}}).serialize()


# ── subscriber 链路：from_protobuf → to_dict → JSON ──────────────────


def test_subscriber_to_dict_json():
    payload = TeleopFrame.from_dict(ACTION_DATA).serialize()
    out = json.dumps(TeleopFrame.from_protobuf(payload).to_dict(), ensure_ascii=False)
    assert "end_effector_poses" in out
    assert "extensions" in out
    parsed = json.loads(out)
    assert parsed["command"] is None
    assert parsed["extensions"]["left.btn.primary"]["dtype"] == "BOOL"
    assert parsed["extensions"]["left.stick.y"]["shape"] == []


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


# ── RawInputs ↔ extensions 便捷互转 ──────────────────────────────────


def test_rawinputs_roundtrip_via_extensions():
    from cloudrobo_r2c.common.models.wrappers.teleop import RawInputs

    raw = RawInputs(primary_left=True, thumbstick_pressed_right=True, thumbstick_y_left=-0.5)
    extensions = raw.to_extensions()
    # 默认值不写入，保持帧精简
    assert set(extensions) == {"left.btn.primary", "right.btn.thumbstick", "left.stick.y"}
    back = RawInputs.from_extensions(extensions)
    assert back == raw


def test_rawinputs_in_frame_roundtrip():
    from cloudrobo_r2c.common.models.wrappers.teleop import RawInputs

    raw = RawInputs(primary_left=True)
    frame = TeleopFrame(timestamp=1, operator_id="op", extensions=raw.to_extensions())
    payload = frame.serialize()
    dec = TeleopFrame.from_protobuf(payload)
    assert dec.extensions["left.btn.primary"].ext_decode() is True
    assert RawInputs.from_extensions(dec.extensions).primary_left is True


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
    minimal_payload = TeleopFrame.from_dict(
        {"timestamp": 1700000000000, "operator_id": "op-1", "start": {}}
    ).serialize()
    timestamp_only = TeleopFrame.from_dict({"timestamp": 1700000000002}).serialize()
    assert _best_wrapper_name(cmd_payload) == "TeleopFrame"
    assert _best_wrapper_name(action_payload) == "TeleopFrame"
    assert _best_wrapper_name(minimal_payload) == "TeleopFrame"
    assert _best_wrapper_name(timestamp_only) == "TeleopFrame"


def test_listener_still_detects_observations_and_actions():
    from cloudrobo_r2c.common.models import Actions, Observations

    obs_payload = Observations(timestamp=1700000000000, task="demo").serialize()
    act_payload = Actions(timestamp=1700000000000).serialize()
    assert _best_wrapper_name(obs_payload) == "Observations"
    assert _best_wrapper_name(act_payload) == "Actions"


# ── 话题名后缀预匹配（auto 模式先按话题锁定 wrapper）─────────────────


@pytest.mark.parametrize(
    "topic,expected",
    [
        ("r2c/local/teleop/actions", "TeleopFrame"),
        ("r2c/local/teleop/commands", "TeleopFrame"),
        ("r2c/local/inference/observations", "Observations"),
        ("r2c/local/actions", "Actions"),
        ("r2c/local/command", "CommandMessage"),
        ("r2c/local/heartbeat", "Heartbeat"),
        ("r2c/local/event", "EventMessage"),
        ("r2c/local/state/current", None),
        ("r2c/local", None),
        ("", None),
    ],
)
def test_topic_preferred_wrapper_name(topic, expected):
    from cloudrobo_r2c.tools.custom_topic_listener import topic_preferred_wrapper_name

    assert topic_preferred_wrapper_name(topic) == expected


def test_prematch_maps_are_resolvable():
    from cloudrobo_r2c.tools.custom_topic_listener import (
        AUTO_WRAPPERS,
        TOPIC_SUFFIX_WRAPPERS,
        resolve_wrappers,
    )

    names = {n for n, _ in resolve_wrappers("auto")}
    for topic_key, wrapper_name in TOPIC_SUFFIX_WRAPPERS.items():
        assert wrapper_name in names, (
            f"pre-match entry {topic_key!r} → {wrapper_name!r} is not in AUTO_WRAPPERS"
        )
    assert any(p.endswith(":TeleopFrame") for p in AUTO_WRAPPERS)


def test_prematch_decodes_colliding_payload():
    # 预匹配的核心价值：载荷字段撞车时也能按话题正确解码。用一条会被
    # 试解析误判的小帧（仅 timestamp，试解析下与其它消息同分）验证：
    # 话题是 teleop/actions 时锁定 TeleopFrame 而非按得分抢。
    from cloudrobo_r2c.tools.custom_topic_listener import topic_preferred_wrapper_name

    payload = TeleopFrame.from_dict({"timestamp": 1700000000002}).serialize()
    assert topic_preferred_wrapper_name("r2c/local/teleop/actions") == "TeleopFrame"
    obj = TeleopFrame.from_protobuf(payload)
    assert obj.timestamp == 1700000000002



# ── pb2 直引用（decode 模式自动适配 Pb2MessageAdapter）───────────────


def test_pb2_direct_reference_still_resolves():
    adapter = resolve_proto_class(
        "cloudrobo_r2c.common.models.generated.teleop_pb2:TeleopFrame", "t", mode="decode"
    )
    view = adapter.from_protobuf(
        TeleopFrame.from_dict({"timestamp": 42, "operator_id": "pb2-direct"}).serialize()
    )
    assert view.to_dict()["operator_id"] == "pb2-direct"
