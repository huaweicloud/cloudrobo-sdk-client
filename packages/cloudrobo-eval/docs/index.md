# cloudrobo-eval

模型评测模块，提供技能仿真评测任务的创建、管理、执行和统计功能。

## 功能特性

- 创建技能仿真评测任务
- 评测任务全生命周期管理（查询、停止、重启、删除）
- 执行记录查询与 VNC 仿真环境访问
- 作业状态统计
- 带泛化性测试的评测
- 批量删除任务

## 安装

```bash
pip install -e packages/cloudrobo-eval
```

## 快速开始

### CLI

```bash
cloudrobo eval create-job --name my-eval --virtual-world-id d6e7f8a9-b0c1-2345-defa-456789012345 --infer-server-id e7f8a9b0-c1d2-3456-efab-567890123456 --model-source CLOUDROBO_SQUARE
```

### SDK

```python
from cloudrobo_eval import EvalClient
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)
client = EvalClient(http)

job = client.create_eval_job({
    "name": "my-eval",
    "virtual_world_id": "d6e7f8a9-b0c1-2345-defa-456789012345",
    "infer_server_id": "e7f8a9b0-c1d2-3456-efab-567890123456",
    "model_source": "CLOUDROBO_SQUARE"
})
```

## 文档导航

- [CLI 命令详情](commands.md)
- [使用示例](examples.md)
- [开发指南](development.md)
