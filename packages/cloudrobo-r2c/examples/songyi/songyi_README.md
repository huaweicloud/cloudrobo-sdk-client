# 新松（SONGYI 双臂）R2C 接入启动指南

> 设计文档：[docs/songyi_r2c_design.md](../../docs/songyi_r2c_design.md)
> 本文只讲怎么跑：**proc 1（机器人主程序，官方 cloudroboclient 统一入口）** 与 **proc 2（forwarder）** 的启动命令。

---

## 0. 前置准备

```bash
# 1. 激活环境（已安装 r2c_sdk）
cd /path/to/cloudrobo-r2c
export PYTHONPATH=$PWD/src:$PYTHONPATH

# 2. 准备文件
#    - examples/songyi/config/robot_config.yaml        机器人配置（真机：主链路 / 三绑定 / 硬件 / 遥操）
#    - examples/songyi/config/robot_mock_config.yaml   机器人配置（Mock 仿真版）
#    - config/forwarder_config.yaml    forwarder 路由（proc 2 用）
#    - 主 session 二选一：
#        · 云端证书 bundle（zip，正式接入平台），下文记为 config/cert_xxx.zip
#        · 或本地 client YAML（config/client_config.yaml，默认已提供）
#
# 3. 确认双臂网络可达（新松 demo SDK：左臂 7003 / 右臂 7004）
ping 192.168.1.230
```

⚠️ **安全提醒**：本接入有两层 dry_run（真机执行前两层都必须为 `false`）：
- `runtime.dry_run`（sync_client 层）：action 不下发到硬件适配器
- `hardware.custom_config.dry_run`（硬件层）：`send_action` 只打印
  `[DRY_RUN] observation/action` 供链路比对，不下发硬件
测试时人员远离工作空间，随时可按急停。

---

## 1. proc 1 — 机器人主程序（官方 cloudroboclient 统一入口）

自 **D157** 起无独立 robot_app：主程序即官方边缘客户端
`python -m cloudrobo_r2c.cloudroboclient --robot-config <robot yaml>`——
主链路（observation 按 action 剩余量按需发布 + action 直接
`send_action` 执行）+ 三绑定自定义 topic（`current_state` /
`command` / `waist_command`）+ 告警 EventManager 推送（D158，topic
固定 `{pid}/{did}/event`）全部由该入口装配；`robot_app.py` 已删除。

**方式 A：云端证书 bundle（正式接入 Cloudrobo 平台）**

```bash
python -m cloudrobo_r2c.cloudroboclient \
    --robot-config examples/songyi/config/robot_config.yaml \
    --bundle       config/cert_xxx.zip
```

**方式 B：本地 YAML 配置（无 bundle，本地/局域网联调）**

```bash
python -m cloudrobo_r2c.cloudroboclient \
    --robot-config examples/songyi/config/robot_config.yaml \
```

**Mock 仿真（无硬件）**：把 `--robot-config` 换成
`examples/songyi/config/robot_mock_config.yaml`（entry-point
`songyi_mock`；mock 告警周期经 EventManager 推送）。

| 参数 | 说明 |
|---|---|
| `--robot-config` | 机器人配置（主链路 + `hardware.topics` 三绑定 + `custom_config`） |
| `--bundle` | 主 session 云端证书；**缺省时回退到 `--client-config` YAML** |
| `--client-config` | 无 bundle 时主 session 的 YAML（project_id/device_id/endpoints/mode/TLS） |
| `--project-id` / `--device-id` | 覆盖 YAML 中的同名键（优先级高于 YAML） |
| `--mode` | 主 session zenoh 模式，默认 `peer`（`client` 模式必须有 zenoh router） |
| `--duration` | 可选，N 秒后自动退出（0 = 一直运行，Ctrl+C 停止） |
| `--record` | 可选，录制 observation（.pkl，回放用） |
| `--hardware-class` | 可选，dotted path 覆盖硬件适配器类（正常以 yaml `hardware.class_path` 为准） |

### 回放模式（不连真机）

在 `robot_songyi_config.yaml` 的 `custom_config` 中启用 `record_observation`
（取消注释即可），proc 1 即以录制文件为 runtime/observation 数据源，
**无需真机、无需连接新松 SDK**：

```yaml
custom_config:
  record_observation:
    path: "docs/data.json"   # 录制文件（358 帧 @30fps）
    loop: true               # 播完循环；false 停在最后一帧
```

启动命令同上（`--bundle` 仍需提供，供主 session 上报观测）。

---

## 2. proc 2 — forwarder（event/taskprocess 转发）

职责：订阅**可配置的本地 topic**（第三方程序发布的 event/taskprocess），
原样转发到**固定的** `{pid}/{did}/event`、`{pid}/{did}/taskprocess`
（发送侧 session 连 Cloudrobo，topic 名由 bundle 解析）。

```bash
python examples/songyi/forwarder.py \
    --system-config config/songyi_system_config.yaml
```

| 参数 | 说明 |
|---|---|
| `--system-config` | 系统配置（默认 `config/songyi_system_config.yaml`），含两条 session 与 routes |
| `--max-duration-s` | 可选，N 秒后自动退出（0 = 一直运行，Ctrl+C 停止） |

> 第三方程序把 event/taskprocess 发布到 `songyi/local/event`、
> `songyi/local/taskprocess`（topic 名在系统配置 `routes[].from` 中可改）。

---

## 3. 联调验证（可选）

```bash
# 终端 3：订阅 runtime topic，确认状态上行（topic 名按 bundle 实际 pid/did 替换）
python examples/observation_subscriber.py

# 终端 4：云适配器（订阅观测 → round-robin 回发 3 个安全位姿，双臂+夹爪）
#   joint 模式（默认）：关节角度 + 夹爪
python examples/songyi/cloud_adapter.py --control-mode joint
#   cartesian 模式：末端位姿(Pose7D，内部由 xyzrpy 转换) + 夹爪
python examples/songyi/cloud_adapter.py --control-mode cartesian
```

推荐顺序：

1. 只跑 proc 1 + 观测订阅 → 确认 runtime/observation 上行正常、关节名（16 个中文名）对齐
2. 加跑云适配器（保持 `dry_run: true`）→ 日志比对观测与动作数据
3. 确认无误后 `dry_run: false`，**急停值守**下小步骤验证真机动作

---

## 4. 自定义 topic 单次收发（custom_topic_publisher / subscriber）

通用工具：按 proto 名查找 wrapper 完成 JSON ↔ protobuf 转换；topic 名支持
`{project_id}/{device_id}` 占位符自动展开。订阅方默认 `--endpoint-role listen`
（稳定接入口），发布方默认 connect + settle 等待发现。

| topic | proto（--topic-proto） | 方向 |
|---|---|---|
| `{pid}/{did}/current_state` | `cloudrobo_r2c.common.models.generated.current_state_pb2:CurrentState` | proc 1 发布 → 订阅读取 |
| `{pid}/{did}/command` | `cloudrobo_r2c.common.models:CommandMessage` | 订阅：proc 1 ← 发布 |
| `{pid}/{did}/event` | `cloudrobo_r2c.common.models:EventMessage` | 发布（正式走 proc 2/bundle） |
| `{pid}/{did}/taskprocess` | `cloudrobo_r2c.common.models:TaskProcessMessage` | 发布（正式走 proc 2/bundle） |

### 4.1 读取 runtime（需先启动 proc 1；收到一帧即退出）

```bash
python -m cloudrobo_r2c.tools.custom_topic_subscriber \
    --project-id r2c --device-id local \
    --endpoints tcp/127.0.0.1:7447 \
    --topic-name "{project_id}/{device_id}/current_state" \
    --topic-proto cloudrobo_r2c.common.models:RunTimeMessage \
    --timeout 10
```

### 4.2 发送 command（遥操指令）

```bash
python -m cloudrobo_r2c.tools.custom_topic_publisher \
    --project-id r2c --device-id local \
    --endpoints tcp/127.0.0.1:7447 --settle-s 2 \
    --topic-name "{project_id}/{device_id}/command" \
    --topic-proto cloudrobo_r2c.common.models:CommandMessage \
    --topic-data '{"timestamp": 1700000000000, "command_type": "teleop_enter"}'
```

> ⚠️ **`teleop_enter` 会真机复位 + 执行 enter 脚本 + 忽略模型动作**；
> `teleop_exit` 恢复。**`dry_run` 不拦截 command**，真机在运行时勿随意发送。

### 4.3 发送 event

```bash
python -m cloudrobo_r2c.tools.custom_topic_publisher --settle-s 2 \
    --topic-name "{project_id}/{device_id}/event" \
    --topic-proto cloudrobo_r2c.common.models:EventMessage \
    --topic-data '{"timestamp": 1700000000000, "metadata": [{"code": "E001", "type": "alarm", "severity": "Minor", "content": "联调测试事件", "starts_at": 1700000000000, "ends_at": 0}]}'
```

### 4.4 发送 taskprocess

```bash
python -m cloudrobo_r2c.tools.custom_topic_publisher --settle-s 2 \
    --topic-name "{project_id}/{device_id}/taskprocess" \
    --topic-proto cloudrobo_r2c.common.models:TaskProcessMessage \
    --topic-data '{"session_id": "demo-001", "timestamp": 1700000000000, "nodes": [{"task_id": "t1", "task_name": "demo_task", "progress": 0.5, "result_state": 0, "retry_count": 0, "start_timestamp": 1700000000000, "duration": 0}], "current_step": {"action_timestamp": 1700000000000, "task_id": "t1", "action_str": "move_to_grasp", "action_duration": 0, "think_timestamp": 1700000000000, "think_duration": 0}}'
```

> 说明：event/taskprocess 的正式发布链路是 proc 2 / bundle（发往 Cloudrobo）；
> 上述命令配合 `--bundle` 可直接发平台，本地无 bundle 时仅用于回环验证。
> 大段 JSON 可写文件后用 `--topic-data @path/to/file.json` 传入。

---

## 5. 常见问题

| 现象 | 排查 |
|---|---|
| 主 session 连不上 | bundle 模式查证书/网络；YAML 模式默认 `mode=peer` 无 router 也能开会话（与 cloudroboclient 行为一致）。若显式 `--mode client` 且 `endpoint-role` 为 `connect`，则必须有 zenoh router 在 endpoints 上 |
| runtime 无数据 | 检查双臂 `ping`、端口 7003/7004；回放模式检查 `record_observation.path` 存在 |
| forwarder 收不到第三方消息 | 核对系统配置 `routes[].from` 与第三方发布 topic 名一致 |
| topic 名不对 | `{project_id}/{device_id}` 占位符由 bundle 解析；无 bundle 的从 session 用配置里的显式声明 |
| 想换降采样/频率 | 系统配置 `topics."{pid}/{did}/current_state".rate_hz / granularity` |
