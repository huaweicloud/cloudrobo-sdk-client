# cloudrobo-asset 使用示例

## CLI 示例

### 仓库与目录

```bash
# 列出所有仓库
cloudrobo asset list-repositories

# 按名称模糊查询仓库
cloudrobo asset list-repositories --name my-repo

# 列出目录
cloudrobo asset list-catalogs --repository-id a1b2c3d4-e5f6-7890-abcd-ef1234567890

# 查看目录详情
cloudrobo asset show-catalog --catalog-id b2c3d4e5-f6a7-8901-bcde-f12345678901
```

### 资产操作

```bash
# 创建资产
cloudrobo asset create-asset --catalog-id b2c3d4e5-f6a7-8901-bcde-f12345678901 --name base-model --type model --ext-metadata '{"model_type":"planning"}'

# 列出资产
cloudrobo asset list-assets --repository-id a1b2c3d4-e5f6-7890-abcd-ef1234567890

# 按类型过滤
cloudrobo asset list-assets --repository-id a1b2c3d4-e5f6-7890-abcd-ef1234567890 --type model

# 查看资产详情
cloudrobo asset show-asset --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012

# 更新资产
cloudrobo asset update-asset --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --name new-name

# 删除资产
cloudrobo asset delete-asset --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012

# 批量删除资产
cloudrobo asset batch-delete-assets --asset-ids "id1,id2,id3"
```

### 版本管理

```bash
# 创建版本
cloudrobo asset create-version --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --version 1.0.0

# 查询版本列表
cloudrobo asset list-versions --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012

# 按版本号模糊查询
cloudrobo asset list-versions --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --version "1.0"

# 查看版本详情
cloudrobo asset show-version --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --version-id d4e5f6a7-b8c9-0123-defa-234567890123

# 更新版本
cloudrobo asset update-version --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --version-id d4e5f6a7-b8c9-0123-defa-234567890123 --description "updated"

# 删除版本
cloudrobo asset delete-version --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --version-id d4e5f6a7-b8c9-0123-defa-234567890123

# 批量删除版本
cloudrobo asset batch-delete-versions --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --version-ids "vid1,vid2"
```

### 标签与血缘

```bash
# 添加标签
cloudrobo asset add-tags --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --tags "production,stable"

# 删除标签
cloudrobo asset delete-tag --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --tag production

# 查询预定义标签
cloudrobo asset list-tags --language zh
cloudrobo asset list-tags --language en --type model

# 查看血缘
cloudrobo asset show-lineage --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --version-id d4e5f6a7-b8c9-0123-defa-234567890123 --type children
```

### Action 管理

```bash
# 查询action列表
cloudrobo asset list-actions --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --version-id d4e5f6a7-b8c9-0123-defa-234567890123

# 添加action
cloudrobo asset create-action --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --version-id d4e5f6a7-b8c9-0123-defa-234567890123 --action-info '{"action":"FFT","algorithm":{"asset_id":"xxx","version_id":"yyy"}}'

# 查看action详情
cloudrobo asset show-action --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --version-id d4e5f6a7-b8c9-0123-defa-234567890123 --action FFT

# 修改action
cloudrobo asset update-action --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --version-id d4e5f6a7-b8c9-0123-defa-234567890123 --action FFT --action-info '{"algorithm":{"asset_id":"xxx","version_id":"yyy"},"status":"ENABLE"}'

# 删除action
cloudrobo asset delete-action --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --version-id d4e5f6a7-b8c9-0123-defa-234567890123 --action FFT
```

### 权限校验

```bash
# 校验资产权限
cloudrobo asset check-permission --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --version-id d4e5f6a7-b8c9-0123-defa-234567890123 --permissions "meta_read,meta_write"
```

### 搜索与广场

```bash
# 搜索广场资产
cloudrobo asset search-assets --keyword robot --limit 10

# 查询官方和社区资产
cloudrobo asset list-publication-assets --type model --status RELEASE

# 按资产能力过滤
cloudrobo asset list-publication-assets --type model --capabilities training,inference
```

### 导入导出

```bash
# 导入资产（创建新资产，type=model 需提供 ext-metadata）
cloudrobo asset import-asset --catalog-id b2c3d4e5-f6a7-8901-bcde-f12345678901 --name my-model --type model --ext-metadata '{"model_type":"planning"}' --local-path ./my-model-dir

# 导入资产（从 local-path/README.md frontmatter 读取元数据，仅需 --catalog-id）
cloudrobo asset import-asset --catalog-id b2c3d4e5-f6a7-8901-bcde-f12345678901 --local-path ./my-model-dir

# 为已有资产创建新版本并上传
cloudrobo asset import-asset --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --local-path ./my-model-v2

# 复用已有版本，增量上传新文件（默认跳过已存在的文件，用于重试失败的上传）
cloudrobo asset import-asset --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --version-id d4e5f6a7-b8c9-0123-defa-234567890123 --local-path ./my-model-v2

# 复用已有版本，强制覆盖所有文件重新上传
cloudrobo asset import-asset --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --version-id d4e5f6a7-b8c9-0123-defa-234567890123 --local-path ./my-model-v2 --overwrite

# 导出资产
cloudrobo asset export-asset --asset-id c3d4e5f6-a7b8-9012-cdef-123456789012 --local-path ./download-dir
```

## SDK 示例

### 基本操作

```python
from cloudrobo_asset import AssetClient
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)
client = AssetClient(http)

# 列出仓库
repos = client.list_repositories()

# 按名称模糊查询
repos = client.list_repositories(name="my-repo", limit=20)

# 列出目录
catalogs = client.list_catalogs(repository_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890")

# 查看目录详情
catalog = client.show_catalog("b2c3d4e5-f6a7-8901-bcde-f12345678901")

# 创建资产
asset = client.create_asset({
    "catalog_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
    "name": "base-model",
    "type": "model",
    "ext_metadata": {"model_type": "planning"}
})
```

### 资产管理

```python
# 列出资产（带过滤条件）
assets = client.list_assets(repository_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890", type="model")

# 查看资产详情
detail = client.show_asset("c3d4e5f6-a7b8-9012-cdef-123456789012")

# 更新资产
client.update_asset("c3d4e5f6-a7b8-9012-cdef-123456789012", {"name": "new-name", "description": "updated"})

# 删除资产
client.delete_asset("c3d4e5f6-a7b8-9012-cdef-123456789012")

# 批量删除资产
client.batch_delete_assets({"asset_ids": ["id1", "id2", "id3"]})
```

### 版本管理

```python
# 创建资产版本
version = client.create_asset_version("c3d4e5f6-a7b8-9012-cdef-123456789012", {"version": "1.0.0"})

# 列出资产版本
versions = client.list_asset_versions("c3d4e5f6-a7b8-9012-cdef-123456789012")

# 查看版本详情
ver = client.show_asset_version("c3d4e5f6-a7b8-9012-cdef-123456789012", "d4e5f6a7-b8c9-0123-defa-234567890123")

# 更新版本
client.update_asset_version("c3d4e5f6-a7b8-9012-cdef-123456789012", "d4e5f6a7-b8c9-0123-defa-234567890123", {"description": "updated"})

# 删除版本
client.delete_asset_version("c3d4e5f6-a7b8-9012-cdef-123456789012", "d4e5f6a7-b8c9-0123-defa-234567890123")

# 批量删除版本
client.batch_delete_asset_versions("c3d4e5f6-a7b8-9012-cdef-123456789012", {"version_ids": ["vid1", "vid2"]})
```

### 标签与血缘

```python
# 添加标签
client.add_tags("c3d4e5f6-a7b8-9012-cdef-123456789012", ["production", "stable"])

# 删除标签
client.delete_tag("c3d4e5f6-a7b8-9012-cdef-123456789012", "production")

# 查询预定义标签
tags = client.list_all_tags(language="zh")
tags = client.list_all_tags(language="en", type="model")

# 查看血缘
tree = client.show_asset_tree("c3d4e5f6-a7b8-9012-cdef-123456789012", "d4e5f6a7-b8c9-0123-defa-234567890123", query_type="children")
```

### Action 管理

```python
asset_id = "c3d4e5f6-a7b8-9012-cdef-123456789012"
version_id = "d4e5f6a7-b8c9-0123-defa-234567890123"

# 查询action列表
actions = client.list_asset_actions(asset_id, version_id)

# 添加action
client.create_asset_action(asset_id, version_id, {"action": "FFT", "algorithm": {"asset_id": "xxx", "version_id": "yyy"}})

# 查看action详情
action = client.show_asset_action(asset_id, version_id, "FFT")

# 修改action
client.update_asset_action(asset_id, version_id, "FFT", {"algorithm": {"asset_id": "xxx", "version_id": "yyy"}, "status": "ENABLE"})

# 删除action
client.delete_asset_action(asset_id, version_id, "FFT")
```

### 权限校验

```python
# 校验资产权限
result = client.check_asset_permission(
    "c3d4e5f6-a7b8-9012-cdef-123456789012",
    "d4e5f6a7-b8c9-0123-defa-234567890123",
    {"permissions": ["meta_read", "meta_write"]}
)
```

### 搜索与广场

```python
# 搜索广场资产
results = client.search_assets({"keyword": "robot", "limit": 10})

# 查询官方和社区资产
assets = client.list_publication_assets(type="model", status=["RELEASE"])

# 按资产能力过滤
assets = client.list_publication_assets(type="model", capabilities=["training", "inference"])
```

### 导入导出

```python
# 导入资产（注册 + 上传OBS）
result = client.import_asset(
    catalog_id="b2c3d4e5-f6a7-8901-bcde-f12345678901",
    name="my-model",
    asset_type="model",
    ext_metadata={"model_type": "planning"},
    local_path="./my-model-dir"
)

# 为已有资产创建新版本（仅需 asset_id + local_path）
result = client.import_asset(
    asset_id="c3d4e5f6-a7b8-9012-cdef-123456789012",
    local_path="./my-model-v2"
)

# 导出资产（下载OBS到本地，默认最新版本）
result = client.export_asset(
    asset_id="c3d4e5f6-a7b8-9012-cdef-123456789012",
    local_path="./download-dir"
)

# 导出指定版本
result = client.export_asset(
    asset_id="c3d4e5f6-a7b8-9012-cdef-123456789012",
    version_id="d4e5f6a7-b8c9-0123-defa-234567890123",
    local_path="./download-dir"
)

# 返回值结构：
# {
#     "asset_id": "...",
#     "version_id": "...",
#     "obs_url": "obs://bucket/path",
#     "local_path": "./download-dir/<asset_id>",
#     "readme_path": "./download-dir/<asset_id>/README.md",
#     "metadata": {name, type, ext_metadata, ...},
#     "status": "exported"
# }
```

### 导入导出高级参数

```python
# 导入资产 — 自定义分片大小和断点续传
result = client.import_asset(
    catalog_id="b2c3d4e5-f6a7-8901-bcde-f12345678901",
    name="my-model",
    asset_type="model",
    ext_metadata={"model_type": "planning"},
    local_path="./my-model-dir",
    part_size=9437184,        # 默认 9MB (9437184 字节)
    enable_checkpoint=True     # 默认 True, 断点续传
)

# 导出资产 — 自定义分片大小和断点续传
result = client.export_asset(
    asset_id="c3d4e5f6-a7b8-9012-cdef-123456789012",
    local_path="./download-dir",
    part_size=5242880,         # 默认 5MB (5242880 字节)
    enable_checkpoint=True      # 默认 True, 断点续传
)
```

### 错误处理

```python
from cloudrobo_core.sdk.exceptions import ResourceNotFoundError, ResourceConflictError
from cloudrobo_asset.validators import ValidationError

# 资产不存在
try:
    asset = client.show_asset("non-existent-id")
except ResourceNotFoundError as e:
    print(f"资产不存在: {e}")

# 资产名冲突（同一目录下重名）
try:
    client.create_asset({"catalog_id": "...", "name": "existing", "type": "model",
                         "ext_metadata": {"model_type": "planning"}})
except ResourceConflictError as e:
    print(f"资源冲突: {e}")

# ext_metadata 校验错误（CLI 前置校验，不会发起 API 请求）
try:
    client.create_asset({"catalog_id": "...", "name": "test", "type": "model"})  # 缺少 ext_metadata
except ValidationError as e:
    print(f"参数校验失败: {e}")

# 导入路径不存在
try:
    client.import_asset(catalog_id="...", name="test", asset_type="model",
                        ext_metadata={"model_type": "planning"},
                        local_path="/nonexistent/path")
except FileNotFoundError as e:
    print(f"路径不存在: {e}")
except Exception as e:
    print(f"操作失败: {e}")
```

## API YAML

OpenAPI 定义位于 `src/cloudrobo_asset/api/asset-manager-openapi.yaml`。
