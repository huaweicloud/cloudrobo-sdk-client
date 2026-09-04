# cloudrobo-infer CLI 命令

## 命令概览

所有 `cloudrobo infer` 子命令用于管理推理服务。

```bash
cloudrobo infer [OPTIONS] COMMAND [ARGS]...
```

## 命令列表

### create

创建推理服务（部署模型）。

```bash
cloudrobo infer create --name <name> --flavor <flavor> --model-json '<{"model_id":..,"model_version_id":..}>' [--workspace-id <workspace-id>] --pool-id <pool-id> --pool-type <pool-type> [--description <desc>] [--image-swr-url <url>] [--cmd <cmd>] [--envs-json <json>] [--stop-schedule-json <json>] [--deploy-timeout-minutes <n>] [--service-invoke-json <json>] [--skill-config-json <json>] [--files-json <json>] [--model-ext-metadata <metadata>] [--startup-health-json <json>] [--readiness-health-json <json>] [--liveness-health-json <json>] [--internet-access-enable] [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--name` | 是 | 服务名称 |
| `--flavor` | 是 | 规格（如 cpu.2） |
| `--model-json` | 是 | 模型配置（JSON 对象，含 `model_id`/`model_version_id`，可选 `mount_path`） |
| `--workspace-id` | 否（默认当前工作空间） | 工作空间 ID |
| `--pool-id` | 是 | 资源池 ID |
| `--pool-type` | 是 | 资源池类型（合法值：SHARED/DEDICATED） |
| `--description` | 否 | 描述 |
| `--image-swr-url` | 否 | 镜像地址 |
| `--cmd` | 否 | 启动命令 |
| `--envs-json` | 否 | 环境变量（JSON字符串） |
| `--stop-schedule-json` | 否 | 定时停止配置（JSON字符串） |
| `--deploy-timeout-minutes` | 否 | 部署超时时间（分钟） |
| `--service-invoke-json` | 否 | 服务调用配置（JSON字符串） |
| `--skill-config-json` | 否 | 技能配置（JSON字符串） |
| `--files-json` | 否 | 文件挂载列表（JSON字符串） |
| `--model-ext-metadata` | 否 | 模型扩展元数据（JSON/YAML字符串） |
| `--startup-health-json` | 否 | 启动健康检查（JSON字符串） |
| `--readiness-health-json` | 否 | 就绪健康检查（JSON字符串） |
| `--liveness-health-json` | 否 | 存活健康检查（JSON字符串） |
| `--internet-access-enable` | 否 | 是否开启公网访问（布尔标志，出现即置 true） |
| `--dry-run` | 否 | 空运行，不实际执行 |

**示例**:
```bash
cloudrobo infer create --name chat-api --flavor cpu.2 --model-json '{"model_id":"a9b0c1d2-e3f4-5678-abcd-789012345678","model_version_id":"d8e9f0a1-b2c3-4567-defa-678901234567"}' --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890 --pool-id pool-public --pool-type SHARED
```

---

### list

查询推理服务列表。

```bash
cloudrobo infer list [--limit <n>] [--offset <n>] [--sort-key <key>] [--sort-dir <dir>] [--name <name>] [--workspace-id <id>] [--status <status>] [--model-id <id>] [--model-name <name>] [--model-version-id <id>] [--model-version-name <name>] [--user-name <name>] [--user-id <id>] [--contain-ext-metadata]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--limit` | 否 | 每页数量(1-50) |
| `--offset` | 否 | 偏移量(0-1000) |
| `--sort-key` | 否 | 排序字段 |
| `--sort-dir` | 否 | 排序方向（ASC/DESC） |
| `--name` | 否 | 按名称过滤 |
| `--workspace-id` | 否 | 按工作空间过滤 |
| `--status` | 否 | 按状态过滤 |
| `--model-id` | 否 | 按模型 ID 过滤 |
| `--model-name` | 否 | 按模型名称过滤 |
| `--model-version-id` | 否 | 按模型版本 ID 过滤 |
| `--model-version-name` | 否 | 按模型版本名称过滤 |
| `--user-name` | 否 | 按用户名过滤 |
| `--user-id` | 否 | 按用户 ID 过滤 |
| `--contain-ext-metadata` | 否 | 布尔标志。省略=返回全部；出现=只返回包含 `model_ext_metadata` 的记录 |

**示例**:
```bash
cloudrobo infer list --status running
```

---

### show

查询推理服务详情。

```bash
cloudrobo infer show --service-id <service-id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--service-id` | 是 | 服务 ID |

**示例**:
```bash
cloudrobo infer show --service-id f8a9b0c1-d2e3-4567-fabc-678901234567
```

---

### update

更新推理服务配置。

```bash
cloudrobo infer update --service-id <service-id> [--description <desc>] [--model-ext-metadata <metadata>] [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--service-id` | 是 | 服务 ID |
| `--description` | 否 | 新描述 |
| `--model-ext-metadata` | 否 | 模型扩展元数据（JSON/YAML 字符串） |
| `--dry-run` | 否 | 空运行，不实际执行 |

**示例**:
```bash
cloudrobo infer update --service-id f8a9b0c1-d2e3-4567-fabc-678901234567 --description "更新后的描述"
```

---

### delete

删除推理服务。

```bash
cloudrobo infer delete --service-id <service-id> [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--service-id` | 是 | 服务 ID |
| `--dry-run` | 否 | 空运行，不实际执行 |

**示例**:
```bash
cloudrobo infer delete --service-id f8a9b0c1-d2e3-4567-fabc-678901234567
```

---

### start

启动推理服务。

```bash
cloudrobo infer start --service-id <service-id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--service-id` | 是 | 服务 ID |

**示例**:
```bash
cloudrobo infer start --service-id f8a9b0c1-d2e3-4567-fabc-678901234567
```

---

### stop

停止推理服务。

```bash
cloudrobo infer stop --service-id <service-id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--service-id` | 是 | 服务 ID |

**示例**:
```bash
cloudrobo infer stop --service-id f8a9b0c1-d2e3-4567-fabc-678901234567
```

---

### list-logs

查询推理服务日志。

```bash
cloudrobo infer list-logs --service-id <service-id> --start-time <start-time> --end-time <end-time> [--limit <limit>] [--is-desc] [--line-num <num>] [--is-count] [--keywords <keywords>] [--highlight] [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--service-id` | 是 | 服务 ID |
| `--start-time` | 否 | 搜索日志起始时间(毫秒时间戳)。CLI 层可选，但请求体始终携带该字段（不传时以 `None` 写入） |
| `--end-time` | 否 | 搜索日志结束时间(毫秒时间戳)。CLI 层可选，但请求体始终携带该字段（不传时以 `None` 写入） |
| `--limit` | 否 | 每次查询的日志条数(1-5000) |
| `--is-desc` | 否 | 倒序查询 |
| `--line-num` | 否 | 日志单行序列号（分页用） |
| `--is-count` | 否 | 是否统计日志条数 |
| `--keywords` | 否 | 日志关键词精确搜索 |
| `--highlight` | 否 | 关键词是否高亮显示 |
| `--dry-run` | 否 | 空运行，不实际执行 |

**示例**:
```bash
cloudrobo infer list-logs --service-id f8a9b0c1-d2e3-4567-fabc-678901234567 --start-time 1779782400000 --end-time 1779868800000 --keywords error --limit 100
```

---

### wait-deploy

等待推理服务部署完成。

```bash
cloudrobo infer wait-deploy --service-id <service-id> [--timeout <seconds>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--service-id` | 是 | 服务 ID |
| `--timeout` | 否 | 等待超时秒数，默认 600 |

**示例**:
```bash
cloudrobo infer wait-deploy --service-id f8a9b0c1-d2e3-4567-fabc-678901234567 --timeout 600
```
