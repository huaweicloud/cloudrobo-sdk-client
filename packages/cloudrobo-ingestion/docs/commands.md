# cloudrobo-ingestion CLI 命令

[模块概览](index.md) · [安装与配置](installation.md) · [CLI 命令](commands.md) · [使用示例](examples.md) · [使用手册](user-guide.md)

## 命令概览

```bash
cloudrobo ingestion [OPTIONS] COMMAND [ARGS]...
```

| 命令 | 说明 |
|---|---|
| `start --config FILE` | 校验配置并启动后台目录监控与上传 |
| `status` | 显示进程、批次、扫描、上报和最近诊断 |
| `status --json` | 输出便于脚本处理的 JSON 状态快照 |
| `stop` | 停止新任务，中断当前处理并退出后台进程 |

使用 `cloudrobo ingestion start --help` 查看完整启动选项；`--help` 不启动任务。

## 常用命令与参数覆盖

### 生命周期

```bash
# 启动后台进程；--config 必填
cloudrobo ingestion start --config ingestion.yaml

# 查看可读状态，或输出便于脚本处理的 JSON
cloudrobo ingestion status
cloudrobo ingestion status --json

# 停止后台进程
cloudrobo ingestion stop
```

`start` 后进程脱离当前终端运行；关闭终端不会按正常流程停止该进程。系统关机、重启或强制结束仍会中断，本工具不注册开机自启。

`status` 和 `stop` 按**当前操作系统用户和运行登记目录**查找进程，不接受 `--config` 或 `--work-dir`。切换系统用户后看到的是另一个用户的实例。`status` 是最近保存的本地快照，后台运行时通常约每秒刷新；进程停止后仍可以离线查询。

没有 `restart`、单批重试、取消某文件或清空历史命令。重启使用 `stop` 后再次 `start`；需要固定重复使用的覆盖项应写回 YAML，因为原 YAML 不会被 CLI 自动修改。

### CLI 覆盖优先级

业务参数优先级为：**显式 CLI 参数 > YAML 配置 > 默认值**。core 凭证另按环境变量和用户配置取值，见[OBS 凭证和服务地址](installation.md#obs-凭证和服务地址)。

| 参数 | 作用 |
|---|---|
| `--run-mode upload_and_report` / `upload_only` | 覆盖业务模式 |
| `--source-dir PATH` | 覆盖源目录 |
| `--work-dir PATH` | 覆盖工作目录 |
| `--obs-path URI` | 覆盖 OBS 目标 |
| `--task-type TEXT` | 覆盖新任务的任务类型 |
| `--collect-method TEXT` | 覆盖新任务的采集方式 |
| `--max-files-per-batch INTEGER` | 覆盖每批文件数上限，必须 ≥1 |
| `--validate-lerobot` | 开启格式校验 |
| `--no-validate-lerobot` | 显式关闭格式校验 |
| `--zenoh-mode client` / `peer` | 覆盖 Zenoh 通信模式 |
| `--zenoh-endpoint ENDPOINT` | 覆盖连接地址，可重复指定 |
| `--zenoh-project-id TEXT` | 覆盖项目标识 |
| `--zenoh-device-id TEXT` | 覆盖设备标识 |
| `--zenoh-client-id TEXT` | 覆盖 client 标识 |

未传的选项不覆盖 YAML；显式 `--no-validate-lerobot` 会覆盖 YAML 中的 `true`。后台收到的是合并后的有效配置，不会丢失 CLI 覆盖。

```bash
cloudrobo ingestion start --config ingestion.yaml --run-mode upload_only --max-files-per-batch 200 --no-validate-lerobot
cloudrobo ingestion start --config ingestion.yaml --work-dir ./other-work --obs-path obs://customer-bucket/test/
cloudrobo ingestion start --config ingestion.yaml --zenoh-project-id r2c --zenoh-device-id robot-001 --zenoh-endpoint tcp/127.0.0.1:7447
```

以上为不同启动示例，不能同时启动；试另一条前先停止当前进程。更换工作目录会使用另一份历史，可能重新上传已存在的源批次，不能把它当作无影响的临时参数。

CLI 中相对目录按**执行命令时的当前目录**解析，YAML 中相对目录仍按 YAML 位置解析。含空格的路径加引号。

多个 `--zenoh-endpoint` 会整体替换 `zenoh.endpoints` 和 `zenoh.connect.endpoints`，保留已有 connect 超时等其他项。没有 endpoint-role、TLS、publisher 或 AK/SK 的回流 CLI 覆盖参数，高级连接项放 YAML，凭证按[安装文档](installation.md#obs-凭证和服务地址)配置。

## 状态、日志与成功判断

### 三个判断应分开

| 现象 | 含义 |
|---|---|
| `start` 成功，进程运行中 | 后台已启动，不代表数据已经上传或 OBS 请求验证通过 |
| 批次为 `done` | 该批所有文件的 OBS 上传调用已成功完成 |
| 上报连接正常 / 本地发布成功 | 本地连接或发布调用正常，不代表云端页面已经接收处理 |

仅在 OBS 看到了部分对象，不能据此确认整批完成；用批次状态结合实际目标文件判断。`running: false` 时旧快照中保留的 `reporter.connected` 不表示现在仍有连接。

### `status --json` 的常用字段

| 字段 | 说明 |
|---|---|
| `running`、`state`、`pid` | 当前锁检测结果、进程状态和最近记录的 PID |
| `run_mode`、`source_dir`、`work_dir`、`obs_path` | 当前/最近一次实际配置，包含 CLI 覆盖后的值 |
| `updated_ms` | 本地状态快照更新时间，毫秒时间戳 |
| `counts` | 按批次状态统计的当前记录数量，包含长期保留的成功历史；不是历次失败尝试的累计次数 |
| `active_batch` | 当前处理批次路径；无活动批次时为空 |
| `progress` | 当前活动批次的文件数、字节数、已上传字节、完成/失败文件数及正在处理的相对路径；无活动批次可能是空对象 |
| `scan` | 扫描状态、最近完成时间、已检查目录数、异常数、等待清单批次数 |
| `scan_errors` | 最近的目录扫描诊断，最多 50 条 |
| `reporter.enabled`、`connected` | 是否启用上报及发布线程的连接诊断 |
| `reporter.pending_batches` | 当前内存中待发布的槽位数，不是扫描到的全部任务数 |
| `reporter.pending_result_batches` | 数据库中保留的待发结果批次数，不是剩余消息条数 |
| `error_info`、`batch_errors`、`reporter.errors` | 顶层、批次处理和进度发布的诊断详情 |

仅上传模式中，原先纳入上报的任务仍可能留有待发结果，数值非零不表示当前正在发送。

常见批次状态为 `queued`（待处理）、`copying`、`validating`、`uploading`、`done`、`failed`（可重试失败）、`rejected`（当前运行拒绝）、`interrupted`（中断）。清理待办单独记录，上传成功也可能伴随清理错误；应查看具体诊断。

### 日志位置和读取

主日志为**有效工作目录**中的 `ingestion.log`。单文件最多 5 MiB，保留 3 份轮转备份 `ingestion.log.1`～`.3`；长期运行时较早记录会被轮换。

Linux / macOS：

```bash
tail -n 100 -f ./ingestion-work/ingestion.log
```

Windows PowerShell：

```powershell
Get-Content '.\ingestion-work\ingestion.log' -Tail 100 -Wait
```

日志命令中的相对路径按终端当前目录解释。若使用了 `--work-dir`，以 `start` 返回和 JSON 状态中的实际路径为准。

错误提示区分 `OBS 上传异常`、`进度上报异常`、`本地处理异常`，包含阶段、错误 ID 和排查建议。按错误 ID 关联日志，可查看批次、文件、OBS 地址、HTTP 状态、服务错误码、请求 ID，以及可用的 errno/winerror 和原因链。无法初始化日志时，优先查看 `start` 直接返回的诊断。
