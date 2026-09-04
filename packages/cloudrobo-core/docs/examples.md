# cloudrobo-core 使用示例

## SDK 基础用法

### 配置管理

```python
from cloudrobo_core.sdk import Config

# 从默认位置加载配置 (~/.cloudrobo/config.yaml)
config = Config()

# 从指定路径加载配置
config = Config("/path/to/custom/config.yaml")

# 访问配置属性
print(config.ak)
print(config.sk)
print(config.region)
print(config.get_endpoint("asset-manager"))

# 代理配置
print(config.http_proxy)
print(config.https_proxy)
print(config.no_proxy)

# SSL 配置
print(config.verify_ssl)
print(config.ca_bundle)
```

### HTTP 客户端

```python
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)

# GET 请求
result = http.get("/api/d8e9f0a1-b2c3-4567-defa-678901234567/repositories")

# POST 请求
result = http.post("/api/d8e9f0a1-b2c3-4567-defa-678901234567/repositories", json={"name": "my-repo"})

# PUT 请求
result = http.put("/api/d8e9f0a1-b2c3-4567-defa-678901234567/repositories/123", json={"name": "updated"})

# PATCH 请求
result = http.patch("/api/d8e9f0a1-b2c3-4567-defa-678901234567/repositories/123", json={"name": "patched"})

# DELETE 请求
result = http.delete("/api/d8e9f0a1-b2c3-4567-defa-678901234567/repositories/123")

# 自定义请求头
result = http.get("/api/d8e9f0a1-b2c3-4567-defa-678901234567/resource", headers={"X-Custom": "value"})

# 分页迭代
for item in http.paginate("/api/d8e9f0a1-b2c3-4567-defa-678901234567/repositories"):
    print(item)

# 使用上下文管理器
with HttpClient(config) as http:
    result = http.get("/api/d8e9f0a1-b2c3-4567-defa-678901234567/repositories")
```

### 创建自定义客户端

```python
from cloudrobo_core.sdk import BaseClient, HttpClient, Config

class MyServiceClient(BaseClient):
    """自定义服务客户端"""
    SERVICE = "my-service"

    def list_items(self):
        return self._client.get(self._url("/items"))

    def create_item(self, data):
        return self._client.post(self._url("/items"), json=data)

    def get_item(self, item_id):
        return self._client.get(self._url(f"/items/{item_id}"))

    def update_item(self, item_id, data):
        return self._client.put(self._url(f"/items/{item_id}"), json=data)

    def delete_item(self, item_id):
        return self._client.delete(self._url(f"/items/{item_id}"))

# 使用
config = Config()
http = HttpClient(config)
client = MyServiceClient(http)
items = client.list_items()
```

## 环境变量配置

```bash
# 设置认证信息
export HUAWEI_CLOUD_AK="your-access-key"
export HUAWEI_CLOUD_SK="your-secret-key"

# 设置代理
export HTTPS_PROXY="http://proxy.example.com:8080"

# 运行命令
cloudrobo workspace list
```

## 配置文件说明

`~/.cloudrobo/config.yaml` 中的 AK/SK 以加密形式存储（`ak_enc`/`sk_enc`），**不应手动编辑**。

如需修改 AK/SK，请使用：
- `cloudrobo config set ak <your-ak> sk <your-sk>` 命令（推荐）
- 环境变量 `HUAWEI_CLOUD_AK` / `HUAWEI_CLOUD_SK`

配置文件适合编辑其他明文配置项，如 `region`、`proxy` 等：

```yaml
cloudrobo:
  region: "cn-southwest-2"
  proxy:
    http: "http://proxy.example.com:8080"
    https: "http://proxy.example.com:8080"
```

## 错误处理

```python
from cloudrobo_core.sdk import Config, HttpClient
from cloudrobo_core.sdk.exceptions import (
    AuthenticationError,
    ResourceNotFoundError,
    ResourceConflictError,
    RateLimitError,
    ServiceError,
)

config = Config()
http = HttpClient(config)

try:
    result = http.post("/api/d8e9f0a1-b2c3-4567-defa-678901234567/resource", json={"name": "test"})
except AuthenticationError as e:
    print(f"认证失败: {e}")
except ResourceNotFoundError as e:
    print(f"资源不存在: {e}")
except ResourceConflictError as e:
    print(f"资源冲突: {e}")
except RateLimitError as e:
    print(f"请求频率超限: {e}")
except ServiceError as e:
    print(f"服务错误 (status={e.status_code}): {e}")
except Exception as e:
    print(f"未知错误: {e}")
```
