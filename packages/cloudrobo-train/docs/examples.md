# cloudrobo-train 使用示例

## CLI 示例

### 创建训练任务

```bash
cloudrobo train create-task --config-file train-config.json
```

其中 `train-config.json` 示例：

```json
{
  "name": "my-finetune",
  "train_mode": "MODEL_TUNING",
  "train_method": "LORA",
  "input_models": [{"model_asset_id": "a9b0c1d2-e3f4-5678-abcd-789012345678"}],
  "datasets": [{"dataset_asset_id": "b0c1d2e3-f4a5-6789-bcde-890123456789"}],
  "spec": "Ascend: 1 * SNT9B2 | 24 vCPUs | 192 GiB"
}
```

### 创建仿真强化学习任务

```bash
cloudrobo train create-task --sim-rl --config-file sim-rl-config.json
```

### 查看训练中的任务

```bash
cloudrobo train list-tasks --status RUNNING
```

### 查看仿真强化学习任务

```bash
cloudrobo train list-tasks --sim-rl --status RUNNING
```

### 查看任务详情

```bash
cloudrobo train show-task --task-id b8c9d0e1-f2a3-4567-bcde-678901234567
cloudrobo train show-task --sim-rl --task-id b8c9d0e1-f2a3-4567-bcde-678901234567
```

### 统计各状态任务数量

```bash
cloudrobo train stats --workspace-id <workspace-id>
cloudrobo train stats --sim-rl --workspace-id <workspace-id>
```

### 停止任务

```bash
cloudrobo train stop-task --task-id b8c9d0e1-f2a3-4567-bcde-678901234567
cloudrobo train stop-task --sim-rl --task-id b8c9d0e1-f2a3-4567-bcde-678901234567
```

### 重启/续训任务

```bash
# 使用原配置重启
cloudrobo train restart-task --task-id b8c9d0e1-f2a3-4567-bcde-678901234567

# 重启并修改配置（非草稿状态不能修改 name/train_mode/train_method）
cloudrobo train restart-task --task-id b8c9d0e1-f2a3-4567-bcde-678901234567 \
  --config '{"description":"updated","spec":"Ascend: 2 * SNT9B2 | 48 vCPUs | 384 GiB"}'

# 使用配置文件修改后重启
cloudrobo train restart-task --task-id b8c9d0e1-f2a3-4567-bcde-678901234567 \
  --config-file restart-config.json

cloudrobo train resume-task --task-id b8c9d0e1-f2a3-4567-bcde-678901234567
```

`restart-config.json` 示例：

```json
{
  "description": "updated description",
  "input_models": [{"model_asset_id": "new-model-id"}],
  "datasets": [{"dataset_asset_id": "new-dataset-id"}],
  "spec": "Ascend: 2 * SNT9B2 | 48 vCPUs | 384 GiB",
  "parameters": "[{\"key\":\"batch_size\",\"value\":\"64\"}]"
}
```

### 克隆仿真强化学习任务

```bash
cloudrobo train clone-task --task-id b8c9d0e1-f2a3-4567-bcde-678901234567
```

### 删除任务

```bash
cloudrobo train delete-tasks --task-id b8c9d0e1-f2a3-4567-bcde-678901234567
cloudrobo train delete-tasks --sim-rl --task-id b8c9d0e1-f2a3-4567-bcde-678901234567
```

### 保存草稿

```bash
cloudrobo train save-draft --config '{"name":"my-draft","method":"lora"}'
cloudrobo train save-draft --sim-rl --config '{"name":"sim-draft"}'
```

### 查看训练阶段

```bash
cloudrobo train get-stages --task-id b8c9d0e1-f2a3-4567-bcde-678901234567
```

### 查看资源使用

```bash
cloudrobo train get-resource-usage --task-id b8c9d0e1-f2a3-4567-bcde-678901234567 \
  --metric gpu_util --start 1716000000 --end 1716003600
```

### 获取训练日志

```bash
cloudrobo train get-logs --task-id b8c9d0e1-f2a3-4567-bcde-678901234567
cloudrobo train get-logs --task-id b8c9d0e1-f2a3-4567-bcde-678901234567 --file-name worker0.log --catalog logs
```

### 获取日志签名URL

```bash
cloudrobo train get-signed-url --task-id b8c9d0e1-f2a3-4567-bcde-678901234567 \
  --file-source TRAIN --file-name worker0.log
```

### 获取训练事件

```bash
cloudrobo train get-events --task-id b8c9d0e1-f2a3-4567-bcde-678901234567 \
  --start-time 1716000000000 --end-time 1716003600000 --level Error
```

## SDK 示例

### 基本训练流程

```python
from cloudrobo_train.client import TrainClient
from cloudrobo_core.sdk import Config, HttpClient
import time

config = Config()
http = HttpClient(config)
client = TrainClient(http)

task = client.create_train_task({
    "name": "my-finetune",
    "base_model_asset_id": "a9b0c1d2-e3f4-5678-abcd-789012345678",
    "dataset_asset_id": "b0c1d2e3-f4a5-6789-bcde-890123456789",
    "method": "lora",
    "spec": {"gpu": "A100"}
})
print(f"Training started: {task['task_id']}")

while True:
    status = client.show_train_task(task["task_id"])
    print(f"Status: {status.get('status')}")
    if status.get("status") in ["completed", "failed"]:
        break
    time.sleep(30)
```

### 任务管理

```python
tasks = client.list_train_tasks(status="RUNNING")

client.stop_train_task("b8c9d0e1-f2a3-4567-bcde-678901234567")

client.restart_train_task("b8c9d0e1-f2a3-4567-bcde-678901234567")

# 重启并修改配置
client.restart_train_task(
    "b8c9d0e1-f2a3-4567-bcde-678901234567",
    req={"description": "updated", "spec": "Ascend: 2 * SNT9B2"},
)

client.batch_delete_train_tasks(["b8c9d0e1-f2a3-4567-bcde-678901234567", "d0e1f2a3-b4c5-6789-defa-890123456789"])

client.resume_train_task("b8c9d0e1-f2a3-4567-bcde-678901234567")

client.count_train_tasks_by_status("<workspace-id>")
```

### 草稿和监控

```python
draft = client.save_draft({"name": "my-draft", "method": "lora"})

stages = client.list_train_stages("b8c9d0e1-f2a3-4567-bcde-678901234567")

usage = client.show_resource_usage(
    "b8c9d0e1-f2a3-4567-bcde-678901234567",
    metric="gpu_util",
    start=1716000000,
    end=1716003600,
)

logs = client.get_log_content("b8c9d0e1-f2a3-4567-bcde-678901234567", file_name="worker0.log")

events = client.list_events(
    "b8c9d0e1-f2a3-4567-bcde-678901234567",
    start_time=1716000000000,
    end_time=1716003600000,
)

signed = client.get_log_signed_url(
    "b8c9d0e1-f2a3-4567-bcde-678901234567",
    file_source="TRAIN",
    file_name="worker0.log",
)
```

### 更新任务

```python
client.update_train_task("b8c9d0e1-f2a3-4567-bcde-678901234567", {"description": "updated"})
```

### 仿真强化学习任务

```python
sim_task = client.create_sim_rl_task({"name": "sim-job"})

client.list_sim_rl_tasks(status="RUNNING")
client.show_sim_rl_task(sim_task["task_id"])
client.stop_sim_rl_task(sim_task["task_id"])
client.copy_sim_rl_task(sim_task["task_id"])
client.delete_sim_rl_task(sim_task["task_id"])

client.count_sim_rl_tasks_by_status("<workspace-id>")
```
