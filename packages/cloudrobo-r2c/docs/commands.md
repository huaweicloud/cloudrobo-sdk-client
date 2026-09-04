# CLI 命令参考

cloudrobo-r2c 通过 `cloudrobo r2c` 命令组提供以下子命令。

> **注意**：以下命令中的配置文件路径均为相对路径，需在 `packages/cloudrobo-r2c/` 目录下执行。

## r2c client

启动 R2C 客户端（长运行进程）。

```bash
cloudrobo r2c client [OPTIONS]
```

**常用选项**（透传到底层 argparse）：

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--bundle` | - | 平台证书 bundle zip/目录路径 |
| `--client-config` | config/client_config.yaml | R2C 客户端配置 YAML |
| `--robot-config` | config/robot_dummy_config.yaml | 机器人硬件/映射配置 |
| `--project-id` | - | 项目 ID |
| `--device-id` | - | 设备 ID |
| `--client-id` | - | 客户端 ID |
| `--endpoints` | - | Zenoh 端点（逗号分隔） |
| `--mode` | peer | 连接模式（peer/client） |
| `--duration` | 0 | 运行时长（秒，0=永久） |
| `--log-level` | INFO | 日志级别 |
| `--log-file` | - | 日志文件路径 |
| `--record` | - | 录制观测数据到 .pkl |
