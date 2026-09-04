# cloudrobo-dataset 使用示例

## CLI 示例

### 查询可用算子

```bash
# 查询内置数据处理算子
cloudrobo asset list-publication-assets --type algorithm --sub-type data_processing

# 查询内置数据评测算子
cloudrobo asset list-publication-assets --type algorithm --sub-type data_evaluating

# 按名称模糊查询
cloudrobo asset list-publication-assets --name 逆运动学
```

### 查询数据集

```bash
# 列出工作空间下的数据集（自动使用当前工作空间 catalog_id）
cloudrobo asset list-assets --type dataset --name ros2-ik

# 查看数据集详情（获取 url/asset_id/version_id 用于构造 dataset_configs）
cloudrobo asset show-asset --asset-id <asset-id>
```

### 创建任务

```bash
cloudrobo dataset proc create-task \
  --name ik-task-01 \
  --algo-type PRESET_ASSETS \
  --task-config '{
    "algo_name": "数据处理--逆运动学求解器",
    "algo_entrance": "bash entrypoint.sh",
    "image": "<image-url>",
    "algo_id": "a3f8b2c1-9d4e-5f6a-b7c8-1e2d3f4a5b6c",
    "catalog_id": "<catalog-id>",
    "resource_pool_type": "PUBLIC_POOL",
    "cluster_type": "CCE",
    "task_framework_type": "K8S",
    "dataset_configs": "[{\"obs_path\":\"obs://<bucket-name>/b-test/.../<asset-id>/\",\"dataset_type\":\"BUILD_IN_ASSET\",\"asset_id\":\"<asset-id>\",\"asset_name\":\"ros2-ik\",\"version_id\":\"<version-id>\"}]",
    "output_type": "BUILD_IN_ASSET",
    "output_path": "obs://bucket/output-path",
    "output_name": "ik-task-01-output",
    "envs": "[{\"key\":\"ROBOT_MODEL\",\"value\":\"galaxea_r1\"}]",
    "head_spec": {"cpu": 0, "memory": 0},
    "worker_spec": {"cpu": 4, "memory": 8},
    "worker_num": 1,
    "evs_spec": 0
  }'
```

### 等待任务完成

```bash
cloudrobo dataset proc wait-task --task-id <task-id>
```

### 查看任务列表

```bash
cloudrobo dataset proc list-tasks --status RUNNING
```

### 查看任务详情

```bash
cloudrobo dataset proc show-task --task-id <task-id>
```

### 查看任务日志

```bash
# 1. 列出系统日志文件
cloudrobo dataset proc get-log --task-id <task-id> --is-system true

# 2. 获取系统日志内容
cloudrobo dataset proc get-log --task-id <task-id> \
  --file-name system-std-output.log \
  --file-path "proc-task/logs/<task-id>/system-std-output.log"

# 3. 列出用户日志文件
cloudrobo dataset proc get-log --task-id <task-id> --is-system false

# 4. 获取用户日志内容
cloudrobo dataset proc get-log --task-id <task-id> \
  --file-name job-std-output.log \
  --file-path "proc-task/logs/<task-id>/job-std-output.log"
```

### 预览任务输出

```bash
cloudrobo dataset proc get-preview --task-id <task-id>
```

### 重启任务

```bash
cloudrobo dataset proc restart-task --task-id <task-id>
```

## SDK 示例

### 基本操作

```python
from cloudrobo_dataset.client import DatasetClient
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)
client = DatasetClient(http)

# 创建任务
task = client.create_task({
    "name": "ik-task-01",
    "algo_type": "PRESET_ASSETS",
    "algo_name": "数据处理--逆运动学求解器",
    # ... 其他 task_config 字段
})
task_id = task["payload"]["id"]
print(f"Task created: {task_id}")

# 查看任务详情
detail = client.get_task_detail(task_id)
print(f"Status: {detail['payload']['status']}")

# 列出任务
tasks = client.list_tasks()
```

### 等待任务完成

```python
result = client.wait_task(
    task_id,
    timeout=1800,
    interval=15,
    on_status=lambda status, detail: print(f"  → {status}")
)
print(f"Final: {result['payload']['status']}")
```

### 日志查询

```python
# 列出日志文件
log_files = client.list_log_files(task_id, is_system=True)
file_path = log_files["payload"]["list"][0]["file_path"]

# 获取日志内容
log_content = client.get_task_log(task_id, "system-std-output.log", file_path=file_path)
print(log_content["payload"]["item"]["content"])
```

### 任务管理

```python
# 重启任务
client.restart_task(task_id)

# 更新任务
client.update_task(task_id, {"description": "updated"})

# 批量删除任务
client.delete_tasks(["id1", "id2"])

# 查看帧数据
frames = client.get_task_frames(task_id, prefix="proc-task/")

# 预览数据
preview = client.get_task_preview(task_id, file_name="output.json")
```

### 错误处理

```python
from cloudrobo_dataset.client import DatasetError
from cloudrobo_core.sdk.exceptions import ServiceError

try:
    detail = client.get_task_detail("non-existent-id")
except DatasetError as e:
    print(f"数据集错误: {e.get_user_message()}")
except ServiceError as e:
    print(f"服务错误: {e}")
```
