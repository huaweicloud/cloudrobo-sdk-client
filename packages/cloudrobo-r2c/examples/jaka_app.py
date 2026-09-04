"""MetaEngine仿真环境Jaka 机器人侧 R2CClient 示例模块。

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
        device_id="jaka-robot-01",
        client_id="ros2-jaka-client-01",
        endpoints=["tcp/0.0.0.0:7447"],
        mode="peer",
        protocol="zenoh"
    )
    return sdk_config


class JakaClient(R2CClientForME):
    """Jaka机器人R2C客户端。

    负责将云端动作转换为Jaka机器人控制指令。
    """

    def convert_gripper_value_to_joints(self, gripper_value: float) -> list[float]:
        """将gripper的单一值转换为2个关节的值。

        根据jaka_gripper_controller.cpp的换算逻辑：
        输入参数 x (范围0-0.1)
        输出2维数组: [5.76*(x-0.1), -5.76*(x-0.1)]

        参数:
            gripper_value: gripper的输入值（如0.1表示打开，0.0表示关闭）

        返回:
            2个关节的值列表 [left_finger, right_finger]
        """
        return [
            5.76 * (gripper_value - 0.1),   # left_finger_joint
            -5.76 * (gripper_value - 0.1)   # right_finger_joint
        ]

    def convert_to_observation(self, frame_data):
        """将机器人数据转换为R2C观测数据。

        Args:
            frame_data: 包含images、joint_states、end_effector_poses、end_effector_states的字典

        Returns:
            Observations 或 ObservationsH264 对象
        """
        end_effector_poses = frame_data.get("end_effector_poses", {})
        poses_names = end_effector_poses.keys()
        poses_values = end_effector_poses.values()

        # 从end_effector_states中提取夹爪的2个关节状态
        end_effector_states = frame_data.get("end_effector_states", [])
        end_effector_states_dict = {name: value for name, value in
                                    zip(self._config.end_effector_states.joint_names, end_effector_states)}

        # 计算夹爪的单一值（从2个关节反推）
        # 使用 left_finger_joint 的值来反推：gripper_value = left_finger_joint / 5.76 + 0.1
        gripper_value = 0.0

        if "left_finger_joint" in end_effector_states_dict:
            gripper_value = end_effector_states_dict["left_finger_joint"] / 5.76 + 0.1

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
                    ["gripper"]
                    if self._config.end_effector_states
                    else []
                ),
                "position": [
                    gripper_value * 1000  # 转换到0-100的区间
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

        return obs

    def convert_actions(self, actions: Actions) -> Actions:
        """将云端动作转换为机器人控制器指令。

        Args:
            actions: 云端下发的动作对象

        Returns:
            Actions: 转换后的Actions对象，包含机器人实际需要的关节数据

        云端动作格式：
        - joint_states.position: 6个手臂关节
        - end_effector_states.position: 1个夹爪值

        机器人实际情况：
        - 手臂关节直接对应6个关节
        - 夹爪topic需要2个数据点，需要使用系数扩展
        - gripper_joints: 2个关节 [left_finger_joint, right_finger_joint]
        """
        logger.info("Jaka client convert_actions")

        # 转换关节名称映射（从模型名称到机器人名称）
        actions.joint_states.names = [self._config.model_to_robot_joint_names_mapping[joint_name.replace("_exp", "")]
                                      for joint_name in actions.joint_states.names]

        # 处理末端执行器状态（单夹爪）
        if actions.end_effector_states.position:
            for i, ee_position in enumerate(actions.end_effector_states.position):
                end_effector_states_dict = {name: value for name, value in
                                            zip(actions.end_effector_states.names, ee_position)}
                # 需要使用系数扩展为2个关节值
                gripper_value = end_effector_states_dict.get("gripper_exp", 0.0)

                # 使用系数扩展为2个关节
                gripper_value = max(gripper_value,20)
                gripper_joints = self.convert_gripper_value_to_joints(gripper_value / 1000)

                # 更新末端执行器状态
                actions.end_effector_states.names = [
                    "left_finger_joint",
                    "right_finger_joint"
                ]
                actions.end_effector_states.position[i] = gripper_joints

        logger.info(f"Actions converted successfully")

        return actions


def main() -> None:
    """主函数：初始化并运行Jaka机器人客户端。"""
    try:
        # 创建Jaka客户端
        client = JakaClient("jaka_app_node")

        # 配置客户端
        robot_r2c_mapping_info = load_mapping_file(ROBOT_R2C_MAPPING_FILE)
        client.configure(build_r2c_config(robot_r2c_mapping_info["jaka_minicobo"]), build_sdk_config())

        # 设置任务描述
        task = "move arm to target position"

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
