# cloudrobo-train CLI 命令

## 命令概览

所有 `cloudrobo train` 子命令用于管理训练任务。普通训练任务与仿真强化学习任务共用同一组扁平命令，通过 `--sim-rl` 开关切换 API 面：未指定时操作 `/v1/training/train-tasks`，指定时操作 `/v1/training/rl-tasks/simulation`。

```bash
cloudrobo train [OPTIONS] COMMAND [ARGS]...
```

## 通用开关

以下命令支持 `--sim-rl` 开关，在普通训练任务与仿真强化学习任务之间切换：

`list-tasks`、`show-task`、`update-task`、`delete-tasks`、`stop-task`、`restart-task`、`save-draft`、`create-task`、`get-stages`、`get-resource-usage`、`get-logs`、`get-signed-url`、`get-events`、`stats`。

> 注：`resume-task` 仅适用于普通训练任务（仿真强化学习无对应端点）。

---

## create-task

创建训练任务（通用，提交完整配置 JSON）。支持 `--sim-rl` 切换到仿真强化学习任务创建。

```bash
cloudrobo train create-task --config <json> [--sim-rl]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--config` | 是 | 任务配置(JSON) |
| `--sim-rl` | 否 | 创建仿真强化学习任务 |

---

## list-tasks

列出训练任务，默认以表格形式展示。支持 `--json` 切换为原始 JSON 输出。支持 `--sim-rl` 列出仿真强化学习任务。

**表格列说明**：
- 普通训练任务（7列）：作业名称/ID、状态、训练方式、实例规格、运行记录、创建者、创建时间
- 仿真强化学习任务（6列）：作业名称/ID、状态、来源模型、实例规格、创建者、创建时间

```bash
cloudrobo train list-tasks [options] [--sim-rl] [--json]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--name` | 否 | 按任务名称模糊查询 |
| `--status` | 否 | 按状态过滤（可多次指定） |
| `--train-mode` | 否 | 按训练模式过滤（可多次指定，仅普通训练） |
| `--offset` | 否 | 偏移量 |
| `--limit` | 否 | 每页数目 |
| `--order` | 否 | 排序方式（DESC/ASC） |
| `--user-name` | 否 | 按创建者名称过滤 |
| `--group-id` | 否 | 按执行组 ID 过滤 |
| `--run-id` | 否 | 按运行 ID 过滤 |
| `--execution-id` | 否 | 按执行 ID 过滤 |
| `--include-archived` | 否 | 包含已归档任务 |
| `--include-history` | 否 | 包含历史记录 |
| `--only-total` | 否 | 仅返回总数 |
| `--exact-name` | 否 | 精确匹配名称 |
| `--order-time` | 否 | 排序时间字段（仅普通训练） |
| `--order-by` | 否 | 排序字段（仅 SimRL） |
| `--display-type` | 否 | 显示类型 |
| `--type` | 否 | 任务类型（仅普通训练） |
| `--sim-rl` | 否 | 列出仿真强化学习任务 |
| `--json` | 否 | 输出原始 JSON 而非表格 |

**示例**:
```bash
cloudrobo train list-tasks --status RUNNING
cloudrobo train list-tasks --sim-rl --status RUNNING
cloudrobo train list-tasks --json
cloudrobo train list-tasks --name my-task --limit 5
```

---

## show-task

查看训练任务详情。支持 `--sim-rl`。

```bash
cloudrobo train show-task --task-id <id> [--sim-rl]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务 ID |
| `--sim-rl` | 否 | 查看仿真强化学习任务 |

---

## update-task

更新训练任务名称和描述。支持 `--sim-rl`。

```bash
cloudrobo train update-task --task-id <id> --config <json> [--sim-rl]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务 ID |
| `--config` | 是 | 更新配置(JSON) |
| `--sim-rl` | 否 | 更新仿真强化学习任务 |

---

## delete-tasks

删除训练任务。普通任务走批量删除；仿真强化学习任务逐个删除。支持 `--sim-rl`。

```bash
cloudrobo train delete-tasks --task-id <id> [--task-id <id> ...] [--sim-rl]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务ID（可重复多次） |
| `--sim-rl` | 否 | 删除仿真强化学习任务 |

**示例**:
```bash
cloudrobo train delete-tasks --task-id b8c9d0e1-f2a3-4567-bcde-678901234567
cloudrobo train delete-tasks --sim-rl --task-id b8c9d0e1-f2a3-4567-bcde-678901234567
```

---

## stop-task

停止训练任务。支持 `--sim-rl`。

```bash
cloudrobo train stop-task --task-id <id> [--sim-rl]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务 ID |
| `--sim-rl` | 否 | 停止仿真强化学习任务 |

---

## restart-task

重新提交训练任务，支持修改配置。支持 `--sim-rl`。

**状态约束**：
- 普通训练任务：
  - 非草稿状态（FAILED/SUBMIT_FAILED/STOPPED/FINISHED）：可修改除 `name`/`train_mode`/`train_method` 外的所有字段
  - 草稿状态（DRAFT）：可修改除 `name` 外的所有字段
- 仿真强化学习任务：仅允许在 `DRAFT` 状态时重启

**output_models 自动转换**：
当用户修改 `input_models`（换基模型）时，如果原任务的 `output_models[0].save_mode` 为 `NEW_MODEL`：
- 用户**未提供** `output_models` → 自动转为 `NEW_VERSION`，版本号 +1（如 v0.0.33 → v0.0.34）
- 用户**提供了** `output_models` 且**不含** `model_asset_id` → 保持 `NEW_MODEL`（创建全新模型）
- 用户**提供了** `output_models` 且**含有** `model_asset_id` → 自动转为 `NEW_VERSION`，版本号 +1

```bash
cloudrobo train restart-task --task-id <id> [--config <json>] [--config-file <path>] [--sim-rl] [--verbose]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务 ID |
| `--config` | 否 | 修改配置(JSON字符串) |
| `--config-file` | 否 | 修改配置文件路径 |
| `--sim-rl` | 否 | 重启仿真强化学习任务 |
| `--verbose` | 否 | 展示提交内容详情 |

**示例**：
```bash
# 使用原配置重启
cloudrobo train restart-task --task-id b8c9d0e1-f2a3-4567-bcde-678901234567

# 修改描述和规格后重启
cloudrobo train restart-task --task-id b8c9d0e1-f2a3-4567-bcde-678901234567 \
  --config '{"description":"updated","spec":"Ascend: 2 * SNT9B2 | 48 vCPUs | 384 GiB"}'

# 使用配置文件修改后重启
cloudrobo train restart-task --task-id b8c9d0e1-f2a3-4567-bcde-678901234567 \
  --config-file restart-config.json
```

---

## resume-task

续训训练任务（仅普通训练任务，仿真强化学习无此端点）。

```bash
cloudrobo train resume-task --task-id <id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务 ID |

---

## clone-task

克隆仿真强化学习任务（仅支持 SimRL，普通训练任务不支持克隆）。

```bash
cloudrobo train clone-task --task-id <id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 仿真强化学习任务 ID |

---

## save-draft

保存训练配置草稿。支持 `--sim-rl`。

```bash
cloudrobo train save-draft --config <json> [--sim-rl]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--config` | 是 | 任务配置(JSON) |
| `--sim-rl` | 否 | 保存仿真强化学习任务草稿 |

---

## stats

统计各状态训练任务数量。支持 `--sim-rl`。

```bash
cloudrobo train stats --workspace-id <id> [--user-id <id>] [--sim-rl]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--workspace-id` | 是 | 工作空间ID |
| `--user-id` | 否 | 用户ID |
| `--sim-rl` | 否 | 统计仿真强化学习任务 |

---

## get-stages

获取训练阶段信息。支持 `--sim-rl`。

```bash
cloudrobo train get-stages --task-id <id> [--sim-rl]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务 ID |
| `--sim-rl` | 否 | 获取仿真强化学习任务阶段 |

---

## get-resource-usage

查看资源使用情况。支持 `--sim-rl`。

```bash
cloudrobo train get-resource-usage --task-id <id> --metric <m> --start <ts> --end <ts> [--worker-index <n>] [--step <n>] [--sim-rl]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务 ID |
| `--metric` | 是 | 指标类型：cpu_util/cpu_used_core/mem_util/mem_used_mb/npu_util/npu_mem_util/npu_mem_used_mb/gpu_util/gpu_mem_util/gpu_mem_used_mb/network_tx_rate/network_rx_rate/disk_read_kb/disk_write_kb |
| `--start` | 是 | 起始时间戳（秒） |
| `--end` | 是 | 结束时间戳（秒） |
| `--worker-index` | 否 | Worker序号，省略表示作业平均值 |
| `--step` | 否 | 采样间隔（秒），默认60 |
| `--sim-rl` | 否 | 查看仿真强化学习任务资源使用 |

**示例**:
```bash
cloudrobo train get-resource-usage --task-id b8c9d0e1-f2a3-4567-bcde-678901234567 --metric gpu_util --start 1716000000 --end 1716003600
```

---

## get-logs

获取训练日志内容。支持 `--sim-rl`。

```bash
cloudrobo train get-logs --task-id <id> [--file-name <name>] [--log-name-pre <pre>] [--work-num <n>] [--catalog <c>] [--start-byte <n>] [--end-byte <n>] [--offset <n>] [--limit <n>] [--sim-rl]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务 ID |
| `--file-name` | 否 | 日志文件名称 |
| `--log-name-pre` | 否 | 日志文件名前缀（file_name 优先） |
| `--work-num` | 否 | 多机训练节点序号 |
| `--catalog` | 否 | 文件目录类型：logs/metrics |
| `--start-byte` | 否 | 起始字节 |
| `--end-byte` | 否 | 结束字节 |
| `--offset` | 否 | 起始页 |
| `--limit` | 否 | 每页条数 |
| `--sim-rl` | 否 | 获取仿真强化学习任务日志 |

**示例**:
```bash
cloudrobo train get-logs --task-id b8c9d0e1-f2a3-4567-bcde-678901234567 --file-name worker0.log --catalog logs
```

---

## get-signed-url

获取日志文件下载签名URL。支持 `--sim-rl`。

```bash
cloudrobo train get-signed-url --task-id <id> --file-source <src> --file-name <name> [--catalog <c>] [--sim-rl]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务 ID |
| `--file-source` | 是 | 日志类型：EVALUATE/TRAIN/TRAINING_METRICS/EVALUATE_REPORT/COMPILE/COMPARISON_LOG/COMPARISON_REPORT/BADCASE |
| `--file-name` | 是 | 日志文件名称 |
| `--catalog` | 否 | 文件目录类型：logs/metrics |
| `--sim-rl` | 否 | 获取仿真强化学习任务签名URL |

---

## get-events

获取训练事件。支持 `--sim-rl`。

```bash
cloudrobo train get-events --task-id <id> --start-time <ts> --end-time <ts> [--level <l>] [--source <s>] [--pattern <p>] [--offset <n>] [--limit <n>] [--order <o>] [--sim-rl]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务 ID |
| `--start-time` | 是 | 开始时间戳（毫秒） |
| `--end-time` | 是 | 结束时间戳（毫秒） |
| `--level` | 否 | 事件级别：Info/Warning/Error |
| `--source` | 否 | 事件来源：K8S/Job/Task |
| `--pattern` | 否 | 事件内容匹配模式 |
| `--offset` | 否 | 起始页 |
| `--limit` | 否 | 每页数目 |
| `--order` | 否 | 排序方式：DESC/ASC |
| `--sim-rl` | 否 | 获取仿真强化学习任务事件 |

**示例**:
```bash
cloudrobo train get-events --task-id b8c9d0e1-f2a3-4567-bcde-678901234567 --start-time 1716000000000 --end-time 1716003600000 --level Error
```

---

## list-checkpoints

获取训练任务 checkpoint 列表（仅普通训练任务，仿真强化学习无此端点）。

```bash
cloudrobo train list-checkpoints --task-id <id> [--offset <n>] [--limit <n>] [--order <o>] [--status <s>] [--name <name>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务 ID |
| `--offset` | 否 | 起始页 |
| `--limit` | 否 | 每页条数（1-50，默认 10） |
| `--order` | 否 | 排序方式：DESC/ASC |
| `--status` | 否 | 注册状态：UNREGISTERED/PENDING/PROCESSING/SUCCESS/FAILED/EXPIRED |
| `--name` | 否 | checkpoint 名称模糊搜索 |

**示例**:
```bash
cloudrobo train list-checkpoints --task-id b8c9d0e1-f2a3-4567-bcde-678901234567 --status SUCCESS --limit 20
```

---

## register-checkpoint

注册 checkpoint 为模型资产版本（仅普通训练任务，仿真强化学习无此端点）。

```bash
cloudrobo train register-checkpoint --task-id <id> --checkpoint-name <name> [--save-mode <mode>] [--version-name <ver>] [--model-name <name>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--task-id` | 是 | 任务 ID |
| `--checkpoint-name` | 是 | checkpoint 名称 |
| `--save-mode` | 否 | 保存方式：NEW_VERSION（默认）/NEW_MODEL |
| `--version-name` | 否 | 版本标签（NEW_VERSION 模式可选） |
| `--model-name` | 否 | 模型名称（NEW_MODEL 模式必填） |

**示例**:
```bash
cloudrobo train register-checkpoint --task-id b8c9d0e1-f2a3-4567-bcde-678901234567 --checkpoint-name checkpoint_1000 --save-mode NEW_VERSION --version-name 0.0.2
```
