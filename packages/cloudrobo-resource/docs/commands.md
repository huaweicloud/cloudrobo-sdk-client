# cloudrobo-resource CLI 命令

## 命令概览

所有 `cloudrobo resource` 子命令用于查询资源配额和资源池信息。

```bash
cloudrobo resource [OPTIONS] COMMAND [ARGS]...
```

## 命令列表

### list-quotas

查询配额列表。

```bash
cloudrobo resource list-quotas [OPTIONS]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--workspace-id` | 否 | 工作空间ID |
| `--resource-id` | 否 | 资源ID |
| `--resource-type` | 否 | 资源类型（CCE / MODELARTS） |
| `--resource-sub-type` | 否 | 资源子类型（CPU / GPU / STANDARD / LITE） |
| `--pool-type` | 否 | 资源池类型（DEDICATED / SHARED） |
| `--limit` | 否 | 每页数量（1-50） |
| `--offset` | 否 | 偏移量 |
| `--order` | 否 | 排序方式（ASC / DESC） |

**示例**:
```bash
cloudrobo resource list-quotas
cloudrobo resource list-quotas --resource-type CCE --pool-type DEDICATED --limit 20
cloudrobo resource list-quotas --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890 --order ASC
```

---

### list-pools

查询资源池列表。

```bash
cloudrobo resource list-pools [OPTIONS]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--resource-type` | 否 | 资源类型（CCE / MODELARTS） |
| `--resource-sub-type` | 否 | 资源子类型（CPU / GPU / STANDARD / LITE） |
| `--pool-type` | 否 | 资源池类型（DEDICATED / SHARED） |
| `--usages` | 否 | 用途列表（逗号分隔） |
| `--limit` | 否 | 每页数量（1-50） |
| `--offset` | 否 | 偏移量 |
| `--order` | 否 | 排序方式（ASC / DESC） |

**示例**:
```bash
cloudrobo resource list-pools
cloudrobo resource list-pools --resource-type MODELARTS --pool-type SHARED
cloudrobo resource list-pools --usages "TRAINING,INFERENCE" --limit 10 --offset 0
```

---

### show-pool

查询资源池详情。

```bash
cloudrobo resource show-pool --pool-id <pool-id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--pool-id` | 是 | 资源池ID |

**示例**:
```bash
cloudrobo resource show-pool --pool-id a1b2c3d4-e5f6-7890-abcd-ef1234567890
```
