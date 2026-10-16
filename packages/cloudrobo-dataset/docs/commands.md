# cloudrobo-dataset CLI 命令

## 命令概览

所有 `cloudrobo dataset` 子命令用于管理数据处理与评测任务。

```bash
cloudrobo dataset [OPTIONS] COMMAND [ARGS]...
```

## 命令列表

| 命令 | 说明 |
|------|------|
| `create-task` | 创建数据处理任务 |
| `list-tasks` | 列出处理任务 |
| `show-task` | 查看任务详情 |
| `wait-task` | 等待任务到达终态 |
| `copy-task` | 复制数据处理任务 |
| `delete-task` | 删除数据处理任务 |
| `update-task` | 修改数据处理任务 |
| `get-frames` | 查看任务帧数据 |
| `get-log` | 获取任务日志 |
| `get-preview` | 预览任务数据 |
| `download-log` | 下载任务日志文件 |
| `get-resource-usage` | 获取任务资源监控数据 |
| `get-events` | 查看任务事件列表 |

### eval 子命令组

| 命令 | 说明 |
|------|------|
| `eval create-task` | 创建数据评测任务 |
| `eval list-tasks` | 列出评测任务 |
| `eval show-task` | 查看评测任务详情 |
| `eval copy-task` | 复制数据评测任务 |
| `eval update-task` | 修改评测任务 |
| `eval delete-task` | 删除评测任务 |
| `eval get-log` | 获取评测任务日志 |
| `eval get-preview` | 获取评测报告预览/下载链接 |
| `eval wait-task` | 等待评测任务到达终态 |
| `eval download-log` | 下载评测任务日志文件 |
| `eval get-events` | 查看评测任务事件列表 |

## eval 命令详情

### eval create-task

```bash
cloudrobo dataset eval create-task --name <name> --task-config <json> [--workspace-id <id>] [--wait] [--timeout <seconds>] [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--name` | 是 | 任务名称 |
| `--task-config` | 是 | 任务配置 JSON，包含以下字段 |
| `--workspace-id` | 否 | 工作空间ID，不提供则使用默认配置 |
| `--wait` | 否 | 创建后等待任务完成 |
| `--timeout` | 否 | 等待超时秒数，默认1800（配合 `--wait` 使用） |
| `--dry-run` | 否 | 仅预览 |

**task-config 字段说明**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `algo_id` | string | 是 | 算子ID |
| `algo_name` | string | 是 | 算子名称 |
| `algo_entrance` | string | 是 | 入口命令，如 `bash entrypoint.sh diversity_evaluation --operator diversity` |
| `dataset_type` | string | 是 | 数据集类型（BUILD_IN_ASSET / CUSTOM_ASSET） |
| `dataset_id` | string | 是 | 数据集ID |
| `dataset_name` | string | 是 | 数据集名称 |
| `dataset_path` | string | 是 | 数据集OBS路径，如 `obs://bucket/path/` |
| `image` | string | 是 | 容器镜像地址 |
| `robot_config` | string | 是 | 机器人配置OBS路径，如 `obs://bucket/path/` |
| `worker_spec` | object | 是 | worker规格，如 `{"cpu":2,"memory":4}` |
| `resource_pool_type` | string | 是 | 资源池类型（PUBLIC_POOL / DEDICATED_POOL） |
| `description` | string | 否 | 任务描述 |

**示例**:
```bash
cloudrobo dataset eval create-task \
  --name test-eval-01 \
  --task-config '{
    "algo_id": "<algo-id>",
    "algo_name": "数据集多样性评测-LeRobot",
    "algo_entrance": "bash entrypoint.sh diversity_evaluation --operator diversity",
    "dataset_type": "BUILD_IN_ASSET",
    "dataset_id": "<dataset-id>",
    "dataset_name": "jaka_place_block_into_tray_sim_50",
    "dataset_path": "obs://<bucket-name>/workspace-test/dataset/jaka_place_block_into_tray_sim_50/",
    "image": "<image-url>",
    "robot_config": "obs://<bucket-name>/workspace-test/dataset/jaka/",
    "worker_spec": {"cpu": 2, "memory": 4},
    "resource_pool_type": "PUBLIC_POOL",
    "description": "评测测试"
  }'
```

---

### eval list-tasks

```bash
cloudrobo dataset eval list-tasks [--status <status>] [--name <name>] [--order-by <field>] [--order <DESC|ASC>] [--offset <n>] [--limit <n>] [--user-name <name>] [--dataset-name <name>] [--workspace-id <id>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--status` | 否 | 状态过滤（逗号分隔，如 RUNNING,SUCCEEDED,FAILED） |
| `--name` | 否 | 按名称模糊查询 |
| `--order-by` | 否 | 排序指标（start_at/update_at/finish_at/create_at） |
| `--order` | 否 | 排序方式（DESC/ASC） |
| `--offset` | 否 | 分页偏移量 |
| `--limit` | 否 | 每页数量，默认20 |
| `--user-name` | 否 | 创建者名称过滤 |
| `--dataset-name` | 否 | 数据集名称过滤 |
| `--workspace-id` | 否 | 工作空间ID，不提供则使用默认配置 |

---

### eval show-task

```bash
cloudrobo dataset eval show-task --task-id <id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务ID |

---

### eval copy-task

```bash
cloudrobo dataset eval copy-task --task-id <id> --name <new-name> [--task-config <json>] [--wait] [--timeout <seconds>] [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 被复制的源评测任务ID |
| `--name` | 是 | 新任务名称 |
| `--task-config` | 否 | 其余可选覆盖字段 JSON，如 `{"description":"...","dataset_name":"..."}` |
| `--wait` | 否 | 复制后等待任务完成 |
| `--timeout` | 否 | 等待超时秒数，默认1800（配合 `--wait` 使用） |
| `--dry-run` | 否 | 仅预览 |

复制流程：先根据 `--task-id` 调用 `eval show-task` 获取源任务的配置信息，将其填充到复制请求体中，再用 `--name`（必填）及 `--task-config` 中的字段覆盖，最后发起复制请求。新任务创建成功后进入排队中（PENDING）状态。

**示例**:
```bash
# 指定新任务名称复制
cloudrobo dataset eval copy-task --task-id <task-id> --name copy-eval

# 指定新任务名称、覆盖部分字段并等待完成
cloudrobo dataset eval copy-task --task-id <task-id> --name copy-eval \
  --task-config '{"description":"从原评测任务复制"}' --wait
```

---

### eval update-task

```bash
cloudrobo dataset eval update-task --task-id <id> --task-config <json>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务ID |
| `--task-config` | 是 | 更新内容JSON |

---

### eval delete-task

```bash
cloudrobo dataset eval delete-task --task-id <id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务ID |

---

### eval get-log

```bash
# 列出日志文件
cloudrobo dataset eval get-log --task-id <id> --is-system true|false

# 获取日志内容
cloudrobo dataset eval get-log --task-id <id> --file-name <name> --file-path <path>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务ID |
| `--is-system` | 否 | True=系统日志，False=用户日志，仅列出日志文件 |
| `--file-name` | 否 | 日志文件名（如 `system-std-output.log`） |
| `--file-path` | 否 | 日志文件路径（通过 `--is-system` 获取） |
| `--all` | 否 | 返回全部日志，默认仅返回最新64KB |

---

### eval get-preview

```bash
cloudrobo dataset eval get-preview --task-id <id> --file-name <name> [--is-download]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务ID |
| `--file-name` | 是 | 评测报告文件名 |
| `--is-download` | 否 | 下载链接（默认为预览链接） |

---

### eval wait-task

```bash
cloudrobo dataset eval wait-task --task-id <id> [--timeout <seconds>] [--interval <seconds>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务ID |
| `--timeout` | 否 | 超时秒数，默认1800 |
| `--interval` | 否 | 轮询间隔秒数，默认10 |

---

### eval download-log

```bash
cloudrobo dataset eval download-log --task-id <id> --file-name <name> --file-path <path> [--is-download]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务ID |
| `--file-name` | 是 | 日志文件名 |
| `--file-path` | 是 | 日志文件路径（通过 `eval get-log --is-system` 获取） |
| `--is-download` | 否 | 返回OBS临时下载链接（默认返回日志内容） |

---

### eval get-events

```bash
cloudrobo dataset eval get-events --task-id <id> --job-name <job-name>
```

| 选项 | 必填 | 说明        |
|------|------|-----------|
| `--task-id` | 是 | 任务ID      |
| `--job-name` | 是 | 事件所属job名称 |

返回评测任务运行过程中的事件列表，包含事件类型（NORMAL/WARNING）、首次/最后发生时间（Unix 时间戳）、事件描述（message/cn_reason）及发生次数。

## 命令详情

### create-task

```bash
cloudrobo dataset proc create-task --name <name> --algo-type <type> --task-config <json> [--workspace-id <id>] [--wait] [--timeout <seconds>] [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--name` | 是 | 任务名称 |
| `--algo-type` | 是 | 算法类型（如 `PRESET_ASSETS`） |
| `--task-config` | 是 | 任务配置 JSON，包含 algo_name/image/dataset_configs 等全部字段 |
| `--workspace-id` | 否 | 工作空间ID，不提供则使用默认配置 |
| `--wait` | 否 | 创建后等待任务完成 |
| `--timeout` | 否 | 等待超时秒数，默认1800（配合 `--wait` 使用） |
| `--dry-run` | 否 | 仅预览 |

**示例**:
```bash
cloudrobo dataset proc create-task \
  --name ik-task-01 \
  --algo-type PRESET_ASSETS \
  --task-config '{
    "algo_name": "数据处理--逆运动学求解器",
    "algo_entrance": "bash entrypoint.sh",
    "image": "<image-url>",
    "algo_id": "a3f8b2c1-9d4e-5f6a-b7c8-1e2d3f4a5b6c",
    "catalog_id": "<catalog-id>",
    "resource_pool_type": "PUBLIC_POOL",
    "cluster_type": "CCE",
    "task_framework_type": "K8S",
    "dataset_configs": "[{\"obs_path\":\"obs://bucket/path/\",\"dataset_type\":\"BUILD_IN_ASSET\",\"asset_id\":\"<asset-id>\",\"asset_name\":\"ros2-ik\",\"version_id\":\"<version-id>\"}]",
    "output_type": "BUILD_IN_ASSET",
    "output_path": "obs://bucket/output-path",
    "output_name": "ik-task-01-output",
    "head_spec": {"cpu": 0, "memory": 0},
    "worker_spec": {"cpu": 4, "memory": 8},
    "worker_num": 1,
    "evs_spec": 0
  }'
```

---

### list-tasks

```bash
cloudrobo dataset proc list-tasks [--status <status>] [--name <name>] [--order-by <field>] [--order <DESC|ASC>] [--offset <n>] [--limit <n>] [--user-id <id>] [--algo-name <name>] [--output-name <name>] [--workspace-id <id>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--status` | 否 | 状态过滤（RUNNING/SUCCEEDED/FAILED 等） |
| `--name` | 否 | 按名称模糊查询 |
| `--order-by` | 否 | 排序指标（start_at/update_at/finish_at/create_at） |
| `--order` | 否 | 排序方式（DESC/ASC） |
| `--offset` | 否 | 分页偏移量 |
| `--limit` | 否 | 每页数量（1-100） |
| `--user-id` | 否 | 创建者ID过滤 |
| `--algo-name` | 否 | 算法名称过滤 |
| `--output-name` | 否 | 输出数据集名称过滤 |
| `--workspace-id` | 否 | 工作空间ID，不提供则使用默认配置 |

---

### show-task

```bash
cloudrobo dataset proc show-task --task-id <id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务ID |

---

### wait-task

```bash
cloudrobo dataset proc wait-task --task-id <id> [--timeout <seconds>] [--interval <seconds>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务ID |
| `--timeout` | 否 | 超时秒数，默认1800 |
| `--interval` | 否 | 轮询间隔秒数，默认10 |

---

### copy-task

```bash
cloudrobo dataset proc copy-task --task-id <id> --name <new-name> --output-name <new-output-name> [--task-config <json>] [--wait] [--timeout <seconds>] [--dry-run]
```

| 选项              | 必填 | 说明                                                          |
|-----------------|------|-------------------------------------------------------------|
| `--task-id`     | 是 | 被复制的源任务ID                                                   |
| `--name`        | 是 | 新任务名称                                                       |
| `--output-name` | 是 | 新输出数据集名称                                                    |
| `--task-config` | 否 | 其余可选覆盖字段 JSON，如 `{"description":"..."}` |
| `--wait`        | 否 | 复制后等待任务完成                                                   |
| `--timeout`     | 否 | 等待超时秒数，默认1800（配合 `--wait` 使用）                               |
| `--dry-run`     | 否 | 仅预览                                                         |

复制流程：先根据 `--task-id` 调用 `show-task` 获取源任务的配置信息，将其填充到复制请求体中，再用 `--name`（必填）、`--output-name`（必填）及 `--task-config` 中的字段覆盖，最后发起复制请求。新任务创建成功后进入排队中（PENDING）状态。

**示例**:
```bash
# 指定新任务名称和输出数据集名称复制
cloudrobo dataset proc copy-task --task-id <task-id> --name copy-task --output-name copy-output

# 指定新任务名称、覆盖部分字段并等待完成
cloudrobo dataset proc copy-task --task-id <task-id> --name copy-task --output-name copy-output \
  --task-config '{"description":"从原任务复制"}' --wait
```

---

### delete-task

```bash
cloudrobo dataset proc delete-task --task-id <id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务ID |

---

### update-task

```bash
cloudrobo dataset proc update-task --task-id <id> --task-config <json>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务ID |
| `--task-config` | 是 | 更新内容JSON |

---

### get-frames

```bash
cloudrobo dataset proc get-frames --task-id <id> --prefix <prefix> [--max-keys <n>] [--next_marker <marker>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务ID |
| `--prefix` | 是 | 前缀，`show-task` 返回的 `target_path`（输出数据集）或 `dataset_configs.obs_path`（输入数据集）去除桶前缀后的值（如 `target_path` 为 `obs://bucket-0/cloudrobo/test/`，则 `prefix` 传 `cloudrobo/test/`） |
| `--max-keys` | 否 | 每页返回最大数量（不超过50） |
| `--next_marker` | 否 | 下一页查询标志（上次返回的 `next_marker` 值） |

---

### get-log

```bash
# 列出日志文件
cloudrobo dataset proc get-log --task-id <id> --is-system true|false

# 获取日志内容
cloudrobo dataset proc get-log --task-id <id> --file-name <name> --file-path <path>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务ID |
| `--is-system` | 否 | True=系统日志，False=用户日志，仅列出日志文件 |
| `--file-name` | 否 | 日志文件名（如 `system-std-output.log`） |
| `--file-path` | 否 | 日志文件路径（通过 `--is-system` 获取） |
| `--all` | 否 | 返回全部日志，默认仅返回最新64KB |

**日志查询流程**：
1. 先用 `--is-system true/false` 获取日志文件列表，拿到 `file_path`
2. 再用 `--file-name` + `--file-path` 获取日志内容

---

### get-preview

```bash
cloudrobo dataset proc get-preview --task-id <id> --file-name <file-path> [--is-download]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务ID |
| `--file-name` | 是 | 文件的OBS路径（去除桶名），如 `cloudrobo/.../file-000.parquet` |
| `--is-download` | 否 | 生成下载链接（默认为预览链接） |

---

### download-log

```bash
cloudrobo dataset proc download-log --task-id <id> --file-name <name> --file-path <path> [--is-download]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务ID |
| `--file-name` | 是 | 日志文件名（通过 `get-log --is-system` 获取） |
| `--file-path` | 是 | 日志文件路径（通过 `get-log --is-system` 获取） |
| `--is-download` | 否 | 返回OBS临时下载链接（默认返回日志内容） |

---

### get-resource-usage

```bash
cloudrobo dataset proc get-resource-usage --task-id <id> --metric <metric> --start <ts> --end <ts> --step <seconds>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务ID |
| `--metric` | 是 | 监控指标：CPU_UTIL/CPU_USED_CORE/MEM_UTIL/MEM_USED_MB/NETWORK_TX_RATE/NETWORK_RX_RATE/DISK_READ_KB/DISK_WRITE_KB |
| `--start` | 是 | 起始时间戳（秒） |
| `--end` | 是 | 结束时间戳（秒） |
| `--step` | 是 | 采样间隔秒数（10-3600） |

---

### get-events

```bash
cloudrobo dataset proc get-events --task-id <id> --job-name <job-name>
```

| 选项           | 必填 | 说明        |
|--------------|------|-----------|
| `--task-id`  | 是 | 任务ID      |
| `--job-name` | 是 | 事件所属job名称 |

返回任务运行过程中的事件列表，包含事件类型（NORMAL/WARNING）、首次/最后发生时间（Unix 时间戳）、事件描述（message/cn_reason）及发生次数。
