# cloudrobo-workspace CLI 命令

## 命令概览

所有 `cloudrobo workspace` 子命令用于管理工作空间。

```bash
cloudrobo workspace [OPTIONS] COMMAND [ARGS]...
```

## 命令列表

### create

创建工作空间。

```bash
cloudrobo workspace create --name <name> --default-obs-path <obs-path> [OPTIONS]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--name` | 是 | 工作空间名称 |
| `--default-obs-path` | 是 | 默认OBS路径 |
| `--description` | 否 | 工作空间描述 |
| `--tags` | 否 | 标签列表（逗号分隔） |
| `--member-list` | 否 | 成员列表（JSON字符串） |
| `--dry-run` | 否 | 仅打印操作，不实际执行 |

**示例**:
```bash
cloudrobo workspace create --name production --default-obs-path obs://bucket/path
cloudrobo workspace create --name dev --default-obs-path obs://bucket/dev --description "开发环境" --tags "tag1,tag2"
cloudrobo workspace create --name team --default-obs-path obs://bucket/team --member-list '[{"user_id":"u1","role_ids":["r1"]}]'
```

---

### list

列出工作空间。

```bash
cloudrobo workspace list [OPTIONS]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--limit` | 否 | 每页返回数量 |
| `--offset` | 否 | 偏移量 |

**示例**:
```bash
cloudrobo workspace list
cloudrobo workspace list --limit 10 --offset 0
```

---

### show

查看工作空间详情。

```bash
cloudrobo workspace show --workspace-id <workspace-id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--workspace-id` | 是 | 工作空间ID |

**示例**:
```bash
cloudrobo workspace show --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890
```

---

### update

更新工作空间。

```bash
cloudrobo workspace update --workspace-id <workspace-id> [OPTIONS]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--workspace-id` | 是 | 工作空间ID |
| `--name` | 否 | 新的工作空间名称 |
| `--description` | 否 | 新的工作空间描述 |
| `--tags` | 否 | 标签列表（逗号分隔，全量替换） |
| `--owner-id` | 否 | 责任人用户ID |
| `--default-obs-path` | 否 | 默认OBS路径 |
| `--bind-obs-policy` | 否 | 仅绑定OBS桶策略，不更新其他字段（flag选项） |
| `--dry-run` | 否 | 仅打印操作，不实际执行 |

**示例**:
```bash
cloudrobo workspace update --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890 --name new-name
cloudrobo workspace update --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890 --description "新描述" --tags "tag1,tag2"
cloudrobo workspace update --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890 --bind-obs-policy
```

---

### delete

删除工作空间。

```bash
cloudrobo workspace delete --workspace-id <workspace-id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--workspace-id` | 是 | 工作空间ID |
| `--dry-run` | 否 | 仅打印操作，不实际执行 |

**示例**:
```bash
cloudrobo workspace delete --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890
```

---

### list-members

列出工作空间成员。

```bash
cloudrobo workspace list-members --workspace-id <workspace-id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--workspace-id` | 是 | 工作空间ID |

**示例**:
```bash
cloudrobo workspace list-members --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890
```

---

### add-members

添加工作空间成员。

```bash
cloudrobo workspace add-members --workspace-id <workspace-id> --member-list <json>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--workspace-id` | 是 | 工作空间ID |
| `--member-list` | 是 | 成员列表（JSON字符串） |

**示例**:
```bash
cloudrobo workspace add-members --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890 --member-list '[{"user_id":"user1","role_ids":["role-id-1"]}]'
```

---

### delete-members

删除工作空间成员。

```bash
cloudrobo workspace delete-members --workspace-id <workspace-id> --user-ids <user-ids>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--workspace-id` | 是 | 工作空间ID |
| `--user-ids` | 是 | 用户ID列表（逗号分隔） |

**示例**:
```bash
cloudrobo workspace delete-members --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890 --user-ids "user1,user2"
```

---

### overview

查看工作空间概览统计。

```bash
cloudrobo workspace overview
```

**示例**:
```bash
cloudrobo workspace overview
```

---

### use

使用指定工作空间，验证有效性并保存工作空间信息。

```bash
cloudrobo workspace use --workspace-id <workspace-id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--workspace-id` | 是 | 工作空间ID |

**示例**:
```bash
cloudrobo workspace use --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890
```

---

### current

显示当前工作空间配置。

```bash
cloudrobo workspace current
```

**示例**:
```bash
cloudrobo workspace current
```
