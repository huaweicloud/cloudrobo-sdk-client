# cloudrobo-robot CLI 命令

## 命令概览

所有 `cloudrobo robot` 子命令用于管理机器人实例。

```bash
cloudrobo robot [OPTIONS] COMMAND [ARGS]...
```

## 命令列表

### create

注册机器人。

```bash
cloudrobo robot create --name <name> --type <type> --manufacturer <mfg> --robot-model <model> --workspace-id <ws-id> [--description <desc>] [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--name` | 是 | 机器人名称 |
| `--type` | 是 | 机器人类型 (HUMANOID-人形/QUADRUPED-四足/ARM-机械臂/OPERATION-复合/WHEELED-轮式/OTHER-其他) |
| `--manufacturer` | 是 | 制造商 |
| `--robot-model` | 是 | 型号 |
| `--workspace-id` | 否 | 工作空间 ID（默认当前工作空间） |
| `--description` | 否 | 描述 |
| `--dry-run` | 否 | 空运行，不实际执行 |

**示例**:
```bash
cloudrobo robot create --name e3f4a5b6-c7d8-9012-efab-123456789012 --type HUMANOID --manufacturer "Mfg A" --robot-model "Model X" --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890
```

---

### list

查询机器人列表。

```bash
cloudrobo robot list [--limit <n>] [--offset <n>] [--sort <field>] [--name <name>] [--status <status>] [--manufacturer <mfg>] [--robot-model <model>] [--workspace-id <id>] [--type <type>] [--user-id <id>] [--user-name <name>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--limit` | 否 | 每页数量(1-100) |
| `--offset` | 否 | 偏移量(0-1000) |
| `--sort` | 否 | 排序字段 |
| `--name` | 否 | 按名称过滤 |
| `--status` | 否 | 按状态过滤 |
| `--manufacturer` | 否 | 按制造商过滤 |
| `--robot-model` | 否 | 按型号过滤 |
| `--workspace-id` | 否 | 按工作空间过滤 |
| `--type` | 否 | 按类型过滤 |
| `--user-id` | 否 | 按用户 ID 过滤 |
| `--user-name` | 否 | 按用户名过滤 |

**示例**:
```bash
cloudrobo robot list --status ONLINE
```

---

### show

查询机器人详情。

```bash
cloudrobo robot show --robot-id <robot-id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--robot-id` | 是 | 机器人 ID |

**示例**:
```bash
cloudrobo robot show --robot-id a5b6c7d8-e9f0-1234-abcd-345678901234
```

---

### update

更新机器人信息。

```bash
cloudrobo robot update --robot-id <robot-id> [--name <name>] [--description <desc>] [--workspace-id <id>] [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--robot-id` | 是 | 机器人 ID |
| `--name` | 否 | 新名称 |
| `--description` | 否 | 新描述 |
| `--workspace-id` | 否 | 工作空间 ID（默认当前工作空间） |
| `--dry-run` | 否 | 空运行，不实际执行 |

**示例**:
```bash
cloudrobo robot update --robot-id a5b6c7d8-e9f0-1234-abcd-345678901234 --description "Updated description"
```

---

### delete

删除机器人。

```bash
cloudrobo robot delete --robot-id <robot-id> [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--robot-id` | 是 | 机器人 ID |
| `--dry-run` | 否 | 空运行，不实际执行 |

**示例**:
```bash
cloudrobo robot delete --robot-id a5b6c7d8-e9f0-1234-abcd-345678901234
```

---

### export-certificate

导出机器人证书。文件名自动生成：`cert_config_{机器人名称}_{时间戳}.zip`。

```bash
cloudrobo robot export-certificate --robot-id <robot-id> [--password <password>] --output <directory> [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--robot-id` | 是 | 机器人 ID |
| `--password` | 否 | 机器人证书加密密码 |
| `--output` | 是 | 机器人证书导出目录，目录必须已存在 |
| `--dry-run` | 否 | 空运行，不实际执行 |

**示例**:
```bash
cloudrobo robot export-certificate --robot-id a5b6c7d8-e9f0-1234-abcd-345678901234 --output ./certs
# 导出文件: ./certs/cert_config_My-Robot_20240101153045.zip
```

---

### show-sdk

查询机器人最新 SDK 包信息。

```bash
cloudrobo robot show-sdk
```

**示例**:
```bash
cloudrobo robot show-sdk
```
