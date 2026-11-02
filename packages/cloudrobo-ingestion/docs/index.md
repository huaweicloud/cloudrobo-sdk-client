# cloudrobo-ingestion

数据回流模块，在 Linux、Windows、macOS 后台监控本地目录，将就绪批次直接上传 OBS，并可通过本地 Zenoh router 上报文件进度。命令入口为 `cloudrobo ingestion`，安装包名为 `hw-cloudrobo-ingestion`，要求 Python ≥3.10。

## 功能特性

- 识别源目录直接子目录中的空文件 `__UPLOAD_READY__`，递归上传该批次内的普通文件。
- 先复制完整工作副本，再执行可选 LeRobot V3 结构校验，按批次、文件串行上传。
- 支持默认上传并上报、仅上传两种模式，共享工作目录和成功历史。
- 提供后台启动、状态/JSON 查询、停止、失败退避重试及中断恢复。
- 默认模式约每秒发布当前上传批次的完整进度，成功结果最多发送三次，后两次各随机等待 1～5 秒。
- 提供 OBS、上报、本地处理三类诊断和轮转日志。

OBS 文件不经过 CloudRobo 云端；工具负责把 Protobuf 状态交给配置的 router，后续转发、存储和展示由接收链路负责。无需先创建 CloudRobo 工作空间或启动机器人硬件适配器。

## 运行模式

| `run_mode` | OBS 上传 | Zenoh 上报 | 适用场景 |
|---|---|---|---|
| `upload_and_report`（默认） | 开启 | 开启 | 上传与云端进度展示 |
| `upload_only` | 开启 | 关闭 | 只保存数据，或临时停用上报 |

业务 `run_mode` 与通信 `zenoh.mode` 是不同配置。Mock 已分离为[独立开发工具](../../../tools/ingestion-mock/README.md)，不属于客户运行模式。

## 安装

在已提供对应版本的包源中：

```bash
python -m pip install hw-cloudrobo-ingestion
# 可选格式校验：
python -m pip install "hw-cloudrobo-ingestion[lerobot-validation]"
```

源码安装、虚拟环境、Windows 操作及 OBS 凭证准备见[安装与配置](installation.md)。工具复用 `hw-cloudrobo-core` 和 `hw-cloudrobo-r2c[client]`，仅上传模式也沿用同一安装依赖。

## 快速开始

1. 准备已有 OBS 桶、可用凭证及匹配的 endpoint；默认模式另准备 Zenoh router。
2. 按[配置说明](user-guide.md#回流配置文件)创建 `ingestion.yaml`，设置源目录、工作目录、OBS 路径和分类；也可从[默认配置](../examples/ingestion.yaml)复制后修改。
3. 在源目录下一层的新批次目录写完所有数据，最后创建零字节普通标记 `__UPLOAD_READY__`。
4. 启动并查询状态：

```bash
cloudrobo ingestion start --config ingestion.yaml
cloudrobo ingestion status
cloudrobo ingestion status --json
cloudrobo ingestion stop
```

完整的三平台首次上传、模式切换和更换 OBS 目标示例见[使用示例](examples.md)。

## 关键约束

- 默认每批最多 **50 个文件**，可通过 `max_files_per_batch` 或 `--max-files-per-batch` 修改；所有递归普通文件参与计数，标记除外。
- 标记后批次保持不变，路径不复用；源目录与工作目录不能相同或互相包含，不使用链接/junction。
- 工作目录保存源目录绑定和成功历史，切换模式继续使用原目录；已成功路径不会自动重传。
- 所有最终结果仅在批次首次入队起 **24 小时**内补报，停止不会等待补发完成。
- 本地发布返回不等于云端确认；OBS 上传成功与进度收到应分别判断。

## 文档导航

| 文档 | 内容 |
|---|---|
| [安装与配置](installation.md) | 环境准备、安装、OBS 凭证与 endpoint |
| [CLI 命令](commands.md) | start/status/stop、全部覆盖参数、状态字段和日志 |
| [使用示例](examples.md) | 首次上传、Windows/Linux/macOS 操作、模式切换、修改目标 |
| [使用手册](user-guide.md) | 配置、数据约束、恢复重试、完整上报字段、24 小时补报、校验与排障 |
| [开发与验证](development.md) | 依赖、跨平台测试、基准与独立 Mock 工具 |

## 配置模板

- [默认上传并上报](../examples/ingestion.yaml)
- [仅上传](../examples/upload-only.yaml)
