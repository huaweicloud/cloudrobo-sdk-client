# cloudrobo-r2c

CloudRobo Robot-to-Cloud (R2C) 数据面 SDK，基于 Zenoh pub/sub 传输、Protobuf 序列化、jpeg\webp\png 图像编码。

## 概览

R2C SDK 提供机器人端与云端之间的实时数据通道：

- **机器人端**：采集传感器数据（图像、关节状态、定位等），发布 Observation
- **云端**：订阅 Observation，经推理后下发 Action 控制指令
- **传输层**：Zenoh pub/sub
- **序列化**：Protobuf（10 个 .proto 定义）
- **图像编码**：jpeg\webp\png 图像编码

## 安装

```bash
# 轻量核心（CLI + 配置校验）
pip install cloudrobo-r2c

# 边缘端完整运行时
pip install cloudrobo-r2c[client]

# 云端推理适配器
pip install cloudrobo-r2c[cloud-adapter]

# 按机器人型号安装硬件 SDK
pip install cloudrobo-r2c[ur5e]
pip install cloudrobo-r2c[flexiv]
pip install cloudrobo-r2c[jaka]
```

## 快速开始

以下示例假设当前目录为 `packages/cloudrobo-r2c/`：

```bash
# 启动 R2C 客户端
cloudrobo r2c client --client-config config/client_config.yaml --robot-config config/robot_dummy_config.yaml
```

## 核心概念

| 概念 | 说明 |
|------|------|
| R2CClient | Zenoh 会话客户端，管理连接、发布、订阅 |
| ClientConfig | 连接配置（project_id, device_id, endpoints, TLS, mode） |
| SyncRobotClient | 同步机器人客户端，集成硬件适配器 + 翻译器 + 会话 |
| 硬件适配器 | 抽象不同机器人厂商 SDK（13+ 型号，entry points 注册） |
| 翻译器 | 设备↔R2C 数据格式转换（device_translator + model_translator） |

## 详细文档

| 文档 | 说明 |
|------|------|
| [命令参考](commands.md) | CLI 命令详情 |
| [使用示例](examples.md) | SDK/CLI 使用示例 |
| [开发指南](development.md) | 开发环境与测试 |