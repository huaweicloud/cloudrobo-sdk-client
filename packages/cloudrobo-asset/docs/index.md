# cloudrobo-asset

资产管理模块，提供资产仓库查询、目录管理、资产及版本的增删改查、标签管理、Action管理、权限校验、搜索及导入导出等功能。

## 功能特性

- 资产仓库查询
- 目录（Catalog）查询
- 资产的创建、查询、更新、删除、批量删除
- 资产版本的创建、查询、更新、删除、批量删除
- 标签管理
- Action 管理（查询、添加、修改、删除）
- 权限校验
- 血缘关系查询
- 广场资产搜索
- 官方和社区资产列表查询
- 导入资产
- 导出资产

## 安装

```bash
pip install -e packages/cloudrobo-asset
```

## 快速开始

### CLI

```bash
# 列出仓库
cloudrobo asset list-repositories

# 创建资产
cloudrobo asset create-asset --catalog-id b2c3d4e5-f6a7-8901-bcde-f12345678901 --name my-model --type model

# 搜索资产
cloudrobo asset search-assets --keyword robot
```

### SDK

```python
from cloudrobo_asset import AssetClient
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)
client = AssetClient(http)

repos = client.list_repositories()
```

## 文档导航

- [安装与集成](installation.md)
- [CLI 命令详情](commands.md)
- [使用示例](examples.md)
- [开发指南](development.md)
