# CloudRobo 数据回流工具

`hw-cloudrobo-ingestion` 在 Linux、Windows、macOS 后台监控就绪数据目录，先复制本地工作副本，再将文件直接上传 OBS。默认模式通过本地 Zenoh router 发布 Protobuf 进度，支持仅上传模式、失败重试和中断恢复。

OBS 文件不经过 CloudRobo 云端；本工具负责本地上传和向 router 发布状态，不负责后续云端转发及接收确认。依赖并复用 `hw-cloudrobo-core`、`hw-cloudrobo-r2c[client]`。

## 安装

要求 Python ≥3.10。已发布包安装：

```bash
python -m pip install hw-cloudrobo-ingestion
# 可选 LeRobot V3 格式校验，不安装整个 LeRobot：
python -m pip install "hw-cloudrobo-ingestion[lerobot-validation]"
```

从当前仓库安装、Windows 虚拟环境和 OBS 凭证配置见[安装与配置](docs/installation.md)。

## 快速开始

准备源目录、独立的工作目录和实际 OBS 桶，按[使用手册](docs/user-guide.md#回流配置文件)创建配置。仅 `source_dir` 下一层目录中的空普通文件 `__UPLOAD_READY__` 会触发整个批次上传；数据写完后最后创建标记。

```text
source_dir/
  batch001/
    data/...
    __UPLOAD_READY__
```

```bash
cloudrobo ingestion start --config ingestion.yaml
cloudrobo ingestion status
cloudrobo ingestion status --json
cloudrobo ingestion stop
```

可直接参考[默认模式配置](examples/ingestion.yaml)和[仅上传配置](examples/upload-only.yaml)，相对目录按配置文件位置解析。首个批次的完整操作见[使用示例](docs/examples.md)。

## 运行模式与约束

| 模式 | OBS 上传 | Zenoh 上报 |
|---|---|---|
| `upload_and_report`（默认） | 开启 | 开启 |
| `upload_only` | 开启 | 关闭 |

- 两种模式共享工作目录和成功历史，切换前先停止进程；同一用户默认单实例。
- 每批默认最多 50 个普通文件，可配置；标记后内容保持不变，批次路径不可复用。
- 工作目录绑定源目录，不绑定 OBS 目标；不要删除历史数据库来处理普通网络问题。
- 当前上传批次约每秒上报完整进度；成功结果最多三次，失败结果正常一次，均以本地调用返回计数。
- 最终结果在首次入队起 24 小时后停止补报；本地发布成功不承诺云端收到。

## 文档导航

- [模块概览](docs/index.md)
- [安装与配置](docs/installation.md)
- [CLI 命令](docs/commands.md)
- [使用示例](docs/examples.md)
- [完整使用手册](docs/user-guide.md)
- [开发与验证](docs/development.md)
- [独立 Mock 开发工具](../../tools/ingestion-mock/README.md)
