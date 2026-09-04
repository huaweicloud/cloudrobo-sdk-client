# 快速开始

## 5分钟上手 CloudRobo Client

### 第一步：安装和配置

```bash
# 安装（开发模式，安装所有子包）
pip install -r requirements-dev-editable.txt

# 配置认证（AK/SK 自动加密存储）
cloudrobo config set ak your-ak sk your-sk
```

### 第二步：创建工作空间

```bash
# CLI 方式
cloudrobo workspace create --name my-project
cloudrobo workspace use --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890
```

```python
# SDK 方式
from cloudrobo_workspace.client import WorkspaceClient
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)
ws_client = WorkspaceClient(http)
workspace = ws_client.create({"name": "my-project"})
```

### 第三步：创建模型仓库

```bash
cloudrobo asset create-repository --name models --type model
```

### 第四步：准备数据集

```bash
cloudrobo dataset proc create-task \
  --name preprocess \
  --algo-type DATA_WASH \
  --dataset '{"input_path": "obs://bucket/raw-data", "output_path": "obs://bucket/processed-data"}' \
  --image "cloudrobo/dataset-toolkit:latest" \
  --spec '{"flavor": "cpu.2"}'
```

### 第五步：训练模型

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

### 第六步：评测模型

```bash
cloudrobo eval create-job \
  --name skill-eval \
  --virtual-world-id d6e7f8a9-b0c1-2345-defa-456789012345 \
  --infer-server-id f8a9b0c1-d2e3-4567-fabc-678901234567 \
  --model-source CLOUDROBO_SQUARE
```

### 第七步：注册机器人

```bash
cloudrobo robot create \
  --name my-robot \
  --type HUMANOID \
  --manufacturer "Mfg A" \
  --robot-model "Model X" \
  --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890
```

### 第八步：部署推理服务

```bash
# 创建推理服务
cloudrobo infer create \
  --name chat-api \
  --model-id a9b0c1d2-e3f4-5678-abcd-789012345678 \
  --model-version-id d8e9f0a1-b2c3-4567-defa-678901234567 \
  --flavor cpu.2 \
  --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890 \
  --pool-id p-123456 \
  --pool-type SHARED

# 等待推理服务部署完成
cloudrobo infer wait-deploy --service-id <service-id> --timeout 600
```

## 下一步

- 查看 [架构文档](architecture.md) 了解架构原则和公共接口
- 浏览 [各模块文档](index.md#功能模块) 了解详细功能
