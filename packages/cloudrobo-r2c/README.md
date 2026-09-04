# cloudrobo-r2c

CloudRobo Robot-to-Cloud (R2C) 数据面 SDK，基于 Zenoh pub/sub 传输、Protobuf 序列化、H264 图像编码，提供机器人端观测数据上报与云端控制指令下发。

## 安装

```bash
# 轻量核心（仅 CLI + 配置校验，不含 Zenoh/视频编解码）
pip install cloudrobo-r2c

# 边缘端完整运行时（Zenoh + Protobuf + H264 + OpenCV）
pip install cloudrobo-r2c[client]

# 云端推理适配器（Zenoh + Protobuf + NumPy）
pip install cloudrobo-r2c[cloud-adapter]

# 全部（含各机器人硬件 SDK 可选依赖）
pip install cloudrobo-r2c[all]
```

## 快速开始

以下示例假设当前目录为 `packages/cloudrobo-r2c/`：

```bash
# 启动 R2C 客户端（长运行）
cloudrobo r2c client --bundle config/<cert-config.zip> --robot-config config/robot_dummy_config.yaml
```

## 文档

| 文档 | 说明 |
|------|------|
| [概览](docs/index.md) | 模块介绍与安装 |
| [命令](docs/commands.md) | CLI 命令详情 |
| [示例](docs/examples.md) | SDK/CLI 使用示例 |
| [开发](docs/development.md) | 开发环境与测试 |
