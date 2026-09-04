# cloudrobo-eval 使用示例

## CLI 示例

### 创建技能仿真评测任务

```bash
cloudrobo eval create-job \
  --name my-eval \
  --virtual-world-id d6e7f8a9-b0c1-2345-defa-456789012345 \
  --infer-server-id e7f8a9b0-c1d2-3456-efab-567890123456 \
  --model-source CLOUDROBO_SQUARE \
  --skill-description "导航技能评测" \
  --testing-round 5
```

### 查询任务列表

```bash
cloudrobo eval list-jobs --status running
```

### 查看任务详情

```bash
cloudrobo eval show-job --job-id e1f2a3b4-c5d6-7890-efab-901234567890
```

### 停止 / 重启 / 删除任务

```bash
cloudrobo eval stop-job --job-id e1f2a3b4-c5d6-7890-efab-901234567890
cloudrobo eval restart-job --job-id e1f2a3b4-c5d6-7890-efab-901234567890
cloudrobo eval delete-job --job-id e1f2a3b4-c5d6-7890-efab-901234567890
```

### 批量删除任务

```bash
cloudrobo eval batch-delete-jobs --job-ids f2a3b4c5-d6e7-8901-fabc-012345678901,a3b4c5d6-e7f8-9012-abcd-123456789012,b4c5d6e7-f8a9-0123-bcde-234567890123
```

### 查询执行记录

```bash
cloudrobo eval list-executions --job-id e1f2a3b4-c5d6-7890-efab-901234567890 --status completed
cloudrobo eval show-execution --job-id e1f2a3b4-c5d6-7890-efab-901234567890 --execution-id c5d6e7f8-a9b0-1234-cdef-345678901234
```

### 获取 VNC 地址

```bash
cloudrobo eval get-vnc-address --job-id e1f2a3b4-c5d6-7890-efab-901234567890 --execution-id c5d6e7f8-a9b0-1234-cdef-345678901234
```

### 作业统计

```bash
cloudrobo eval show-stats --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890
```

### 带泛化性测试的评测

```bash
cloudrobo eval run-with-generalization \
  --config '{"name":"gen-eval","virtual_world_id":"d6e7f8a9-b0c1-2345-defa-456789012345","infer_server_id":"e7f8a9b0-c1d2-3456-efab-567890123456","model_source":"CLOUDROBO_SQUARE"}' \
  --generalization-types noise,obstacle
```

## SDK 示例

```python
from cloudrobo_eval import EvalClient
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)
client = EvalClient(http)

# 创建评测任务
job = client.create_eval_job({
    "name": "my-eval",
    "virtual_world_id": "d6e7f8a9-b0c1-2345-defa-456789012345",
    "infer_server_id": "e7f8a9b0-c1d2-3456-efab-567890123456",
    "model_source": "CLOUDROBO_SQUARE"
})

# 查询任务列表
jobs = client.list_eval_jobs(status="running")

# 查看任务详情
detail = client.show_eval_job("e1f2a3b4-c5d6-7890-efab-901234567890")

# 停止任务
client.update_eval_job("e1f2a3b4-c5d6-7890-efab-901234567890", {"action": "STOP"})

# 重启任务
client.update_eval_job("e1f2a3b4-c5d6-7890-efab-901234567890", {"action": "RESTART"})

# 删除任务
client.delete_eval_job("e1f2a3b4-c5d6-7890-efab-901234567890")

# 批量删除
client.batch_delete_eval_jobs(["f2a3b4c5-d6e7-8901-fabc-012345678901", "a3b4c5d6-e7f8-9012-abcd-123456789012", "b4c5d6e7-f8a9-0123-bcde-234567890123"])

# 查询执行记录
executions = client.list_executions("e1f2a3b4-c5d6-7890-efab-901234567890", status="completed")
execution = client.show_execution("e1f2a3b4-c5d6-7890-efab-901234567890", "c5d6e7f8-a9b0-1234-cdef-345678901234")

# 获取 VNC 地址
vnc = client.get_vnc_address("e1f2a3b4-c5d6-7890-efab-901234567890", "c5d6e7f8-a9b0-1234-cdef-345678901234")

# 作业统计
stats = client.show_eval_stats(workspace_id="c1d2e3f4-a5b6-7890-cdef-901234567890")
```
