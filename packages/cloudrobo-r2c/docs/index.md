# cloudrobo-r2c

CloudRobo Robot-to-Cloud (R2C) 数据面 SDK，基于 Zenoh pub/sub 传输、Protobuf 序列化、H264 / jpeg / webp / png 图像编码。

## 概览

R2C SDK 提供机器人端与云端之间的实时数据通道：

- **机器人端**：采集传感器数据（图像、关节状态、定位等），发布 Observation
- **云端**：订阅 Observation，经推理后下发 Action 控制指令
- **传输层**：Zenoh pub/sub
- **序列化**：Protobuf（19 个 .proto 定义）
- **图像编码**：H264（传输）/ jpeg / webp / png（扩展字段）
- **相机推流**：LiveKit WebRTC（可选，`camera` extra）
- **数据面转发**：forwarder 将本地主链路数据透传到云端会话

## 安装

```bash
# 轻量核心（CLI + 配置校验）
pip install hw-cloudrobo-r2c

# 边缘端完整运行时
pip install hw-cloudrobo-r2c[client]

# 云端推理适配器（含 WebSocket/HTTP policy client）
pip install hw-cloudrobo-r2c[cloud-adapter]

# 相机推流（LiveKit + RealSense，包含 client 运行时）
pip install hw-cloudrobo-r2c[camera]

# 按机器人型号安装硬件 SDK
pip install hw-cloudrobo-r2c[ur5e]
pip install hw-cloudrobo-r2c[flexiv]
pip install hw-cloudrobo-r2c[jaka]
pip install hw-cloudrobo-r2c[a1z]
```

## 快速开始

以下示例假设当前目录为 `packages/cloudrobo-r2c/`：

```bash
# 启动 R2C 客户端
cloudrobo r2c client --client-config config/client_config.yaml --robot-config config/robot_dummy_config.yaml

# 主链路 + 云端 forwarder
cloudrobo r2c client --bundle config/<cert-config.zip> \
  --robot-config config/robot_dummy_realcam_config.yaml \
  --forwarder-config config/forwarder_config.yaml
```

## 核心概念

| 概念 | 说明 |
|------|------|
| R2CClient | Zenoh 会话客户端，管理连接、发布、订阅 |
| ClientConfig | 连接配置（project_id, device_id, endpoints, TLS, mode） |
| SyncRobotClient | 同步机器人客户端，集成硬件适配器 + 翻译器 + 会话 + 心跳 + 自定义 topic |
| 硬件适配器 | 抽象不同机器人厂商 SDK（16+ 型号，entry points 注册） |
| 翻译器 | 设备↔R2C 数据格式转换（device_translator + configurable translator + model_translator） |
| CameraManager | LiveKit WebRTC 相机推流生命周期管理（`camera/`） |
| Forwarder | 本地主会话 ↔ 云端会话的原始数据透传（`forwarder.py`） |
| EventManager | 告警/事件单例发布（限频 + 缓存补发） |
| ModeControl | AUTO / CONTROLLED / ASSISTED 运行模式统一决策 |
| ProtoRegistry | 通过 `r2c_sdk.protos` entry-point 解析第三方 proto |

## 消息 Topic

| 类别 | 消息类型 | Topic 结构 |
| :--- | :--- | :--- |
| Inference | Observation | `{project}/{device}/inference/observations` |
| Inference | Action | `{project}/{device}/inference/actions` |
| State | JointState | `{project}/{device}/state/joint_states` |
| State | EndEffectorState | `{project}/{device}/state/end_effector_states` |
| State | LocalizationState | `{project}/{device}/state/localization_states` |
| State | IMUState | `{project}/{device}/state/imu_states` |
| State | Heartbeat | `{project}/{device}/state/heartbeats` |
| Runtime | RunTimeMessage | `{project}/{device}/state/current` |
| Event | EventMessage | `{project}/{device}/event` |
| Job | Command / Action | `{project}/{device}/job/command`、`{project}/{device}/job/action` |
| Job | TaskProcess | `{project}/{device}/job/task` |
| Camera | CameraCapability / CameraCommand | `{project}/{device}/device/camera_capability`、`{project}/{device}/job/command` |
| Token | TokenRenewRequest | `{project}/{device}/device/token_renew_request` |
| Ingestion | DataUploadProgress | `{project}/{device}/data_ingestion/upload_status` |

## 详细文档

| 文档 | 说明 |
|------|------|
| [命令参考](commands.md) | CLI 命令详情 |
| [使用示例](examples.md) | SDK/CLI 使用示例 |
