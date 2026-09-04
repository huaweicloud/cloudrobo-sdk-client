# cloudrobo-workspace 使用示例

## CLI 示例

### 创建并切换工作空间

```bash
# 创建工作空间（含完整参数）
cloudrobo workspace create --name dev --default-obs-path obs://bucket/dev --description "开发环境" --tags "internal,dev"

# 创建工作空间并指定成员
cloudrobo workspace create --name team --default-obs-path obs://bucket/team --member-list '[{"user_id":"<user-id>","role_ids":["<role-id>"]}]'

# 切换到开发环境
cloudrobo workspace use --workspace-id d2e3f4a5-b6c7-8901-defa-012345678901

# 查看当前工作空间
cloudrobo workspace current
```

### 列出工作空间（分页）

```bash
# 列出所有
cloudrobo workspace list

# 分页查询
cloudrobo workspace list --limit 10 --offset 0
```

### 查看和更新工作空间

```bash
# 查看详情
cloudrobo workspace show --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890

# 更新名称
cloudrobo workspace update --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890 --name new-name

# 更新描述和标签
cloudrobo workspace update --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890 --description "新描述" --tags "tag1,tag2"

# 更新责任人
cloudrobo workspace update --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890 --owner-id <user-id>
```

### 管理工作空间成员

```bash
# 列出成员
cloudrobo workspace list-members --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890

# 添加成员（JSON格式）
cloudrobo workspace add-members --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890 --member-list '[{"user_id":"<user-id>","role_ids":["<role-id>"]}]'

# 删除成员
cloudrobo workspace delete-members --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890 --user-ids "user1,user2"
```

### 查看概览统计

```bash
cloudrobo workspace overview
```

### 删除工作空间

```bash
cloudrobo workspace delete --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890
```

### Dry-run 模式

```bash
cloudrobo workspace create --name test --default-obs-path obs://bucket/test --dry-run
cloudrobo workspace update --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890 --name test --dry-run
cloudrobo workspace delete --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890 --dry-run
```

## SDK 示例

### 基本操作

```python
from cloudrobo_workspace.client import WorkspaceClient
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)
client = WorkspaceClient(http)

# 创建工作空间（含完整参数）
dev_ws = client.create_workspace({
    "name": "dev",
    "default_obs_path": "obs://bucket/dev",
    "description": "开发环境",
    "tags": ["internal", "dev"],
})

# 列出工作空间（含分页）
workspaces = client.list_workspaces(limit=10, offset=0)

# 查看工作空间详情
ws = client.show_workspace("c1d2e3f4-a5b6-7890-cdef-901234567890")

# 更新工作空间
client.update_workspace("c1d2e3f4-a5b6-7890-cdef-901234567890", {
    "name": "new-name",
    "description": "新描述",
    "tags": ["tag1", "tag2"],
    "owner_id": "<user-id>",
})

# 删除工作空间
client.delete_workspace("c1d2e3f4-a5b6-7890-cdef-901234567890")
```

### 成员管理

```python
# 列出成员
members = client.list_workspace_members("c1d2e3f4-a5b6-7890-cdef-901234567890")

# 添加成员
client.add_workspace_members("c1d2e3f4-a5b6-7890-cdef-901234567890", {
    "member_list": [
        {"user_id": "<user-id>", "role_ids": ["<role-id>"]}
    ]
})

# 更新成员角色
client.update_workspace_member("c1d2e3f4-a5b6-7890-cdef-901234567890", {
    "user_id": "user1",
    "role_ids": ["<role-id>"],
})

# 删除成员
client.delete_workspace_members("c1d2e3f4-a5b6-7890-cdef-901234567890", ["user1", "user2"])
```

### 概览统计

```python
overview = client.get_workspace_overview()
print(f"工作空间容量: {overview['workspace_capacity']}")
print(f"已使用: {overview['workspace_used']}")
print(f"可用: {overview['workspace_available']}")
print(f"成员数: {overview['member_count']}/{overview['member_capacity']}")
```

### 工作空间配置管理

```python
from cloudrobo_workspace.config import load_workspace, save_workspace

# 保存工作空间配置
save_workspace({
    "workspace_id": "c1d2e3f4-a5b6-7890-cdef-901234567890",
    "name": "production",
    "asset_catalog_id": "<catalog-id>",
    "default_obs_path": "obs://bucket/prod",
})

# 读取当前工作空间配置
ws = load_workspace()
print(ws["workspace_id"])
```
