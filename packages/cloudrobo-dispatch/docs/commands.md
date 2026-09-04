# cloudrobo-dispatch CLI 命令

## 命令概览

所有 `cloudrobo dispatch` 子命令用于管理会话任务（对应 `robo-operations.yaml` 中 RoboDispatcherTaskManagement）。

```bash
cloudrobo dispatch [OPTIONS] COMMAND [ARGS]...
```

## 命令列表

### create-task

创建任务（CreateDispatcherTask）。请求体严格按 `CreateDispatcherTaskRequestBody`：`name`、`task`、`constraints`（含 `model.exec_model_id`、`robot_id`、可选 `exec_constraints`）。

```bash
cloudrobo dispatch create-task --session-id <id> --name <name> --task <task> --constraints-json <json> [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--session-id` | 否（默认当前工作空间） | 会话 ID |
| `--name` | 是 | 任务名称 |
| `--task` | 是 | 任务描述 |
| `--constraints-json` | 是 | 执行约束对象 JSON，形如 `{"model":{"exec_model_id":"m1"},"robot_id":"r1","exec_constraints":{"max_iter_num":100,"max_run_time":10}}` |
| `--dry-run` | 否 | 试运行，不发送请求 |

**示例**:
```bash
cloudrobo dispatch create-task --session-id b6c7d8e9-f0a1-2345-bcde-456789012345 --name "task-1" --task "grasp red cube" --constraints-json '{"model":{"exec_model_id":"m1"},"robot_id":"r1","exec_constraints":{"max_iter_num":100,"max_run_time":10}}'
```

---

### list-tasks

列出会话任务（ListDispatcherTasks），支持分页、排序与多条件筛选。

```bash
cloudrobo dispatch list-tasks --session-id <id> [--limit <n>] [--offset <n>] [--sort-key <key>] [--sort-dir <dir>] [--status <status>] [--robot-id <id>] [--start-time <ms>] [--end-time <ms>] [--infer-service-id <id>] [--content-match <text>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--session-id` | 否（默认当前工作空间） | 会话 ID |
| `--limit` | 否 | 每页数量(1-100) |
| `--offset` | 否 | 偏移量(0-10000) |
| `--sort-key` | 否 | 排序字段（默认 updated_at，支持 created_at/updated_at/create_at/update_at） |
| `--sort-dir` | 否 | 排序方向（ASC/DESC，默认 DESC） |
| `--status` | 否 | 按执行状态筛选（RUNNING/COMPLETED/FAILED/CANCELLED） |
| `--robot-id` | 否 | 按机器人 ID 筛选 |
| `--start-time` | 否 | 开始时间（UTC 毫秒时间戳） |
| `--end-time` | 否 | 结束时间（UTC 毫秒时间戳） |
| `--infer-service-id` | 否 | 按推理服务 ID 筛选 |
| `--content-match` | 否 | 技能 prompt 或服务名称模糊搜索内容 |

---

### show-task

查询任务详情（ShowDispatcherTask）。

```bash
cloudrobo dispatch show-task --session-id <id> --task-id <id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--session-id` | 否（默认当前工作空间） | 会话 ID |
| `--task-id` | 是 | 任务 ID |

---

### cancel-task

取消任务（CancelDispatcherTask）。

```bash
cloudrobo dispatch cancel-task --session-id <id> --task-id <id> [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--session-id` | 否（默认当前工作空间） | 会话 ID |
| `--task-id` | 是 | 任务 ID |
| `--dry-run` | 否 | 试运行，不发送请求 |

---

### show-task-result

获取任务执行结果（ShowDispatcherTaskResult，任务信息与日志）。

```bash
cloudrobo dispatch show-task-result --session-id <id> --task-id <id> [--inverse] [--limit <n>] [--offset <n>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--session-id` | 否（默认当前工作空间） | 会话 ID |
| `--task-id` | 是 | 任务 ID |
| `--inverse` | 否 | 倒序查询，offset=0 代表最后一个字节（默认 false） |
| `--limit` | 否 | 单次请求日志字节限制（100-10000，默认 200） |
| `--offset` | 否 | 单次请求日志字节偏移量（0-2147483647，默认 0） |

---

### wait-task

等待任务完成（每 5 秒查询一次任务状态，直到状态不在 `RUNNING` 或超时）。状态枚举仅 `RUNNING`/`COMPLETED`/`FAILED`/`CANCELLED`。

```bash
cloudrobo dispatch wait-task --session-id <id> --task-id <id> [--timeout <秒>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--session-id` | 否（默认当前工作空间） | 会话 ID |
| `--task-id` | 是 | 任务 ID |
| `--timeout` | 否 | 等待超时时间（秒，默认 600，范围 1-3600） |

**示例**:
```bash
cloudrobo dispatch wait-task --session-id b6c7d8e9-f0a1-2345-bcde-456789012345 --task-id 123e4567-e89b-12d3-a456-426614174000 --timeout 600
```
