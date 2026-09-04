# cloudrobo-workspace

工作空间模块，提供工作空间的创建、查询、更新、删除、成员管理、概览统计及切换等功能。

## 功能特性

- 创建和管理工作空间（含描述、标签、成员列表）
- 查看和更新工作空间详情
- 管理工作空间成员（添加、列出、更新角色、删除）
- 查询工作空间概览统计（容量、使用量、成员数）
- 切换当前工作空间
- 查看当前工作空间配置
- 隔离项目资源
- 多环境支持

## 安装

```bash
pip install -e packages/cloudrobo-workspace
```

## 快速开始

### CLI

```bash
cloudrobo workspace create --name project-a --default-obs-path obs://bucket/project-a
cloudrobo workspace use --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890
cloudrobo workspace current
cloudrobo workspace overview
```

### SDK

```python
from cloudrobo_workspace.client import WorkspaceClient
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)
client = WorkspaceClient(http)

workspace = client.create_workspace({
    "name": "project-a",
    "default_obs_path": "obs://bucket/project-a",
})
overview = client.get_workspace_overview()
```

## 文档导航

- [CLI 命令详情](commands.md)
- [使用示例](examples.md)
- [开发指南](development.md)
