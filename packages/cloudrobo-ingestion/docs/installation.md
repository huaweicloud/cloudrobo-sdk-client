# cloudrobo-ingestion 安装与配置

[模块概览](index.md) · [安装与配置](installation.md) · [CLI 命令](commands.md) · [使用示例](examples.md) · [使用手册](user-guide.md)

## 环境准备

### 操作系统与 Python

支持 Linux、Windows 和 macOS。包声明要求 Python **≥3.10**，当前跨平台 CI 覆盖 Python **3.10、3.12**；新环境可选择已覆盖的版本。

准备 Python、pip；从源码安装时还需要 Git。以下命令均在普通用户自己的终端中运行，后台进程也使用该用户的身份和权限。

| 项目 | 准备要求 |
|---|---|
| 源目录 | 已存在；运行用户能读取全部待上传文件和遍历各级目录 |
| 工作目录 | 可创建、写入、删除；不能与源目录相同或互相包含 |
| 本地磁盘 | 需要容纳当前整个批次的完整副本，另预留数据库、日志和 checkpoint 空间 |
| OBS | 目标桶已存在；服务地址、区域、凭证与桶匹配 |
| OBS 权限 | 允许写入目标对象，并允许 SDK 所需的分段上传及中止未完成分段上传操作 |
| Zenoh | 默认模式下准备可连接的 router 和发布权限；默认地址为本机 `tcp/127.0.0.1:7447` |
| 网络 | 本机可访问 OBS endpoint；默认模式还需访问 router；本地进程控制需能使用 loopback 通信 |

本工具不会自动创建 OBS 桶，也不会替用户启动 Zenoh router。仅上传模式不要求 router，但安装包仍复用 R2C client 依赖，不是独立的精简安装包。

### 创建 Python 虚拟环境

Linux / macOS：

```bash
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Windows PowerShell：

```powershell
py -3.12 --version
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

如果 PowerShell 不允许执行激活脚本，可以直接使用虚拟环境内的可执行文件，无需修改全局执行策略：

```powershell
.\.venv\Scripts\python.exe -m pip --version
.\.venv\Scripts\cloudrobo.exe ingestion --help
```

后续示例中的 `python` 和 `cloudrobo` 均应来自同一虚拟环境。后台运行期间保留这个环境，不要删除、移动或原地升级其依赖。

## 安装与检查

### 从当前仓库安装

需要仓库 main 分支的最新功能时，可从源码安装。在已激活的虚拟环境中执行：

```bash
git clone --branch main https://github.com/huaweicloud/cloudrobo-sdk-client.git
cd cloudrobo-sdk-client
python -m pip install -e packages/cloudrobo-workspace -e packages/cloudrobo-resource -e packages/cloudrobo-core -e "packages/cloudrobo-r2c[client]" -e packages/cloudrobo-ingestion
```

该命令适用于上述三种平台，安装回流工具及其本仓库依赖；不要求安装机器人硬件适配器或整个 LeRobot。已有仓库时，先确认工作区修改已妥善保存，再拉取对应分支并执行安装。

已完成上述源码安装、需要追加格式校验依赖时，在仓库根目录执行：

```bash
python -m pip install -e "packages/cloudrobo-ingestion[lerobot-validation]"
```

### 使用已发布的安装包

如果使用的包源已提供包含本功能的对应版本，也可以直接安装：

```bash
python -m pip install hw-cloudrobo-ingestion
# 需要 LeRobot 校验时改用：
python -m pip install "hw-cloudrobo-ingestion[lerobot-validation]"
```

完整聚合包 `hw-cloudrobo-client` 也声明了回流包依赖。包是否可下载、实际包含哪些分支修改，以交付的版本及包源为准；不要仅凭包名或相同的版本号判断已经获得最新源码功能。

### 检查安装

```bash
python -m pip show hw-cloudrobo-ingestion hw-cloudrobo-core hw-cloudrobo-r2c
cloudrobo ingestion --help
cloudrobo ingestion start --help
```

默认上报模式还需要生成的 Protobuf 模块，可以在不连接 OBS/router 的情况下检查：

```bash
python -c "from cloudrobo_ingestion.progress import message_class; print(message_class().DESCRIPTOR.full_name)"
```

预期输出 `r2c_sdk.DataUploadProgress`。正常发行包构建会编译 Protobuf；源码 editable 环境中若缺少生成文件，在仓库根目录执行：

```bash
python -m pip install "grpcio-tools==1.80.0"
python -c "from cloudrobo_r2c._build_commands import compile_protobuf_files; compile_protobuf_files()"
```

仅上传模式运行时不会初始化 Zenoh 或导入进度 Protobuf；不要把上述 Protobuf 检查当作仅上传模式的运行前置条件。

## OBS 凭证和服务地址

回流 YAML 管理数据目录、运行模式、OBS **存储路径**等业务配置。AK/SK 和 OBS **服务地址**沿用 core SDK 配置，不放入回流 YAML。

| 项目 | 示例 | 配置位置 |
|---|---|---|
| OBS 存储路径 | `obs://customer-bucket/robot-data/` | 回流 YAML 的 `obs_path`，或 `--obs-path` |
| OBS 服务地址 | `https://obs.cn-southwest-2.myhuaweicloud.com` | `CLOUDROBO_OBS_ENDPOINT`，或 core 配置 |
| AK / SK | 实际用户凭证 | core 加密配置，或 `HUAWEI_CLOUD_AK` / `HUAWEI_CLOUD_SK` |

`obs_path` 不是 HTTPS 地址；endpoint 也不应填写 `obs://` URI 或桶内目录。

### 通过 core 配置保存凭证

```bash
cloudrobo config set ak "<AK>" sk "<SK>" region "cn-southwest-2"
cloudrobo config list
```

用真实值替换尖括号占位符；区域也应与客户桶匹配。AK/SK 会加密写入当前用户的 `~/.cloudrobo/config.yaml`，加密与机器绑定；换机器或换运行用户时重新配置。命令包含凭证时注意终端历史，不要把真实命令复制到工单或日志。

`cloudrobo config list` 只显示凭证是否已配置；它不证明凭证有上传权限，也不检测 OBS 连通性。

### 通过环境变量提供凭证及 endpoint

Linux / macOS：

```bash
export HUAWEI_CLOUD_AK='<AK>'
export HUAWEI_CLOUD_SK='<SK>'
export CLOUDROBO_OBS_ENDPOINT='https://obs.cn-southwest-2.myhuaweicloud.com'
```

Windows PowerShell：

```powershell
$env:HUAWEI_CLOUD_AK = '<AK>'
$env:HUAWEI_CLOUD_SK = '<SK>'
$env:CLOUDROBO_OBS_ENDPOINT = 'https://obs.cn-southwest-2.myhuaweicloud.com'
```

非空 AK/SK 环境变量优先于 core 用户配置。OBS endpoint 优先使用非空的 `CLOUDROBO_OBS_ENDPOINT`；未设置时，读取 core 的 `cloudrobo.endpoints.cloudrobo-obs`，并用 `cloudrobo.region` 替换地址中的 `{region}`。默认模板是 `https://obs.{region}.myhuaweicloud.com`。

如需把 endpoint 固定在用户配置中，可合并以下片段，保留文件里其他配置及 `ak_enc` / `sk_enc`，不要覆盖整个文件：

```yaml
cloudrobo:
  region: cn-southwest-2
  endpoints:
    cloudrobo-obs: https://obs.cn-southwest-2.myhuaweicloud.com
```

当前 `cloudrobo config set` 只支持 `ak`、`sk`、`region`；没有设置 endpoint 的子命令。此上传路径使用的 endpoint 环境变量就是 `CLOUDROBO_OBS_ENDPOINT`。区域使用上述 core 配置，不依赖 `CLOUDROBO_REGION`。

后台继承启动命令的环境变量。修改另一个终端的变量不会改变已运行进程；调整凭证、endpoint 或区域后，停止并重新启动工具。
