# cloudrobo-train

模型训练模块，提供训练任务管理、仿真强化学习任务管理等功能。

## 功能特性

- 管理训练任务生命周期（创建、停止、重启、克隆、续训、删除）
- 保存训练配置草稿
- 统计各状态任务数量
- 查看训练阶段、资源使用、日志、签名URL和事件
- 仿真强化学习任务全生命周期管理（创建、查询、更新、删除、停止、重启、克隆、监控）
- 通过 SDK 或 CLI 操作；CLI 用 `--sim-rl` 开关在普通训练任务与仿真强化学习任务之间切换

## 安装

```bash
pip install -e packages/cloudrobo-train
```

## 快速开始

### CLI

```bash
cloudrobo train create-task --config '{"name":"my-task","train_mode":"MODEL_TUNING","train_method":"LORA","input_models":[{"model_asset_id":"a9b0c1d2-e3f4-5678-abcd-789012345678"}],"datasets":[{"dataset_asset_id":"b0c1d2e3-f4a5-6789-bcde-890123456789"}],"spec":"Ascend: 1 * SNT9B2 | 24 vCPUs | 192 GiB"}'
```

### SDK

```python
from cloudrobo_train.client import TrainClient
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)
client = TrainClient(http)

task = client.create_train_task({
    "name": "my-finetune",
    "train_mode": "MODEL_TUNING",
    "train_method": "LORA",
    "input_models": [{"model_asset_id": "a9b0c1d2-e3f4-5678-abcd-789012345678"}],
    "datasets": [{"dataset_asset_id": "b0c1d2e3-f4a5-6789-bcde-890123456789"}],
    "spec": "Ascend: 1 * SNT9B2 | 24 vCPUs | 192 GiB"
})
```

## 文档导航

- [CLI 命令详情](commands.md)
- [使用示例](examples.md)
- [开发指南](development.md)
