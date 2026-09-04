# cloudrobo-resource

资源管理模块，提供资源配额查询和资源池查询功能。

## 功能特性

- 查询配额列表（支持工作空间、资源类型、资源池类型等过滤）
- 查询资源池列表（支持资源类型、用途等过滤）
- 查询资源池详情

## 安装

```bash
pip install -e packages/cloudrobo-resource
```

## 快速开始

### CLI

```bash
cloudrobo resource list-quotas
cloudrobo resource list-pools --resource-type CCE
cloudrobo resource show-pool --pool-id a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

### SDK

```python
from cloudrobo_resource.client import ResourceClient
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)
client = ResourceClient(http)

quotas = client.list_quotas()
pools = client.list_pools(resource_type="CCE")
pool = client.show_pool("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
```

## 文档导航

- [CLI 命令详情](commands.md)
- [使用示例](examples.md)
- [开发指南](development.md)
