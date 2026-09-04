"""MetaEngine仿真环境AzureLoong 机器人侧 R2CClient 示例模块。

该模块负责：
- 按配置订阅相机、关节、末端等数据并组装 observation。
- 使用 r2c sdk 发布 observation。
- 订阅云端 actions 并转发到机器人侧 topic。
"""

from __future__ import annotations

import logging
import time

from cloudrobo_r2c.common import Actions
from cloudrobo_r2c.common.models import Observations, ObservationsH264

from ros_adapter.config import R2CClientConfig, R2CSDKConfig
from ros_adapter.r2c_client_for_me import R2CClientForME
from robot_common_utils import build_r2c_config, load_mapping_file, ROBOT_R2C_MAPPING_FILE

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
        device_id="azureloong-robot-01",
        client_id="ros2-azureloong-client-01",
        endpoints=["tcp/0.0.0.0:7447"],
        mode="peer",
        protocol="zenoh"
    )
    return sdk_config


class AzureLoongClient(R2CClientForME):
    """AzureLoong机器人R2C客户端。

    负责将云端动作转换为AzureLoong机器人控制指令。
    """

    def get_visual_joint_values(self, target_gripper_joint_pos, current_gripper_joint_pos):
        # 计算真实夹爪的关节点位置
        left_gripper_command = target_gripper_joint_pos + 0.05 * (target_gripper_joint_pos - current_gripper_joint_pos)
        width = left_gripper_command * 2
        command_joint = width * -10
        res = {
            "Left_1_Joint": command_joint,
            "Left_2_Joint": command_joint * -1,
            "Left_in_Joint": command_joint * -0.2,
            "Left_up_Joint": command_joint * -1,
            "Right_1_Joint": command_joint * 1,
            "Right_2_Joint": command_joint * 1,
            "Right_in_Joint": command_joint * 0.2,
            "Right_up_Joint": command_joint * -1
        }
        return res

    def get_visual_joints_values_for_action(self, width: float):
        command_joint = width * -10
        return [
            command_joint,
            command_joint * -1,
            command_joint * -0.2,
            command_joint * -1,
            command_joint * 1,
            command_joint * 1,
            command_joint * 0.2,
            command_joint * -1
        ]

    def convert_to_observation(self, frame_data):
        end_effector_poses = frame_data.get("end_effector_poses", {})
        poses_names = end_effector_poses.keys()
        poses_values = end_effector_poses.values()

        end_effector_states = frame_data.get("end_effector_states", [])
        end_effector_states_dict = {name: value for name, value in
                                    zip(self._config.end_effector_states.joint_names, end_effector_states)}

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
                "position": frame_data.get("joint_states", []),
                "velocity": [],
                "torque": [],
            },
            "end_effector_poses": {"names": poses_names, "pose": poses_values},
            "end_effector_states": {
                "names": (
                    ["left_gripper", "right_gripper"]
                    if self._config.end_effector_states
                    else []
                ),
                "position": [
                    0 if end_effector_states_dict["left_gripper_left_finger_joint"] * 2000 < 90 else 100,
                    0 if end_effector_states_dict["right_gripper_left_finger_joint"] * 2000 < 90 else 100
                ],
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
        # 计算真实夹爪的关节点位置

        self.visual_left_gripper_pos = self.get_visual_joint_values(
            end_effector_states_dict["left_gripper_left_finger_joint"],
            end_effector_states_dict["left_gripper_Left_1_Joint"] / (-10.0)
        )
        self.visual_right_gripper_pos = self.get_visual_joint_values(
            end_effector_states_dict["right_gripper_left_finger_joint"],
            end_effector_states_dict["right_gripper_Left_1_Joint"] / (-10.0)
        )
        return obs

    def convert_actions(self, actions: Actions) -> dict[str, list]:
        actions.joint_states.names = [self._config.model_to_robot_joint_names_mapping[joint_name.replace("_exp", "")]
                                      for joint_name in actions.joint_states.names]

        # 处理每个时间步的末端执行器状态
        if actions.end_effector_states.position:
            converted_positions = []
            for ee_position in actions.end_effector_states.position:
                # 使用原始名称创建字典（在更新names之前）
                end_effector_states_dict = {name: value for name, value in
                                            zip(actions.end_effector_states.names, ee_position)}

                converted_position = [
                    end_effector_states_dict["left_gripper_exp"] / 2000.,
                    end_effector_states_dict["left_gripper_exp"] / 2000.,
                    end_effector_states_dict["right_gripper_exp"] / 2000.,
                    end_effector_states_dict["right_gripper_exp"] / 2000.,
                    *self.get_visual_joints_values_for_action(end_effector_states_dict["left_gripper_exp"] / 1000),
                    *self.get_visual_joints_values_for_action(end_effector_states_dict["right_gripper_exp"] / 1000),
                ]
                converted_positions.append(converted_position)

            actions.end_effector_states.position = converted_positions

            # 更新末端执行器状态名称（在处理完数据之后）
            actions.end_effector_states.names = [
                "left_gripper_left_finger_joint",
                "left_gripper_right_finger_joint",
                "right_gripper_left_finger_joint",
                "right_gripper_right_finger_joint",
                "left_gripper_Left_1_Joint",
                "left_gripper_Left_2_Joint",
                "left_gripper_Left_in_Joint",
                "left_gripper_Left_up_Joint",
                "left_gripper_Right_1_Joint",
                "left_gripper_Right_2_Joint",
                "left_gripper_Right_in_Joint",
                "left_gripper_Right_up_Joint",
                "right_gripper_Left_1_Joint",
                "right_gripper_Left_2_Joint",
                "right_gripper_Left_in_Joint",
                "right_gripper_Left_up_Joint",
                "right_gripper_Right_1_Joint",
                "right_gripper_Right_2_Joint",
                "right_gripper_Right_in_Joint",
                "right_gripper_Right_up_Joint"
            ]

        return actions

def main() -> None:
    """主函数：初始化并运行AzureLoong机器人客户端。"""
    try:
        # 创建AzureLoong客户端
        client = AzureLoongClient("azureloong_app_node")

        # 配置客户端
        robot_r2c_mapping_info = load_mapping_file(ROBOT_R2C_MAPPING_FILE)
        client.configure(build_r2c_config(robot_r2c_mapping_info["qinglong"]), build_sdk_config())

        # 设置任务描述
        task = "move right arm to target position"

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
