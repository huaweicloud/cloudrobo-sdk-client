# 隙锋人形机器人 R2C 适配（`cloudrobo_r2c.robots.xifeng`）

把优艾智合「隙锋」人形机器人（私有 `pypilot` Python SDK）接入华为 Cloud Robo（R2C SDK）。

**交付形态**：本适配已**集成进 r2c_sdk 树**，是自包含子包，遵循
`skills/r2c/references/r2c-custom-robot.md` 规范的「custom + class_path」方式。
所有上行调度 / chunk 拆分 / 节奏对齐 / 心跳 / 序列化都由 R2C SDK 内置框架完成，
本子包只提供「用 pypilot / Mock 读写隙锋机器人」这一最薄的硬件适配层。

## 架构

```
                    华为 Cloud Robo (Zenoh + Protobuf)
                              │
                    R2C Session  ←─── R2C SDK 自带
                              │
                    SyncRobotClient
                       │           │
        IDeviceTranslator       IRobotHardwareAdapter
        (ConfigurableDeviceTranslator)  │
        - device_to_r2c YAML            │ 通过 hardware.class_path 注入
        - r2c_to_device YAML            ▼
                              XifengHardwareAdapter  ← 本子包
                                    │ robot_factory(custom_config)
                                    ▼
                              XifengRobot（mock 或 real）
                                    │  pypilot.PilotSDK (real 模式时)
                                    ▼
                              隙锋机器人本体
```

## 安装（开发环境）

要求 Python ≥ 3.10（真机模式的 pypilot 是 cp310 wheel，只能装在 Python 3.10）。

```bash
pip install -e packages/cloudrobo-r2c
# 真机模式额外装隙锋官方 cp310 wheel：
# pip install pypilot-2.0.0.260612.0-cp310-cp310-linux_x86_64.whl
```

适配子包随 r2c_sdk 一并安装，无需独立安装；`class_path` 直接写在 robot config 里。

## 快速开始 —— 本地联调（无真机、无云端）

```bash
cd packages/cloudrobo-r2c

# 终端 A：本地 mock 云端
python examples/xifeng_mock_cloud.py --duration 60

# 终端 B：用 R2C SDK 原生 CLI 启动桥（class_path 已写在 config 里）
python -m cloudrobo_r2c.cloudroboclient \
    --client-config config/client_xifeng_local.yaml \
    --robot-config config/robot_xifeng_config.yaml \
    --duration 60
```

跑单元烟测：

```bash
/home/ryan/miniforge3/envs/py310/bin/python -m pytest tests/xifeng/ -v
```

## 连真华为云（mock 数据，无真机）

只需 CloudRobo 平台下发的凭证 bundle，**不需要真机也不需要 pypilot**：

1. 在 CloudRobo 平台「运行管理 > 机器人 > 创建机器人」下载 bundle（zip 或解压目录都行）
2. 放进 `secrets/`，权限 `chmod 700 secrets/cert_xxx` + `chmod 600 secrets/cert_xxx/*`
3. 一键启动：
   ```bash
   python -m cloudrobo_r2c.cloudroboclient \
       --bundle /home/ryan/huawei_cloudrobo/secrets/cert_xxx \
       --robot-config config/robot_xifeng_config.yaml
   ```
4. 控制台机器人会从「待激活」变为「在线」。
5. （正常日志）`Timed out waiting for actions (5.000s), republishing observations.`
   —— SyncRobotClient 的健康行为，平台未部署模型时会一直出现，**不是 bug**。

## 连真机（pypilot real 模式）

1. `pip install pypilot-2.0.0.260612.0-cp310-cp310-linux_x86_64.whl`（Python 3.10）
2. 改 `config/robot_xifeng_config.yaml`：
   - `hardware.custom_config.source: real`
   - 填实 `real.robot_host / arm_local_ip / arm_ip` 等 IP
   - **首次联调保持 `dry_run: true`**，比对关节名/数据无误后再改 false
3. 重跑上面的 cloudroboclient 命令

## 代码结构

```
src/r2c_sdk/robots/xifeng/
├── __init__.py            # 重导出 XifengHardwareAdapter 等
├── xifeng_adapter.py      # XifengHardwareAdapter：R2C 注入点 + dry_run 开关
├── robot_interface.py     # R2C 通用语义名常量 + Protocol
├── robot_mock.py          # MockXifengRobot：合成数据
└── robot_real.py          # RealXifengRobot：真 pypilot 2.0 实现
config/robot_xifeng_config.yaml   # R2C schema v2.1 配置（交付物 #1）
config/client_xifeng_local.yaml   # 本地联调 zenoh peer 配置
examples/xifeng_mock_cloud.py     # 本地 mock 云端
tests/xifeng/                     # 烟测 + pypilot 2.0 适配回归 + dry_run 回归
```

## 关键命名约定（与 r2c_sdk/examples 一致）

- **关节**：`left_arm_1` .. `left_arm_7`, `right_arm_1` .. `right_arm_7`（弧度）
- **末端**：`left_gripper`, `right_gripper`（0-100 开合度）
- **图像**：目前隙锋**不支持 RGB 数据推流**，observation 不含图像

云端模型 `r2c.json` 占位符 `@{left_arm_1}` 等必须与此一致才能正确推理。

## dry_run 调试开关

`hardware.custom_config.dry_run: true` 时，`send_action` 只打印
`[DRY_RUN] observation` / `[DRY_RUN] action` 日志，不向底层 robot 下发动作。
用于接入真机首次联调时安全比对通信链路与数据格式（见
`skills/r2c/references/r2c-custom-robot.md` §7 测试流程）。交付默认 `true`，测通后改 `false`。

## 实现细节备忘

- 走 `hardware.type=custom` + `class_path` 而非 `raw_sdk`：cloudroboclient CLI 不暴露
  `raw_sdk_robot_factory`，custom 模式更干净；`XifengHardwareAdapter` 内部仍复用
  `VendorSDKHardwareAdapter` 的 connect/disconnect/get_observation/send_action 转发。
- `MockXifengRobot` 仿 `DummyRobotHardwareAdapter` 的 `_step_simulation`，让关节按
  `max_joint_speed_rad_s` 缓慢追赶目标，模拟真机响应延迟。
- 真机模式 pypilot 关节单位是**度**，在 `RealXifengRobot` 内部一次性 `rad ↔ deg` 换算，
  对外完全暴露弧度，避免在 YAML mappings 里到处写自定义 transform。
- 隙锋夹爪通过串口 hex 命令离散开/合（`robot_real.DEFAULT_GRIPPER_COMMANDS`），阈值 50。
- 目前隙锋**不支持 RGB 数据推流**，故 observation 只含关节 + 夹爪、不含图像
  （pypilot 2.0 相机回调也只有点云、无 RGB）。未来若支持，可在 `real` 加 `cameras`
  配置并在 `robot_real` 接入独立 USB/RTSP 抓帧。
```
