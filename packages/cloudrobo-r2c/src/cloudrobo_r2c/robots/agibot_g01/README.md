# AgiBot G01（智元精灵 G01）适配器 — 环境配置与启动指导

> 本文件负责 `agibot_g01` 适配器的环境配置、启动与现场验证；R2C 数据面与 CLI 的通用用法见
> `packages/cloudrobo-r2c/docs/index.md`，字段映射细节见同目录
> `config/robot_agibot_g01_config.yaml` 的注释。适配器接口依据
> 《智元精灵G01+GDK 使用指南》的 RobotDds / CosineCamera 接口章节。

## 1. 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| 运行机 | 与 G01 同网段的 **x86 Ubuntu 22.04** PC | GDK（`a2d_sdk`）仅提供 Linux 环境；R2C SDK 侧无平台限制，但适配器必须与 a2d_sdk 同进程运行 |
| Python | a2d_sdk 侧 **3.10**；R2C SDK 侧 ≥ 3.10 | 建议 conda/venv 隔离 |
| ROS2 | 不需要 | 本适配器走 GDK Python 接口，不依赖 ROS2 |
| 机器人 | 已开机并完成初始化（开机后等待约半分钟至一分钟再切换模式） | 见《指南》2.3 |

## 2. 安装 GDK（a2d_sdk）

`a2d_sdk` 不发布到 PyPI，必须从机器人内置的安装服务获取（《指南》2.2）：

```bash
# 1) PC 与 G01 网络连通后，在目标虚拟环境中执行
sudo apt install iproute2
pip install -r requirements_gui.txt   # GDK 官方依赖清单（numpy/protobuf/scipy/opencv 等）
curl -sSL http://10.42.0.101:8849/install.sh | bash   # 10.42.0.101 为机器人默认地址

# 2) 安装完成后必须执行（加载 DDS 等环境变量）
cd a2d_sdk
source env.sh
```

注意：

- 需先进入目标虚拟环境再执行安装脚本；GDK 升级后需重新更新 PC 侧环境信息。
- `source env.sh` 是 `RobotDds` / `CosineCamera` 正常通信的前提（每次新开终端都需要）。
- 未安装 `a2d_sdk` 时适配器 `connect()` 会抛出带本指引的 `RuntimeError`。

## 3. 机器人切换 Copilot 模式

外部调用 GDK 接口要求机器人处于 **Copilot 模式**（《指南》2.3）：

```bash
# 进入机器人容器/虚拟环境后执行
robot-service -s -c ./conf/copilot.pbtxt
```

- Copilot 模式默认开放 4 路相机：头部 D455/D457 彩色+深度、左右腕 D405 彩色，默认 30Hz。
- 分辨率：D455 彩色/深度 1280×720，D457 1280×800，D405 848×480。
- 修改 `copilot.pbtxt` 后需保存并重启完成模式切换。

## 4. 安装 R2C SDK

```bash
# 仓库根目录
pip install -r requirements-dev-editable.txt   # 开发安装（含 hw-cloudrobo-r2c）
# 或仅安装运行所需
pip install "hw-cloudrobo-r2c[client]"
```

适配器本身只额外依赖 `numpy`（已包含在 `client` extra 中）。

## 5. 机器人配置

使用 `src/cloudrobo_r2c/config/robot_agibot_g01_config.yaml`，关键项
（`hardware.config`，均有安全默认值）：

| 键 | 默认 | 说明 |
|---|---|---|
| `init_wait_s` | 0.5 | `RobotDds` 初始化等待秒数（《指南》7.4 建议等待资源就绪） |
| `camera_enabled` | true | 是否启动 `CosineCamera` 取流线程 |
| `camera_group` | head / hand_left / hand_right | 启用的 G01 相机通道（`head_depth`、`*_fisheye` 等可扩展） |
| `camera_key_map` | head→cam_high、hand_left→cam_left_wrist、hand_right→cam_right_wrist | G01 通道名 → R2C 图像键 |
| `camera_poll_interval_s` | 0.03 | 取流轮询间隔（≈30Hz） |
| `enable_motion_controller` | true | 启用 `RobotController`（《指南》RobotController 接口节）：末端位姿/关节控制；创建失败自动降级为仅RobotDds 关节控制 |
| `control_lifetime_s` | 1.0 | RobotController 位姿/关节指令有效时间（秒），超时未收到下一帧则停止 |
| `pose_control_group` | ["left_arm", "right_arm"] | 位姿控制部位（`set_end_effector_pose_control` 的 control_group） |
| `dry_run` | false | 适配器级干运行：只打印动作不执行 |
| `location` | "" | `state/current` 帧 `location` 字段（`hardware.config.location`，按现场填写） |
| `ee_pose_channel` | `end_effector_poses` | 末端位姿发布位置（单选）：`end_effector_poses`（专用字段）或 `joint_states`（镜像） |
| `gripper_channel` | `end_effector_states` | 末端夹爪发布位置（单选）：`end_effector_states`（专用字段）或 `joint_states`（镜像 `left/right_gripper`） |
| `ee_pose_repr` | `quat` | 末端位姿角度表示（对 `ee_pose_channel` 两种取值都生效）：`quat`（`[x,y,z,rx,ry,rz,rw]` 7 维）或 `euler`（`[x,y,z,roll,pitch,yaw]` 6 维，ZYX） |
| `commands.go_home` | 全零示例位姿 | GDK `reset()` 复位目标，现场按需修改 |

两个 channel 均为**单选、互斥不可同时发**，观测与 `state/current` 使用同一选择；
`ee_pose_repr` 决定位姿的角度表示（观测与 state/current 同一表示），选择
`joint_states` 时云端模型输入维度需按镜像后的 `joint_states.position` 维度配置
（14 关节 + 位姿段（quat 每侧 7 / euler 每侧 6）+ 夹爪段（2，仅 `gripper_channel:
joint_states` 时））。

state/current 发布：配置已内置 `hardware.topics` 的
`{project_id}/{device_id}/state/current` 绑定（10Hz，`collector: get_runtime`）。
发布内容与 observation 一致（同一构造路径，不取相机图像）：双臂 14 关节 +
末端位姿（按 `ee_pose_channel` 单选：`end_effector_poses` 段或 `joint_states`
镜像，角度表示按 `ee_pose_repr`）+ 夹爪位置（按 `gripper_channel` 单选：
`end_effector_states` 段或 `joint_states` 镜像，`[35, 120]` 归一化 `[0, 1]`）；
位姿不可用时与观测一致省略。

深度/鱼眼通道接入：在 `camera_group` 中加入通道名，并在 `device_to_r2c.mappings`
仿照现有条目增加 `images.color.*`（或深度用 `images.depth.*` + `ndarray_to_png`）映射。
`body_pose`（整体姿态）维度随整机配置变化，默认不映射，现场确认维度后可仿照
`extensions.head_joint` 增加（`dtype: FLOAT32` + 固定 `shape`）。

### 控制路由：按 joint_states.names 逐维区分第 7 / 8 章

云端动作的 `joint_states.names` 逐维决定适配器行为（YAML 已内置 `joint_names` +
全量 `position` 映射），names 与 position 一一对应：

| names 逐维内容 | 章节 | GDK 调用 | 说明 |
|---|---|---|---|
| `left_arm_joint_1..7` / `right_arm_joint_1..7`（须 14 维齐全） | 7 章 | `move_arm` | 关节角按名回填；**不全不硬执行** |
| `left_pose_x/y/z` + `left_pose_rx/ry/rz/rw`（四元数） | 8 章 | `set_end_effector_pose_control` | 各侧分量须完整，内部无补帧 |
| `left_pose_x/y/z` + `left_pose_roll/pitch/yaw`（欧拉角，ZYX） | 8 章 | 同上 | 内部转换为四元数 |
| `left_gripper` / `right_gripper`（两侧须同时出现） | 7 章 | `move_gripper` | 优先于 `gripper_cmd_pos` 通道 |
| `left_arm_joint1..7` / `right_arm_joint1..7`（逐关节，不带下划线，须齐全） | 8 章 | `set_joint_position_control` | 按关节编号组装为 left_arm/right_arm 组 |
| 全部为部位组名（`left_arm` / `right_arm` 等，每组 7 关节） | 8 章 | `set_joint_position_control` | 组名路由（遗留兼容） |

**互斥与完整性规则（违反即拒绝执行整条动作，不做补帧兜底）**：

- 关节与末端位姿互斥：同一动作不得同时包含关节分量与位姿分量（含经
  `end_effector_poses` 通道下发的位姿）
- 末端位姿双通道互斥：位姿同时出现在 `joint_states` 与 `end_effector_poses` → 拒绝
- 四元数与欧拉角互斥：同一动作内不得混用两种表示（左右侧之间也须一致）
- 关节名须统一为 left_arm_jointN 格式
- 不全不硬执行：关节须 14 维齐全、每组关节须 7 维、位姿各侧分量须完整、
  夹爪须两侧同时出现，否则拒绝执行
- 未知 names 视为协议不一致，拒绝执行

### 观测发布位置：末端位姿与夹爪单选（ee_pose_channel / gripper_channel）

末端位姿与夹爪的观测发布位置由 `hardware.config` 的 `ee_pose_channel` /
`gripper_channel` 各自单选（**互斥，不可同时发**），观测与 `state/current`
使用同一选择；位姿角度表示由 `ee_pose_repr` 选择（同样对两种通道都生效）：

| 内容 | `end_effector_*`（默认） | `joint_states`（镜像） |
|---|---|---|
| 末端位姿（`ee_pose_channel`） | `end_effector_poses.names/pose`（专用字段，pose 每行按 `ee_pose_repr`） | `joint_states` 镜像：按 `ee_pose_repr` 单表示，每侧 `left_pose_x/y/z/rx/ry/rz/rw`（quat 7 维）或 `left_pose_x/y/z/roll/pitch/yaw`（euler 6 维，ZYX） |
| 夹爪（`gripper_channel`） | `end_effector_states.names/position`（专用字段，`[0,1]` 归一化） | `joint_states` 镜像：`left_gripper` / `right_gripper` 2 维 |

- 默认（专用字段 + quat）：`joint_states.position` = 双臂 14 关节；
  `end_effector_poses.pose` 每行 `[x, y, z, qx, qy, qz, qw]`
- `ee_pose_repr: euler`：`end_effector_poses.pose` 每行 `[x, y, z, roll, pitch, yaw]`；
  镜像模式每侧 6 维
- 全选 `joint_states`：`joint_states.position` = 14 + 位姿段（quat 14 / euler 12）
  + 夹爪段 2，**云端模型输入维度需按所选通道配置**；位姿不可用（运控器禁用/
  未就绪）时位姿段与观测一致自动省略
- 动作侧：`joint_states` 位姿按 names 识别 quat/euler 分量（二者互斥）；ee 通道
  （`left/right_pose_cmd_ee`）按长度识别 7 维 quat / 6 维 euler，euler 内部转
  quat 下发，与观测 `ee_pose_repr` 输出闭环

### 样例 cloud-adapter：动作组往复

`examples/agibot_g01_cloud_adapter.py` 演示云端在 `GROUPS` dict 数组定义的动作组
之间来回下发（默认三组为真机调试采集的RobotDds 关节动作）：

- **关节/位姿/夹爪全程插值**：从当前值线性插值到目标（位姿为平移线性 +
  四元数 SLERP，欧拉角由连续四元数逐步转换，天然避免过圈），
  **chunk size = 2 × publish_hz**（默认 30Hz → 60 步，每组执行约 2 秒），
  避免单步跳变超 0.2618 rad（15°）限幅
- 夹爪任意角度：三组分别 `0.15/0.15`、`0.85/0.35`、`0.5/0.8`（连续值透传）
- 往复序列 `0 → 1 → 2 → 1 → 0 → ...`
- 连接约定（client config）：**zenohd 与网关监听 `tcp/0.0.0.0:7447`**（所有网卡；
  网关侧 client config 用 `endpoint_role: "listen"` + `mode: "peer"`）；云端适配器
  `endpoint_role: "connect"`，同机部署连 `tcp/0.0.0.0:7447`，跨机部署用
  `--zenoh-endpoints tcp/<网关IP>:7447` 指向实际地址；`project_id` / `device_id`
  按现场部署填写（示例默认 `r2c` / `local`），与机器人端取值一致
- RobotController 示例组（GROUP_POSE，默认注释）：取消注释并加入
  `GROUPS` 即可演示——`GROUP_POSE` 为**绝对位姿**（组内 `left_pose` /
  `right_pose` 直接下发，经 `--pose-channel joint_states|ee` 选择通道、
  `--pose-repr quat|euler` 选择角度表示）；`GROUP_JOINTS8` 为逐关节名
  （`left_arm_joint1..7`）→ `set_joint_position_control`

```bash
# 云端侧（与机器人端 r2c client 同网段；建议机器人端先 dry_run: true）
python examples/agibot_g01_cloud_adapter.py --project-id r2c --device-id local \
    --steps 60 --duration 60
# RobotController 示例组：取消 GROUP_POSE 注释并加入 GROUPS 后，
# 可用 --pose-channel / --pose-repr 选择下发通道与角度表示
```

## 6. 启动与验证

```bash
# 1) 干运行（安全默认）：观测正常发布，动作只打日志不执行
cloudrobo r2c client \
    --bundle <cert_config_*.zip> \
    --robot-config <path/to/robot_agibot_g01_config.yaml> \
    --log-level INFO
```

验证要点：

- 日志出现 `AgiBot G01 client connected` 与相机取流启动日志；无 `a2d_sdk` 时会得到
  明确报错并指向本文件第 2 节。
- 云端能收到观测（关节数值随姿态变化；相机首帧可能为空——`CosineCamera` 首帧返回
  `None`，取流线程会自动过滤，稍等即有图像）。
- 云端能收到 `state/current`（日志出现 `publishing .../state/current @ 10.0Hz`）。
- 下发动作仅出现在 `[AgiBotG01][DRY_RUN]` 日志中。

```bash
# 2) 真实执行：确认无误后关闭两级 dry_run
#    runtime.dry_run: false（客户端级）与 hardware.config.dry_run: false（适配器级）
```

安全注意（《指南》5.1 / 7.5）：所有角度为弧度，腰部高度单位为厘米；手臂目标关节角与
实时值差值需小于 0.2618 rad（15°），由上层规划保证；首次上电先 `go_home` 复位验证。

## 7. 常见问题

| 现象 | 处理 |
|---|---|
| `a2d_sdk 未安装` RuntimeError | 未部署 GDK 或未 `source env.sh`，见第 2 节 |
| 相机长时间无图像 | 确认机器人处于 Copilot 模式且 `copilot.pbtxt` 开启对应相机通道；首帧 `None` 属正常 |
| 关节状态查询失败告警 | 单次 DDS 异常，适配器自动沿用上一帧值；持续出现检查网络与模式 |
| 动作维度报错跳过 | 云端动作维度需为 双臂 14 / 夹持器 2 / 灵巧手 12 / 头 2 / 腰 2 / 轮 2 |
| `body_pose` 未上报到云端 | 默认不映射（维度未确认），见第 5 节 |

## 8. 离线自检（无真机）

```bash
python -m pytest packages/cloudrobo-r2c/tests/test_agibot_g01_adapter.py -v
```

测试以假 `a2d_sdk` 模块覆盖连接生命周期、观测组装、动作分发、干运行与
YAML→翻译器端到端链路，无需 GDK 环境（假模块定义在
`tests/fake_a2d_sdk.py`，由 `tests/conftest.py` 在导入被测模块前安装）。

## 9. 无真机运行（mock 适配器）

`hardware.type: "agibot_g01_mock"`（entry point `agibot_g01_mock`）提供与真机
适配器**接口完全一致**的内存仿真，用于无 GDK / 无真机环境跑通完整数据链路：

- 观测组装、`state/current` 采集（`get_runtime`）、动作按 `joint_states.names`
  的第 7/8 章路由、互斥与完整性校验、`ee_pose_channel` / `gripper_channel` /
  `ee_pose_repr` 单选、`go_home` 复位命令——全部复用真机适配器实现
- 底层 `AgiBotG01MockClient`：动作立即更新内存状态（无运动时间/限速），
  关节与末端位姿状态独立维护（不做正运动学联动）；夹爪内部 `[0, 1]` 归一化，
  `gripper_states` 返回 `[35, 120]` 原始量程（与 GDK 一致）；相机为合成渐变帧，
  分辨率由 `camera_width` / `camera_height` 配置（默认 64×48）
- **初始关节与末端位姿默认取自云端示例
  `examples/agibot_g01_cloud_adapter.py`：双臂 14 关节 = `GROUP_A`（joint_a）
  的目标关节角，末端位姿 = `GROUP_POSE_A`（pose_a）的 left/right 7 维位姿，
  夹爪 = joint_a 的 `[0.15, 0.15]`**；灵巧手/头/腰初始零位，均可用
  `hardware.config` 的 `initial_joints` / `initial_gripper` /
  `initial_ee_poses` / `initial_hand` / `initial_head` / `initial_waist` /
  `initial_body_pose` 覆盖

```bash
# 机器人端（mock；runtime.dry_run 默认 false，动作直接更新 mock 状态；
# 只观察动作日志时把任一 dry_run 改回 true）
cloudrobo r2c client \
    --robot-config src/cloudrobo_r2c/config/robot_agibot_g01_mock_config.yaml

# 云端侧：仍用真机样例（同一 zenohd 部署）
python examples/agibot_g01_cloud_adapter.py --project-id r2c --device-id local
```

配套配置 `src/cloudrobo_r2c/config/robot_agibot_g01_mock_config.yaml` 与真机
配置同构（仅 `hardware.type`、mock 初始状态与 dry_run 默认值不同）；
`commands.go_home` 默认回到 joint_a 初始位姿。

mock 适配器的单测见
`packages/cloudrobo-r2c/tests/test_agibot_g01_mock_adapter.py`（初始基线、
全量控制路由、`get_runtime`、配置 YAML→翻译器端到端等）。
