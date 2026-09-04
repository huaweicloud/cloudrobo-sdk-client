# 安装与集成指南

## 安装

### 单独安装

```bash
pip install -e packages/cloudrobo-dataset
```

### 聚合安装（全部模块）

```bash
pip install -e .  # 在项目根目录执行，安装 hw-cloudrobo-client
```

### 前置条件

- Python >= 3.8
- 已配置 AK/SK 认证（环境变量 `HUAWEI_CLOUD_AK` / `HUAWEI_CLOUD_SK`，或 `~/.cloudrobo/config.yaml`）
- 已设置默认工作空间（`cloudrobo workspace use <workspace-id>`）

## CLI 使用

安装后即可通过 `cloudrobo dataset` 命令操作：

```bash
# 查看帮助
cloudrobo dataset --help

# 查询算子
cloudrobo asset list-publication-assets --type algorithm --sub-type data_processing

# 创建任务
cloudrobo dataset proc create-task --name <name> --algo-type PRESET_ASSETS --task-config '<json>'

# 查看任务
cloudrobo dataset proc show-task --task-id <id>

# 等待任务完成
cloudrobo dataset proc wait-task --task-id <id>
```

## Skill 对接 Agent 方式

cloudrobo-dataset 提供了 AI Agent Skill，让具备 CLI 执行能力的 Agent（如 Claude Code、Cursor 等）通过自然语言驱动完整的数据处理流程。

### 安装 Skill 到 Agent 平台

```bash
# 1. 克隆 skill 仓库
git clone <cloudrobo-skills-url> ~/cloudrobo-skills

# 2. 安装到指定平台
cloudrobo skill install --source ~/cloudrobo-skills/skills --target claude-code

# 3. 安装指定 skill
cloudrobo skill install --source ~/cloudrobo-skills/skills --target claude-code --skill-name huawei-cloud-cloudrobo-dataset
```

### Skill 工作流程

Agent 安装 Skill 后，用户只需自然语言描述需求，Agent 会自动：

1. **查询算子** — 根据用户描述匹配合适的算子
2. **获取数据集** — 查找工作空间中的数据集资产
3. **构造配置** — 提取算子字段、工作空间 catalog_id、环境变量等
4. **创建任务** — 调用 `create-task` 提交
5. **轮询状态** — 自动等待任务完成并反馈状态变更
6. **查看结果** — 任务成功后输出日志和预览，失败时引导查看日志

### Skill 优先使用 CLI

Skill 设计为 CLI-first：优先执行 `cloudrobo dataset` 命令，用户可直接复现。

## 典型应用场景

### 场景一：逆运动学求解

将 ROS2 rosbag 数据集通过逆运动学求解器转换为关节角数据。

```bash
# 1. 查询算子
cloudrobo asset list-publication-assets --sub-type data_processing --name 逆运动学

# 2. 查询数据集
cloudrobo asset list-assets --type dataset --name ros2-ik

# 3. 创建任务
cloudrobo dataset proc create-task \
  --name ik-solve-01 \
  --algo-type PRESET_ASSETS \
  --task-config '{
    "algo_name": "数据处理--逆运动学求解器",
    "algo_entrance": "bash entrypoint.sh",
    "image": "<image-url>",
    "algo_id": "<从算子查询结果获取>",
    "catalog_id": "<工作空间catalog_id>",
    "resource_pool_type": "PUBLIC_POOL",
    "cluster_type": "CCE",
    "task_framework_type": "K8S",
    "dataset_configs": "[{\"obs_path\":\"<数据集url>\",\"dataset_type\":\"BUILD_IN_ASSET\",\"asset_id\":\"<数据集asset_id>\",\"asset_name\":\"ros2-ik\",\"version_id\":\"<数据集version_id>\"}]",
    "output_type": "BUILD_IN_ASSET",
    "output_path": "obs://bucket/output-path",
    "output_name": "ik-solve-01-output",
    "envs": "[{\"key\":\"ROBOT_MODEL\",\"value\":\"galaxea_r1\"},{\"key\":\"IK_SOLVER_PRIORITY_MODE\",\"value\":\"PRIORITY_OPTION_DUAL_ARM\"}]",
    "head_spec": {"cpu": 0, "memory": 0},
    "worker_spec": {"cpu": 4, "memory": 8},
    "worker_num": 1,
    "evs_spec": 0
  }'

# 4. 等待完成
cloudrobo dataset proc wait-task --task-id <task-id>
```

**Agent 自然语言触发**：`帮我把 ros2-ik 数据集用逆运动学求解器算一下`

### 场景二：ROS 数据转 LeRobot 格式

将 ROS bag 数据转换为 LeRobot V21 格式，用于模型训练。

```bash
# 查询转换算子
cloudrobo asset list-publication-assets --name ros转LeRobot

# 创建转换任务（task-config 类似场景一，替换算子和数据集信息）
```

**Agent 自然语言触发**：`把 ros-bag-001 数据集转成 LeRobot 格式`

### 场景三：数据评测

对模型输出结果进行评测分析。

```bash
# 查询评测算子
cloudrobo asset list-publication-assets --sub-type data_evaluating

# 创建评测任务（algo_type 同样为 PRESET_ASSETS，sub_type 为 data_evaluating）
```

**Agent 自然语言触发**：`评测一下 eval-output 数据集的得分`

### 场景四：任务故障排查

任务失败后查看日志定位问题。

```bash
# 查看系统日志
cloudrobo dataset proc get-log --task-id <id> --is-system true
cloudrobo dataset proc get-log --task-id <id> --file-name system-std-output.log --file-path "<path>"

# 查看用户日志
cloudrobo dataset proc get-log --task-id <id> --is-system false
cloudrobo dataset proc get-log --task-id <id> --file-name job-std-output.log --file-path "<path>"

# 重启任务
cloudrobo dataset proc restart-task --task-id <id>
```

**Agent 自然语言触发**：`任务 ik-task-01 失败了，帮我看看日志`
