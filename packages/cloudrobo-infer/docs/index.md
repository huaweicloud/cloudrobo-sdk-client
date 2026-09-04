# CloudRobo Infer

推理服务模块，提供推理服务创建、查询、启停等功能。

## CLI 命令

```bash
# 查看帮助
cloudrobo infer --help

# 创建推理服务
cloudrobo infer create --name <名称> --flavor <规格> --model-json '{"model_id":"<模型ID>","model_version_id":"<版本ID>"}' --workspace-id <工作空间ID> --pool-id <资源池ID> --pool-type <资源池类型>

# 查询推理服务列表
cloudrobo infer list [--name <名称>] [--status <状态>]

# 查询推理服务详情
cloudrobo infer show --service-id <服务ID>

# 更新推理服务配置
cloudrobo infer update --service-id <服务ID> [--description <描述>] [--model-ext-metadata <模型扩展元数据>]

# 删除推理服务
cloudrobo infer delete --service-id <服务ID>

# 启动推理服务
cloudrobo infer start --service-id <服务ID>

# 停止推理服务
cloudrobo infer stop --service-id <服务ID>

# 查询推理服务日志
cloudrobo infer list-logs --service-id <服务ID> --start-time <起始时间> --end-time <结束时间>

# 等待推理服务部署完成
cloudrobo infer wait-deploy --service-id <服务ID> [--timeout <超时秒数>]
```

## SDK 使用

```python
from cloudrobo_infer import InferClient
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)
client = InferClient(http)

# 创建推理服务
result = client.create_infer_service({
    "name": "infer-service-1",
    "flavor": "cpu.2",
    "model": {
        "model_id": "a9b0c1d2-e3f4-5678-abcd-789012345678",
        "model_version_id": "d8e9f0a1-b2c3-4567-defa-678901234567"
    },
    "workspace_id": "c1d2e3f4-a5b6-7890-cdef-901234567890",
    "pool_id": "pool-public",
    "pool_type": "SHARED",
})

# 查询推理服务列表
result = client.list_infer_services(name="infer-service-1")

# 查询推理服务详情
result = client.show_infer_service("f8a9b0c1-d2e3-4567-fabc-678901234567")

# 更新推理服务配置
result = client.update_infer_service("f8a9b0c1-d2e3-4567-fabc-678901234567", {"description": "更新后的描述"})

# 删除推理服务
client.delete_infer_service("f8a9b0c1-d2e3-4567-fabc-678901234567")

# 启动推理服务
result = client.start_infer_service("f8a9b0c1-d2e3-4567-fabc-678901234567")

# 停止推理服务
result = client.stop_infer_service("f8a9b0c1-d2e3-4567-fabc-678901234567")

# 查询推理服务日志
result = client.list_infer_service_logs(
    "f8a9b0c1-d2e3-4567-fabc-678901234567",
    {"start_time": 1779782400000, "end_time": 1779868800000, "keywords": "error"},
)

# 等待推理服务部署完成（默认超时 600 秒，超时抛 RuntimeError）
result = client.wait_deploy("f8a9b0c1-d2e3-4567-fabc-678901234567", timeout=600)
```
