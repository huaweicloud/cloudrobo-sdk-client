"""Field-number stability and round-trip tests for current_state.proto.

``state/current`` 由云端与其他消费者共同解析，字段号一旦重排会让既有解析端
读不到末端等字段（现场出现过"state/current 没有末端了"）。这里锁定编号：
location=3 / joint_states=4 / end_effector_states=5 / end_effector_poses=6，
新增的 orientation 用未占用的 7（声明在 location 之前）。
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


def test_current_state_field_numbers_are_stable():
    numbers = {f.name: f.number for f in current_state_pb2.CurrentState.DESCRIPTOR.fields}
    assert numbers["source"] == 1
    assert numbers["timestamp"] == 2
    # 既有字段号必须保持不变（wire 兼容）
    assert numbers["location"] == 3
    assert numbers["joint_states"] == 4
    assert numbers["end_effector_states"] == 5
    assert numbers["end_effector_poses"] == 6
    # 新增 orientation 用未占用编号
    assert numbers["orientation"] == 7


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
