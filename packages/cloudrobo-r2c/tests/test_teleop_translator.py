"""Tests for the teleop frame → device command translation (plan B).

``teleop/actions`` 上的 ``TeleopFrame`` 由 ``device_translator`` 转换为硬件
适配器 ``send_action`` 接受的平铺字段（方案 B，复用 send_action，零新增
适配器接口）。覆盖：

- 函数式默认转换（无配置回退路径）；
- ``ConfigurableDeviceTranslator._teleop_to_device_mapper``（与
  ``r2c_to_device`` 同构的配置段）；
- 左右臂分开：只出现一侧时只下发该侧；
- 启动校验（``collect_mapping_config_errors`` 覆盖新段）；
- AgiBot G01 真机/mock 配置加载与 mock 端到端。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
import yaml
from cloudrobo_r2c.common.models import TeleopFrame

# teleop.proto 生成的 pb2 由构建命令编译到 common/models/generated，不入库；
# 涉及 protobuf 编解码的用例在 pb2 缺失时整体 skip（与 test_teleop_tools 一致）。
try:
    from cloudrobo_r2c.common.models.generated import teleop_pb2  # type: ignore

    _PB2_OK = teleop_pb2 is not None
except Exception:  # pragma: no cover - pb2 not built yet
    _PB2_OK = False

pytestmark = pytest.mark.skipif(not _PB2_OK, reason="teleop_pb2 not generated")

REAL_CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "cloudrobo_r2c"
    / "config"
    / "robot_agibot_g01_config.yaml"
)
MOCK_CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "cloudrobo_r2c"
    / "config"
    / "robot_agibot_g01_mock_config.yaml"
)

LEFT_POSE = [0.42, 0.33, 1.07, 0.24, 0.75, -0.57, 0.24]
RIGHT_POSE = [0.44, -0.33, 1.02, -0.61, -0.19, -0.46, 0.62]

ACTION_FRAME = {
    "timestamp": 1794805799102,
    "operator_id": "quest3-operator",
    "session_id": "9acb6c88",
    "end_effector_poses": {
        "names": ["left", "right"],
        "pose": [LEFT_POSE, RIGHT_POSE],
    },
    "end_effector_states": {
        "names": ["left", "right"],
        "position": [0.2, 0.8],
    },
}

LEFT_ONLY_FRAME = {
    "timestamp": 1794805799103,
    "end_effector_poses": {"names": ["left"], "pose": [LEFT_POSE]},
    "end_effector_states": {"names": ["left"], "position": [0.35]},
}


def _load_yaml(path: Path) -> Dict[str, Any]:
    assert path.exists(), f"missing shipped config: {path}"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _translator(config: Dict[str, Any]):
    from cloudrobo_r2c.translators import ConfigurableDeviceTranslator

    return ConfigurableDeviceTranslator.from_config(config)


_CUSTOM_CONFIG = {
    "translator": {"type": "configurable"},
    "teleop_to_device": {
        "mappings": [
            {
                "target": "ee_left_target",
                "source": "end_effector_poses.left.pose",
                "use_assign_dotted": False,
                "required": False,
            },
            {
                "target": "teleop_command",
                "source": "command",
                "use_assign_dotted": False,
                "required": False,
            },
        ]
    },
}


# ---------------------------------------------------------------------------
# 函数式默认转换（无配置回退路径）
# ---------------------------------------------------------------------------


def test_proto_roundtrip_single_side_pose_map():
    """新 proto（end_effector_poses 为 map<hand, pose>）单侧 round-trip。"""
    frame = TeleopFrame.from_dict(LEFT_ONLY_FRAME)
    back = TeleopFrame.from_protobuf(frame.serialize())
    assert back.end_effector_poses.names == ["left"]
    assert list(back.end_effector_poses.pose[0]) == pytest.approx(LEFT_POSE)
    assert back.end_effector_states.names == ["left"]
    assert list(back.end_effector_states.position) == pytest.approx([0.35])


def test_from_dict_accepts_map_pose_shape():
    """from_dict 同时接受 proto map 形态（{"pose": {hand: pose}}）。"""
    frame = TeleopFrame.from_dict(
        {
            "timestamp": 1,
            "end_effector_poses": {"pose": {"left": LEFT_POSE, "right": RIGHT_POSE}},
        }
    )
    assert frame.end_effector_poses.names == ["left", "right"]
    assert list(frame.end_effector_poses.pose[1]) == pytest.approx(RIGHT_POSE)


def test_functional_dual_side_pose_and_gripper():
    from cloudrobo_r2c.translators import teleop_to_device

    command = teleop_to_device(TeleopFrame.from_dict(ACTION_FRAME))
    assert command["left_pose_cmd_ee"] == pytest.approx(LEFT_POSE)
    assert command["right_pose_cmd_ee"] == pytest.approx(RIGHT_POSE)
    # 夹爪按侧分开输出
    assert command["left_gripper"] == pytest.approx(0.2)
    assert command["right_gripper"] == pytest.approx(0.8)
    assert "teleop_command" not in command


def test_functional_command_frame_no_device_action():
    from cloudrobo_r2c.translators import teleop_to_device

    start = TeleopFrame.from_dict({"timestamp": 1, "start": {}})
    assert teleop_to_device(start) == {"teleop_command": "start"}


def test_functional_single_side_only_emits_that_side():
    """只握左手：只输出左侧字段，右侧完全不下发（不是补齐、不是猜测）。"""
    from cloudrobo_r2c.translators import teleop_to_device

    command = teleop_to_device(TeleopFrame.from_dict(LEFT_ONLY_FRAME))
    assert command["left_pose_cmd_ee"] == pytest.approx(LEFT_POSE)
    assert command["left_gripper"] == pytest.approx(0.35)
    assert "right_pose_cmd_ee" not in command
    assert "right_gripper" not in command
    assert "gripper_cmd_pos" not in command


def test_functional_right_only():
    from cloudrobo_r2c.translators import teleop_to_device

    frame = TeleopFrame.from_dict(
        {"timestamp": 1, "end_effector_states": {"names": ["right"], "position": [0.9]}}
    )
    assert teleop_to_device(frame) == {"right_gripper": pytest.approx(0.9)}


def test_functional_empty_action_frame_is_noop():
    from cloudrobo_r2c.translators import teleop_to_device

    frame = TeleopFrame.from_dict({"timestamp": 1, "operator_id": "op"})
    assert teleop_to_device(frame) == {}


# ---------------------------------------------------------------------------
# 配置驱动映射（teleop_to_device.mappings，与 r2c_to_device 同构）
# ---------------------------------------------------------------------------


def test_configured_mapper_maps_by_rules():
    translator = _translator(_CUSTOM_CONFIG)
    frame = TeleopFrame.from_dict(ACTION_FRAME)
    command = translator.teleop_to_device(frame)
    assert command["ee_left_target"] == pytest.approx(LEFT_POSE)
    # 动作帧无 command（None 缺省）→ required: false 规则跳过
    assert "teleop_command" not in command

    start = TeleopFrame.from_dict({"timestamp": 1, "start": {}})
    assert translator.teleop_to_device(start)["teleop_command"] == "start"


def test_configured_mapper_single_side():
    """配置路径同样按侧：只出左手的帧不产生右侧目标键。"""
    config = {
        "translator": {"type": "configurable"},
        "teleop_to_device": {
            "mappings": [
                {
                    "target": "left_gripper",
                    "source": "end_effector_states.left.position",
                    "use_assign_dotted": False,
                    "required": False,
                },
                {
                    "target": "right_gripper",
                    "source": "end_effector_states.right.position",
                    "use_assign_dotted": False,
                    "required": False,
                },
            ]
        },
    }
    translator = _translator(config)
    assert translator.teleop_to_device(TeleopFrame.from_dict(LEFT_ONLY_FRAME)) == {
        "left_gripper": pytest.approx(0.35)
    }
    right_only = TeleopFrame.from_dict(
        {"timestamp": 1, "end_effector_states": {"names": ["right"], "position": [0.6]}}
    )
    assert translator.teleop_to_device(right_only) == {"right_gripper": pytest.approx(0.6)}


def test_mapper_absent_without_section():
    translator = _translator({"translator": {"type": "configurable"}})
    assert translator._teleop_to_device_mapper is None
    assert _translator({"teleop_to_device": {}})._teleop_to_device_mapper is None


def test_interface_requires_teleop_to_device():
    """IDeviceTranslator 与 r2c_to_device 对称：teleop_to_device 是必需方法。"""
    from cloudrobo_r2c.core.interfaces import IDeviceTranslator

    class _LegacyOnly(IDeviceTranslator):
        def device_to_r2c(self, raw_device_observation):
            return None

        def r2c_to_device(self, action_step):
            return {}

    with pytest.raises(TypeError):
        _LegacyOnly()

    translator = _translator({"translator": {"type": "configurable"}})
    assert isinstance(translator, IDeviceTranslator)
    assert callable(translator.teleop_to_device)


def test_build_teleop_to_device_mapper_helper():
    from cloudrobo_r2c.translators import ConfigurableDeviceTranslator

    assert ConfigurableDeviceTranslator._build_teleop_to_device_mapper({}) is None
    mapper = ConfigurableDeviceTranslator._build_teleop_to_device_mapper(_CUSTOM_CONFIG)
    assert mapper is not None


def test_extensions_decoded_for_rules():
    # extensions 值在映射前先 ext_decode，键按点分组可点路径查找
    from cloudrobo_r2c.translators.device_translator import _teleop_frame_to_mapping

    config = {
        "translator": {"type": "configurable"},
        "teleop_to_device": {
            "mappings": [
                {
                    "target": "left_stick_x",
                    "source": "extensions.left.stick.x",
                    "use_assign_dotted": False,
                    "required": False,
                },
            ]
        },
    }
    translator = _translator(config)
    frame = TeleopFrame.from_dict({"timestamp": 1, "extensions": {"left.stick.x": 0.5}})
    assert translator.teleop_to_device(frame)["left_stick_x"] == pytest.approx(0.5)
    assert _teleop_frame_to_mapping(frame)["extensions"]["left"]["stick"]["x"] == pytest.approx(0.5)


def test_invalid_teleop_mapping_rule_fails_startup_validation():
    from cloudrobo_r2c.core.config_mapper import collect_mapping_config_errors

    bad = {"teleop_to_device": {"mappings": [{"target": "x"}]}}  # 无 source/default
    errors = collect_mapping_config_errors(bad)
    assert errors and "teleop_to_device" in errors[0]
    # 缺失段合法（可选段）
    assert collect_mapping_config_errors({}) == []


# ---------------------------------------------------------------------------
# AgiBot G01 配置（真机 + mock）加载与 mock 端到端
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config_path",
    [REAL_CONFIG_PATH, MOCK_CONFIG_PATH],
    ids=["real", "mock"],
)
def test_agibot_config_teleop_section_loads_and_maps(config_path):
    from cloudrobo_r2c.core.config_mapper import collect_mapping_config_errors

    config = _load_yaml(config_path)
    assert collect_mapping_config_errors(config) == []
    translator = _translator(config)
    assert translator._teleop_to_device_mapper is not None

    command = translator.teleop_to_device(TeleopFrame.from_dict(ACTION_FRAME))
    assert command["left_pose_cmd_ee"] == pytest.approx(LEFT_POSE)
    assert command["right_pose_cmd_ee"] == pytest.approx(RIGHT_POSE)
    assert command["left_gripper"] == pytest.approx(0.2)
    assert command["right_gripper"] == pytest.approx(0.8)

    # 单侧帧：只出该侧目标键（另一侧保持不动）
    single = translator.teleop_to_device(TeleopFrame.from_dict(LEFT_ONLY_FRAME))
    assert single["left_pose_cmd_ee"] == pytest.approx(LEFT_POSE)
    assert single["left_gripper"] == pytest.approx(0.35)
    assert "right_pose_cmd_ee" not in single
    assert "right_gripper" not in single

    # 命令帧：只输出会话字段，不驱动设备
    start = translator.teleop_to_device(TeleopFrame.from_dict({"timestamp": 1, "start": {}}))
    assert start == {"teleop_command": "start"}


def _mock_adapter(config: Dict[str, Any]):
    from cloudrobo_r2c.robots.agibot_g01.agibot_g01_mock_adapter import (
        create_agibot_g01_mock_adapter,
    )

    adapter = create_agibot_g01_mock_adapter(config.get("hardware", {}).get("config", {}))
    adapter.connect()
    return adapter


def test_agibot_mock_end_to_end_teleop_to_mock_state():
    config = _load_yaml(MOCK_CONFIG_PATH)
    translator = _translator(config)
    adapter = _mock_adapter(config)

    before = adapter.get_observation()
    command = translator.teleop_to_device(TeleopFrame.from_dict(ACTION_FRAME))
    adapter.send_action(command)

    obs = adapter.get_observation()
    assert list(before["ee_poses_data"]["pose"][0]) != pytest.approx(LEFT_POSE)
    assert list(obs["ee_poses_data"]["pose"][0]) == pytest.approx(LEFT_POSE)
    assert list(obs["gripper_positions"]) == pytest.approx([0.2, 0.8])


def test_agibot_mock_teleop_command_frame_does_not_move_device():
    config = _load_yaml(MOCK_CONFIG_PATH)
    translator = _translator(config)
    adapter = _mock_adapter(config)

    before = adapter.get_observation()
    start = TeleopFrame.from_dict({"timestamp": 1, "start": {}})
    assert translator.teleop_to_device(start) == {"teleop_command": "start"}
    # command 帧只含会话字段，直接下发给适配器不会改变任何状态
    obs = adapter.get_observation()
    assert list(obs["gripper_positions"]) == pytest.approx(list(before["gripper_positions"]))


def test_agibot_mock_end_to_end_single_side_keeps_other_side():
    """只握左手：左侧更新、右侧保持当前值不动（遥操单手握持核心语义）。"""
    config = _load_yaml(MOCK_CONFIG_PATH)
    translator = _translator(config)
    adapter = _mock_adapter(config)

    before = adapter.get_observation()
    before_gripper = list(before["gripper_positions"])
    before_right_pose = list(before["ee_poses_data"]["pose"][1])
    before_arm = list(before["joint_positions"])

    command = translator.teleop_to_device(TeleopFrame.from_dict(LEFT_ONLY_FRAME))
    assert "right_pose_cmd_ee" not in command
    assert "right_gripper" not in command
    adapter.send_action(command)

    obs = adapter.get_observation()
    # 左侧更新
    assert list(obs["ee_poses_data"]["pose"][0]) == pytest.approx(LEFT_POSE)
    assert obs["gripper_positions"][0] == pytest.approx(0.35)
    # 右侧保持不动（位姿与夹爪均未下发）
    assert list(obs["ee_poses_data"]["pose"][1]) == pytest.approx(before_right_pose)
    assert obs["gripper_positions"][1] == pytest.approx(before_gripper[1])
    # 关节未在帧内 → 完全不变
    assert list(obs["joint_positions"]) == pytest.approx(before_arm)


def test_agibot_mock_single_side_joints_keep_other_arm():
    """只下发左臂 7 关节：右臂保持当前关节值（不补零）。"""
    config = _load_yaml(MOCK_CONFIG_PATH)
    adapter = _mock_adapter(config)

    before = list(adapter.get_observation()["joint_positions"])
    target_left = [0.5] * 7
    adapter.send_action(
        {
            "joint_names": [f"left_arm_joint{i}" for i in range(1, 8)],
            "arm_cmd_joint_pos": target_left,
        }
    )
    after = list(adapter.get_observation()["joint_positions"])
    assert after[:7] == pytest.approx(target_left)
    # 右臂保持原值（而不是被补零）
    assert after[7:] == pytest.approx(before[7:])
