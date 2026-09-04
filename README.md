# CloudRobo Client

CloudRobo command-line tool and Python SDK for Huawei Cloud's Embodied Intelligence Platform (CloudRobo) - resource management and task orchestration.

[中文文档](README_CN.md)

## Features

- **Asset Management**: Manage lifecycle of models, datasets, and algorithms
- **Data Processing**: Create and monitor data processing tasks (cleaning, format conversion, etc.)
- **Model Training**: Submit and monitor training tasks (pre-training, fine-tuning, simulation reinforcement learning)
- **Model Evaluation**: Create simulation evaluation tasks to assess model performance
- **Inference Service**: Deploy and manage model inference services
- **Robot Management**: Register robots and export device certificates
- **Task Dispatch**: Execute embodied intelligence tasks on robots
- **Workspace Management**: Manage resource-isolated workspaces
- **Resource Management**: Query resource quotas and resource pools
- **Data Plane SDK**: Robot-side Zenoh data plane client (optional)

## Installation

### For Users (Recommended)

Install from PyPI:

```bash
pip install hw-cloudrobo-client
```

After installation, the `cloudrobo` CLI tool is ready to use.

### For Developers

Install from source (requires git):

```bash
git clone <repository-url>
cd cloudrobo-client
pip install -r requirements-dev-editable.txt
```

This installs all sub-packages in editable mode. Code changes take effect immediately.

## Quick Start

### 1. Configure Authentication

```bash
# Configure Huawei Cloud AK/SK (automatically encrypted)
cloudrobo config set ak your-access-key sk your-secret-key

# Configure region
cloudrobo config set region cn-southwest-2
```

Or use environment variables:

```bash
export HUAWEI_CLOUD_AK="your-access-key"
export HUAWEI_CLOUD_SK="your-secret-key"
export CLOUDROBO_REGION="cn-southwest-2"
```

### 2. View Available Commands

```bash
cloudrobo --help
```

### 3. Example: List Workspaces

```bash
cloudrobo workspace list
```

## Documentation

| Document | Description |
|----------|-------------|
| [Installation Guide](docs/installation.md) | System requirements, installation methods, authentication configuration |
| [Quick Start](docs/quickstart.md) | Get started in 5 minutes |
| [Architecture](docs/architecture.md) | Architecture principles, public interfaces, development guidelines |
| [Environment Variables](docs/env.md) | Environment variable configuration |

Each functional module's documentation is maintained by its respective package:

| Package | Description | Documentation |
|---------|-------------|---------------|
| cloudrobo-core | Core SDK + CLI Framework | [docs/](packages/cloudrobo-core/docs/index.md) |
| cloudrobo-asset | Asset Management | [docs/](packages/cloudrobo-asset/docs/index.md) |
| cloudrobo-dataset | Dataset Processing | [docs/](packages/cloudrobo-dataset/docs/index.md) |
| cloudrobo-train | Model Training | [docs/](packages/cloudrobo-train/docs/index.md) |
| cloudrobo-eval | Model Evaluation | [docs/](packages/cloudrobo-eval/docs/index.md) |
| cloudrobo-infer | Inference Service | [docs/](packages/cloudrobo-infer/docs/index.md) |
| cloudrobo-robot | Robot Management | [docs/](packages/cloudrobo-robot/docs/index.md) |
| cloudrobo-dispatch | Task Dispatch | [docs/](packages/cloudrobo-dispatch/docs/index.md) |
| cloudrobo-workspace | Workspace Management | [docs/](packages/cloudrobo-workspace/docs/index.md) |
| cloudrobo-resource | Resource Management | [docs/](packages/cloudrobo-resource/docs/index.md) |
| cloudrobo-r2c | Data Plane SDK (Zenoh, heavy optional dependencies) | [docs/](packages/cloudrobo-r2c/docs/index.md) |

## Project Structure

This project uses a Monorepo architecture where each functional module is an independent package:

```
cloudrobo-client/
├── packages/                    # Independent functional packages
│   ├── cloudrobo-core/          # Core SDK + CLI framework
│   ├── cloudrobo-asset/         # Asset management
│   ├── cloudrobo-dataset/       # Dataset processing
│   ├── cloudrobo-train/         # Model training
│   ├── cloudrobo-eval/          # Model evaluation
│   ├── cloudrobo-infer/         # Inference service
│   ├── cloudrobo-robot/         # Robot management
│   ├── cloudrobo-dispatch/      # Task dispatch
│   ├── cloudrobo-workspace/     # Workspace management
│   ├── cloudrobo-resource/      # Resource management
│   └── cloudrobo-r2c/           # Data Plane SDK
├── docs/                        # Project-level documentation
└── pyproject.toml               # Aggregate installation configuration
```

## Testing

```bash
# Run all tests
python -m pytest tests/ packages/ -v

# Run tests for a single package
python -m pytest packages/cloudrobo-asset/tests/ -v
```

## License

Apache License 2.0
