# CloudRobo Client

[English](README.md)

CloudRobo 命令行工具与 Python SDK，用于华为云具身智能平台（CloudRobo）的资源管理与任务编排。

## 功能特性

- **资产管理**：管理模型、数据集、算法等资产的生命周期
- **数据处理**：创建和监控数据处理任务（数据清洗、格式转换等）
- **模型训练**：提交和监控模型训练任务（预训练、微调、仿真强化学习）
- **模型评测**：创建仿真评测任务，评估模型性能
- **推理服务**：部署和管理模型推理服务
- **机器人管理**：注册机器人、导出设备证书
- **智能体调度**：在机器人上执行具身智能任务
- **工作空间**：管理资源隔离的工作空间
- **资源管理**：查询资源配额和资源池
- **数据面 SDK**：机器人侧 Zenoh 数据面客户端（可选）

## 安装

### 用户安装（推荐）

从 PyPI 安装：

```bash
pip install hw-cloudrobo-client
```

安装完成后即可使用 `cloudrobo` 命令行工具。

### 开发者安装

从源码安装（需要 git）：

```bash
git clone <repository-url>
cd cloudrobo-client
pip install -r requirements-dev-editable.txt
```

这将以可编辑模式安装所有子包，代码修改即时生效。

## 快速开始

### 1. 配置认证

```bash
# 配置华为云 AK/SK（自动加密存储）
cloudrobo config set ak your-access-key sk your-secret-key

# 配置区域
cloudrobo config set region cn-southwest-2
```

或使用环境变量：

```bash
export HUAWEI_CLOUD_AK="your-access-key"
export HUAWEI_CLOUD_SK="your-secret-key"
export CLOUDROBO_REGION="cn-southwest-2"
```

### 2. 查看可用命令

```bash
cloudrobo --help
```

### 3. 示例：查看工作空间

```bash
cloudrobo workspace list
```

## 文档

| 文档 | 说明 |
|------|------|
| [安装指南](docs/installation.md) | 系统要求、安装方式、认证配置 |
| [快速开始](docs/quickstart.md) | 5 分钟上手 |
| [架构文档](docs/architecture.md) | 架构原则、公共接口、开发规范 |
| [环境变量](docs/env.md) | 环境变量配置说明 |

各功能模块文档由各 package 自维护：

| Package | 说明 | 文档入口 |
|---------|------|----------|
| cloudrobo-core | 核心 SDK + CLI 框架 | [docs/](packages/cloudrobo-core/docs/index.md) |
| cloudrobo-asset | 资产管理 | [docs/](packages/cloudrobo-asset/docs/index.md) |
| cloudrobo-dataset | 数据集处理 | [docs/](packages/cloudrobo-dataset/docs/index.md) |
| cloudrobo-train | 模型训练 | [docs/](packages/cloudrobo-train/docs/index.md) |
| cloudrobo-eval | 模型评测 | [docs/](packages/cloudrobo-eval/docs/index.md) |
| cloudrobo-infer | 推理服务 | [docs/](packages/cloudrobo-infer/docs/index.md) |
| cloudrobo-robot | 机器人管理 | [docs/](packages/cloudrobo-robot/docs/index.md) |
| cloudrobo-dispatch | 智能体调度 | [docs/](packages/cloudrobo-dispatch/docs/index.md) |
| cloudrobo-workspace | 工作空间 | [docs/](packages/cloudrobo-workspace/docs/index.md) |
| cloudrobo-resource | 资源管理 | [docs/](packages/cloudrobo-resource/docs/index.md) |
| cloudrobo-r2c | 数据面 SDK（Zenoh，重型可选依赖） | [docs/](packages/cloudrobo-r2c/docs/index.md) |

## 项目结构

本项目采用 Monorepo 架构，各功能模块独立成包：

```
cloudrobo-client/
├── packages/                    # 独立功能包
│   ├── cloudrobo-core/          # 核心 SDK + CLI 框架
│   ├── cloudrobo-asset/         # 资产管理
│   ├── cloudrobo-dataset/       # 数据集处理
│   ├── cloudrobo-train/         # 模型训练
│   ├── cloudrobo-eval/          # 模型评测
│   ├── cloudrobo-infer/         # 推理服务
│   ├── cloudrobo-robot/         # 机器人管理
│   ├── cloudrobo-dispatch/      # 智能体调度
│   ├── cloudrobo-workspace/     # 工作空间
│   ├── cloudrobo-resource/      # 资源管理
│   └── cloudrobo-r2c/           # 数据面 SDK
├── docs/                        # 项目级文档
└── pyproject.toml               # 聚合安装配置
```

## 测试

```bash
# 运行全部测试
python -m pytest tests/ packages/ -v

# 运行单个包的测试
python -m pytest packages/cloudrobo-asset/tests/ -v
```

## 许可证

Apache License 2.0
