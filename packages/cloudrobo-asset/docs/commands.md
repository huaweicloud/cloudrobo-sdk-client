# cloudrobo-asset CLI 命令

## 命令概览

所有 `cloudrobo asset` 子命令用于管理资产仓库、目录、资产、版本、标签、Action等。

```bash
cloudrobo asset [OPTIONS] COMMAND [ARGS]...
```

## 命令列表

### 仓库管理

| 命令 | 说明 |
|------|------|
| `list-repositories` | 列出资产库列表 |

### 目录管理

| 命令 | 说明 |
|------|------|
| `list-catalogs` | 列出目录 |
| `show-catalog` | 查看目录详情 |

### 资产管理

| 命令 | 说明 |
|------|------|
| `create-asset` | 创建资产 |
| `list-assets` | 列出资产 |
| `show-asset` | 查看资产详情 |
| `update-asset` | 更新资产 |
| `delete-asset` | 删除资产 |
| `batch-delete-assets` | 批量删除资产 |

### 版本管理

| 命令 | 说明 |
|------|------|
| `create-version` | 创建资产版本 |
| `list-versions` | 查询资产版本列表 |
| `show-version` | 查看资产版本详情 |
| `update-version` | 更新资产版本 |
| `delete-version` | 删除资产版本 |
| `batch-delete-versions` | 批量删除资产版本 |

### 标签

| 命令 | 说明 |
|------|------|
| `add-tags` | 添加标签 |
| `delete-tag` | 删除资产标签 |
| `list-tags` | 查询预定义标签列表 |


### Action 管理

| 命令 | 说明 |
|------|------|
| `list-actions` | 查询资产支持的action列表 |
| `create-action` | 添加资产action |
| `show-action` | 查询资产action详情 |
| `update-action` | 修改资产action |
| `delete-action` | 删除资产action |

### 权限校验与血缘

| 命令 | 说明 |
|------|------|
| `check-permission` | 校验资产权限 |
| `show-lineage` | 查看血缘关系 |

### 搜索与广场

| 命令 | 说明 |
|------|------|
| `search-assets` | 搜索广场资产 |
| `list-publication-assets` | 查询官方和社区资产列表 |

### 导入导出

| 命令 | 说明 |
|------|------|
| `import-asset` | 导入资产 |
| `export-asset` | 导出资产 |

## 命令详情

### list-repositories

```bash
cloudrobo asset list-repositories [--name <name>] [--sort-dir <dir>] [--offset <n>] [--limit <n>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--name` | 否 | 根据资产库名称模糊查询 |
| `--sort-dir` | 否 | 排序方向（asc/desc） |
| `--offset` | 否 | 起始数据偏移量 |
| `--limit` | 否 | 返回的对象数量 |

---

### list-catalogs

```bash
cloudrobo asset list-catalogs --repository-id <id> [--name <name>] [--sort-dir <dir>] [--offset <n>] [--limit <n>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--repository-id` | 是 | 仓库ID |
| `--name` | 否 | 根据资产目录名称模糊查询 |
| `--sort-dir` | 否 | 排序方向（asc/desc） |
| `--offset` | 否 | 起始数据偏移量 |
| `--limit` | 否 | 返回的对象数量 |

---

### show-catalog

```bash
cloudrobo asset show-catalog --catalog-id <id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--catalog-id` | 是 | 目录ID |

---

### 前置步骤：获取 catalog_id

多个命令需要 `catalog_id`。优先从当前 workspace 获取：

1. **从当前 workspace 获取（优先）:** `cloudrobo workspace current` → 读取 `asset_catalog_id` 字段
2. **备选 — 列出仓库和目录:** 若未配置 workspace 或 `asset_catalog_id` 为空：
   1. `cloudrobo asset list-repositories` → 获取 `repository_id`
   2. `cloudrobo asset list-catalogs --repository-id <repo-id>` → 获取 `catalog_id`

---

### create-asset

```bash
cloudrobo asset create-asset --catalog-id <id> --type <type> [--name <name>] [--sub-type <sub>] [--description <desc>] [--status <status>] [--tags <tags>] [--url <url>] [--ext-metadata <json>] [--parent-asset-version-id <id>] [--generation-method <method>] [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--catalog-id` | 是 | 目录ID |
| `--name` | 否 | 资产名称（image类型可不传） |
| `--type` | 是 | 资产类型 |
| `--sub-type` | 否 | 子类型 |
| `--description` | 否 | 描述 |
| `--status` | 否 | 状态（CREATING/DRAFT/ALPHA/BETA/RELEASE/STABLE/DEPRECATED/ARCHIVE） |
| `--tags` | 否 | 标签列表（逗号分隔） |
| `--url` | 否 | OBS或SWR路径 |
| `--ext-metadata` | 条件必填 | 扩展元数据（JSON字符串），model/dataset/algorithm/image/simulation 类型必填 |
| `--parent-asset-version-id` | 否 | 父资产版本ID |
| `--generation-method` | 否 | 资产生成方法 |
| `--dry-run` | 否 | 仅预览 |

---

### list-assets

```bash
cloudrobo asset list-assets [--repository-id <id>] [--catalog-id <id>] [--type <type>] [--sub-type <sub>] [--ids <ids>] [--name <name>] [--exact-name <name>] [--mine] [--author <uids>] [--tags <tags>] [--tags-operator <op>] [--status <statuses>] [--sort-key <key>] [--sort-dir <dir>] [--offset <n>] [--limit <n>] [--ext-metadata <kv>] [--permissions <perms>] [--actions <actions>] [--actions-operator <op>] [--recommend-score] [--action-status <statuses>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--repository-id` | 否 | 仓库ID（与 `--catalog-id` 至少提供一个，同时提供时 AND 叠加） |
| `--catalog-id` | 否 | 目录ID（与 `--repository-id` 至少提供一个，同时提供时 AND 叠加） |
| `--type` | 否 | 资产类型 |
| `--sub-type` | 否 | 子类型 |
| `--ids` | 否 | 资产ID列表（逗号分隔） |
| `--name` | 否 | 按资产名称模糊查询 |
| `--exact-name` | 否 | 按资产名称精确查询 |
| `--mine` | 否 | 查询我创建的资产（flag） |
| `--author` | 否 | 创建者用户ID列表（逗号分隔） |
| `--tags` | 否 | 按标签查询（逗号分隔） |
| `--tags-operator` | 否 | 多tags筛选规则（and/or） |
| `--status` | 否 | 状态列表（逗号分隔） |
| `--sort-key` | 否 | 排序字段（asset_id/repository_id/catalog_id/name/created_at/updated_at） |
| `--sort-dir` | 否 | 排序方向（asc/desc） |
| `--offset` | 否 | 起始数据偏移量 |
| `--limit` | 否 | 每页返回的资产数量 |
| `--ext-metadata` | 否 | 根据ext_metadata的key=value对检索 |
| `--permissions` | 否 | 要校验的权限列表（逗号分隔） |
| `--actions` | 否 | 根据action列表检索（逗号分隔） |
| `--actions-operator` | 否 | 多actions筛选规则（and/or） |
| `--recommend-score` | 否 | 是否按运营推荐分排序（flag） |
| `--action-status` | 否 | action状态过滤（逗号分隔，ENABLE/DISABLE） |

---

### show-asset

```bash
cloudrobo asset show-asset --asset-id <id>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-id` | 是 | 资产ID |

---

### update-asset

```bash
cloudrobo asset update-asset --asset-id <id> [--name <name>] [--description <desc>] [--status <status>] [--tags <tags>] [--ext-metadata <json>] [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-id` | 是 | 资产ID |
| `--name` | 否 | 新名称 |
| `--description` | 否 | 新描述 |
| `--status` | 否 | 状态（CREATING/DRAFT/ALPHA/BETA/RELEASE/STABLE/DEPRECATED/ARCHIVE） |
| `--tags` | 否 | 标签列表（逗号分隔，全量替换） |
| `--ext-metadata` | 否 | 扩展元数据（JSON字符串） |
| `--dry-run` | 否 | 仅预览 |

**不可变字段:** `catalog_id`、`type`、`sub_type`、`url`、`parent_asset_version_id`、`generation_method` 不可更新，CLI 不暴露为选项。

---

### delete-asset

```bash
cloudrobo asset delete-asset --asset-id <id> [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-id` | 是 | 资产ID |
| `--dry-run` | 否 | 仅预览 |

**警告:** 删除操作不可逆，资产及其所有版本将被永久删除。

---

### batch-delete-assets

```bash
cloudrobo asset batch-delete-assets --asset-ids <id1,id2,...> [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-ids` | 是 | 资产ID列表（逗号分隔） |
| `--dry-run` | 否 | 仅预览 |

---

### create-version

```bash
cloudrobo asset create-version --asset-id <id> [--version <ver>] [--description <desc>] [--status <status>] [--url <url>] [--ext-metadata <json>] [--parent-asset-version-id <id>] [--generation-method <method>] [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-id` | 是 | 资产ID |
| `--version` | 否 | 版本号 |
| `--description` | 否 | 描述 |
| `--status` | 否 | 状态（CREATING/DRAFT/ALPHA/BETA/RELEASE/STABLE/DEPRECATED/ARCHIVE） |
| `--url` | 否 | OBS或SWR路径 |
| `--ext-metadata` | 否 | 扩展元数据（JSON字符串） |
| `--parent-asset-version-id` | 否 | 父资产版本ID |
| `--generation-method` | 否 | 资产生成方法 |
| `--dry-run` | 否 | 仅预览 |

---

### list-versions

```bash
cloudrobo asset list-versions --asset-id <id> [--version <ver>] [--exact-version <ver>] [--limit <n>] [--offset <n>] [--sort-key <key>] [--sort-dir <dir>] [--actions <actions>] [--actions-operator <op>] [--ext-metadata <kv>] [--action-status <statuses>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-id` | 是 | 资产ID |
| `--version` | 否 | 根据版本号模糊查询 |
| `--exact-version` | 否 | 根据版本号精确查询 |
| `--limit` | 否 | 每页返回数量 |
| `--offset` | 否 | 偏移量 |
| `--sort-key` | 否 | 排序字段（created_at/updated_at/version/image_size） |
| `--sort-dir` | 否 | 排序方向（asc/desc） |
| `--actions` | 否 | 根据action列表检索（逗号分隔） |
| `--actions-operator` | 否 | 多actions筛选规则（and/or） |
| `--ext-metadata` | 否 | 根据ext_metadata的key=value对检索 |
| `--action-status` | 否 | action状态过滤（逗号分隔，ENABLE/DISABLE） |

**Note:** `--version` 为模糊查询（如 `1.0` 匹配 `1.0.0`/`1.0.1`），`--exact-version` 为精确匹配。

---

### show-version

```bash
cloudrobo asset show-version --asset-id <id> --version-id <vid>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-id` | 是 | 资产ID |
| `--version-id` | 是 | 版本ID |

---

### update-version

```bash
cloudrobo asset update-version --asset-id <id> --version-id <vid> [--version <ver>] [--description <desc>] [--status <status>] [--ext-metadata <json>] [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-id` | 是 | 资产ID |
| `--version-id` | 是 | 版本ID |
| `--version` | 否 | 版本号 |
| `--description` | 否 | 描述 |
| `--status` | 否 | 状态（CREATING/DRAFT/ALPHA/BETA/RELEASE/STABLE/DEPRECATED/ARCHIVE） |
| `--ext-metadata` | 否 | 扩展元数据（JSON字符串） |
| `--dry-run` | 否 | 仅预览 |

---

### delete-version

```bash
cloudrobo asset delete-version --asset-id <id> --version-id <vid> [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-id` | 是 | 资产ID |
| `--version-id` | 是 | 版本ID |
| `--dry-run` | 否 | 仅预览 |

**警告:** 删除操作不可逆，版本将被永久删除。

---

### batch-delete-versions

```bash
cloudrobo asset batch-delete-versions --asset-id <id> --version-ids <vid1,vid2,...> [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-id` | 是 | 资产ID |
| `--version-ids` | 是 | 版本ID列表（逗号分隔） |
| `--dry-run` | 否 | 仅预览 |

---

### check-permission

```bash
cloudrobo asset check-permission --asset-id <id> --version-id <vid> --permissions <perm1,perm2,...>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-id` | 是 | 资产ID |
| `--version-id` | 是 | 版本ID |
| `--permissions` | 是 | 权限列表（逗号分隔），合法值：meta_read、meta_write、data_read、data_write、data_usable（OpenAPI 定义，服务端校验） |

---

### add-tags

```bash
cloudrobo asset add-tags --asset-id <id> --tags <tag1,tag2,...> [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-id` | 是 | 资产ID |
| `--tags` | 是 | 标签列表（逗号分隔） |
| `--dry-run` | 否 | 仅预览 |

---

### delete-tag

```bash
cloudrobo asset delete-tag --asset-id <id> --tag <tag> [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-id` | 是 | 资产ID |
| `--tag` | 是 | 标签名 |
| `--dry-run` | 否 | 仅预览 |

---

### list-tags

```bash
cloudrobo asset list-tags --language <zh|en> [--type <type>] [--sub-type <sub>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--language` | 是 | 语言(zh/en) |
| `--type` | 否 | 资产类型 |
| `--sub-type` | 否 | 子类型 |

---

### show-lineage

```bash
cloudrobo asset show-lineage --asset-id <id> --version-id <vid> --type <children|parent>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-id` | 是 | 资产ID |
| `--version-id` | 是 | 版本ID |
| `--type` | 是 | 查询方式：children=父查子，parent=子查父 |

---

### list-actions

```bash
cloudrobo asset list-actions --asset-id <id> --version-id <vid>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-id` | 是 | 资产ID |
| `--version-id` | 是 | 版本ID |

---

### create-action

```bash
cloudrobo asset create-action --asset-id <id> --version-id <vid> --action-info <json> [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-id` | 是 | 资产ID |
| `--version-id` | 是 | 版本ID |
| `--action-info` | 是 | Action信息（JSON 格式） |
| `--dry-run` | 否 | 仅预览 |

**action-info 结构**：
```json
{
  "action": "FFT",
  "algorithm": {"asset_id": "<算法资产ID>", "version_id": "<算法版本ID>"},
  "status": "DISABLE",
  "inherited": true
}
```

Action 枚举值：`PRETRAINING`、`FFT`、`LORA`、`ONLINE_DEPLOYMENT`、`LIBERO_SPATIAL`、`LIBERO_OBJECT`、`LIBERO_GOAL`、`LIBERO_10`（OpenAPI 定义，CLI 不做前置校验）

---

### show-action

```bash
cloudrobo asset show-action --asset-id <id> --version-id <vid> --action <name>
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-id` | 是 | 资产ID |
| `--version-id` | 是 | 版本ID |
| `--action` | 是 | Action名称 |

---

### update-action

```bash
cloudrobo asset update-action --asset-id <id> --version-id <vid> --action <name> --action-info <json> [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-id` | 是 | 资产ID |
| `--version-id` | 是 | 版本ID |
| `--action` | 是 | Action名称 |
| `--action-info` | 是 | Action更新信息（JSON 格式） |
| `--dry-run` | 否 | 仅预览 |

**Note:** `action-info` 中 `algorithm` 字段（含 `asset_id` 和 `version_id`）始终必填，即使仅更新 `status` 或 `inherited` 也需传入完整的 `algorithm` 信息。

---

### delete-action

```bash
cloudrobo asset delete-action --asset-id <id> --version-id <vid> --action <name> [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-id` | 是 | 资产ID |
| `--version-id` | 是 | 版本ID |
| `--action` | 是 | Action名称 |
| `--dry-run` | 否 | 仅预览 |

---

### search-assets

```bash
cloudrobo asset search-assets --keyword <keyword> [--type <type>] [--limit <n>] [--offset <n>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--keyword` | 是 | 搜索关键词 |
| `--type` | 否 | 资产类型（simulation/model/dataset） |
| `--limit` | 否 | 返回数量 |
| `--offset` | 否 | 偏移量 |

---

### list-publication-assets

```bash
cloudrobo asset list-publication-assets [--type <type>] [--sub-type <sub>] [--ids <ids>] [--name <name>] [--exact-name <name>] [--tags <tags>] [--tags-operator <op>] [--status <statuses>] [--sort-key <key>] [--sort-dir <dir>] [--offset <n>] [--limit <n>] [--ext-metadata <kv>] [--permissions <perms>] [--actions <actions>] [--actions-operator <op>] [--recommend-score] [--capabilities <caps>] [--action-status <statuses>]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--type` | 否 | 资产类型 |
| `--sub-type` | 否 | 子类型 |
| `--ids` | 否 | 资产ID列表（逗号分隔） |
| `--name` | 否 | 按资产名称模糊查询 |
| `--exact-name` | 否 | 按资产名称精确查询 |
| `--tags` | 否 | 按标签查询（逗号分隔） |
| `--tags-operator` | 否 | 多tags筛选规则（and/or） |
| `--status` | 否 | 状态列表（逗号分隔） |
| `--sort-key` | 否 | 排序字段（asset_id/repository_id/catalog_id/name/created_at/updated_at） |
| `--sort-dir` | 否 | 排序方向（asc/desc） |
| `--offset` | 否 | 起始数据偏移量 |
| `--limit` | 否 | 每页返回的资产数量 |
| `--ext-metadata` | 否 | 根据ext_metadata的key=value对检索 |
| `--permissions` | 否 | 要校验的权限列表（逗号分隔） |
| `--actions` | 否 | 根据action列表检索（逗号分隔） |
| `--actions-operator` | 否 | 多actions筛选规则（and/or） |
| `--recommend-score` | 否 | 是否按运营推荐分排序（flag） |
| `--capabilities` | 否 | 按资产能力过滤（逗号分隔，training/inference/reinforcement_learning） |
| `--action-status` | 否 | action状态过滤（逗号分隔，ENABLE/DISABLE） |

---

### import-asset

```bash
cloudrobo asset import-asset --local-path <path> [--catalog-id <id>] [--name <name>] [--type <type>] [--sub-type <sub>] [--ext-metadata <json>] [--asset-id <id>] [--version-id <vid>] [--overwrite] [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--catalog-id` | 条件必填 | 目录ID，创建新资产时必填，创建新版本时不需要 |
| `--name` | 条件必填 | 资产名称，创建新资产时必填（可从 README.md frontmatter 读取） |
| `--type` | 条件必填 | 资产类型，创建新资产时必填（可从 README.md frontmatter 读取） |
| `--sub-type` | 条件必填 | 子类型，simulation 类型时必填（可从 README.md frontmatter 读取） |
| `--local-path` | 是 | 本地文件夹路径，自动从该路径下README.md读取frontmatter元数据 |
| `--ext-metadata` | 条件必填 | 扩展元数据（JSON字符串），model/dataset/algorithm/image/simulation 类型必填 |
| `--asset-id` | 否 | 资产ID，不传则创建新资产，传则创建新版本 |
| `--version-id` | 否 | 版本ID，与 --asset-id 同时使用时复用已有版本（不创建新版本），用于增量上传/重试失败的上传 |
| `--overwrite` | 否 | 强制覆盖已存在的OBS文件（默认增量上传，仅 --version-id 模式生效） |
| `--dry-run` | 否 | 仅预览 |

**流程**: 读取 README.md frontmatter → 解析参数（frontmatter > CLI）→ 校验 ext_metadata（仅新建资产路径）→ 根据模式执行：

- **Mode 1（无 --asset-id）**: 创建新资产+版本 → 获取 OBS URL → 上传 → 若版本状态为 CREATING 则更新为 DRAFT
- **Mode 2（仅 --asset-id）**: 校验资产存在 → 创建新版本 → 获取 OBS URL → 上传 → 若版本状态为 CREATING 则更新为 DRAFT
- **Mode 3（--asset-id + --version-id）**: 校验资产+版本存在 → 获取 OBS URL → **增量上传**（跳过已存在的文件，仅上传新文件；用 `--overwrite` 强制覆盖全部）→ 若版本状态为 CREATING 则更新为 DRAFT

**状态流转:** 用户未指定 `--status` 时，后端创建版本默认状态为 `CREATING`（表示正在上传）。OBS 上传成功后，若版本状态仍为 `CREATING`，自动更新为 `DRAFT`。若用户通过 frontmatter 或 CLI 显式指定了其他状态（如 `RELEASE`），上传完成后不自动修改。

**错误恢复:** 若 OBS 上传失败（Mode 1/2），资产和版本已创建但文件未上传，版本停留在 `CREATING` 状态。使用 `--asset-id --version-id` 重试上传，避免创建重复版本；上传成功后会自动将 `CREATING` 状态更新为 `DRAFT`。Mode 3 默认增量上传——只补传上次缺失的文件；需要全量重传时加 `--overwrite`。

**增量上传:** Mode 3 默认开启增量上传（`overwrite=False`），对每个本地文件先检查 OBS 上是否已存在，存在则跳过。适用于给已存在版本继续追加新文件、或重试部分失败的上传。`--overwrite` 可强制覆盖已存在文件。

**Frontmatter 字段:** 从 `local-path/README.md` YAML frontmatter 自动读取以下 10 个字段（frontmatter 优先级高于 CLI 参数）：
`name`、`type`、`sub_type`、`description`、`status`、`tags`、`version`、`ext_metadata`、`parent_asset_version_id`、`generation_method`

**注意:** `description`、`status`、`tags`、`version`、`parent_asset_version_id`、`generation_method` 仅通过 frontmatter 设置，CLI 不提供对应选项。`name`、`type`、`sub_type`、`ext_metadata` 同时支持 CLI 选项和 frontmatter。

#### ext_metadata 必填字段规则

创建新资产时，以下类型的 `--ext-metadata` 必须包含对应必填字段（CLI 在调用 API 前前置校验）：

| 类型 | 必填字段 | 枚举/约束 |
|------|----------|-----------|
| `model` | `model_type` | `planning` / `perception` / `vla` / `vln`；`vla`/`vln` 支持可选 `skills`（≤50项,每项含 name[1-64字符,无首尾空格]+prompt[1-1024字符],prompt 不允许重复）和 `strict`（布尔） |
| `dataset` | `annotation_status` | boolean |
| `algorithm` | `engine` (必填 `image_url` 和 `image_source`), `command` | `engine` 中 `image_url` 和 `image_source` 均为必填；`image_source`: `preset` / `custom`; `image_url`: SWR 镜像格式 `swr.{endpoint}/{namespace}/{repo}:{tag}`；可选: `code_dir`(`boot_file`或`preset`时必填), `boot_file`(必须以 `obs://` 开头,以 `.py` 结尾,路径在 `code_dir` 之下), `inputs`(≤10), `outputs`(≤5), `hyperparams`(≤90), `environment_variables`(≤90), `resource`, `yaml_config` |
| `image` | `arch`, `device_type` | `sub_type`: `inference` / `data_processing` / `training` / `notebook` / `rl`；`arch`: `x86_64` / `arm`; `device_type`: `CPU` / `GPU` / `ASCEND` (数组) |
| `simulation` | 无（`sub_type=robot` 时需 `robot_type`, `robot_manufacturer`） | `robot_type`: `humanoid` / `mobile_manipulator` / `robot_arm` / `quadruped_robot` / `wheeled_robot` / `other`; `robot_manufacturer`: 1-64字符,支持中英文/数字/连字符/点/下划线/空格 |

示例：

```bash
# model
--ext-metadata '{"model_type":"planning"}'

# dataset
--ext-metadata '{"annotation_status":true}'

# algorithm
--ext-metadata '{"engine":{"image_url":"swr.cn-southwest-2.myhuaweicloud.com/ns/repo:v1","image_source":"custom"},"command":"python train.py"}'

# image
--ext-metadata '{"arch":"x86_64","device_type":["CPU","GPU"]}'

# simulation (sub_type=robot)
--ext-metadata '{"robot_type":"humanoid","robot_manufacturer":"Unitree"}'
```

---

### export-asset

```bash
cloudrobo asset export-asset --asset-id <id> --local-path <path> [--version-id <vid>] [--dry-run]
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--asset-id` | 是 | 资产ID |
| `--local-path` | 是 | 本地目标路径 |
| `--version-id` | 否 | 版本ID，不指定则导出最新版本 |
| `--dry-run` | 否 | 仅预览 |

**流程**: 查询版本列表 → 解析指定/最新版本 → 提取 OBS URL → 校验 local_path 非文件 → 查询资产/版本详情 → 创建目录 → 下载到 `local_path/<asset_id>/` → 生成 README.md（含 frontmatter 元数据）→ 返回导出结果
