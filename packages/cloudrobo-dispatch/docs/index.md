# cloudrobo-dispatch

智能体调度模块，提供会话任务管理（对应 `robo-operations.yaml`）。

## 功能特性

- 调度任务管理：创建、列举、查询、取消任务、查看任务结果、等待任务完成（wait-task）

## 安装

```bash
pip install -e packages/cloudrobo-dispatch
```

## 快速开始

### CLI

```bash
cloudrobo dispatch create-task --session-id <id> --name task-1 --task "grasp red cube" --constraints-json '{"model":{"exec_model_id":"m1"},"robot_id":"r1"}'
```

### SDK

```python
from cloudrobo_dispatch import DispatchClient
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)
client = DispatchClient(http)

task = client.create_dispatcher_task(
    "session-001",
    {"name": "task-1", "task": "grasp red cube", "constraints": {"model": {"exec_model_id": "m1"}, "robot_id": "r1"}},
)
```

## 文档导航

- [CLI 命令详情](commands.md)
- [使用示例](examples.md)
- [开发指南](development.md)
