"""Unit tests for the AgiBot G01 mock hardware adapter (no GDK / robot needed).

The mock adapter reuses the real adapter's observation/action pipeline and only
swaps the backend for an in-memory simulation, so these tests cover the mock
client state machine, the initial joint_a / pose_a baseline, action routing
end-to-end and the shipped YAML config.
"""

from __future__ import annotations

import struct
import sys
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest
import yaml
from cloudrobo_r2c.robots.agibot_g01.agibot_g01_hardware_adapter import (
    ARM_DOF,
    GRIPPER_DOF,
    HAND_DOF,
)
from cloudrobo_r2c.robots.agibot_g01.agibot_g01_mock_adapter import (
    AgiBotG01MockAdapter,
    create_agibot_g01_mock_adapter,
)
from cloudrobo_r2c.robots.agibot_g01.agibot_g01_mock_client import (
    DEFAULT_INITIAL_EE_POSES,
    DEFAULT_INITIAL_GRIPPER,
    DEFAULT_INITIAL_JOINTS,
)

CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "cloudrobo_r2c"
    / "config"
    / "robot_agibot_g01_mock_config.yaml"
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _arm_names() -> List[str]:
    return [f"left_arm_joint{i}" for i in range(1, 8)] + [
        f"right_arm_joint{i}" for i in range(1, 8)
    ]


def _pose_dim_names(side: str, pose_repr: str = "quat") -> List[str]:
    dims = (
        ["x", "y", "z", "rx", "ry", "rz", "rw"]
        if pose_repr == "quat"
        else ["x", "y", "z", "roll", "pitch", "yaw"]
    )
    return [f"{side}_pose_{d}" for d in dims]


def _pose_names(pose_repr: str = "quat") -> List[str]:
    return _pose_dim_names("left", pose_repr) + _pose_dim_names("right", pose_repr)


def _connect(config: Dict[str, Any] = None) -> AgiBotG01MockAdapter:
    adapter = create_agibot_g01_mock_adapter(config or {})
    adapter.connect()
    return adapter


def _register_commands(adapter: AgiBotG01MockAdapter) -> None:
    """与工厂路径一致：按 ``hardware.config.commands`` 实例化已注册的命令类。"""
    from cloudrobo_r2c.robots.robot_factory import RobotFactory

    RobotFactory._register_adapter_commands(adapter, adapter._config)


@pytest.fixture
def _mock_hardware_type_known():
    """本地环境未重装包时，schema 校验所需的 entry_point 名单注入。"""
    from cloudrobo_r2c.robots.robot_factory import AdapterRegistry

    AdapterRegistry._ensure_scanned()
    injected = "agibot_g01_mock" not in AdapterRegistry._entry_points
    if injected:
        # available_types() 只消费键名；真实安装后由 entry_points 自动发现
        AdapterRegistry._entry_points["agibot_g01_mock"] = None
    try:
        yield
    finally:
        if injected:
            AdapterRegistry._entry_points.pop("agibot_g01_mock", None)


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


def test_factory_creates_mock_adapter_with_defaults():
    adapter = create_agibot_g01_mock_adapter({})
    assert isinstance(adapter, AgiBotG01MockAdapter)
    assert not adapter.is_connected
    # 初始状态默认取 joint_a / pose_a
    assert adapter._initial_joints == pytest.approx(DEFAULT_INITIAL_JOINTS)
    assert adapter._initial_ee_poses["left"] == pytest.approx(DEFAULT_INITIAL_EE_POSES["left"])
    assert adapter._initial_ee_poses["right"] == pytest.approx(DEFAULT_INITIAL_EE_POSES["right"])
    assert adapter._initial_gripper == pytest.approx(DEFAULT_INITIAL_GRIPPER)


def test_connect_disconnect_lifecycle():
    adapter = _connect()
    assert adapter.is_connected
    client = adapter._client
    assert client is not None and client.is_connected
    adapter.disconnect()
    assert not adapter.is_connected
    assert not client.is_connected
    # 幂等
    adapter.disconnect()
    adapter.connect()
    adapter.connect()
    assert adapter.is_connected


def test_requires_connection_for_observation_and_action():
    adapter = create_agibot_g01_mock_adapter({})
    with pytest.raises(RuntimeError, match="not connected"):
        adapter.get_observation()
    with pytest.raises(RuntimeError, match="not connected"):
        adapter.send_action({"arm_cmd_joint_pos": [0.0] * ARM_DOF})


# ---------------------------------------------------------------------------
# 初始状态：joint_a / pose_a 基线
# ---------------------------------------------------------------------------


def test_initial_observation_matches_joint_a_and_pose_a():
    adapter = _connect()
    obs = adapter.get_observation()
    assert obs["id"] == 1
    assert isinstance(obs["timestamp"], int)
    # 双臂 14 关节 = joint_a
    assert list(obs["joint_positions"]) == pytest.approx(DEFAULT_INITIAL_JOINTS)
    # 夹爪（原始 [35, 120] 归一化后）= joint_a 的 [0.15, 0.15]
    assert list(obs["gripper_positions"]) == pytest.approx(DEFAULT_INITIAL_GRIPPER)
    # 末端位姿 = pose_a 的 left/right（四元数）
    assert obs["left_ee_pose"] == pytest.approx(DEFAULT_INITIAL_EE_POSES["left"])
    assert obs["right_ee_pose"] == pytest.approx(DEFAULT_INITIAL_EE_POSES["right"])
    assert obs["ee_poses"][0] == pytest.approx(obs["left_ee_pose"])
    assert obs["ee_poses"][1] == pytest.approx(obs["right_ee_pose"])
    assert list(obs["ee_poses_data"]["names"]) == ["left", "right"]
    # 灵巧手/头/腰/整体姿态初始零位
    assert list(obs["hand_joint_positions"]) == [0.0] * HAND_DOF
    assert list(obs["head_joint_positions"]) == pytest.approx([0.0, 0.0])
    assert list(obs["waist_joint_positions"]) == pytest.approx([0.0, 0.0])
    assert list(obs["body_pose"]) == []
    # 合成相机帧按 camera_key_map 暴露
    assert obs["cam_high"].shape == (48, 64, 3)
    assert obs["cam_high"].dtype == np.uint8
    assert obs["cam_left_wrist"].shape == (48, 64, 3)
    assert obs["cam_right_wrist"].shape == (48, 64, 3)
    # 各通道底色不同
    assert not np.array_equal(obs["cam_high"], obs["cam_left_wrist"])
    # 第二帧 id 自增
    assert adapter.get_observation()["id"] == 2


def test_default_baseline_matches_cloud_adapter_joint_a_and_pose_a():
    """mock 默认初始状态必须与云端示例 GROUP_A（joint_a）/ GROUP_POSE_A（pose_a）一致。"""
    pytest.importorskip("zenoh")
    examples_dir = Path(__file__).resolve().parents[1] / "examples"
    sys.path.insert(0, str(examples_dir))
    try:
        import agibot_g01_cloud_adapter as demo
    finally:
        sys.path.remove(str(examples_dir))
    assert DEFAULT_INITIAL_JOINTS == pytest.approx(demo.GROUP_A["joints"])
    assert DEFAULT_INITIAL_GRIPPER == pytest.approx(demo.GROUP_A["gripper"])
    assert DEFAULT_INITIAL_EE_POSES["left"] == pytest.approx(demo.GROUP_POSE_A["left_pose"])
    assert DEFAULT_INITIAL_EE_POSES["right"] == pytest.approx(demo.GROUP_POSE_A["right_pose"])


def test_initial_state_overrides_and_validation_errors():
    adapter = _connect({"initial_joints": [0.1] * ARM_DOF, "initial_head": [0.2, -0.2]})
    obs = adapter.get_observation()
    assert list(obs["joint_positions"]) == pytest.approx([0.1] * ARM_DOF)
    assert list(obs["head_joint_positions"]) == pytest.approx([0.2, -0.2])

    with pytest.raises(ValueError, match="initial_joints"):
        create_agibot_g01_mock_adapter({"initial_joints": [0.0] * (ARM_DOF - 1)})
    with pytest.raises(ValueError, match="initial_ee_poses"):
        create_agibot_g01_mock_adapter({"initial_ee_poses": {"left": [0.0] * 3}})


# ---------------------------------------------------------------------------
# 动作 → 状态更新（全量控制路由）
# ---------------------------------------------------------------------------


def test_joint_action_updates_observation():
    adapter = _connect()
    adapter.send_action({"joint_names": _arm_names(), "arm_cmd_joint_pos": [0.1] * ARM_DOF})
    assert list(adapter.get_observation()["joint_positions"]) == pytest.approx([0.1] * ARM_DOF)


def test_partial_joint_action_rejected():
    # 互斥/完整性校验继承自真机适配器：14 维须齐全，不全不硬执行
    adapter = _connect()
    adapter.send_action({"joint_names": _arm_names()[:13], "arm_cmd_joint_pos": [0.1] * 13})
    obs = adapter.get_observation()
    assert list(obs["joint_positions"]) == pytest.approx(DEFAULT_INITIAL_JOINTS)


def test_ee_pose_action_updates_motion_status():
    left = [0.5, 0.0, 0.5, 0.0, 0.0, 0.0, 1.0]
    right = [0.4, 0.1, 0.6, 0.0, 0.0, 0.0, -1.0]
    adapter = _connect()
    adapter.send_action({"joint_names": _pose_names("quat"), "arm_cmd_joint_pos": left + right})
    obs = adapter.get_observation()
    assert obs["left_ee_pose"] == pytest.approx(left)
    assert obs["right_ee_pose"] == pytest.approx(right)
    # 位姿路由不更新关节状态
    assert list(obs["joint_positions"]) == pytest.approx(DEFAULT_INITIAL_JOINTS)


def test_ee_channel_euler_action_converted_to_quat():
    from cloudrobo_r2c.robots.agibot_g01.agibot_g01_hardware_adapter import (
        _euler_rpy_to_quat,
    )

    adapter = _connect()
    # joint_states 仅夹爪（无关节/位姿分量）→ 允许经 ee 通道下发 6 维欧拉角位姿
    adapter.send_action(
        {
            "joint_names": ["left_gripper", "right_gripper"],
            "arm_cmd_joint_pos": [0.3, 0.3],
            "left_pose_cmd_ee": [0.2, 0.0, 0.3, 0.1, -0.2, 0.3],
        }
    )
    obs = adapter.get_observation()
    qx, qy, qz, qw = _euler_rpy_to_quat(0.1, -0.2, 0.3)
    assert obs["left_ee_pose"] == pytest.approx([0.2, 0.0, 0.3, qx, qy, qz, qw])
    # 右侧未下发，保持 pose_a
    assert obs["right_ee_pose"] == pytest.approx(DEFAULT_INITIAL_EE_POSES["right"])


def test_joint_position_control_updates_arm():
    adapter = _connect({"joint_control_interface": "robot_controller"})
    left = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
    right = [0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4]
    adapter.send_action({"joint_names": _arm_names(), "arm_cmd_joint_pos": left + right})
    assert list(adapter.get_observation()["joint_positions"]) == pytest.approx(left + right)


def test_gripper_action_accepts_normalized_and_raw():
    adapter = _connect()
    adapter.send_action(
        {"joint_names": ["left_gripper", "right_gripper"], "arm_cmd_joint_pos": [0.5, 0.9]}
    )
    assert list(adapter.get_observation()["gripper_positions"]) == pytest.approx([0.5, 0.9])
    # 原始量程输入（[35, 120]）：90 → (90 - 35) / 85
    adapter.send_action(
        {"joint_names": ["left_gripper", "right_gripper"], "arm_cmd_joint_pos": [90.0, 120.0]}
    )
    assert list(adapter.get_observation()["gripper_positions"]) == pytest.approx([55.0 / 85.0, 1.0])


def test_partial_gripper_rejected():
    adapter = _connect()
    adapter.send_action({"joint_names": ["left_gripper"], "arm_cmd_joint_pos": [0.5]})
    assert list(adapter.get_observation()["gripper_positions"]) == pytest.approx(
        DEFAULT_INITIAL_GRIPPER
    )


def test_ext_parts_update_observation():
    adapter = _connect()
    payload = struct.pack("<2f", 0.2, -0.1)
    adapter.send_action(
        {
            "head_cmd_joint_pos": {
                "dtype": "FLOAT32",
                "shape": [2],
                "data": payload,
                "mime_type": "",
            },
            "waist_cmd_joint_pos": [0.1, 20.0],
            "hand_cmd_joint_pos": [0.05] * HAND_DOF,
            "wheel_cmd_speed": [0.3, 0.0],
        }
    )
    obs = adapter.get_observation()
    assert list(obs["head_joint_positions"]) == pytest.approx([0.2, -0.1])
    assert list(obs["waist_joint_positions"]) == pytest.approx([0.1, 20.0])
    assert list(obs["hand_joint_positions"]) == pytest.approx([0.05] * HAND_DOF)
    # 底盘速度记录在 mock 状态中（观测无对应字段）
    assert adapter._client._wheel_speed == pytest.approx([0.3, 0.0])


def test_dry_run_does_not_update_state():
    adapter = _connect({"dry_run": True})
    adapter.send_action({"joint_names": _arm_names(), "arm_cmd_joint_pos": [0.5] * ARM_DOF})
    obs = adapter.get_observation()
    assert list(obs["joint_positions"]) == pytest.approx(DEFAULT_INITIAL_JOINTS)


def test_go_home_resets_to_joint_a():
    adapter = _connect(
        {
            "commands": {
                "go_home": {
                    "type": "go_home",
                    "arm_positions": list(DEFAULT_INITIAL_JOINTS),
                    "gripper_positions": list(DEFAULT_INITIAL_GRIPPER),
                }
            }
        }
    )
    _register_commands(adapter)
    adapter.send_action({"joint_names": _arm_names(), "arm_cmd_joint_pos": [0.9] * ARM_DOF})
    assert adapter.execute_command("go_home") is True
    obs = adapter.get_observation()
    assert list(obs["joint_positions"]) == pytest.approx(DEFAULT_INITIAL_JOINTS)
    assert list(obs["gripper_positions"]) == pytest.approx(DEFAULT_INITIAL_GRIPPER)


def test_camera_disabled_omits_images():
    adapter = _connect({"camera_enabled": False})
    obs = adapter.get_observation()
    assert "cam_high" not in obs
    assert adapter._client.get_camera_images() == {}


def test_ee_poses_absent_when_motion_controller_disabled():
    adapter = _connect({"enable_motion_controller": False})
    obs = adapter.get_observation()
    assert "left_ee_pose" not in obs
    assert "ee_poses_data" not in obs
    # 关节观测不受影响
    assert list(obs["joint_positions"]) == pytest.approx(DEFAULT_INITIAL_JOINTS)


# ---------------------------------------------------------------------------
# state/current（get_runtime）
# ---------------------------------------------------------------------------


def test_get_runtime_frame_contents():
    adapter = _connect({"location": "mock-site"})
    frame = adapter.get_runtime()
    names = frame["joint_states"]["names"]
    positions = frame["joint_states"]["position"]
    # 默认通道：joint_states 仅双臂 14 关节；夹爪/位姿在专用字段
    assert names == _arm_names()
    assert positions == pytest.approx(DEFAULT_INITIAL_JOINTS)
    assert frame["end_effector_states"]["names"] == ["left_gripper", "right_gripper"]
    assert frame["end_effector_states"]["position"] == pytest.approx(DEFAULT_INITIAL_GRIPPER)
    assert frame["end_effector_poses"]["names"] == ["left", "right"]
    assert frame["end_effector_poses"]["pose"][0] == pytest.approx(DEFAULT_INITIAL_EE_POSES["left"])
    assert frame["location"] == "mock-site"


def test_mirrored_channels_variant():
    # ee_pose_channel / gripper_channel 选 joint_states（镜像），ee_pose_repr euler
    adapter = _connect(
        {
            "ee_pose_channel": "joint_states",
            "gripper_channel": "joint_states",
            "ee_pose_repr": "euler",
        }
    )
    obs = adapter.get_observation()
    # 位姿镜像（euler，每侧 6 维）与夹爪镜像字段
    assert len(obs["ee_pose_names"]) == 12
    assert obs["ee_pose_names"][:6] == _pose_dim_names("left", "euler")
    assert len(obs["ee_poses_flat"]) == 12
    assert "ee_poses_data" not in obs
    assert obs["joint_gripper_names"] == ["left_gripper", "right_gripper"]
    frame = adapter.get_runtime()
    # state/current 与观测同一选择：14 + 位姿段 12 + 夹爪段 2
    assert len(frame["joint_states"]["position"]) == ARM_DOF + 12 + GRIPPER_DOF
    assert frame["joint_states"]["names"][-2:] == ["left_gripper", "right_gripper"]
    assert "end_effector_poses" not in frame


# ---------------------------------------------------------------------------
# 配置 YAML + 翻译链路（ConfigurableDeviceTranslator）
# ---------------------------------------------------------------------------


def _load_mock_config() -> Dict[str, Any]:
    assert CONFIG_PATH.exists(), f"missing shipped config: {CONFIG_PATH}"
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def test_mock_config_yaml_passes_schema_validation(_mock_hardware_type_known):
    from cloudrobo_r2c.common.robot_config_schema import RobotConfig

    config = _load_mock_config()
    assert config["hardware"]["type"] == "agibot_g01_mock"
    RobotConfig.model_validate(config)


def test_mock_config_yaml_translates_observation_and_action(_mock_hardware_type_known):
    from cloudrobo_r2c.common.models import Observations
    from cloudrobo_r2c.translators.device_translator import ConfigurableDeviceTranslator

    translator = ConfigurableDeviceTranslator.from_config(_load_mock_config())
    adapter = _connect()
    observation = translator.device_to_r2c(adapter.get_observation())
    assert isinstance(observation, Observations)
    # 默认通道：joint_states 仅双臂 14 关节；夹爪/位姿走专用字段
    assert len(observation.joint_states.position) == ARM_DOF
    assert observation.joint_states.names[0] == "left_arm_joint1"
    assert observation.end_effector_states.position == pytest.approx(DEFAULT_INITIAL_GRIPPER)
    assert list(observation.end_effector_poses.names) == ["left", "right"]
    assert observation.end_effector_poses.pose[0] == pytest.approx(DEFAULT_INITIAL_EE_POSES["left"])
    jpeg = observation.images.color.get("cam_high")
    assert jpeg is not None and jpeg[:2] == b"\xff\xd8"
    head_ext = observation.extensions["head_joint"]
    assert list(np.frombuffer(head_ext.data, dtype="<f4")) == pytest.approx([0.0, 0.0])

    # 云端动作 → 适配器 → mock 状态更新
    step = {
        "timestamp": 1,
        "id": 1,
        "chunk_index": 0,
        "joint_states": {
            "names": _arm_names(),
            "position": [0.2] * ARM_DOF,
            "velocity": [],
            "torque": [],
        },
        "end_effector_states": {
            "names": ["left_gripper", "right_gripper"],
            "position": [0.4, 0.6],
            "velocity": [],
            "torque": [],
        },
        "end_effector_poses": {},
        "localization": {"odom_pose": [], "map_pose": []},
        "extensions": {},
    }
    device_command = translator.r2c_to_device(step)
    assert device_command["joint_names"] == _arm_names()
    assert device_command["arm_cmd_joint_pos"] == [0.2] * ARM_DOF
    adapter.send_action(device_command)
    obs = adapter.get_observation()
    assert list(obs["joint_positions"]) == pytest.approx([0.2] * ARM_DOF)
    assert list(obs["gripper_positions"]) == pytest.approx([0.4, 0.6])


# ---------------------------------------------------------------------------
# entry point 注册
# ---------------------------------------------------------------------------


def test_entry_point_registered_when_installed():
    eps = entry_points(group="r2c_sdk.adapters")
    mock_eps = [ep for ep in eps if ep.name == "agibot_g01_mock"]
    if not mock_eps:
        pytest.skip(
            "hw-cloudrobo-r2c 未在本环境重装（editable 指向旧副本）；"
            "pyproject.toml 已注册 agibot_g01_mock entry point"
        )
    factory = mock_eps[0].load()
    assert factory is create_agibot_g01_mock_adapter
