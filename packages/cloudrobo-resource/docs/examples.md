# cloudrobo-resource 使用示例

## CLI 示例

### 查询配额列表

```bash
# 查询所有配额
cloudrobo resource list-quotas

# 按资源类型过滤
cloudrobo resource list-quotas --resource-type CCE

# 按工作空间和资源池类型过滤
cloudrobo resource list-quotas --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890 --pool-type DEDICATED

# 分页查询
cloudrobo resource list-quotas --limit 20 --offset 0 --order ASC
```

### 查询资源池列表

```bash
# 查询所有资源池
cloudrobo resource list-pools

# 按资源类型过滤
cloudrobo resource list-pools --resource-type MODELARTS --resource-sub-type STANDARD

# 按用途过滤
cloudrobo resource list-pools --usages "TRAINING,INFERENCE"

# 按资源池类型过滤并分页
cloudrobo resource list-pools --pool-type SHARED --limit 10 --offset 20
```

### 查询资源池详情

```bash
cloudrobo resource show-pool --pool-id a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

## SDK 示例

### 基本用法

```python
from cloudrobo_resource.client import ResourceClient
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)
client = ResourceClient(http)

# 查询所有配额
quotas = client.list_quotas()

# 查询所有资源池
pools = client.list_pools()

# 查询资源池详情
pool = client.show_pool("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
```

### 带过滤参数查询

```python
# 按资源类型和资源池类型查询配额
quotas = client.list_quotas(
    resource_type="CCE",
    pool_type="DEDICATED",
    workspace_id="c1d2e3f4-a5b6-7890-cdef-901234567890",
)

# 按资源子类型和用途查询资源池
pools = client.list_pools(
    resource_type="MODELARTS",
    resource_sub_type="STANDARD",
    usages=["TRAINING", "INFERENCE"],
)
```

### 分页查询

```python
# 配额分页查询
quotas_page1 = client.list_quotas(limit=10, offset=0, order="DESC")
quotas_page2 = client.list_quotas(limit=10, offset=10, order="DESC")

# 资源池分页查询
pools_page1 = client.list_pools(limit=20, offset=0)
```
