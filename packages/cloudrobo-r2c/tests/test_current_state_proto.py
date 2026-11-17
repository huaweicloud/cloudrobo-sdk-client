"""Field-number tests for current_state.proto.

按 2026-11-17 决策：`orientation` 以字段插入方式占 3（排在 location 之前），
`location`/`joint_states`/`end_effector_states`/`end_effector_poses` 顺移为
4/5/6/7。两端（发布与解析）须使用同一份 proto；本测试锁定该编号约定。
"""

from __future__ import annotations

import pytest
from cloudrobo_r2c.common.models import RunTimeMessage

try:
    from cloudrobo_r2c.common.models.generated import current_state_pb2  # type: ignore

    _PB2_OK = current_state_pb2 is not None
except Exception:  # pragma: no cover - pb2 not built yet
    _PB2_OK = False

pytestmark = pytest.mark.skipif(not _PB2_OK, reason="current_state_pb2 not generated")

LEFT_POSE = [0.42, 0.33, 1.07, 0.24, 0.75, -0.57, 0.24]
RIGHT_POSE = [0.44, -0.33, 1.02, -0.61, -0.19, -0.46, 0.62]


def test_current_state_field_numbers_orientation_first():
    numbers = {f.name: f.number for f in current_state_pb2.CurrentState.DESCRIPTOR.fields}
    assert numbers["source"] == 1
    assert numbers["timestamp"] == 2
    # orientation 插在 location 之前（field 3），其余字段顺移
    assert numbers["orientation"] == 3
    assert numbers["location"] == 4
    assert numbers["joint_states"] == 5
    assert numbers["end_effector_states"] == 6
    assert numbers["end_effector_poses"] == 7


def test_runtime_message_roundtrip_keeps_end_effector_poses():
    message = RunTimeMessage(
        timestamp=1,
        location="lab",
        orientation=90.0,
        end_effector_poses={
            "names": ["left", "right"],
            "pose": [LEFT_POSE, RIGHT_POSE],
        },
    )
    payload = message.to_protobuf().SerializeToString()
    back = RunTimeMessage.from_protobuf(payload)
    assert back.orientation == pytest.approx(90.0)
    assert back.location == "lab"
    assert list(back.end_effector_poses.names) == ["left", "right"]
    assert list(back.end_effector_poses.pose[0]) == pytest.approx(LEFT_POSE)
    assert list(back.end_effector_poses.pose[1]) == pytest.approx(RIGHT_POSE)


def test_state_current_frame_carries_end_effector_poses_end_to_end():
    """适配器 get_runtime → RunTimeMessage → protobuf → 解析，末端仍在。"""
    from cloudrobo_r2c.robots.agibot_g01.agibot_g01_mock_adapter import (
        create_agibot_g01_mock_adapter,
    )

    adapter = create_agibot_g01_mock_adapter({"camera_enabled": False})
    adapter.connect()
    frame = dict(adapter.get_runtime())
    assert "end_effector_poses" in frame, sorted(frame)
    message = RunTimeMessage.from_dict(frame)
    back = RunTimeMessage.from_protobuf(message.to_protobuf().SerializeToString())
    assert list(back.end_effector_poses.names) == ["left", "right"]
    assert len(back.end_effector_poses.pose) == 2
