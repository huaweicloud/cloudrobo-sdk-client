"""Unit tests for the AgiBot G01 hardware adapter (fake ``a2d_sdk``).

The fake ``a2d_sdk`` modules are installed *before* the adapter is imported so
the client module's import-time guard binds the fakes — no GDK environment or
robot required.
"""

from __future__ import annotations

import struct
import sys
import types
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest
import yaml
from cloudrobo_r2c.robots.agibot_g01 import agibot_g01_client  # noqa: E402
from cloudrobo_r2c.robots.agibot_g01.agibot_g01_hardware_adapter import (  # noqa: E402
    ARM_DOF,
    GRIPPER_DOF,
    POSE_DOF,
    AgiBotG01HardwareAdapter,
    create_agibot_g01_adapter,
)

# ---------------------------------------------------------------------------
# Fake a2d_sdk：类定义移至 tests/fake_a2d_sdk.py，由 tests/conftest.py 在任何
# 测试模块导入前安装（agibot_g01_client 的导入时绑定与收集顺序无关）
# ---------------------------------------------------------------------------
from fake_a2d_sdk import (  # noqa: E402
    FakeCosineCamera,
    FakeRobotController,
    FakeRobotDds,
    reset_fake_instances,
)

CONFIG_PATH = (
    Path(agibot_g01_client.__file__).resolve().parents[2]
    / "config"
    / "robot_agibot_g01_config.yaml"
)


@pytest.fixture(autouse=True)
def _reset_fakes():
    reset_fake_instances()
    yield
    reset_fake_instances()


def _last_controller() -> FakeRobotController:
    assert FakeRobotController.instances, "FakeRobotController was never constructed"
    return FakeRobotController.instances[-1]


def _connect(config: Dict[str, Any] = None) -> AgiBotG01HardwareAdapter:
    adapter = create_agibot_g01_adapter(config or {})
    adapter.connect()
    return adapter


def _last_robot() -> FakeRobotDds:
    assert FakeRobotDds.instances, "FakeRobotDds was never constructed"
    return FakeRobotDds.instances[-1]


def _register_commands(adapter: AgiBotG01HardwareAdapter) -> None:
    """与工厂路径一致：按 ``hardware.config.commands`` 实例化已注册的命令类。"""
    from cloudrobo_r2c.robots.robot_factory import RobotFactory

    RobotFactory._register_adapter_commands(adapter, adapter._config)


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


def test_factory_creates_adapter_with_defaults():
    adapter = create_agibot_g01_adapter({})
    assert isinstance(adapter, AgiBotG01HardwareAdapter)
    assert not adapter.is_connected
    # 默认启用相机并映射到 R2C 键
    assert adapter._camera_enabled is True
    assert adapter._camera_key_map["head"] == "cam_high"


def test_connect_disconnect_lifecycle():
    adapter = _connect()
    assert adapter.is_connected
    assert _last_robot() is not None
    camera = FakeCosineCamera.instances[-1]
    assert camera.camera_group == ["head", "hand_left", "hand_right"]
    adapter.disconnect()
    assert not adapter.is_connected
    assert _last_robot().shutdown_count == 1
    assert camera.closed


def test_connect_failure_cleans_up():
    # 相机构造失败时，机器人连接应被回滚
    from cloudrobo_r2c.robots.agibot_g01 import agibot_g01_client as client_mod

    original = client_mod._CosineCamera

    def _boom(group):
        raise RuntimeError("camera unavailable")

    client_mod._CosineCamera = _boom
    try:
        adapter = create_agibot_g01_adapter({})
        with pytest.raises(RuntimeError, match="camera unavailable"):
            adapter.connect()
        assert not adapter.is_connected
        assert _last_robot().shutdown_count == 1
    finally:
        client_mod._CosineCamera = original


def test_connect_raises_without_sdk(monkeypatch):
    from cloudrobo_r2c.robots.agibot_g01 import agibot_g01_client as client_mod

    monkeypatch.setattr(client_mod, "_RobotDds", None)
    adapter = create_agibot_g01_adapter({})
    with pytest.raises(RuntimeError, match="a2d_sdk"):
        adapter.connect()
    assert not adapter.is_connected


# ---------------------------------------------------------------------------
# 观测
# ---------------------------------------------------------------------------


def test_get_observation_assembles_flat_fields():
    adapter = _connect()
    obs = adapter.get_observation()

    assert obs["id"] == 1
    assert isinstance(obs["timestamp"], int)
    assert list(obs["joint_positions"]) == pytest.approx(_last_robot().arm)
    assert list(obs["gripper_positions"]) == pytest.approx([0.25, 0.75])
    assert len(obs["hand_joint_positions"]) == 12
    assert list(obs["head_joint_positions"]) == pytest.approx([0.1, -0.2])
    assert list(obs["waist_joint_positions"]) == pytest.approx([0.05, 30.0])
    assert list(obs["body_pose"]) == pytest.approx([0.0, 0.1, 0.2, 0.3])
    # 相机键按 camera_key_map 暴露
    assert obs["cam_high"].shape == (4, 4, 3)
    assert obs["cam_left_wrist"].shape == (4, 4, 3)
    assert obs["cam_right_wrist"].shape == (4, 4, 3)

    # 第二帧 id 自增
    assert adapter.get_observation()["id"] == 2


def test_get_observation_falls_back_on_state_failure():
    adapter = _connect()
    first = adapter.get_observation()
    robot = _last_robot()
    robot.fail_next_state_query = True
    second = adapter.get_observation()
    # 状态查询失败：沿用上一帧值，不抛异常
    assert list(second["joint_positions"]) == pytest.approx(first["joint_positions"])
    assert list(second["joint_positions"]) == pytest.approx(robot.arm)


def test_get_observation_skips_cameras_when_disabled():
    adapter = _connect({"camera_enabled": False})
    obs = adapter.get_observation()
    assert "cam_high" not in obs
    assert FakeCosineCamera.instances == []


def test_get_observation_maps_custom_camera_keys():
    adapter = _connect(
        {
            "camera_group": ["head", "head_depth"],
            "camera_key_map": {"head": "cam_high", "head_depth": "cam_high_depth"},
        }
    )
    obs = adapter.get_observation()
    assert "cam_high" in obs
    assert "cam_high_depth" in obs


# ---------------------------------------------------------------------------
# 末端位姿观测（device_to_r2c，第 8 章 get_motion_status）
# ---------------------------------------------------------------------------


def test_observation_exposes_ee_poses():
    """默认（ee_pose_channel: end_effector_poses）位姿只发专用字段。"""
    adapter = _connect()
    obs = adapter.get_observation()
    assert obs["left_ee_pose"] == pytest.approx([0.13, 0.89, 0.85, -0.7071, 0.0, 0.0, 0.7071])
    assert obs["right_ee_pose"] == pytest.approx([0.13, -0.89, 0.85, 0.0, -0.7071, 0.7071, 0.0])
    # 合并字段 ee_poses：按 left、right 顺序
    assert obs["ee_poses"][0] == pytest.approx(obs["left_ee_pose"])
    assert obs["ee_poses"][1] == pytest.approx(obs["right_ee_pose"])
    # 专用字段数据源（单选）：ee_poses_data 在，joint_states 镜像字段不在
    assert obs["ee_poses_data"]["names"] == ["left", "right"]
    assert obs["ee_poses_data"]["pose"][0] == pytest.approx(obs["left_ee_pose"])
    assert obs["ee_poses_data"]["pose"][1] == pytest.approx(obs["right_ee_pose"])
    assert "ee_pose_names" not in obs
    assert "ee_poses_flat" not in obs


def test_observation_ee_pose_mirror_in_joint_states_mode():
    """ee_pose_channel: joint_states 时位姿镜像字段在、专用字段数据源不在。"""
    adapter = _connect({"ee_pose_channel": "joint_states"})
    obs = adapter.get_observation()
    # 默认 ee_pose_repr: quat —— 镜像为单表示（每侧 7 维四元数），不再双表示
    assert obs["ee_pose_names"] == _pose_dim_names("left") + _pose_dim_names("right")
    assert obs["ee_poses_flat"][:7] == pytest.approx(obs["left_ee_pose"])
    assert obs["ee_poses_flat"][7:14] == pytest.approx(obs["right_ee_pose"])
    # 单选互斥：专用字段数据源不再产出
    assert "ee_poses_data" not in obs


def test_observation_ee_pose_euler_repr():
    """ee_pose_repr: euler 对 end_effector_poses（默认通道）生效：pose 6 维 ZYX。"""
    from cloudrobo_r2c.robots.agibot_g01.agibot_g01_hardware_adapter import (
        _quat_to_euler_rpy,
    )

    left_rpy = _quat_to_euler_rpy(-0.7071, 0.0, 0.0, 0.7071)
    adapter = _connect({"ee_pose_repr": "euler"})
    obs = adapter.get_observation()
    # 专用字段 pose 转为 [x, y, z, roll, pitch, yaw]（raw left_ee_pose 仍为 quat）
    pose = obs["ee_poses_data"]["pose"][0]
    assert pose[:3] == pytest.approx([0.13, 0.89, 0.85])
    assert pose[3:6] == pytest.approx(list(left_rpy))
    assert len(pose) == 6
    assert obs["ee_poses_data"]["names"] == ["left", "right"]
    assert "ee_pose_names" not in obs


def test_observation_ee_pose_mirror_euler_repr():
    """ee_pose_repr: euler + ee_pose_channel: joint_states → 欧拉角单表示镜像。"""
    from cloudrobo_r2c.robots.agibot_g01.agibot_g01_hardware_adapter import (
        _quat_to_euler_rpy,
    )

    left_rpy = _quat_to_euler_rpy(-0.7071, 0.0, 0.0, 0.7071)
    right_rpy = _quat_to_euler_rpy(0.0, -0.7071, 0.7071, 0.0)
    adapter = _connect({"ee_pose_channel": "joint_states", "ee_pose_repr": "euler"})
    obs = adapter.get_observation()
    assert obs["ee_pose_names"] == (
        _pose_dim_names("left", "euler") + _pose_dim_names("right", "euler")
    )
    # 每侧 [x, y, z, roll, pitch, yaw] 6 维
    assert obs["ee_poses_flat"][0:3] == pytest.approx([0.13, 0.89, 0.85])
    assert obs["ee_poses_flat"][3:6] == pytest.approx(list(left_rpy))
    assert obs["ee_poses_flat"][6:9] == pytest.approx([0.13, -0.89, 0.85])
    assert obs["ee_poses_flat"][9:12] == pytest.approx(list(right_rpy))
    assert len(obs["ee_poses_flat"]) == 12


def test_observation_gripper_channel_joint_states_mode():
    """gripper_channel: joint_states 时夹爪镜像字段在、专用字段不在。"""
    adapter = _connect({"gripper_channel": "joint_states"})
    obs = adapter.get_observation()
    assert obs["joint_gripper_names"] == ["left_gripper", "right_gripper"]
    assert list(obs["joint_gripper_positions"]) == pytest.approx([0.25, 0.75])
    assert "gripper_positions" not in obs
    assert "ee_gripper_names" not in obs


def test_invalid_channel_config_rejected():
    with pytest.raises(ValueError, match="ee_pose_channel"):
        create_agibot_g01_adapter({"ee_pose_channel": "both"})
    with pytest.raises(ValueError, match="gripper_channel"):
        create_agibot_g01_adapter({"gripper_channel": "both"})
    with pytest.raises(ValueError, match="ee_pose_repr"):
        create_agibot_g01_adapter({"ee_pose_repr": "both"})


def test_observation_ee_poses_absent_without_motion_status():
    adapter = _connect()
    _last_controller().motion_status = None
    obs = adapter.get_observation()
    assert "left_ee_pose" not in obs
    assert "right_ee_pose" not in obs
    assert "ee_poses" not in obs


def test_observation_ee_poses_absent_when_motion_controller_disabled():
    adapter = _connect({"enable_motion_controller": False})
    obs = adapter.get_observation()
    assert "ee_poses" not in obs
    # 第 7 章关节观测不受影响
    assert len(obs["joint_positions"]) == ARM_DOF


# ---------------------------------------------------------------------------
# 动作
# ---------------------------------------------------------------------------


def test_send_action_dispatches_all_keys():
    adapter = _connect()
    adapter.send_action(
        {
            "arm_cmd_joint_pos": [0.0] * ARM_DOF,
            "gripper_cmd_pos": [0.5, 0.5],
            "hand_cmd_joint_pos": [0.0] * 12,
            "head_cmd_joint_pos": [0.1, 0.0],
            "waist_cmd_joint_pos": [0.0, 25.0],
            "wheel_cmd_speed": [0.2, 0.0],
        }
    )
    calls = {c[0]: c for c in _last_robot().calls}
    assert calls["move_arm"][1] == [0.0] * ARM_DOF
    assert calls["move_gripper"][1] == [0.5, 0.5]
    assert calls["move_head"][1] == [0.1, 0.0]
    assert calls["move_waist"][1] == [0.0, 25.0]
    assert calls["move_wheel"][1:] == (0.2, 0.0)


def test_send_action_skips_wrong_dims():
    adapter = _connect()
    adapter.send_action({"arm_cmd_joint_pos": [0.0] * 6})
    # 维度不匹配：记录错误并跳过，不应调用 GDK
    assert not any(c[0] == "move_arm" for c in _last_robot().calls)


def test_send_action_dry_run_skips_hardware():
    adapter = _connect({"dry_run": True})
    adapter.connect()
    adapter.send_action({"arm_cmd_joint_pos": [0.0] * ARM_DOF})
    robot = _last_robot()
    assert not any(c[0].startswith("move_") for c in robot.calls)
    adapter.disconnect()
    assert robot.shutdown_count == 1


def test_send_action_decodes_extension_value():
    adapter = _connect()
    payload = struct.pack("<2f", 0.1, 0.2)
    extension = {
        "dtype": "FLOAT32",
        "shape": [2],
        "data": payload,
        "mime_type": "",
    }
    adapter.send_action({"head_cmd_joint_pos": extension})
    assert _last_robot().calls[-1] == ("move_head", pytest.approx([0.1, 0.2]))


def test_send_action_ignores_unknown_command():
    adapter = _connect()
    adapter.send_action({"unknown_field": [1.0]})
    assert not any(c[0].startswith("move_") for c in _last_robot().calls)


def test_go_home_command_calls_reset():
    adapter = _connect({"commands": {"go_home": {"type": "go_home", "head_positions": [0.0, 0.0]}}})
    adapter.connect()
    _register_commands(adapter)
    assert adapter.execute_command("go_home") is True
    reset_calls = [c for c in _last_robot().calls if c[0] == "reset"]
    assert reset_calls == [("reset", {"head_positions": [0.0, 0.0]})]


def test_go_home_command_rejects_wrong_dims():
    adapter = _connect({"commands": {"go_home": {"type": "go_home", "arm_positions": [0.0, 1.0]}}})
    adapter.connect()
    _register_commands(adapter)
    with pytest.raises(ValueError, match="arm_positions"):
        adapter.execute_command("go_home")


def test_actions_require_connection():
    adapter = create_agibot_g01_adapter({})
    with pytest.raises(RuntimeError, match="not connected"):
        adapter.get_observation()
    with pytest.raises(RuntimeError, match="not connected"):
        adapter.send_action({"arm_cmd_joint_pos": [0.0] * ARM_DOF})
    with pytest.raises(RuntimeError, match="not connected"):
        adapter.get_runtime()


# ---------------------------------------------------------------------------
# state/current 发布绑定（get_runtime collector）
# ---------------------------------------------------------------------------


def test_get_runtime_returns_current_state_frame():
    """state/current 帧与 observation 一致（默认：位姿/夹爪发专用字段）。"""
    from cloudrobo_r2c.common.models import RunTimeMessage
    from cloudrobo_r2c.translators.device_translator import (
        ConfigurableDeviceTranslator,
    )

    location = "广东深圳坂田基地E3实验室"
    adapter = _connect({"location": location})
    config = _load_robot_config()
    translator = ConfigurableDeviceTranslator.from_config(config)
    observation = translator.device_to_r2c(adapter.get_observation())

    frame = adapter.get_runtime()
    assert isinstance(frame["timestamp"], int)
    assert frame["location"] == location
    js = frame["joint_states"]
    # joint_states 与 observation 一致：默认模式只含双臂 14 关节（位姿/夹爪
    # 走专用字段，单选互斥）
    assert js["names"] == list(observation.joint_states.names)
    assert js["position"] == pytest.approx(list(observation.joint_states.position))
    assert len(js["position"]) == ARM_DOF
    assert js["names"][0] == "left_arm_joint1"
    assert js["velocity"] == []
    assert js["torque"] == []
    # end_effector_states：夹爪位置（[35, 120] → [0, 1]）与 observation 一致
    assert frame["end_effector_states"]["names"] == ["left_gripper", "right_gripper"]
    assert frame["end_effector_states"]["position"] == pytest.approx([0.25, 0.75])
    assert frame["end_effector_states"]["position"] == pytest.approx(
        list(observation.end_effector_states.position)
    )
    # end_effector_poses：位置 x/y/z 来自 get_motion_status frames，与 observation 一致
    assert frame["end_effector_poses"]["names"] == ["left", "right"]
    assert frame["end_effector_poses"]["pose"][0] == pytest.approx(
        [0.13, 0.89, 0.85, -0.7071, 0.0, 0.0, 0.7071]
    )
    assert frame["end_effector_poses"]["pose"][1] == pytest.approx(
        list(observation.end_effector_poses.pose[1])
    )
    assert "end_effector_poses" in frame and "end_effector_states" in frame
    # 帧 dict 可被 state/current 绑定的 proto wrapper（RunTimeMessage）接受
    message = RunTimeMessage.from_dict(frame)
    assert message.location == location
    assert message.end_effector_states.position == pytest.approx([0.25, 0.75])
    assert message.end_effector_poses.pose[0] == pytest.approx(
        [0.13, 0.89, 0.85, -0.7071, 0.0, 0.0, 0.7071]
    )


def test_get_runtime_joint_states_mode_matches_observation():
    """channel 均选 joint_states 时，帧与 observation 的 joint_states 一致（36 维）。"""
    from cloudrobo_r2c.translators.device_translator import (
        ConfigurableDeviceTranslator,
    )

    adapter = _connect({"ee_pose_channel": "joint_states", "gripper_channel": "joint_states"})
    config = _load_robot_config()
    translator = ConfigurableDeviceTranslator.from_config(config)
    observation = translator.device_to_r2c(adapter.get_observation())

    frame = adapter.get_runtime()
    js = frame["joint_states"]
    assert js["names"] == list(observation.joint_states.names)
    assert js["position"] == pytest.approx(list(observation.joint_states.position))
    # 14 关节 + 位姿镜像（默认 quat：每侧 7 维） + 夹爪 2 = 30
    assert len(js["position"]) == ARM_DOF + 2 * POSE_DOF + GRIPPER_DOF
    assert js["names"][:ARM_DOF][0] == "left_arm_joint1"
    assert js["position"][ARM_DOF : ARM_DOF + 3] == pytest.approx([0.13, 0.89, 0.85])
    assert js["position"][-GRIPPER_DOF:] == pytest.approx([0.25, 0.75])
    # 单选互斥：专用字段不再出现在帧里
    assert "end_effector_states" not in frame
    assert "end_effector_poses" not in frame


def test_get_runtime_euler_repr_matches_observation():
    """ee_pose_repr: euler 对 get_runtime 与 observation 同步生效（euler pose 6 维）。"""
    from cloudrobo_r2c.robots.agibot_g01.agibot_g01_hardware_adapter import (
        _quat_to_euler_rpy,
    )
    from cloudrobo_r2c.translators.device_translator import (
        ConfigurableDeviceTranslator,
    )

    left_rpy = _quat_to_euler_rpy(-0.7071, 0.0, 0.0, 0.7071)
    adapter = _connect({"ee_pose_repr": "euler"})
    config = _load_robot_config()
    config["hardware"]["config"]["ee_pose_repr"] = "euler"
    translator = ConfigurableDeviceTranslator.from_config(config)
    observation = translator.device_to_r2c(adapter.get_observation())

    frame = adapter.get_runtime()
    # end_effector_poses 段与 observation 一致（euler 6 维）
    assert frame["end_effector_poses"]["pose"][0][:3] == pytest.approx([0.13, 0.89, 0.85])
    assert frame["end_effector_poses"]["pose"][0][3:6] == pytest.approx(list(left_rpy))
    assert len(frame["end_effector_poses"]["pose"][0]) == 6
    assert len(frame["end_effector_poses"]["pose"][0]) == len(
        list(observation.end_effector_poses.pose[0])
    )
    assert frame["joint_states"]["position"] == pytest.approx(
        list(observation.joint_states.position)
    )


def test_get_runtime_zero_fills_failed_state_query():
    adapter = _connect()
    robot = _last_robot()
    robot.fail_next_state_query = True
    frame = adapter.get_runtime()
    # 双臂查询失败 → 零值回退；位姿与夹爪查询不受影响
    assert frame["joint_states"]["position"][:ARM_DOF] == [0.0] * ARM_DOF
    assert frame["end_effector_states"]["position"] == pytest.approx([0.25, 0.75])
    assert frame["end_effector_poses"]["pose"][0] == pytest.approx(
        [0.13, 0.89, 0.85, -0.7071, 0.0, 0.0, 0.7071]
    )


def test_robot_config_yaml_binds_state_current_topic():
    config = _load_robot_config()
    topics = (config.get("hardware") or {}).get("topics") or {}
    binding = topics.get("{project_id}/{device_id}/state/current")
    assert binding, "shipped config must bind state/current"
    assert binding["direction"] == "publish"
    assert binding["rate_hz"] == 10
    assert binding["collector"] == "get_runtime"
    assert binding["proto"] == "cloudrobo_r2c.common.models:RunTimeMessage"
    assert config["hardware"]["config"].get("location") == "广东深圳坂田基地E3实验室"
    # 发布位置单选默认值：专用字段（end_effector_poses / end_effector_states）
    assert config["hardware"]["config"].get("ee_pose_channel") == "end_effector_poses"
    assert config["hardware"]["config"].get("gripper_channel") == "end_effector_states"
    # 角度表示默认值：quat（对 ee_pose_channel 两种取值都生效）
    assert config["hardware"]["config"].get("ee_pose_repr") == "quat"
    # 绑定可被真实解析器接受（proto 名、collector 名等校验）
    from cloudrobo_r2c.client.topic_config import parse_topic_bindings

    bindings = parse_topic_bindings(topics)
    assert len(bindings) == 1
    assert bindings[0].rate_hz == 10


def test_robot_config_yaml_joint_states_channel_mode():
    """ee_pose_channel/gripper_channel 均选 joint_states 时端到端镜像（quat 30 维）。"""
    from cloudrobo_r2c.translators.device_translator import (
        ConfigurableDeviceTranslator,
    )

    config = _load_robot_config()
    config["hardware"]["config"]["ee_pose_channel"] = "joint_states"
    config["hardware"]["config"]["gripper_channel"] = "joint_states"
    translator = ConfigurableDeviceTranslator.from_config(config)
    adapter = _connect({"ee_pose_channel": "joint_states", "gripper_channel": "joint_states"})
    observation = translator.device_to_r2c(adapter.get_observation())
    # joint_states：14 关节 + 位姿镜像（默认 quat，每侧 7 维） + 夹爪 2 = 30
    assert len(observation.joint_states.position) == ARM_DOF + 2 * POSE_DOF + GRIPPER_DOF
    assert observation.joint_states.names[:ARM_DOF][0] == "left_arm_joint1"
    assert list(observation.joint_states.names[ARM_DOF : ARM_DOF + 7]) == _pose_dim_names("left")
    assert list(observation.joint_states.names[-GRIPPER_DOF:]) == [
        "left_gripper",
        "right_gripper",
    ]
    assert observation.joint_states.position[ARM_DOF : ARM_DOF + 7] == pytest.approx(
        [0.13, 0.89, 0.85, -0.7071, 0.0, 0.0, 0.7071]
    )
    assert observation.joint_states.position[-GRIPPER_DOF:] == pytest.approx([0.25, 0.75])
    # 单选互斥：专用字段不再发布位姿/夹爪内容
    assert len(observation.end_effector_states.position) == 0
    assert len(observation.end_effector_poses.pose) == 0


def test_robot_config_yaml_euler_repr_end_effector_poses():
    """ee_pose_repr: euler 对默认通道（end_effector_poses）生效：pose 6 维。"""
    from cloudrobo_r2c.robots.agibot_g01.agibot_g01_hardware_adapter import (
        _quat_to_euler_rpy,
    )
    from cloudrobo_r2c.translators.device_translator import (
        ConfigurableDeviceTranslator,
    )

    left_rpy = _quat_to_euler_rpy(-0.7071, 0.0, 0.0, 0.7071)
    config = _load_robot_config()
    config["hardware"]["config"]["ee_pose_repr"] = "euler"
    translator = ConfigurableDeviceTranslator.from_config(config)
    adapter = _connect({"ee_pose_repr": "euler"})
    observation = translator.device_to_r2c(adapter.get_observation())
    # end_effector_poses.pose 每行 [x, y, z, roll, pitch, yaw]；joint_states 仍 14 关节
    assert len(observation.joint_states.position) == ARM_DOF
    assert len(observation.end_effector_poses.pose) == 2
    assert observation.end_effector_poses.pose[0][:3] == pytest.approx([0.13, 0.89, 0.85])
    assert observation.end_effector_poses.pose[0][3:6] == pytest.approx(list(left_rpy))
    assert len(observation.end_effector_poses.pose[0]) == 6


# ---------------------------------------------------------------------------
# 配置 YAML + 翻译链路（ConfigurableDeviceTranslator）
# ---------------------------------------------------------------------------


def _load_robot_config() -> Dict[str, Any]:
    assert CONFIG_PATH.exists(), f"missing shipped config: {CONFIG_PATH}"
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def test_robot_config_yaml_passes_schema_validation():
    from cloudrobo_r2c.common.robot_config_schema import RobotConfig

    config = _load_robot_config()
    # hardware.type 校验依赖 entry point 注册（agibot_g01）
    RobotConfig.model_validate(config)


def test_robot_config_yaml_translates_observation_and_action():
    from cloudrobo_r2c.common.models import Observations
    from cloudrobo_r2c.translators.device_translator import (
        ConfigurableDeviceTranslator,
    )

    config = _load_robot_config()
    translator = ConfigurableDeviceTranslator.from_config(config)

    adapter = _connect()
    obs = adapter.get_observation()
    observation = translator.device_to_r2c(obs)
    assert isinstance(observation, Observations)
    # 默认（ee_pose_channel: end_effector_poses / gripper_channel:
    # end_effector_states）：joint_states 只含双臂 14 关节，位姿与夹爪
    # 分别发专用字段（单选互斥，不可同时发）
    assert len(observation.joint_states.position) == ARM_DOF
    assert observation.joint_states.names[0] == "left_arm_joint1"
    assert observation.joint_states.names[-1] == "right_arm_joint7"
    assert observation.joint_states.position == pytest.approx(
        [float(i + 1) / 10 for i in range(ARM_DOF)]
    )
    # 夹爪 → end_effector_states（[35, 120] 归一化到 [0, 1]）
    assert list(observation.end_effector_states.names) == ["left_gripper", "right_gripper"]
    assert observation.end_effector_states.position == pytest.approx([0.25, 0.75])
    # 相机映射为 JPEG 字节
    jpeg = observation.images.color.get("cam_high")
    assert jpeg is not None and jpeg[:2] == b"\xff\xd8"
    # extensions：head/waist/hand 均以 FLOAT32 打包（body_pose 变长默认不映射）
    head_ext = observation.extensions["head_joint"]
    assert list(np.frombuffer(head_ext.data, dtype="<f4")) == pytest.approx([0.1, -0.2])
    assert observation.extensions["hand_joint"].shape == [12]
    # 末端位姿观测 → end_effector_poses（顺序与 names 对应：left, right）
    assert list(observation.end_effector_poses.names) == ["left", "right"]
    assert len(observation.end_effector_poses.pose) == 2
    assert observation.end_effector_poses.pose[0] == pytest.approx(
        [0.13, 0.89, 0.85, -0.7071, 0.0, 0.0, 0.7071]
    )

    # r2c_to_device：云端动作 → 适配器平铺字段
    from cloudrobo_r2c.common.models import ExtensionValue

    step = {
        "timestamp": 1,
        "id": 1,
        "chunk_index": 0,
        "joint_states": {
            "names": [],
            "position": [0.1] * ARM_DOF,
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
        "extensions": {
            "head_joint": ExtensionValue(
                dtype="FLOAT32",
                shape=[2],
                data=struct.pack("<2f", 0.3, -0.1),
                mime_type="",
            )
        },
    }
    device_command = translator.r2c_to_device(step)
    assert device_command["arm_cmd_joint_pos"] == [0.1] * ARM_DOF
    assert device_command["gripper_cmd_pos"] == [0.4, 0.6]
    # extension 源以 ExtensionValue 透传，由适配器解码
    assert AgiBotG01HardwareAdapter.as_float_list(
        device_command["head_cmd_joint_pos"]
    ) == pytest.approx([0.3, -0.1])
    # 云端未下发 waist/wheel 时自动跳过
    assert "waist_cmd_joint_pos" not in device_command
    assert "wheel_cmd_speed" not in device_command

    # 端到端：翻译结果可直接送入适配器
    adapter.send_action(device_command)
    calls = {c[0]: c for c in _last_robot().calls}
    assert calls["move_arm"][1] == [0.1] * ARM_DOF
    assert calls["move_head"][1] == pytest.approx([0.3, -0.1])
    # joint_states.names 映射为 joint_names（第 7/8 章路由依据；此处为空 → 第 7 章）
    assert device_command["joint_names"] == []


# ---------------------------------------------------------------------------
# 第 7 / 8 章路由（按 joint_states.names 区分，《指南》8.1）
# ---------------------------------------------------------------------------


LEFT_POSE = [0.2, 0.0, 0.3, 0.0, 0.0, 0.0, 1.0]
RIGHT_POSE = [0.2, 0.0, 0.3, 0.0, 0.0, 0.0, -1.0]


def _pose_dim_names(side: str, pose_repr: str = "quat") -> List[str]:
    """逐维位姿 names：四元数 x/y/z/rx/ry/rz/rw 或欧拉角姿态 x/y/z/roll/pitch/yaw。"""
    dims = (
        ["x", "y", "z", "rx", "ry", "rz", "rw"]
        if pose_repr == "quat"
        else ["x", "y", "z", "roll", "pitch", "yaw"]
    )
    return [f"{side}_pose_{d}" for d in dims]


def test_route_pose_via_joint_states_names():
    adapter = _connect()
    adapter.send_action(
        {
            "joint_names": _pose_dim_names("left") + _pose_dim_names("right"),
            "arm_cmd_joint_pos": LEFT_POSE + RIGHT_POSE,
        }
    )
    controller = _last_controller()
    lifetime, control_group, left_pose, right_pose = controller.pose_calls[0]
    assert lifetime == 1.0
    assert control_group == ["left_arm", "right_arm"]
    assert left_pose == pytest.approx(
        {"x": 0.2, "y": 0.0, "z": 0.3, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0}
    )
    assert right_pose == pytest.approx(
        {"x": 0.2, "y": 0.0, "z": 0.3, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": -1.0}
    )
    # 位姿路由不走第 7 章 move_arm
    assert not any(c[0] == "move_arm" for c in _last_robot().calls)


def test_route_pose_euler_names_converted_to_quaternion():
    # 欧拉角表示（roll/pitch/yaw，ZYX）：适配器内部转换为四元数下发

    from cloudrobo_r2c.robots.agibot_g01.agibot_g01_hardware_adapter import (
        _euler_rpy_to_quat,
    )

    adapter = _connect()
    roll, pitch, yaw = 0.1, -0.2, 0.3
    qx, qy, qz, qw = _euler_rpy_to_quat(roll, pitch, yaw)
    names = []
    values = []
    for side in ("left", "right"):
        names += [f"{side}_pose_x", f"{side}_pose_y", f"{side}_pose_z"]
        names += [f"{side}_pose_roll", f"{side}_pose_pitch", f"{side}_pose_yaw"]
        values += [0.2, 0.0, 0.3, roll, pitch, yaw]
    adapter.send_action({"joint_names": names, "arm_cmd_joint_pos": values})
    _, _, left_pose, right_pose = _last_controller().pose_calls[0]
    expected_quat = {"x": 0.2, "y": 0.0, "z": 0.3, "qx": qx, "qy": qy, "qz": qz, "qw": qw}
    assert left_pose == pytest.approx(expected_quat)
    assert right_pose == pytest.approx(expected_quat)


def test_route_gripper_via_joint_states():
    # 夹爪经 joint_states 逐维 names（left_gripper/right_gripper）→ move_gripper
    adapter = _connect()
    adapter.send_action(
        {
            "joint_names": ["left_gripper", "right_gripper"],
            "arm_cmd_joint_pos": [0.25, 0.9],
        }
    )
    assert any(
        c[0] == "move_gripper" and c[1] == pytest.approx([0.25, 0.9]) for c in _last_robot().calls
    )


def test_route_reject_pose_in_both_channels():
    # 末端位姿同时出现在 joint_states 与 end_effector_poses → 拒绝执行
    adapter = _connect()
    adapter.send_action(
        {
            "joint_names": _pose_dim_names("left") + _pose_dim_names("right"),
            "arm_cmd_joint_pos": LEFT_POSE + RIGHT_POSE,
            "left_pose_cmd_ee": [9.0, 9.0, 9.0, 0.0, 0.0, 0.0, 1.0],
            "right_pose_cmd_ee": [9.0, 9.0, 9.0, 0.0, 0.0, 0.0, 1.0],
        }
    )
    assert _last_controller().pose_calls == []


def test_route_joints8_per_joint_names():
    # 关节控制接口为 robot_controller → set_joint_position_control
    adapter = _connect({"joint_control_interface": "robot_controller"})
    left_group = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
    right_group = [0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4]
    names = [f"left_arm_joint{i}" for i in range(1, 8)]
    names += [f"right_arm_joint{i}" for i in range(1, 8)]
    adapter.send_action({"joint_names": names, "arm_cmd_joint_pos": left_group + right_group})
    controller = _last_controller()
    lifetime, joint_group = controller.joint_calls[0]
    assert lifetime == 1.0
    assert joint_group["left_arm"] == pytest.approx(left_group)
    assert joint_group["right_arm"] == pytest.approx(right_group)
    assert not any(c[0] == "move_arm" for c in _last_robot().calls)
    # 默认不自动切换运动模式（mode=2 仅适用 trajectory_tracking）
    assert controller.motion_mode_calls == []


def test_motion_mode_auto_disabled():
    # auto_motion_control_mode 默认 False → 不自动切换模式
    adapter = _connect()
    pose_names = _pose_dim_names("left") + _pose_dim_names("right")
    adapter.send_action({"joint_names": pose_names, "arm_cmd_joint_pos": LEFT_POSE + RIGHT_POSE})
    assert _last_controller().motion_mode_calls == []


def test_route_reject_partial_joints8():
    # 第 8 章关节名不全（缺 joint7）→ 不硬执行
    adapter = _connect()
    names = [f"left_arm_joint{i}" for i in range(1, 7)]  # 缺 7
    names += [f"right_arm_joint{i}" for i in range(1, 8)]
    adapter.send_action({"joint_names": names, "arm_cmd_joint_pos": [0.1] * 13})
    assert _last_controller().joint_calls == []


def test_route_reject_mixed_joint_name_formats():
    # 第 7 章（带下划线）与第 8 章（不带下划线）关节名混用 → 拒绝
    adapter = _connect()
    names = ["left_arm_joint1", "left_arm_joint1"]
    adapter.send_action({"joint_names": names, "arm_cmd_joint_pos": [0.1, 0.2]})
    assert not any(c[0] == "move_arm" for c in _last_robot().calls)
    assert _last_controller().joint_calls == []


def test_route_pose_fallback_to_ee_channel():
    # joint_states 仅夹爪分量（无关节/位姿分量）→ ee 通道位姿兜底 + 夹爪下发
    adapter = _connect()
    adapter.send_action(
        {
            "joint_names": ["left_gripper", "right_gripper"],
            "arm_cmd_joint_pos": [0.3, 0.3],
            "left_pose_cmd_ee": LEFT_POSE,
            "right_pose_cmd_ee": RIGHT_POSE,
        }
    )
    assert any(c[0] == "move_gripper" for c in _last_robot().calls)
    _, _, left_pose, right_pose = _last_controller().pose_calls[0]
    assert left_pose == pytest.approx(
        {"x": 0.2, "y": 0.0, "z": 0.3, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0}
    )
    assert right_pose is not None


def test_route_ee_channel_accepts_euler_pose():
    """ee 通道按长度识别：6 维 euler（ZYX）内部转 quat 下发，与观测 euler 输出闭环。"""
    from cloudrobo_r2c.robots.agibot_g01.agibot_g01_hardware_adapter import (
        _euler_rpy_to_quat,
    )

    adapter = _connect()
    roll, pitch, yaw = 0.1, -0.2, 0.3
    qx, qy, qz, qw = _euler_rpy_to_quat(roll, pitch, yaw)
    adapter.send_action(
        {
            "left_pose_cmd_ee": [0.2, 0.0, 0.3, roll, pitch, yaw],
            "right_pose_cmd_ee": [0.2, 0.0, 0.3, roll, pitch, yaw],
        }
    )
    _, _, left_pose, right_pose = _last_controller().pose_calls[0]
    assert left_pose == pytest.approx(
        {"x": 0.2, "y": 0.0, "z": 0.3, "qx": qx, "qy": qy, "qz": qz, "qw": qw}
    )
    assert right_pose == pytest.approx(
        {"x": 0.2, "y": 0.0, "z": 0.3, "qx": qx, "qy": qy, "qz": qz, "qw": qw}
    )


def test_route_ee_channel_rejects_wrong_dims():
    """ee 通道维度既非 7（quat）也非 6（euler）→ 该侧跳过，不调用 GDK。"""
    adapter = _connect()
    adapter.send_action({"left_pose_cmd_ee": [0.2, 0.0, 0.3]})
    assert _last_controller().pose_calls == []


def test_route_reject_joint_and_ee_pose_together():
    # 关节与末端互斥：joint_states 含关节分量时不得经 ee 通道下发位姿
    adapter = _connect()
    adapter.send_action(
        {
            "joint_names": [f"left_arm_joint{i}" for i in range(1, 8)]
            + [f"right_arm_joint{i}" for i in range(1, 8)],
            "arm_cmd_joint_pos": [0.1] * ARM_DOF,
            "left_pose_cmd_ee": LEFT_POSE,
            "right_pose_cmd_ee": RIGHT_POSE,
        }
    )
    assert not any(c[0] == "move_arm" for c in _last_robot().calls)
    assert _last_controller().pose_calls == []


def test_route_reject_mixed_joint_and_pose_names():
    # 关节与末端互斥：同一动作不得同时包含关节分量与位姿分量
    adapter = _connect()
    adapter.send_action(
        {
            "joint_names": [f"left_arm_joint{i}" for i in range(1, 8)]
            + [f"right_arm_joint{i}" for i in range(1, 8)]
            + ["left_pose_x", "left_pose_y", "left_pose_z"]
            + ["left_pose_rx", "left_pose_ry", "left_pose_rz", "left_pose_rw"]
            + ["right_pose_x", "right_pose_y", "right_pose_z"]
            + ["right_pose_rx", "right_pose_ry", "right_pose_rz", "right_pose_rw"],
            "arm_cmd_joint_pos": [0.1] * ARM_DOF + LEFT_POSE + RIGHT_POSE,
        }
    )
    assert not any(c[0] == "move_arm" for c in _last_robot().calls)
    assert _last_controller().pose_calls == []
    assert _last_controller().joint_calls == []


def test_route_reject_mixed_quat_and_euler():
    # 四元数与欧拉角互斥：同一侧同时出现两种表示 → 拒绝
    adapter = _connect()
    names = ["left_pose_x", "left_pose_y", "left_pose_z", "left_pose_rx"]
    names += ["left_pose_roll", "left_pose_pitch", "left_pose_yaw"]
    adapter.send_action({"joint_names": names, "arm_cmd_joint_pos": [0.1] * 7})
    assert _last_controller().pose_calls == []

    # 跨侧表示不一致同样拒绝
    names = _pose_dim_names("left") + [
        "right_pose_x",
        "right_pose_y",
        "right_pose_z",
        "right_pose_roll",
        "right_pose_pitch",
        "right_pose_yaw",
    ]
    adapter.send_action({"joint_names": names, "arm_cmd_joint_pos": LEFT_POSE + [0.1] * 6})
    assert _last_controller().pose_calls == []


def test_route_reject_partial_joints():
    # 关节不全（仅 6 个关节名）→ 不硬执行（不做补帧兜底）
    adapter = _connect()
    adapter.send_action(
        {
            "joint_names": [f"left_arm_joint{i}" for i in range(1, 7)],
            "arm_cmd_joint_pos": [0.1] * 6,
        }
    )
    assert not any(c[0] == "move_arm" for c in _last_robot().calls)


def test_route_reject_partial_group():
    # 组关节数不全（每组须 7 维）→ 拒绝
    adapter = _connect()
    adapter.send_action(
        {
            "joint_names": ["left_arm", "right_arm"],
            "arm_cmd_joint_pos": [0.1] * 7 + [0.2] * 5,  # right_arm 仅 5 维
        }
    )
    assert _last_controller().joint_calls == []


def test_route_reject_partial_gripper():
    # 夹爪仅一侧 → 不硬执行
    adapter = _connect()
    adapter.send_action(
        {
            "joint_names": ["left_gripper"],
            "arm_cmd_joint_pos": [0.25],
        }
    )
    assert not any(c[0] == "move_gripper" for c in _last_robot().calls)


def test_route_reject_unknown_names():
    # 未知 names 视为协议不一致 → 拒绝执行
    adapter = _connect()
    adapter.send_action(
        {
            "joint_names": ["left_pose_w", "right_pose_w"],
            "arm_cmd_joint_pos": [0.1, 0.1],
        }
    )
    assert _last_controller().pose_calls == []
    assert not any(c[0] == "move_arm" for c in _last_robot().calls)


def test_mapper_shared_default_list_not_accumulated():
    """回归：default 列表 + 后续 list_mode extend 规则不得跨 publish 累积污染。

    现场曾出现 joint_states.names 每次发布追加一对 gripper 名（350+ 维），
    根因是 default 共享可变列表被 extend 规则原地修改。
    """
    from cloudrobo_r2c.core.config_mapper import ConfigDrivenMapper

    rules = [
        {"target_key": "names", "default": ["a", "b"]},
        {
            "target_key": "names",
            "source_path": "extra",
            "list_mode": "extend",
            "required": False,
        },
    ]
    mapper = ConfigDrivenMapper.from_rule_mappings(rules)
    payload = {"extra": ["c"]}
    assert mapper.map(payload)["names"] == ["a", "b", "c"]
    # 第二次发布：names 仍为 a/b/c，不累积
    assert mapper.map(payload)["names"] == ["a", "b", "c"]
    assert mapper.map(payload)["names"] == ["a", "b", "c"]


def test_route_joints8_via_group_names():
    # names 均为第 8 章部位组名（每组 7 关节）→ set_joint_position_control
    adapter = _connect()
    left_group = [0.1] * 7
    right_group = [0.2] * 7
    adapter.send_action(
        {
            "joint_names": ["left_arm", "right_arm"],
            "arm_cmd_joint_pos": left_group + right_group,
        }
    )
    controller = _last_controller()
    lifetime, joint_group = controller.joint_calls[0]
    assert lifetime == 1.0
    assert joint_group["left_arm"] == pytest.approx(left_group)
    assert joint_group["right_arm"] == pytest.approx(right_group)
    assert not any(c[0] == "move_arm" for c in _last_robot().calls)
    assert controller.pose_calls == []


def test_route_joints7_with_arm_joint_names():
    # names 为双臂关节名 → 第 7 章 move_arm（默认路径）
    adapter = _connect()
    arm = [0.1] * ARM_DOF
    adapter.send_action(
        {
            "joint_names": [f"left_arm_joint{i}" for i in range(1, 8)]
            + [f"right_arm_joint{i}" for i in range(1, 8)],
            "arm_cmd_joint_pos": arm,
        }
    )
    assert not _last_controller().pose_calls
    assert not _last_controller().joint_calls
    assert any(c[0] == "move_arm" for c in _last_robot().calls)


def test_route_pose_rejects_wrong_dims():
    adapter = _connect()
    # 位姿分量不完整（只有 x/y，缺 z 与姿态）→ 不下发
    adapter.send_action(
        {
            "joint_names": ["left_pose_x", "left_pose_y"],
            "arm_cmd_joint_pos": [0.1, 0.2],
        }
    )
    assert _last_controller().pose_calls == []


def test_translator_maps_ee_pose_and_joint_names():
    from cloudrobo_r2c.common.models import Pose7D
    from cloudrobo_r2c.translators.device_translator import ConfigurableDeviceTranslator

    config = _load_robot_config()
    translator = ConfigurableDeviceTranslator.from_config(config)
    pose_names = _pose_dim_names("left") + _pose_dim_names("right")
    step = {
        "timestamp": 1,
        "id": 1,
        "chunk_index": 0,
        "joint_states": {
            "names": pose_names,
            "position": LEFT_POSE + RIGHT_POSE,
            "velocity": [],
            "torque": [],
        },
        "end_effector_states": {"names": [], "position": [], "velocity": [], "torque": []},
        "end_effector_poses": {
            # 与 sync_client._extract_action_step 的 per-step 结构一致（pose 为 Pose7D）
            "left": {"pose": Pose7D(list(LEFT_POSE))},
            "right": {"pose": Pose7D(list(RIGHT_POSE))},
        },
        "localization": {"odom_pose": [], "map_pose": []},
        "extensions": {},
    }
    device_command = translator.r2c_to_device(step)
    assert device_command["joint_names"] == pose_names
    # joint_states 位姿逐维值全量透传（适配器按 names 解析重组）
    assert AgiBotG01HardwareAdapter.as_float_list(device_command["arm_cmd_joint_pos"]) == (
        pytest.approx(LEFT_POSE + RIGHT_POSE)
    )
    # ee 通道：per-step pose 为 7 元素列表，映射为 *_pose_cmd_ee
    assert AgiBotG01HardwareAdapter.as_float_list(device_command["left_pose_cmd_ee"]) == (
        pytest.approx(LEFT_POSE)
    )
    assert AgiBotG01HardwareAdapter.as_float_list(device_command["right_pose_cmd_ee"]) == (
        pytest.approx(RIGHT_POSE)
    )
    # 端到端：末端位姿同时出现在 joint_states 与 end_effector_poses → 拒绝执行
    adapter = _connect()
    adapter.send_action(device_command)
    assert _last_controller().pose_calls == []
    assert not any(c[0] == "move_arm" for c in _last_robot().calls)


# ---------------------------------------------------------------------------
# 样例 cloud-adapter（examples/agibot_g01_cloud_adapter.py）
# ---------------------------------------------------------------------------


def _load_example_module():
    pytest.importorskip("zenoh")
    examples_dir = Path(agibot_g01_client.__file__).resolve().parents[4] / "examples"
    examples_dir_str = str(examples_dir)
    if examples_dir_str not in sys.path:
        sys.path.insert(0, examples_dir_str)
    import agibot_g01_cloud_adapter as demo

    return demo


def _demo_observation():
    joint_names = (
        [f"left_arm_joint{i}" for i in range(1, 8)]
        + [f"right_arm_joint{i}" for i in range(1, 8)]
        + ["left_gripper", "right_gripper"]
    )
    joint_position = [0.1] * ARM_DOF + [0.3, 0.3]
    return types.SimpleNamespace(
        joint_states=types.SimpleNamespace(names=joint_names, position=joint_position),
        end_effector_states=types.SimpleNamespace(position=[0.3, 0.3]),
    )


# 第 8 章示例组（与样例中注释掉的 GROUP_POSE / GROUP_JOINTS8 同构）
_EE_POSE_GROUP = {
    "name": "ee_pose(绝对位姿)",
    "kind": "ee_pose",
    "left_pose": LEFT_POSE,
    "right_pose": RIGHT_POSE,
    "gripper": [0.3, 0.3],
}
_JOINT_POSITION_GROUP = {
    "name": "joint_position(组名关节)",
    "kind": "joint_position",
    "joints": [0.2] * ARM_DOF,
    "gripper": [0.5, 0.8],
}


def test_example_ee_pose_group_joint_states_channel():
    """ee_pose 组：绝对位姿经 joint_states 逐维 names（四元数）+ 夹爪。"""
    demo = _load_example_module()
    cycler = demo.ActionGroupCycler(
        groups=[dict(_EE_POSE_GROUP)],
        steps=5,
        pose_channel="joint_states",
        pose_repr="quat",
    )
    current = demo._extract_current(_demo_observation())
    a = cycler.build_action(1000, current["joints"], current["gripper"])
    assert a.joint_states.names == (
        demo._pose_dim_names("left")
        + demo._pose_dim_names("right")
        + ["left_gripper", "right_gripper"]
    )
    row = a.joint_states.position[0]
    assert row[:7] == pytest.approx(LEFT_POSE)
    assert row[7:14] == pytest.approx(RIGHT_POSE)
    assert row[14:] == pytest.approx([0.3, 0.3])  # 夹爪保持当前角度
    assert a.end_effector_poses == {}


def test_example_ee_pose_group_ee_channel_and_euler_repr():
    """ee_pose 组：--pose-channel ee --pose-repr euler。

    关节与末端互斥 → joint_states 仅携带夹爪分量；ee 通道为四元数原生格式
    （欧拉角在云端先转换为四元数）。
    """
    demo = _load_example_module()
    cycler = demo.ActionGroupCycler(
        groups=[dict(_EE_POSE_GROUP)],
        steps=5,
        pose_channel="ee",
        pose_repr="euler",
    )
    current = demo._extract_current(_demo_observation())
    a = cycler.build_action(1000, current["joints"], current["gripper"])
    assert a.joint_states.names == ["left_gripper", "right_gripper"]
    assert a.joint_states.position[0] == pytest.approx([0.3, 0.3])
    left_pose = list(a.end_effector_poses["left"].pose[0])
    assert left_pose == pytest.approx(LEFT_POSE)
    assert list(a.end_effector_poses["right"].pose[0]) == pytest.approx(RIGHT_POSE)


def test_example_joint_position_group_per_joint_names():
    """joint_position 组：left_arm_joint1..7 / right_arm_joint1..7 逐关节名。"""
    demo = _load_example_module()
    cycler = demo.ActionGroupCycler(
        groups=[dict(_JOINT_POSITION_GROUP)],
        steps=5,
    )
    current = demo._extract_current(_demo_observation())
    a = cycler.build_action(1000, current["joints"], current["gripper"])
    assert a.joint_states.names == [f"left_arm_joint{i}" for i in range(1, 8)] + [
        f"right_arm_joint{i}" for i in range(1, 8)
    ]
    assert a.joint_states.position[-1] == pytest.approx(_JOINT_POSITION_GROUP["joints"])
    assert a.end_effector_poses == {}


def test_example_cycler_cycles_groups_ping_pong():
    """GROUPS dict 数组驱动：默认往复序列 0 → 1 → 2 → 1 → 0。"""
    demo = _load_example_module()
    cycler = demo.ActionGroupCycler(
        groups=[
            {"name": "t0", "kind": "joints", "joints": [0.1] * ARM_DOF, "gripper": [0.15, 0.15]},
            {"name": "t1", "kind": "joints", "joints": [0.3] * ARM_DOF, "gripper": [0.85, 0.35]},
            {"name": "t2", "kind": "joints", "joints": [0.5] * ARM_DOF, "gripper": [0.5, 0.8]},
        ],
        steps=5,
    )
    current = demo._extract_current(_demo_observation())
    now = 1000

    def next_action():
        return cycler.build_action(now, current["joints"], current["gripper"])

    # 组 0 → 1 → 2 → 1 → 0（往复）
    a1 = next_action()
    assert a1.joint_states.position[-1] == pytest.approx([0.1] * ARM_DOF)
    assert a1.end_effector_states.position[-1] == pytest.approx([0.15, 0.15])
    a2 = next_action()
    assert a2.joint_states.position[-1] == pytest.approx([0.3] * ARM_DOF)
    a3 = next_action()
    assert a3.joint_states.position[-1] == pytest.approx([0.5] * ARM_DOF)
    assert "head_joint" not in a3.extensions
    a4 = next_action()
    assert a4.joint_states.position[-1] == pytest.approx([0.3] * ARM_DOF)
    a5 = next_action()
    assert a5.joint_states.position[-1] == pytest.approx([0.1] * ARM_DOF)
    # 夹爪用最终角度不插值（真机调试结论）
    assert a1.end_effector_states.position[0] == pytest.approx([0.15, 0.15])


def test_example_ee_pose_interp_joint_states_channel():
    """ee_pose 组（joint_states 通道）：位姿全程插值（平移线性 + SLERP）。"""
    demo = _load_example_module()
    target_left = [0.3, 0.0, 0.3, 0.0, 0.0, 0.0, 1.0]
    group_pose = {
        "name": "ee_pose(绝对位姿)",
        "kind": "ee_pose",
        "left_pose": target_left,
        "right_pose": [0.3, 0.0, 0.3, 0.0, 0.0, 0.0, 1.0],
    }
    cycler = demo.ActionGroupCycler(groups=[group_pose], steps=5)
    observation = _demo_observation()
    observation.end_effector_poses = types.SimpleNamespace(
        names=["left", "right"],
        pose=[[0.2, 0.0, 0.3, 0.0, 0.0, 0.0, 1.0]] * 2,
    )
    current = demo._extract_current(observation)
    a = cycler.build_action(1000, current["joints"], current["gripper"])
    assert a is not None
    # 绝对位姿组直接下发组内配置的目标（无插值依赖观测位姿）
    xs = [row[0] for row in a.joint_states.position]
    assert xs == pytest.approx([0.3] * 5)
    # 每步姿态保持不变
    for i in range(4):
        assert abs(a.joint_states.position[i][5] - a.joint_states.position[i + 1][5]) < 0.05
    # 最后一步到达目标
    assert a.joint_states.position[-1][:7] == pytest.approx(target_left)


def test_example_joint_position_group_interpolates():
    """joint_position 组：关节与夹爪均从当前值线性插值到目标。"""
    demo = _load_example_module()
    cycler = demo.ActionGroupCycler(groups=[dict(_JOINT_POSITION_GROUP)], steps=5)
    current = demo._extract_current(_demo_observation())
    a = cycler.build_action(1000, current["joints"], current["gripper"])
    # 首步 0.1 → 0.2 五步插值
    assert a.joint_states.position[0] == pytest.approx([0.12] * ARM_DOF)
    assert a.joint_states.position[-1] == pytest.approx([0.2] * ARM_DOF)
    # 夹爪用最终角度不插值（真机调试结论）
    assert a.end_effector_states.position[0] == pytest.approx([0.5, 0.8])
    assert a.end_effector_states.position[-1] == pytest.approx([0.5, 0.8])
