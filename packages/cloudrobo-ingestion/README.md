# CloudRobo 数据回流工具

跨平台后台监控客户目录，将完整批次直接上传 OBS，每秒向本地 Zenoh router
发布 `r2c_sdk.DataUploadProgress`。依赖 `hw-cloudrobo-core` 和
`hw-cloudrobo-r2c[client]`，复用现有配置、OBS SDK、Zenoh transport 和 Protobuf。
本工具不负责 router 后续转发及云端接收确认。

## 运行模式

工具使用顶层 `run_mode` 选择业务行为，`zenoh.mode` 只表示通信模式：

| run_mode | OBS 上传 | Zenoh 上报 | 配置示例 |
|---|---|---|---|
| upload_and_report（默认） | 真实上传 | 每秒真实进度 | [ingestion.yaml](examples/ingestion.yaml) |
| upload_only | 真实上传 | 未启用 | [upload-only.yaml](examples/upload-only.yaml) |

旧配置未设置 `run_mode` 时保持默认行为。两种模式共享用户级单实例控制，
同一时刻仅运行一个。默认模式和仅上传模式共用工作目录及成功记录。

```bash
cloudrobo ingestion start --config upload-only.yaml
# 切换真实上传模式：先停止，再使用同一配置和工作目录启动
cloudrobo ingestion stop
cloudrobo ingestion start --config ingestion.yaml --run-mode upload_only
# 恢复上报，已经成功的批次不会重复上传
cloudrobo ingestion stop
cloudrobo ingestion start --config ingestion.yaml --run-mode upload_and_report
```

## 安装和启动

需要 Python >=3.10。可安装完整 Client 或单独安装回流包：

```bash
pip install hw-cloudrobo-ingestion
# 可选的格式检查（不安装整个 LeRobot）
pip install 'hw-cloudrobo-ingestion[lerobot-validation]'

cloudrobo ingestion start --config ingestion.yaml
cloudrobo ingestion status
cloudrobo ingestion status --json
cloudrobo ingestion stop
```

开发环境安装后，需执行 R2C 自带的 Protobuf 编译命令生成新消息；正常 wheel
构建自动编译：

```bash
python -c 'from cloudrobo_r2c._build_commands import compile_protobuf_files; compile_protobuf_files()'
```

## 配置

默认模式下源目录、工作目录、OBS URI、`task_type` 和 `collect_method` 必填。
只上传模式允许省略两个分类字段，并忽略不使用的 Zenoh 配置；提供分类时仍校验。
分类必须为长度 1～64 的字符串（按 Unicode 字符计数）。
配置中的源/工作相对目录按原配置文件位置解释；CLI 路径按当前工作目录解释。
Windows 推荐使用 `C:/data/source` 或单引号路径。
OBS 使用现有 `cloudrobo config`、`HUAWEI_CLOUD_AK`、`HUAWEI_CLOUD_SK`、
`CLOUDROBO_OBS_ENDPOINT` 等配置，不将密钥写入回流数据库。

Zenoh 默认 client 模式连接 `tcp/127.0.0.1:7447`，source=`r2c/local`，
topic=`r2c/local/data_ingestion/upload_status`。支持 R2C ClientConfig 的 TLS、
connect 和 publisher 选项；身份不允许包含 `/` 或通配符。默认 BACKGROUND/DROP，
因此发布调用完成不表示 router 或云端已经接收，拥塞期间也可能丢样本。

### CLI 覆盖

`--config` 仍必需。优先级为 **显式 CLI 参数 > 配置文件 > 默认值**。
未传入的参数不覆盖 YAML；显式关闭和零值仍生效。后台使用合并后的配置，
原 YAML 不会改写。临时有效配置文件权限受限，启动成功或失败后删除。

| 配置 | CLI |
|---|---|
| 运行模式 | `--run-mode` |
| 路径 | `--source-dir`、`--work-dir`、`--obs-path` |
| 分类 | `--task-type`、`--collect-method` |
| 每批文件数上限 | `--max-files-per-batch` |
| 校验开关 | `--validate-lerobot` / `--no-validate-lerobot` |
| Zenoh 通信模式 | `--zenoh-mode` |
| 地址列表 | 可重复的 `--zenoh-endpoint` |
| 身份 | `--zenoh-project-id`、`--zenoh-device-id`、`--zenoh-client-id` |

CLI 地址整体替换 `zenoh.endpoints` 和实际的 `zenoh.connect.endpoints`，
保留 connect 超时等其他项。TLS、publisher 等高级设置只通过 YAML 配置。

## 客户目录约定

```text
source/
  __UPLOAD_READY__         # 忽略，不上传 source 本身
  batch-001/
    __UPLOAD_READY__       # 所有写入关闭后，最后创建的空文件
    meta/...
    data/...
    videos/...
    nested/
      __UPLOAD_READY__     # 忽略，不单独触发任务，也不上传标记
      data.parquet         # 随 batch-001 上传
  unmarked/
    nested/
      __UPLOAD_READY__     # 不触发上传，unmarked 缺少直接标记
```

每 2 秒仅检查源目录的直接子目录，只有直接子目录中的空标记才触发整个子目录上传。
源目录根部和更深层的标记不触发任务，也不会导致嵌套批次报错。
有效批次内部仍递归收集普通数据文件，所有层级的标记均不复制、不上传、不计数。
标记创建后目录必须保持不变，批次路径不可复用。
LeRobot V3 应在 `finalize()` 完成后于整个数据集根目录创建标记，不要仅标记 data
或 videos 子目录。无标记目录不会上传。

每批默认最多 **100** 个普通文件，通过顶层 `max_files_per_batch` 配置，或使用
`--max-files-per-batch` 覆盖。该值必须为正整数，不接受布尔值、字符串或小数，
没有固定的 5,000 上限。例如 `max_files_per_batch: 5000` 可允许一批 5,000 个文件。
此限制适用于两个真实上传模式。
初次扫描、重试和复制复查均使用当前有效上限，超限拒绝整个批次，不复制、不上传。
源目录只读，禁止
源目录与工作目录重叠、符号链接或 Windows junction。复制完成且可选校验通过后
才开始上传，OBS 保留相对源目录结构，标记不上传，本地删除不会删除 OBS 对象。

## 恢复和结果

真实上传模式的工作目录包含 `mode.json`、`batches.sqlite3`、`staging/`、
`checkpoints/` 和轮转日志。`mode.json` 标识真实上传工作目录，两种真实模式可以复用。
当前功能尚未上线，本次不兼容旧工作目录结构；旧测试目录需要更换为新的空目录，
不提供迁移脚本。新结构建立后可持续共享上传历史。
数据库保存批次记录及必要的一次性最终补报结果，不保存逐秒快照或逐文件恢复进度；
首次入队时间和批次 UUID 持久化。
文件 UUIDv5 从批次 UUID 和相对路径派生，重试/重启稳定。

### 上传失败与重试的展示

一次批次处理失败时，已完成文件保持 `done`，其余文件统一显示 `failed`，
包括因复制、OBS 初始化或前面的文件失败而未能开始上传的文件；这里的失败
表示“本轮批次处理未完成”，不表示该文件曾经实际发起过 OBS 请求。
子进程启动失败、异常退出、读取清单失败等也会更新已知文件清单的失败状态。
尚未建立合法清单（如首次发现就超限）的错误仍仅在本地状态和日志展示。

等待重试、重新复制和初始化期间保留失败状态，不重置为排队。每个文件真正
开始新一轮 OBS 上传时才变为 `uploading`，字节进度、开始时间、速率及 ETA
重新计算，成功后变为 `done`。整批重传时，上轮已完成的文件也会再次进入上传中。
迟到的 OBS 回调不会将已完成或已失败状态改回上传中。

重启不还原逐文件进度，不新增周期快照：根据批次尝试次数及错误记录，
将未完成旧批次的文件重建为失败／待重试；从未开始处理的新批次仍为排队。
恢复后的开始时间、速率、ETA 清零，批次 UUID、文件 UUID 和首次入队时间保持不变。
已记录整批成功的任务继续跳过。

失败会触发立即发布，并在退避等待期间按秒持续上报。子进程异常退出按失败
进入 5～300 秒退避，避免立即重试覆盖失败状态；用户主动停止仍作为中断处理。
上报通道故障不改变真实上传结果，恢复后补报最新状态。工具不累积失败历史，
断连期间被后续成功覆盖的中间失败不保证重放，也不承诺云端已接收。

成功或失败都清理数据副本。复制/上传错误按 5～300 秒退避重试，每次重新复制
并整批重传，可能覆盖之前上传成功的对象；OBS 上可能存在未完成批次的部分文件。
拒绝类错误本次运行不重复尝试，修正后下次启动重新检查。成功批次不会重传。
工作目录只绑定源目录，不绑定 OBS 地址。更换源目录仍需要新工作目录。

### 模式切换与补报

两种真实模式共用成功记录，切换不会重新上传已成功的批次；未完成批次保持 UUID、
首次入队时间并继续整批重试。默认模式建立的批次标记为已纳入上报，切到仅上传时
暂停发布；这些批次在仅上传期间成功后保存一次最终结果 JSON，切回默认时补报成功。
因此副本清理或已完成源批次被移走后，仍能补齐结果。

仅上传期间新建且已完成的批次不会在以后补报。尚未完成的批次切回默认时开始上报，
缺失的分类从当前配置补齐并固定，已有分类保持不变。最终成功结果替代过时的失败消息；
本地发布调用完成后清除待发布结果，上传成功记录长期保留。切换模式沿用相同的
Zenoh 身份和 topic，不提供跨身份迁移。

仅上传模式不初始化 Zenoh、不导入 Protobuf、不生成 Protobuf 上报缓存，本地进度仍可查询；
只为原有上报任务保留必要的最终结果 JSON。

### 修改 OBS 地址

修改配置或使用 `--obs-path` 后重新启动：已成功批次保持原实际地址，不自动重传，
其待补报结果也保持原地址；未完成批次更新为新目标并整批重试，新批次使用新目标。
批次身份和首次入队时间保持不变，日志记录目标变化及受影响批次数量。
旧目标已上传的对象不会自动删除；旧 checkpoint 按原 bucket 和对象路径清理，
清理未确认时暂停该批次，不会将旧分段上传复用到新地址。

`stop` 尽快停止当前子进程，超过 5 秒终止；未完成批次下次启动重新复制。
OBS SDK checkpoint 独立于副本，确认分段上传中止后删除；强制终止时可能有远端
残留分段，建议桶设置清理未完成 multipart 的生命周期策略作为兜底。
清理失败不丢记录，暂停该批次并继续其他任务。

### 错误提示与日志

`status` 分别展示 **OBS 上传异常、进度上报异常、本地处理异常**，包括中文原因、
失败阶段、文件或地址、排查建议及错误 ID。上传成功与进度上报成功分别判断，
上报失败不会改写 OBS 上传结果。对应操作恢复成功后清除当前异常，日志记录恢复事件。
仅上传模式始终显示“上报未启用”。

例如 endpoint 无法解析时：

```text
OBS 上传异常 [OBS_DNS_FAILED]
阶段：上传 OBS 文件
原因：OBS 上传失败：无法解析服务器地址
endpoint：https://obs.example.invalid
file：data/chunk-000/file-000.parquet
建议：请检查服务器地址、区域配置和本机 DNS。
错误 ID：……
```

router 无法连接时显示“进度上报异常”，附带连接地址和 topic。进度消息构建失败
属于“本地处理异常 / 构建进度消息”，不会误报为 router 故障。未知原因保留原始诊断，
明确提示失败模块，不将所有 `OSError` 都解释成地址、网络或权限问题。

`status --json` 的顶层、`batch_errors` 和 `reporter` 使用统一 `error_info`：
`error_id`、`component`（obs/report/local）、`stage`、`code`、`message`、`hint`、
`time_ms`、`context`、`details`。Reporter 的 `errors` 可同时包含连接或消息构建错误。
批次诊断持久化，支持离线查询；致命退出尽可能保存 `state=failed`，与正常停止区分。
如果无法读取实例锁，显示“状态无法确定”及失败路径。错误 ID 用于关联状态与详细日志。

上传子进程返回失败后立即更新本地批次状态，并上报未完成文件的失败状态；等待清理
及重试期间保持失败，开始下一次实际上传后才恢复为上传中。子进程结束时的管道关闭
（包括 Windows `WinError 109`、EOF、断管或连接重置）不会覆盖已返回的 OBS 错误或
导致主进程退出。若管道关闭前未收到结果，则将未完成文件标为失败，待子进程退出后
记录子进程异常并安排重试，不将断管视为上传成功。

日志位于本次运行实际使用的 `work_dir/ingestion.log`（包括 CLI `--work-dir`
覆盖后的目录），单文件最大 5 MiB，保留 3 份轮转备份 `ingestion.log.1`～`.3`。
上传子进程通过 IPC 将日志交给主进程统一写入，记录批次 ID、失败阶段、文件、
本地副本和 OBS 目标。OBS 失败时记录可用的 HTTP 状态码、错误码、请求 ID；
异常保留原因链、脱敏消息、errno/Windows winerror 和调用栈位置，不记录凭证、签名
查询参数或栈局部变量。重复的上报异常首次立即记录，其后同类错误每 60 秒汇总次数；
错误变化与恢复立即记录。上报只保留当前诊断，不累积逐次重试历史。

Windows PowerShell 实时查看（将路径替换为实际工作目录）：

```powershell
cloudrobo ingestion status --json
Get-Content 'C:\data\ingestion-work\ingestion.log' -Tail 100 -Wait
```

例如 `OBS_ACCESS_DENIED` 加 HTTP 403 / `AccessDenied` 表示 OBS 访问被拒绝；
`stage=copying` 加 `winerror` 表示复制阶段的 Windows 文件操作错误。
功能尚未上线，本次调整不提供旧数据库迁移，开发验证请使用新的工作目录。

每秒发送每批全量节点，不限制为 8 KB。状态变更即时发送，断连期间只保留最新
快照及待发布最终消息，不积压逐秒历史。超限等无法生成完整节点清单的错误通过
本地 status/log 展示；现有协议没有错误原因字段。

## 可选 LeRobot 校验

`validation.enabled: false`（默认）无需 PyArrow 校验依赖；开启时缺依赖会在启动
报错，不静默跳过。第一版针对标准 Parquet/MP4 的 `codebase_version: v3.0`，
以 LeRobot v0.4.3 的标准布局为依据，要求 `meta/tasks.parquet`。
检查 info/stats、Parquet schema/footer、episode 引用/范围、视频容器/视频流，
不自动修复、不逐帧解码、不评判训练质量；不支持 Lance 和内嵌 image 特征。
校验副本失败不上传、清理副本、记录拒绝原因。关闭校验可上传其他普通数据格式。

## 开发自测

Mock 已移至独立前台开发入口，不属于客户运行模式，也不接受客户配置中的
`mock` 或 `run_mode: mock_report`。场景、参数和云端验收步骤见
[开发测试说明](../../tools/ingestion-mock/README.md)。

## 平台与验收

不依赖 Unix 信号完成生命周期控制，使用后台 subprocess、平台文件锁与认证的
loopback 控制通道。每个操作系统用户单实例，控制登记在 `~/.cloudrobo/ingestion`。
`CLOUDROBO_INGESTION_RUNTIME_DIR` 可指定独立登记位置（主要用于测试/受管理部署）；
不同登记目录会形成独立实例域。工作目录应由该用户独占，不要手动修改 SQLite。

跨平台自动化 CI 与真实 OBS/router 联调分开。性能验收需在目标机器人及客户实际
数据上测量：单个 5,000 文件批次运行 30 分钟，序列化 p95 ≤200 ms、发布间隔
p95 ≤1.2 秒、OBS 吞吐相对关闭上报下降 ≤5%。通过单元测试不代表满足这些指标。

可复现的序列化微基准（输出机器信息、消息大小和 p95；不发送数据）：

```bash
python packages/cloudrobo-ingestion/benchmarks/serialize.py --files 5000 --iterations 100
```
