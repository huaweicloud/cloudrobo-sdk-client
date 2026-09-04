"""MetaEngine仿真环境SO101 机器人侧 R2CClient 示例模块。

该模块负责：
- 按配置订阅相机、关节、末端等数据并组装 observation。
- 使用 r2c sdk 发布 observation。
- 订阅云端 actions 并转发到机器人侧 topic。
"""

from __future__ import annotations

import logging
import math
import time

from cloudrobo_r2c.common import Actions
from cloudrobo_r2c.common.models import Observations, ObservationsH264
from robot_common_utils import load_mapping_file, ROBOT_R2C_MAPPING_FILE, build_r2c_config

from ros_adapter.config import R2CSDKConfig
from ros_adapter.r2c_client_for_me import R2CClientForME

logger = logging.getLogger(__name__)


def build_sdk_config() -> R2CSDKConfig:
    """构建R2C SDK连接配置。

    请根据实际环境修改以下参数：
    - project_id: 项目标识
    - device_id: 设备唯一标识
    - client_id: 客户端标识
    - endpoints: R2C服务端点地址
    """
    sdk_config = R2CSDKConfig(
        project_id="test-tenant",
        device_id="so101-robot-01",
        client_id="ros2-so101-client-01",
        endpoints=["tcp/0.0.0.0:7447"],
        mode="peer",
        protocol="zenoh"
    )
    return sdk_config


class SO101Client(R2CClientForME):
    """SO101机器人R2C客户端。

    负责将云端动作转换为SO101机器人控制指令。
    """

    def convert_action_value_to_joint(self, action_value: float) -> float:
        """将gripper的单一值转换为实际关节值。
        输入参数 width
        输出关节值: 压缩至-0.17-0.52之间

        参数:
            action_value: gripper的输入值（如0.5表示打开，-0.17表示关闭）

        返回:
            实际关节值
        """
        return math.radians(action_value / 2.5 - 10)

    def convert_observation_to_gripper_value(self, obs_value: float) -> float:
        """将实际关节值转换为gripper的单一值。
        输入关节值
        输出值: 转换至0~100区间

        参数:
            obs_value: 实际关节值

        返回:
            gripper的单一值
        """
        return min((math.degrees(obs_value)+10) * 2.5, 100)

    def convert_to_observation(self, frame_data):
        """将机器人数据转换为R2C观测数据。

        Args:
            frame_data: 包含images、joint_states、end_effector_poses、end_effector_states的字典

        Returns:
            Observations 或 ObservationsH264 对象
        """
        # 计算夹爪的单一值（从实际关节反推）
        joint_positions = frame_data.get("joint_states", [])
        gripper_idx = self._config.joint_states.joint_names.index('gripper')
        gripper_value = self.convert_observation_to_gripper_value(joint_positions[gripper_idx])
        joint_positions[gripper_idx] = gripper_value

        obs_data = {
            "timestamp": int(time.time() * 1000),
            "task": f"{self._task}",
            "id": self._sequence,
            "images": frame_data.get("images", {}),
            "joint_states": {
                "names": (
                    [self._config.robot_to_model_joint_names_mapping[joint_name] for joint_name in
                     self._config.joint_states.joint_names]
                    if self._config.joint_states
                    else []
                ),
                "position": joint_positions,
                "velocity": [],
                "torque": [],
            },
            "end_effector_poses": {"names": [], "pose": []},
            "end_effector_states": {
                "names": [],
                "position": [],
                "force": [],
            },
        }
        image_sources = frame_data.get("images", {})

        if not image_sources:
            raise ValueError("No images found in observation")

        if self._config.image_encode == "h264":
            encoded_payload, _ = self.encoder.encode_images({"images": image_sources})
            images_section = encoded_payload.get("images", {})
            obs_data["images"] = {
                "h264_data": images_section.get("h264_data", b""),
                "metadata": images_section.get("metadata", {}),
            }
            obs = ObservationsH264.from_dict(obs_data)
        else:
            obs_data["images"] = image_sources
            obs = Observations.from_dict(obs_data)

        return obs

    def convert_actions(self, actions: Actions) -> Actions:
        """将云端动作转换为机器人控制器指令。

        Args:
            actions: 云端下发的动作对象

        Returns:
            Actions: 转换后的Actions对象，包含机器人实际需要的关节数据

        云端动作格式：
        - joint_states.position: 5个手臂关节 + 1个夹爪（共6个），包含多个时间步（chunk_size）

        机器人实际情况：
        - 手臂关节: 5个关节直接控制
        - 夹爪: 需要使用系数转换：joint_value = 7 * gripper_value
        """
        logger.info("SO101 client convert_actions")

        # 转换关节名称映射（从模型名称到机器人名称）
        # joint_6为夹爪，当前在joint_names里面一通传递，需要单独处理这个值
        gripper_idx = actions.joint_states.names.index('joint_6')

        # 更新关节名称列表（移除夹爪）
        actions.joint_states.names = [
            self._config.model_to_robot_joint_names_mapping[joint_name]
            for joint_name in actions.joint_states.names if joint_name != 'joint_6'
        ]

        # 提取夹爪位置数据（从每个时间步中提取第6个关节的值）
        # position 是一个2D列表：[[step1_joints], [step2_joints], [step3_joints]]
        gripper_positions = []
        arm_positions = []

        for step_idx, step_positions in enumerate(actions.joint_states.position):
            # 提取当前时间步的夹爪值
            gripper_value = step_positions[gripper_idx]
            # 转换为机器人夹爪关节值
            robot_gripper_value = self.convert_action_value_to_joint(gripper_value)
            gripper_positions.append([robot_gripper_value])

            # 移除夹爪值，保留手臂关节值
            arm_step_positions = [val for idx, val in enumerate(step_positions) if idx != gripper_idx]
            arm_positions.append(arm_step_positions)

        # 更新joint_states的position（只保留手臂关节）
        actions.joint_states.position = arm_positions

        # 更新end_effector_states（包含夹爪数据）
        actions.end_effector_states.names = ["gripper"]
        actions.end_effector_states.position = gripper_positions

        logger.info(f"Actions converted successfully")

        return actions


def main() -> None:
    """主函数：初始化并运行SO101机器人客户端。"""
    try:
        # 创建SO101客户端
        client = SO101Client("so101_app_node")

        # 配置客户端
        robot_r2c_mapping_info = load_mapping_file(ROBOT_R2C_MAPPING_FILE)
        client.configure(build_r2c_config(robot_r2c_mapping_info["so101"]), build_sdk_config())

        # 设置任务描述
        task = "pick and place task"

        # 执行任务（默认60秒超时）
        client.execute(task)

    except KeyboardInterrupt:
        logger.info("interrupted by user.")
    finally:
        client.close()


if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    main()
