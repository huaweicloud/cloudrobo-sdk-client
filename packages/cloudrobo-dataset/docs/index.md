# cloudrobo-dataset

数据处理与评测模块，提供数据处理/评测任务的创建、监控、日志查看、结果预览等功能。

## 功能特性

- 查询内置算子和自定义算子
- 创建数据处理（data_processing）和数据评测（data_evaluating）任务
- 任务状态轮询与监控
- 任务日志查看（系统日志 + 用户日志）
- 输出数据预览
- 任务重启、批量删除
- AI Agent Skill 支持

## 安装

```bash
pip install -e packages/cloudrobo-dataset
```

## 快速开始

### CLI

```bash
# 1. 查询可用算子
cloudrobo asset list-publication-assets --type algorithm --sub-type data_processing

# 2. 创建任务
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
    "dataset_configs": "[{\"obs_path\":\"obs://bucket/path/\",\"dataset_type\":\"BUILD_IN_ASSET\",\"asset_id\":\"<asset-id>\",\"asset_name\":\"ros2-ik\",\"version_id\":\"<version-id>\"}]",
    "output_type": "BUILD_IN_ASSET",
    "output_path": "obs://bucket/output-path",
    "output_name": "ik-task-01-output",
    "head_spec": {"cpu": 0, "memory": 0},
    "worker_spec": {"cpu": 4, "memory": 8},
    "worker_num": 1,
    "evs_spec": 0
  }'

# 3. 等待任务完成
cloudrobo dataset proc wait-task --task-id <task-id>
```

### SDK

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
    # ... 其他字段
})

# 查看任务详情
detail = client.get_task_detail(task["payload"]["id"])

# 列出任务
tasks = client.list_tasks()
```

## 文档导航

- [安装与集成指南](installation.md)
- [CLI 命令详情](commands.md)
- [使用示例](examples.md)
- [运行案例](runbook.md)
- [开发指南](development.md)
