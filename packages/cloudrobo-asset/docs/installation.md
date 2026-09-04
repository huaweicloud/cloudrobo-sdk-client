# cloudrobo-asset 安装指南

## 系统要求

- Python >= 3.8
- pip >= 21.0

## 前置依赖

cloudrobo-asset 依赖 cloudrobo-core，需先安装核心 SDK：

```bash
pip install -e packages/cloudrobo-core
```

## 安装方式

### 方式一：开发模式安装（推荐）

```bash
pip install -e packages/cloudrobo-asset
```

以开发模式（editable）安装，代码修改即时生效。

### 方式二：安装全部模块

```bash
pip install -r requirements-dev-editable.txt
```

以开发模式安装所有子包，包含 cloudrobo-asset。

### 方式三：正式安装（需已发布到 pip 源）

```bash
pip install cloudrobo-asset
```

## 配置认证

cloudrobo-asset 使用 `cloudrobo-asset-manager` 服务端点，认证配置与 cloudrobo-core 一致。

### 方式一：命令行配置（推荐）

```bash
# AK/SK 自动加密存储
cloudrobo config set ak your-access-key sk your-secret-key
cloudrobo config set region cn-southwest-2
```

### 方式二：环境变量

```bash
export HUAWEI_CLOUD_AK="your-access-key"
export HUAWEI_CLOUD_SK="your-secret-key"
```

### 配置文件说明

`~/.cloudrobo/config.yaml` 中的 AK/SK 以加密形式存储（`ak_enc`/`sk_enc`），**不应手动编辑**。如需修改 AK/SK，请使用上述命令或环境变量。

配置文件适合编辑其他明文配置项，如 `region`、`endpoints`、`proxy` 等：

```yaml
cloudrobo:
  endpoints:
    cloudrobo-asset-manager: "https://cloudrobo-gallery.{region}.myhuaweicloud.com"
  region: "cn-southwest-2"
```

### 服务端点覆盖

如需指定自定义端点，可通过环境变量覆盖：

```bash
export CLOUDROBO_ENDPOINT_CLOUDROBO_ASSET_MANAGER="https://custom-endpoint.example.com"
```

或直接在配置文件中设置 `cloudrobo.endpoints.cloudrobo-asset-manager`。

## CLI 使用

安装后即可通过 `cloudrobo asset` 命令操作：

```bash
# 查看帮助
cloudrobo asset --help

# 仓库与目录
cloudrobo asset list-repositories
cloudrobo asset list-catalogs --repository-id <repo-id>
cloudrobo asset show-catalog --catalog-id <catalog-id>

# 资产管理
cloudrobo asset create-asset --catalog-id <catalog-id> --name my-model --type model --ext-metadata '{"model_type":"planning"}'
cloudrobo asset list-assets --catalog-id <catalog-id>
cloudrobo asset show-asset --asset-id <asset-id>
cloudrobo asset update-asset --asset-id <asset-id> --name new-name --description "updated"
cloudrobo asset delete-asset --asset-id <asset-id>
cloudrobo asset batch-delete-assets --asset-ids "id1,id2,id3"

# 版本管理
cloudrobo asset create-version --asset-id <asset-id> --version 1.0.0
cloudrobo asset list-versions --asset-id <asset-id>
cloudrobo asset update-version --asset-id <asset-id> --version-id <version-id> --description "updated"
cloudrobo asset delete-version --asset-id <asset-id> --version-id <version-id>
cloudrobo asset batch-delete-versions --asset-id <asset-id> --version-ids "vid1,vid2"

# 标签与权限
cloudrobo asset add-tags --asset-id <asset-id> --tags "tag1,tag2"
cloudrobo asset delete-tag --asset-id <asset-id> --tag <tag>
cloudrobo asset list-tags --language <zh/en>
cloudrobo asset check-permission --asset-id <asset-id> --version-id <version-id> --permissions "meta_read,meta_write"

# 血缘关系
cloudrobo asset show-lineage --asset-id <asset-id> --version-id <version-id> --type children

# Action 管理
cloudrobo asset list-actions --asset-id <asset-id> --version-id <version-id>
cloudrobo asset create-action --asset-id <asset-id> --version-id <version-id> --action-info '{"action":"FFT","algorithm":{"asset_id":"<算法资产ID>","version_id":"<算法版本ID>"}}'
cloudrobo asset show-action --asset-id <asset-id> --version-id <version-id> --action FFT
cloudrobo asset update-action --asset-id <asset-id> --version-id <version-id> --action FFT --action-info '{"algorithm":{"asset_id":"<id>","version_id":"<id>"}}'
cloudrobo asset delete-action --asset-id <asset-id> --version-id <version-id> --action FFT

# 搜索与广场
cloudrobo asset search-assets --keyword robot
cloudrobo asset list-publication-assets --type model

# 导入导出
cloudrobo asset import-asset --catalog-id <catalog-id> --name my-model --type model --ext-metadata '{"model_type":"planning"}' --local-path ./my-model
cloudrobo asset export-asset --asset-id <asset-id> --local-path ./download
```

## Skill 对接 Agent 方式

cloudrobo-asset 提供了 AI Agent Skill，让具备 CLI 执行能力的 Agent（如 Claude Code、Cursor 等）通过自然语言驱动完整的资产管理流程。

### 安装 Skill 到 Agent 平台

```bash
# 获取 skill 仓库
git clone <cloudrobo-skills-url> ~/cloudrobo-skills

# 安装到 Claude Code
cloudrobo skill install --source ~/cloudrobo-skills/skills --target claude-code

# 安装指定 skill
cloudrobo skill install --source ~/cloudrobo-skills/skills --target claude-code --skill-name huawei-cloud-cloudrobo-asset
```

### Skill 工作流程

Agent 安装 Skill 后，用户只需自然语言描述需求，Agent 会自动：

1. **查询仓库与目录** — 根据用户描述定位目标仓库和目录
2. **创建资产** — 自动填充 catalog_id、name、type 等参数
3. **创建资产版本** — 创建版本、上传内容
4. **搜索资产** — 在广场中按关键词搜索可用资产
5. **导入导出** — 将本地资产上传到 OBS 或从 OBS 下载到本地
6. **权限与标签** — 校验访问权限、管理资产标签

### Skill 优先使用 CLI

Skill 设计为 CLI-first：优先执行 `cloudrobo asset` 命令，用户可直接复现。

## 典型应用场景

### 场景一：导入本地模型资产

将本地训练好的模型文件导入到资产仓库。

```bash
# 1. 查询仓库和目录
cloudrobo asset list-repositories
cloudrobo asset list-catalogs --repository-id <repo-id>

# 2. 导入资产
cloudrobo asset import-asset \
  --catalog-id <catalog-id> \
  --name my-robot-model \
  --type model \
  --ext-metadata '{"model_type":"planning"}' \
  --local-path ./my-model

# 3. 查看导入结果
cloudrobo asset show-asset --asset-id <asset-id>
```

**Agent 自然语言触发**：`帮我把 ./my-model 目录下的模型导入到资产仓库`

### 场景二：搜索广场资产

从广场搜索所需资产。

```bash
# 1. 搜索资产
cloudrobo asset search-assets --keyword "VLA模型"

# 2. 查看资产详情
cloudrobo asset show-asset --asset-id <asset-id>
```

**Agent 自然语言触发**：`搜索一下具身广场上VLA模型资产`

### 场景三：创建资产版本

为已有资产创建新版本。

```bash
# 1. 查看资产当前版本
cloudrobo asset list-versions --asset-id <asset-id>

# 2. 为已有资产创建新版本并上传内容
cloudrobo asset import-asset \
  --asset-id <asset-id> \
  --local-path ./my-model-v2

# 3. 更新资产状态
cloudrobo asset update-asset --asset-id <asset-id> --description "v2.0.0 released"
```

**Agent 自然语言触发**：`给 my-model 资产创建新版本 2.0.0`


## 验证安装

### CLI 验证

```bash
cloudrobo asset --help
cloudrobo asset list-repositories
```

### SDK 验证

```python
from cloudrobo_asset import AssetClient
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)
client = AssetClient(http)

repos = client.list_repositories()
print(repos)
```

## 依赖说明

| 依赖 | 版本要求 | 说明 |
|------|----------|------|
| cloudrobo-core | >= 0.1.0 | 核心 SDK，提供 Config、HttpClient、BaseClient |
| click | >= 8.0 | CLI 框架 |

## 常见问题

### 找不到 cloudrobo asset 命令

确保 cloudrobo-core 和 cloudrobo-asset 均已安装，且 pip 安装的 bin 目录在 PATH 中。

### 认证失败

检查 `HUAWEI_CLOUD_AK` / `HUAWEI_CLOUD_SK` 环境变量或 `~/.cloudrobo/config.yaml` 中的 AK/SK 配置是否正确。

### 连接超时

确认 `region` 配置正确，默认端点格式为 `https://cloudrobo-gallery.{region}.myhuaweicloud.com`。如需代理，配置 `cloudrobo.proxy` 相关字段或设置 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量。
