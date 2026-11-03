# cloudrobo-r2c

CloudRobo Robot-to-Cloud (R2C) 数据面 SDK，基于 Zenoh pub/sub 传输、Protobuf 序列化、H264 图像编码，提供机器人端观测数据上报、云端控制指令下发、相机推流与数据面转发能力。

## 主要能力

- **观测/动作通道**：Observation / Action 双向数据流，支持 H264 图像编码
- **状态上报**：关节、末端执行器、定位、IMU、心跳（battery 可选）
- **自定义 Topic**：`hardware.topics` 声明式绑定，支持定频发布与订阅
- **事件上报**：`EventManager` 单例推送告警事件（2Hz 限频 + 缓存补发）
- **相机推流**：LiveKit WebRTC 推流（`camera/`），支持 RealSense / V4L2 / 视频 / 静态图 / 随机图帧源
- **数据面转发**：`forwarder.py` 将本地主链路数据透传到云端会话（`--forwarder-config`）
- **运行模式**：`ModeControl` 统一管理 AUTO / CONTROLLED / ASSISTED 遥操与辅助模式
- **Proto 扩展**：`r2c_sdk.protos` entry-point 注册第三方 proto

## 安装

```bash
# 轻量核心（仅 CLI + 配置校验，不含 Zenoh/视频编解码）
pip install hw-cloudrobo-r2c

# 边缘端完整运行时（Zenoh + Protobuf + H264 + OpenCV）
pip install hw-cloudrobo-r2c[client]

# 云端推理适配器（Zenoh + Protobuf + NumPy + websockets/requests）
pip install hw-cloudrobo-r2c[cloud-adapter]

# 相机推流（LiveKit + RealSense；包含 client 运行时）
pip install hw-cloudrobo-r2c[camera]

# 全部（含各机器人硬件 SDK 可选依赖）
pip install hw-cloudrobo-r2c[all]
```

> `livekit==1.1.20` 已在主依赖中声明（与上游 r2c_sdk 0.2.8 一致）；livekit 要求 Python ≥ 3.9，因此该依赖在 Python 3.8 下自动跳过。相机推流的其余运行时依赖由 `camera` / `client` extra 提供。

## 快速开始

以下示例假设当前目录为 `packages/cloudrobo-r2c/`：

```bash
# 启动 R2C 客户端（长运行）
cloudrobo r2c client --bundle config/<cert-config.zip> --robot-config config/robot_dummy_config.yaml

# 启用数据面转发（主链路 + 云端 forwarder 会话）
cloudrobo r2c client --bundle config/<cert-config.zip> \
  --robot-config config/robot_dummy_realcam_config.yaml \
  --forwarder-config config/forwarder_config.yaml
```

最小 Python 示例：

```python
from cloudrobo_r2c import R2CClient, ClientConfig

config = ClientConfig.from_yaml("config/client_config.yaml")
client = R2CClient.connect(config)

client.subscribe_actions(lambda action: print(action.joint_states.position))
client.publish_observations({
    "timestamp": 0,
    "task": "pick_and_place",
    "images": {"front": image_array},          # numpy ndarray
    "joint_states": {"names": ["j1"], "position": [0.0]},
}, image_encode="h264")
```

## 文档

| 文档 | 说明 |
|------|------|
| [概览](docs/index.md) | 模块介绍与安装 |
| [命令](docs/commands.md) | CLI 命令详情 |
| [示例](docs/examples.md) | SDK/CLI 使用示例 |
