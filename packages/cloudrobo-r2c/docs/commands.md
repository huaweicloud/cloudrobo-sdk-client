# CLI 命令参考

cloudrobo-r2c 通过 `cloudrobo r2c` 命令组提供以下子命令。

> **注意**：以下命令中的配置文件路径均为相对路径，需在 `packages/cloudrobo-r2c/` 目录下执行；找不到时会回退到已安装包内的 `config/` 目录。

## r2c client

启动 R2C 客户端（长运行进程）。

```bash
cloudrobo r2c client [OPTIONS]
```

**常用选项**（透传到底层 argparse）：

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--bundle` | - | 平台证书 bundle zip/目录路径（推荐） |
| `--client-config` | config/client_config.yaml | R2C 客户端配置 YAML |
| `--forwarder-config` | - | 数据面转发配置；仅在同时提供 `--bundle` 时启用 |
| `--robot-config` | config/robot_dummy_config.yaml | 机器人硬件/映射配置 |
| `--project-id` | r2c | 项目 ID |
| `--device-id` | local | 设备 ID |
| `--client-id` | - | 客户端 ID |
| `--endpoints` | - | Zenoh 端点（逗号分隔） |
| `--mode` | 取 YAML（缺省 peer） | 连接模式（peer/client）；显式指定后不再按 YAML/回退逻辑处理 |
| `--endpoint-role` | 取 YAML（缺省 connect） | Zenoh 端点角色（connect/listen） |
| `--hardware-class` | - | 自定义 `IRobotHardwareAdapter` 类路径（`pkg.mod.Class`） |
| `--translator-class` | - | 自定义 `IDeviceTranslator` 类路径（`pkg.mod.Class`） |
| `--private-key-password` | - | 加密 `server_key.pem` 的密码（避免写入 shell 历史） |
| `--private-key-password-env` | - | 从指定环境变量读取私钥密码 |
| `--prompt-password` | 关闭 | 需要密码时交互式提示 |
| `--no-prompt-password` | 关闭 | 需要密码时直接失败，不提示 |
| `--duration` | 0 | 运行时长（秒，0=永久） |
| `--log-level` | INFO | 日志级别（DEBUG/INFO/WARNING/...） |
| `--log-file` | - | 轮转日志文件路径（100 MB × 5） |
| `--record` | - | 录制观测数据到 `.pkl` |

> 连接回退：当 `--mode` 与 `--endpoint-role` 均未显式指定时，若默认连接失败，会以 `mode=peer, endpoint_role=listen` 重试一次。

## 配置能力

### 心跳（`runtime.heartbeat`）

```yaml
runtime:
  heartbeat:
    enabled: true            # 缺省开启
    interval_ms: 500         # 发布周期
    jitter_ms: 50            # 抖动
    status: ONLINE
    mode: AUTO               # AUTO / CONTROLLED / ASSISTED
    battery_percentage: 85   # 可选；不配置时心跳帧不含 battery 字段
```

运行期可通过 `R2CSession.set_heartbeat_mode()` 切换模式。

### 自定义 Topic（`hardware.topics`）

```yaml
hardware:
  topics:
    "{project_id}/{device_id}/state/current":
      proto: "cloudrobo_r2c.common.models:RunTimeMessage"
      direction: "publish"        # publish / subscribe
      rate_hz: 2
      collector: "get_runtime"    # publish：适配器方法名
      # on_message: "on_message"  # subscribe：适配器回调方法名
```

Topic 名称支持 `{project_id}` / `{device_id}` / `{client_id}` 占位符。

### 事件（EventManager）

适配器内部通过单例 `cloudrobo_r2c.client.event_manager.event_manager.publish(event_dict)` 上报告警事件；实际发布限频 2Hz，超频事件进入内存缓存并按 FIFO 补发。

### 运行模式（`runtime.mode_control`）

```yaml
runtime:
  mode_control:
    teleop_enter_cb: on_teleop_enter
    teleop_exit_cb: on_teleop_exit
    assist_enter_cb: on_assist_enter
    assist_exit_cb: on_assist_exit
```

云端 `job/command` 下发 `teleop_enter` / `teleop_exit` / `assist_enter` / `assist_exit` 后，模式立即切换并由心跳上报；`*_cb` 的值是硬件适配器上的方法名（未配置的回调会被跳过，模式仍会切换）。

### 相机推流（`camera_streaming`）

参考 `config/robot_dummy_realcam_config.yaml`：

```yaml
camera_streaming:
  enabled: true
  camera_device_map:
    front: "front"             # 相机方位 → hardware.config.image_specs 的帧源 key
    wrist: "wrist"
  default_width: 640
  default_height: 480
  default_fps: 20
  max_bitrate: 300000
  max_framerate: 20
  connect_timeout: 5.0
  dynacast: true
  jwt_renewal_min_window: 60
  auto_stop_on_no_viewer: true
```

帧源支持 `realsense` / `source`（V4L2）/ `video` / `file` / `h`+`w`+`c`（随机图）。云端下发 `CAMERA_START` 后开始推流，能力信息上报到 `device/camera_capability`。

### 数据面转发（`--forwarder-config`）

参考 `config/forwarder_config.yaml`：

```yaml
forwarder_session:
  mode: "client"

topic_defaults:
  sample_min_interval_s: 0.5
  cache_size: 20

routes:
  - from: "{project_id}/{device_id}/inference/observations"
    to: "{project_id}/{device_id}/inference/observations"
    from_session: "main"
    to_session: "forwarder_session"
    proto: "cloudrobo_r2c.common.models:Observations"
    cache_size: 0
    sample_min_interval_s: 0.1
```

仅在同时提供 `--bundle` 与 `--forwarder-config` 时启用；否则退化为纯本地主链路。

## 其他入口

```bash
# 配置格式迁移工具
python -m cloudrobo_r2c.r2c_config_migrator old_config.json -o new_config.yaml

# 自定义 topic 发布/订阅/监听工具
python -m cloudrobo_r2c.tools.custom_topic_publisher --help
python -m cloudrobo_r2c.tools.custom_topic_subscriber --help
python -m cloudrobo_r2c.tools.custom_topic_listener --help
```
