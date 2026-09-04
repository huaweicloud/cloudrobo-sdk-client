# cloudrobo-dispatch 使用示例

## CLI 示例

### 会话任务

```bash
# 创建任务（请求体严格按 CreateDispatcherTaskRequestBody）
TASK_ID=$(cloudrobo dispatch create-task --session-id $SESSION_ID \
  --name "抓取红色立方体任务" --task "grasp red cube" \
  --constraints-json '{"model":{"exec_model_id":"m1"},"robot_id":"r1","exec_constraints":{"max_iter_num":100,"max_run_time":10}}' | jq -r '.task_id')

# 列出会话任务
cloudrobo dispatch list-tasks --session-id $SESSION_ID --limit 20 --status RUNNING

# 查询任务详情
cloudrobo dispatch show-task --session-id $SESSION_ID --task-id $TASK_ID

# 获取任务结果
cloudrobo dispatch show-task-result --session-id $SESSION_ID --task-id $TASK_ID --inverse --limit 100

# 等待任务完成（每5秒查询直到状态非RUNNING或超时）
cloudrobo dispatch wait-task --session-id $SESSION_ID --task-id $TASK_ID --timeout 600

# 取消任务
cloudrobo dispatch cancel-task --session-id $SESSION_ID --task-id $TASK_ID
```

### 试运行模式

```bash
cloudrobo dispatch create-task --session-id $SESSION_ID --name t --task grasp --constraints-json '{"model":{"exec_model_id":"m1"},"robot_id":"r1"}' --dry-run
```

## SDK 示例

```python
from cloudrobo_dispatch import DispatchClient
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)
client = DispatchClient(http)

# 创建任务（严格按 CreateDispatcherTaskRequestBody）
task = client.create_dispatcher_task(session_id, {
    "name": "task-1",
    "task": "grasp red cube",
    "constraints": {
        "model": {"exec_model_id": "m1"},
        "robot_id": "r1",
        "exec_constraints": {"max_iter_num": 100, "max_run_time": 10},
    },
})
task_id = task["task_id"]

# 列出 / 查询 / 取消任务
tasks = client.list_dispatcher_tasks(session_id, limit=20, status="RUNNING")
detail = client.show_dispatcher_task(session_id, task_id)
result = client.show_dispatcher_task_result(session_id, task_id, inverse=True, limit=100)
client.cancel_dispatcher_task(session_id, task_id)

# 等待任务完成（每5秒查询直到状态非RUNNING或超时，超时抛 TimeoutError）
final = client.wait_dispatcher_task(session_id, task_id, timeout=600)
```
