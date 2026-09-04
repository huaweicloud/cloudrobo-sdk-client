# cloudrobo-infer 使用示例

## CLI 示例

### 创建推理服务

```bash
cloudrobo infer create --name chat-api --flavor cpu.2 --model-json '{"model_id":"a9b0c1d2-e3f4-5678-abcd-789012345678","model_version_id":"d8e9f0a1-b2c3-4567-defa-678901234567"}' --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890 --pool-id pool-public --pool-type SHARED
```

### 查询运行中的服务

```bash
cloudrobo infer list --status running
```

### 查询服务详情

```bash
cloudrobo infer show --service-id f8a9b0c1-d2e3-4567-fabc-678901234567
```

### 更新服务描述与模型扩展元数据

```bash
cloudrobo infer update --service-id f8a9b0c1-d2e3-4567-fabc-678901234567 --description "更新后的描述" --model-ext-metadata "{r2c: config}"
```

### 启动/停止服务

```bash
cloudrobo infer stop --service-id f8a9b0c1-d2e3-4567-fabc-678901234567
cloudrobo infer start --service-id f8a9b0c1-d2e3-4567-fabc-678901234567
```

### 删除服务

```bash
cloudrobo infer delete --service-id f8a9b0c1-d2e3-4567-fabc-678901234567
```

### 查询服务日志

```bash
cloudrobo infer list-logs --service-id f8a9b0c1-d2e3-4567-fabc-678901234567 --start-time 1779782400000 --end-time 1779868800000 --keywords error --limit 100
```

### 等待推理服务部署完成

```bash
cloudrobo infer wait-deploy --service-id f8a9b0c1-d2e3-4567-fabc-678901234567 --timeout 600
```

## SDK 示例

```python
from cloudrobo_infer.client import InferClient
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)
client = InferClient(http)

# 创建推理服务
service = client.create_infer_service({
    "name": "chat-api",
    "flavor": "cpu.2",
    "model": {
        "model_id": "a9b0c1d2-e3f4-5678-abcd-789012345678",
        "model_version_id": "d8e9f0a1-b2c3-4567-defa-678901234567"
    },
    "workspace_id": "c1d2e3f4-a5b6-7890-cdef-901234567890",
    "pool_id": "pool-public",
    "pool_type": "SHARED",
})
print(f"Service created: {service['id']}")

# 查询推理服务列表
services = client.list_infer_services(status="running")
for s in services.get("items", []):
    print(s["name"], s["status"])

# 查询推理服务详情
service = client.show_infer_service("f8a9b0c1-d2e3-4567-fabc-678901234567")
print(f"Name: {service['name']}")
print(f"Status: {service['status']}")

# 更新推理服务配置
client.update_infer_service(
    service_id="f8a9b0c1-d2e3-4567-fabc-678901234567",
    req={"description": "更新后的描述"}
)

# 启动/停止推理服务
client.start_infer_service("f8a9b0c1-d2e3-4567-fabc-678901234567")
client.stop_infer_service("f8a9b0c1-d2e3-4567-fabc-678901234567")

# 删除推理服务
client.delete_infer_service("f8a9b0c1-d2e3-4567-fabc-678901234567")

# 查询推理服务日志
logs = client.list_infer_service_logs(
    service_id="f8a9b0c1-d2e3-4567-fabc-678901234567",
    req={"start_time": 1779782400000, "end_time": 1779868800000, "keywords": "error", "limit": 100},
)
print(f"Log count: {logs['count']}")

# 等待推理服务部署完成（默认超时 600 秒，超时抛 RuntimeError）
client.wait_deploy("f8a9b0c1-d2e3-4567-fabc-678901234567", timeout=600)
```
