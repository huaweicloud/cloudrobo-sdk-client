# cloudrobo-eval CLI 命令

## 命令概览

所有 `cloudrobo eval` 子命令用于管理技能仿真评测任务。

```bash
cloudrobo eval [OPTIONS] COMMAND [ARGS]...
```

## 命令列表

### create-job

创建技能仿真评测任务。

```bash
cloudrobo eval create-job --name <name> --virtual-world-id <id> --infer-server-id <id> --model-source <source> [--skill-description <desc>] [--testing-round <n>] [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--name` | 是 | 任务名称 |
| `--virtual-world-id` | 是 | 仿真世界 ID |
| `--infer-server-id` | 是 | 推理服务 ID |
| `--model-source` | 是 | 模型来源：CLOUDROBO_SQUARE / WORKSPACE |
| `--skill-description` | 否 | 技能描述 |
| `--testing-round` | 否 | 测试轮数 |
| `--dry-run` | 否 | 试运行，不实际创建 |

**示例**:
```bash
cloudrobo eval create-job --name my-eval --virtual-world-id d6e7f8a9-b0c1-2345-defa-456789012345 --infer-server-id e7f8a9b0-c1d2-3456-efab-567890123456 --model-source CLOUDROBO_SQUARE
```

---

### list-jobs

查询评测任务列表。

```bash
cloudrobo eval list-jobs [--status <status>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--status` | 否 | 按状态过滤 |

**示例**:
```bash
cloudrobo eval list-jobs --status running
```

---

### show-job

查询评测任务详情。

```bash
cloudrobo eval show-job --job-id <id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--job-id` | 是 | 任务 ID |

**示例**:
```bash
cloudrobo eval show-job --job-id e1f2a3b4-c5d6-7890-efab-901234567890
```

---

### stop-job

停止评测任务。

```bash
cloudrobo eval stop-job --job-id <id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--job-id` | 是 | 任务 ID |

**示例**:
```bash
cloudrobo eval stop-job --job-id e1f2a3b4-c5d6-7890-efab-901234567890
```

---

### restart-job

重启评测任务。

```bash
cloudrobo eval restart-job --job-id <id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--job-id` | 是 | 任务 ID |

**示例**:
```bash
cloudrobo eval restart-job --job-id e1f2a3b4-c5d6-7890-efab-901234567890
```

---

### delete-job

删除评测任务。

```bash
cloudrobo eval delete-job --job-id <id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--job-id` | 是 | 任务 ID |

**示例**:
```bash
cloudrobo eval delete-job --job-id e1f2a3b4-c5d6-7890-efab-901234567890
```

---

### batch-delete-jobs

批量删除评测任务。

```bash
cloudrobo eval batch-delete-jobs --job-ids <id1,id2,...>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--job-ids` | 是 | 任务 ID 列表（逗号分隔） |

**示例**:
```bash
cloudrobo eval batch-delete-jobs --job-ids f2a3b4c5-d6e7-8901-fabc-012345678901,a3b4c5d6-e7f8-9012-abcd-123456789012,b4c5d6e7-f8a9-0123-bcde-234567890123
```

---

### list-executions

查询执行记录列表。

```bash
cloudrobo eval list-executions --job-id <id> [--status <status>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--job-id` | 是 | 任务 ID |
| `--status` | 否 | 按状态过滤 |

**示例**:
```bash
cloudrobo eval list-executions --job-id e1f2a3b4-c5d6-7890-efab-901234567890 --status completed
```

---

### show-execution

查询执行记录详情。

```bash
cloudrobo eval show-execution --job-id <id> --execution-id <eid>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--job-id` | 是 | 任务 ID |
| `--execution-id` | 是 | 执行记录 ID |

**示例**:
```bash
cloudrobo eval show-execution --job-id e1f2a3b4-c5d6-7890-efab-901234567890 --execution-id c5d6e7f8-a9b0-1234-cdef-345678901234
```

---

### get-vnc-address

获取仿真环境 VNC 登录链接。

```bash
cloudrobo eval get-vnc-address --job-id <id> --execution-id <eid>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--job-id` | 是 | 任务 ID |
| `--execution-id` | 是 | 执行记录 ID |

**示例**:
```bash
cloudrobo eval get-vnc-address --job-id e1f2a3b4-c5d6-7890-efab-901234567890 --execution-id c5d6e7f8-a9b0-1234-cdef-345678901234
```

---

### show-stats

作业状态统计。

```bash
cloudrobo eval show-stats [--workspace-id <id>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--workspace-id` | 否 | 工作空间 ID |

**示例**:
```bash
cloudrobo eval show-stats --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890
```

---

### run-with-generalization

带泛化性测试的评测。

```bash
cloudrobo eval run-with-generalization --config <json> --generalization-types <types> [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--config` | 是 | 评测配置（JSON 字符串） |
| `--generalization-types` | 是 | 泛化测试类型（逗号分隔） |
| `--dry-run` | 否 | 试运行，不实际创建 |

**示例**:
```bash
cloudrobo eval run-with-generalization --config '{"name":"gen-eval","virtual_world_id":"d6e7f8a9-b0c1-2345-defa-456789012345","infer_server_id":"e7f8a9b0-c1d2-3456-efab-567890123456","model_source":"CLOUDROBO_SQUARE"}' --generalization-types noise,obstacle
```
