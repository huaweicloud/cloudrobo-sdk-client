# cloudrobo-ingestion 使用手册

[模块概览](index.md) · [安装与配置](installation.md) · [CLI 命令](commands.md) · [使用示例](examples.md) · [使用手册](user-guide.md)

面向机器人本地操作和运维，说明当前实现的配置、数据约定、重试恢复、上报协议及排障规则。首次部署请从[安装与配置](installation.md)开始；配置值和命令以当前分支代码为准。

## 阅读导航

- [回流配置文件](#回流配置文件)
- [数据准备与就绪标记](#数据准备与就绪标记)
- [重试、重启和工作目录](#重试重启和工作目录)
- [进度上报及补报规则](#进度上报及补报规则)
- [可选 LeRobot V3 格式校验](#可选-lerobot-v3-格式校验)
- [故障排查](#故障排查)
- [用户约束与交付检查](#用户约束与交付检查)

## 回流配置文件

### 默认模式完整示例

创建 UTF-8 编码的 `ingestion.yaml`：

```yaml
run_mode: upload_and_report
source_dir: ./sample
work_dir: ./ingestion-work
obs_path: obs://customer-bucket/robot-data/
max_files_per_batch: 50

task_type: 超市取货
collect_method: 遥操接管

validation:
  enabled: false
  format: lerobot_v3

zenoh:
  mode: client
  endpoint_role: connect
  endpoints: ["tcp/127.0.0.1:7447"]
  project_id: r2c
  device_id: local
  client_id: cloudrobo-ingestion
```

Windows 绝对路径可写成 `D:/robot-data/sample`，或使用单引号 `'D:\robot-data\sample'`；避免在 YAML 双引号字符串中直接使用未转义的反斜杠。

### 仅上传最小配置

```yaml
run_mode: upload_only
source_dir: ./sample
work_dir: ./ingestion-work
obs_path: obs://customer-bucket/robot-data/
max_files_per_batch: 50
```

此模式可以省略分类、Zenoh 和校验配置，默认关闭校验。也可以使用默认模式的完整配置，仅通过 CLI 覆盖 `run_mode`。

### 字段说明

| 字段 | 必填 / 默认值 | 说明 |
|---|---|---|
| `run_mode` | 默认 `upload_and_report` | 仅接受本手册列出的两种模式 |
| `source_dir` | 两种模式必填 | 已存在的源目录；其直接子目录各自构成批次 |
| `work_dir` | 两种模式必填 | 工作目录，可自动创建；绑定源目录并保存历史 |
| `obs_path` | 两种模式必填 | `obs://bucket/prefix/`；可使用桶根路径，不接受查询参数、片段、凭证及 `.` / `..` 路径段 |
| `max_files_per_batch` | 默认 `50` | 正整数；不接受字符串、小数或布尔值，没有固定的 5,000 硬上限 |
| `task_type` | 默认模式必填 | 字符串，1～64 个 Unicode 字符，自定义取值 |
| `collect_method` | 默认模式必填 | 同上；仅上传模式可省略两个分类字段，提供时仍按相同规则检查 |
| `validation.enabled` | 默认 `false` | YAML 布尔值；开启前安装校验依赖 |
| `validation.format` | 默认 `lerobot_v3` | 当前只接受这一格式名 |
| `zenoh.mode` | 默认 `client` | 接受 `client` 或 `peer`，与业务 `run_mode` 含义不同 |
| `zenoh.endpoint_role` | 默认 `connect` | 当前必须为 `connect` |
| `zenoh.endpoints` | 默认本机 7447 | 非空字符串列表，例如 `["tcp/127.0.0.1:7447"]` |
| `zenoh.project_id` | 默认 `r2c` | source 和 topic 中的项目标识 |
| `zenoh.device_id` | 默认 `local` | source 和 topic 中的设备标识，多机器人部署按实际身份配置 |
| `zenoh.client_id` | 默认 `cloudrobo-ingestion` | R2C client 配置标识 |

三个 Zenoh 身份字段均须为非空字符串，不能包含 `/`、`*`、`?`、`#`、`$`。仅上传模式忽略未使用的 Zenoh 配置。

配置文件中的相对 `source_dir` / `work_dir` **相对于 YAML 所在目录**解析，不是执行命令的目录。例如 `/opt/robot/ingestion.yaml` 中的 `./sample` 指向 `/opt/robot/sample`。

Zenoh 的 TLS、认证、`connect` 和 publisher 等高级项沿用 R2C `ClientConfig`；按 router 的实际要求配置在 YAML 中。若显式设置 `zenoh.connect.endpoints`，实际连接使用该地址列表，应注意与 `zenoh.endpoints` 保持一致。

## 数据准备与就绪标记

### 每个直接子目录是一批数据

假设 `source_dir: ./sample`：

```text
sample/
  __UPLOAD_READY__                 # 不生效，不会把 sample 本身作为批次
  abc/
    __UPLOAD_READY__               # 生效：上传整个 abc
    meta/info.json
    data/chunk-000/file-000.parquet
    videos/camera/file-000.mp4
    def/
      __UPLOAD_READY__             # 不单独触发；这个标记也不会上传
      extra.txt                    # 随 abc 上传
  waiting/
    def/
      __UPLOAD_READY__             # 不生效：waiting 没有直接标记
```

工具每轮只发现 `source_dir` 下一层的批次目录；**进入有效批次后递归收集全部普通数据文件**。更深层的同名标记不会创建另一批次，所有层级的 `__UPLOAD_READY__` 都不复制、不上传、不计入文件数。

目标扫描间隔为 2 秒，包含启动时已经存在的就绪目录。大目录的一轮扫描可能超过 2 秒，不会重叠扫描；文件清单最多预取当前批次和一个候选批次，排队不会让所有文件节点同时出现在云端。

源目录直接放置的其他文件也不会作为独立上传任务。隐藏文件没有排除规则，例如批次中的 `.DS_Store`、临时文件会计数并上传；标记前请清理不需要交付的文件。

### 标记文件要求与创建时机

标记名必须准确为 `__UPLOAD_READY__`，且必须是**零字节的普通文件**。目录、链接、非空文本文件均不符合要求。

正确的数据生产顺序：

1. 创建一个新的、从未作为历史批次使用过的子目录。
2. 写入全部数据和元数据，完成数据集的收尾操作，关闭文件句柄。
3. 确认文件数没有超过配置上限。
4. 最后创建空标记。
5. 标记后不再增加、修改、重命名或删除该批次内的文件，保留源数据直到整批上传成功。

LeRobot 数据应在数据集完成 `finalize()` 后，在整个数据集根目录创建标记；不要分别对 `data/` 或 `videos/` 创建上传批次。

Linux / macOS：

```bash
# 前提：abc 数据已写完，且还没有标记文件
touch sample/abc/__UPLOAD_READY__
```

Windows PowerShell：

```powershell
# 前提相同；不要用会写入换行或 BOM 的命令创建标记
New-Item -ItemType File -Path .\sample\abc\__UPLOAD_READY__
```

`touch` 不会清空已存在的非空文件。如果标记原先错误地写入了内容，修正为空文件；批次已被拒绝时还需停止并重启工具。

### 文件数、链接和目录一致性

- 默认每批 **50 个**普通文件，包含 `meta/`、`data/`、`videos/` 等所有子目录的文件；目录本身不计数，标记不计数。
- 例如一批有 4 个元数据文件、2 个 Parquet、45 个视频，共 51 个文件，默认会拒绝整批；需要显式调整上限。
- 文件数上限在两种模式、校验开关关闭时同样生效。超限时不复制、不上传；提高上限会增大全量进度消息，应结合实际批次测量。
- 不允许用符号链接或 Windows junction/reparse point 连接批次数据。直接子目录是链接时不会被发现为批次；有效批次内部发现链接会拒绝。
- 普通目录内的非普通文件不支持。源目录与工作目录本身也不得是链接/junction，不能相同或互相包含。
- 复制前后会检查清单及文件属性变化。发现变化时拒绝该批次；这不能替代“标记后保持不变”的生产约定。

### OBS 路径映射

若相关配置片段为：

```yaml
source_dir: ./sample
obs_path: obs://customer-bucket/robot-data/
```

则映射为：

| 本地文件 | OBS 对象 URI |
|---|---|
| `sample/abc/data/chunk-000/file-000.parquet` | `obs://customer-bucket/robot-data/abc/data/chunk-000/file-000.parquet` |
| `sample/abc/videos/camera/file-000.mp4` | `obs://customer-bucket/robot-data/abc/videos/camera/file-000.mp4` |

目标包含批次目录名，不自动附加 `project_id`、`device_id` 或批次 UUID。多机器人指向同一个 OBS 前缀且使用相同批次目录名时，对象路径可能冲突；应由配置为机器人分配不同前缀，或确保批次路径全局唯一。不同机器上的文件 UUID 不承担 OBS 对象路径隔离职责。

没有目录删除同步、文件过滤配置或远端事务：本地删除不会删除 OBS 对象，空子目录不单独创建目录对象，上传失败时 OBS 可能已经存在这一批的部分文件。已有同路径对象可能在重传时被覆盖，具体保留历史版本行为取决于桶设置。

## 重试、重启和工作目录

### 处理与重试策略

真实处理按批次、文件串行执行；必须整批复制完、可选校验通过后才上传。下一批上传不等待上一批结果补发。

| 情况 | 行为 | 操作建议 |
|---|---|---|
| OBS 请求或普通复制错误 | 自动退避重试，通常 5、10、20…秒，最长 300 秒 | 排查原因；每次重新复制并整批重传 |
| 超限、无效标记、格式错误、源文件变化 | 拒绝该批，本次运行不重复处理 | 修正后 `stop` → `start` |
| 单个子目录发现时权限/读取异常 | 展示扫描诊断，继续发现其他目录；临时异常后续继续尝试 | 修正访问问题，观察恢复 |
| 源目录整体不可访问 | 暂停发现及启动新上传，已经运行的上传、清理及上报继续 | 恢复挂载或权限，观察扫描恢复 |
| 已登记批次建立清单时出现读取错误 | 记录批次失败并安排重新检查 | 根据错误阶段排查；拒绝类仍须重启 |
| 本地副本或远端分段清理失败 | 保留清理记录，暂停受影响批次，继续其他任务 | 解决占用、权限或网络问题，不要删除恢复记录 |
| Router 不可用 | OBS 上传继续；保留最新状态和未过期结果，尝试恢复连接 | 恢复 router，或临时切为仅上传 |
| 主动停止/崩溃/断电 | 重启清理残留，未完成批次整批重试 | 保留源数据和工作目录，以原配置启动 |

这里的整批重试不是从云端显示的百分比继续：曾经完成的文件也可能重传同路径对象。OBS SDK 的 checkpoint 用于分段上传管理，不表示本工具承诺跨次批次处理的逐文件断点续传。

### 失败文件的状态含义

本轮批次失败时，已完成文件保持 `done`，其余已知文件统一标记 `failed`，包括还没轮到上传的文件。该状态表示“这一轮批次未完成”，不等于每个文件都向 OBS 发起过失败请求。

等待重试、重新复制及初始化时保留失败状态；每个文件真正开始新尝试才恢复为 `uploading`，进度和开始时间重新计算。异常恢复后 UUID 不变，云端可能看到同一文件从失败变成上传中再变成成功。

未能建立合法文件清单的错误只在本地状态/日志显示，云端不一定有对应失败节点。

### 工作目录与历史

```text
ingestion-work/
  mode.json           # 工作目录用途标记
  batches.sqlite3     # 批次、目标、首次入队时间、成功历史和待发结果
  staging/            # 临时完整副本，处理结束后清理
  checkpoints/        # OBS 分段上传恢复/清理记录
  ingestion.log       # 轮转日志
```

运行时 SQLite 还可能有 `-wal` / `-shm` 文件；启动过程可能短暂出现临时配置目录，正常启动流程结束后清理。

成功记录长期保留，批次按相对源目录路径识别。**同一路径即使换成另一份内容，仍可能被作为已完成历史跳过**；不支持用删除标记、替换文件等方式“重新提交”已成功路径。

工作目录绑定解析后的 `source_dir`，不绑定两种真实模式或 OBS 地址。更换源目录必须使用新的工作目录；仅改变运行模式应继续使用原目录。旧用途标记不兼容或标记缺失而数据库存在时会报错，不要通过手工伪造标记绕过检查。

不要手工修改 SQLite、删除 `mode.json`、checkpoint 或复制中的 staging。更换/清空工作目录会失去去重历史，源目录中的旧批次可能全部再次上传。迁移或备份应先停止工具，再完整保留工作目录；换机器后源目录路径与权限也需重新核对。

首次入队时间、批次 UUID 持久化；文件 UUID 由批次 UUID 和相对路径派生，同一批次重试/重启稳定。没有逐秒历史、逐文件断点进度持久化。不同工作目录创建的批次不共享身份或成功记录。

### 停止与升级

`stop` 停止新任务并请求上传子进程退出，等待约 5 秒后必要时终止子进程；控制命令还会等待后台清理，默认等待上限为 15 秒，超时可能提示仍在清理。此时先查状态和日志，避免立即重复启动。

停止不等待所有最终消息补发完成。不要把 `stop` 当作“云端全部收到”的确认。

升级流程：停止 → 确认进程已退出 → 备份工作目录和配置 → 更新代码/安装包 → 检查命令与依赖 → 用原工作目录启动。工作目录属于运行状态，不能依靠重新创建它修复普通网络错误。

## 进度上报及补报规则

### 地址与消息

默认连接模式为 `client`，endpoint-role 为 `connect`，地址 `tcp/127.0.0.1:7447`。消息类型为 `r2c_sdk.DataUploadProgress`，身份和 topic 为：

```text
source = {project_id}/{device_id}
topic  = {project_id}/{device_id}/data_ingestion/upload_status

默认 source = r2c/local
默认 topic  = r2c/local/data_ingestion/upload_status
```

每条消息含一个批次的完整文件节点。批次级字段为：

| 字段 | 含义 |
|---|---|
| `source` | `{project_id}/{device_id}`，默认 `r2c/local` |
| `timestamp` | 批次首次入队时间，毫秒时间戳；重启/重试不变 |
| `asset_path` | 该批次完整 OBS URI |
| `task_state` | 只有全批成功为 `1`；其他为 `0`，不能单靠它区分失败和进行中 |
| `nodes` | 该批次全部文件节点的列表 |

例如 `obs_path: obs://customer-bucket/path/to/obs/`，批次目录为 `source_dir/batch001/`，则 `asset_path` 为 `obs://customer-bucket/path/to/obs/batch001/`，包含桶名、批次目录和末尾的 `/`。

每个 `nodes` 元素包含以下字段：

| 字段 | 含义 |
|---|---|
| `file_id` | 从批次 UUID 和文件相对路径派生的 UUID，重启/重试稳定 |
| `task_name` | 文件 basename，不含目录；不同子目录同名文件应通过 `file_id` 区分 |
| `data_type` | 视频 0、图像 1、点云 2、文本 3、其他 4，按扩展名判断；Parquet 为其他 |
| `size` | 文件大小，单位字节 |
| `enqueue_time` | 批次首次入队时间，毫秒时间戳；该批所有文件相同 |
| `start_time` | 当前尝试中该文件开始上传的毫秒时间戳；未开始为 0 |
| `progress` | 当前尝试的文件百分比；上传中最高 99.9，完成为 100 |
| `result_state` | `0` 排队、`1` 上传中、`2` 完成、`3` 失败 |
| `eta_ms` | 预计剩余毫秒数，无法估算时为 0 |
| `avg_bps` | 最近约 5 秒窗口的平均速率，单位字节/秒；无有效速率时为 0 |
| `task_type` | 用户配置的任务类型 |
| `collect_method` | 用户配置的采集方式 |

协议不包含具体错误原因、错误码、重试次数、扫描异常或进程状态；这些通过本地 `status --json` 和日志查询，不能指望云端仅凭当前消息显示完整故障诊断。

### 发送节奏和边界

- 只有当前批次从第一个文件实际开始上传后周期发送。扫描、排队、复制、格式校验和 OBS 初始化期间不上报该批的周期进度，因此“本地已经排队，云端暂时没有任务”可能是正常行为。
- 同批次发布启动时间至少相隔 1 秒，整个客户进程的任意发布至少相隔 200 ms。状态变化更新最新快照，不额外突发发送；整个进程有历史结果补发时可能一秒多条，不承诺墙钟上精确一秒一条。
- 批次成功后冻结相同结果，最多本地正常发布三次；后两次分别在上次调用返回后随机等待 1～5 秒，并满足发送节拍。
- 失败结果正常发送一次，不重复三次；发布异常会重试且不计数。开始下一次上传尝试时取消旧失败结果，后续成功替代失败。
- 空批次在关闭格式校验时可成功，直接发送结果；复制/校验/初始化失败时，只要有合法文件清单，也可发送失败结果。
- 未完成批次的最新状态、未过期待发结果会在连接恢复后尝试发布，不保存每秒历史，断网期间的所有中间状态不会逐条重放。

默认 publisher 优先级是 `BACKGROUND`，拥塞策略为 `DROP`；保持现有 transport 语义。最终补发提高送达概率，**没有云端 ACK，也不保证绝对恰好三次或云端必达**。发布返回后、次数落库前崩溃，恢复后可能额外重复。消息没有 8 KB 上限，也没有分片或增量协议。

### 统一的 24 小时补报期限

所有成功、失败及模式切换留下的结果，期限都是：

```text
截止时间 = 批次首次入队时间 + 24 小时
```

到期后，无论此前已正常发布 0、1、2 次，都停止剩余结果发送并清除消息缓存，保留上传历史和原有失败重试安排。不是从上传完成、恢复网络或重新启动时计时。

例如某批周一 10:00 首次入队，切到仅上传后周二 09:00 完成：周二 10:00 前切回默认模式仍有补报机会；10:00 后才切回则不补报。刚上传成功也不会重置期限。

期限不禁止继续上传或发送尚在上传批次的实时进度，但首次入队已满 24 小时的批次不再安排最终结果发送。已开始的发布调用无法撤回。服务器和本机时间应保持正确，避免身份签名与期限判断异常。

## 可选 LeRobot V3 格式校验

默认关闭；关闭时可上传其他普通文件格式，不要求额外安装 LeRobot 校验 extra。R2C client 自身的依赖仍会按安装声明安装。

开启前安装校验 extra，再修改配置或使用 CLI：

```bash
# 源码安装环境：
python -m pip install -e "packages/cloudrobo-ingestion[lerobot-validation]"
# 已发布包环境使用：python -m pip install "hw-cloudrobo-ingestion[lerobot-validation]"

cloudrobo ingestion start --config ingestion.yaml --validate-lerobot
```

也可在 YAML 中设置：

```yaml
validation:
  enabled: true
  format: lerobot_v3
```

当前检查标准 Parquet/MP4 形式的 **`codebase_version: v3.0`**，也支持无视频数据，不安装整个 LeRobot。需要的典型结构如下，具体分片和视频位置以元数据引用为准：

```text
sample/dataset-001/
  __UPLOAD_READY__
  meta/
    info.json
    stats.json
    tasks.parquet
    episodes/chunk-000/file-000.parquet
  data/chunk-000/file-000.parquet
  videos/<camera-key>/chunk-000/file-000.mp4
```

校验包括：

- `meta/info.json`、`meta/stats.json` 可读及基本元数据约束；版本、总数、fps、特征声明有效。
- `meta/tasks.parquet`、数据分片、episode 分片可打开，Parquet footer/schema 及必要列/类型符合要求。
- episode 索引、数量、数据文件引用、帧索引及范围一致，不出现重叠或空洞。
- 引用的视频可打开，包含视频流，引用时间范围有效，已知时长足以覆盖引用。

这不是完整的训练质量或所有媒体内容校验：不自动修复、不逐帧解码，也不保证所有传感器值正确。当前不支持 Lance 或内嵌 `dtype: image` 特征。

校验在**工作副本上、OBS 上传前**执行，可能读取部分 Parquet 索引数据并增加准备耗时。缺依赖时启动报错，不静默跳过；空批次校验失败；格式不符会拒绝整批，修复后重启。不要用[首次运行示例](examples.md#首次运行示例)的普通文本测试样本验证开启后的 LeRobot 校验链路。

## 故障排查

### 命令或进程问题

| 现象 | 排查与处理 |
|---|---|
| 找不到 `cloudrobo` 或没有 `ingestion` 命令 | 检查是否激活安装时的环境，执行 `python -m pip show hw-cloudrobo-ingestion`；Windows 可直接执行虚拟环境中的 `cloudrobo.exe` |
| 默认模式提示缺少 `data_upload_progress_pb2` | 按[安装检查](installation.md#检查安装)补齐源码环境的 Protobuf 生成文件 |
| 提示已有回流进程 | 使用同一用户执行 `status`、`stop`，不要手工删除运行锁绕过单实例 |
| 停止后看起来还“connected” | 先看 `running` 和快照更新时间，旧连接字段不是实时云端检测 |
| 修改 YAML 没生效 | 确认启动所用文件及 CLI 覆盖，停止再启动；不支持热加载 |
| `state=unknown` / 状态无法确定 | 检查当前用户对运行登记目录和锁文件的访问，不要将它当作明确的“已停止” |

运行登记默认位于 `~/.cloudrobo/ingestion`，保存控制、锁和最近状态，区别于 `work_dir`。受管理部署可用 `CLOUDROBO_INGESTION_RUNTIME_DIR` 改变登记位置，但 `start/status/stop` 必须使用同一设置；普通用户无需设置它。不要用多个登记目录绕过单实例后并发操作同一工作目录。

### 本地有数据但不上传

依次检查：

1. `running` 是否为 `true`，实际 `source_dir` 是否是预期路径。
2. 数据是否位于其**直接子目录**，有效位置是否有空的普通 `__UPLOAD_READY__` 文件。
3. 路径是否已经成功使用过；已完成路径不会因内容变化重传。
4. `scan_errors`、`batch_errors` 是否有超限、链接、访问或格式错误。
5. 若显示 `rejected`，修正问题后停止并重启；不会在当前运行中自动复查该拒绝批次。
6. 是否有前面的批次正在复制/校验/上传或等待清理；当前工具串行上传，不支持并发数配置。

### OBS、上报和本地错误

| 诊断例子 | 优先检查 |
|---|---|
| `OBS_DNS_FAILED` | `CLOUDROBO_OBS_ENDPOINT`、core 区域、DNS；不是只检查 `obs_path` |
| `OBS_BUCKET_NOT_FOUND` | 桶名、桶是否存在、endpoint 所在区域 |
| `OBS_AUTH_FAILED` | AK/SK、区域和本机时间；环境变量是否覆盖了已保存凭证 |
| `OBS_ACCESS_DENIED` / HTTP 403 | 凭证授权、桶策略、对象前缀及分段上传/中止权限 |
| OBS timeout / connection / TLS 错误 | 实际服务地址、网络、端口、防火墙和证书；以具体原因链为准 |
| `进度上报异常` / connect、publish | Router 是否运行、地址与端口、实际 connect endpoints、认证/TLS 和 topic 权限 |
| `本地处理异常` / copying、upload_local_io | 文件是否被移动或占用、读写权限、磁盘空间、Windows winerror |
| `本地处理异常` / validating | 数据格式与版本；安装依赖后再校验，或按需求显式关闭校验 |
| `本地处理异常` / database、cleanup | 工作目录权限、占用和磁盘状态；保留历史及 checkpoint 后再排查 |

OBS 出错不一定是网络错误，上报出错也不会把真实上传结果改成失败。不要仅依据 Python 异常类名判断模块，应结合 `component`、`stage`、错误 ID 与日志原因链。

### OBS 已有文件，云端仍显示上传中

先检查该批是否已 `done`，再检查 `reporter.enabled`、当前上报诊断及待发结果数量。可能原因包括：仍在仅上传模式、router/后续云端链路异常、消息拥塞丢弃、最终结果已过期，或云端尚未处理结果。

成功结果有三次发送机会，但本地发布完成不等于云端入库。仅上传模式新建并完成的任务本来就不补报，超过首次入队 24 小时的结果也不会恢复发送。没有“强制重新发送历史结果”的客户命令，不要为了刷新云端删除上传数据库。

### 提交排障信息

保留以下信息，隐藏凭证及客户敏感数据：

- 操作系统、Python 版本和 `pip show` 的包版本；源码环境另附 Git 提交号。
- 实际启动命令和脱敏 YAML，说明是否使用 CLI 覆盖或 endpoint 环境变量。
- `cloudrobo ingestion status --json` 输出、错误 ID 及其前后的日志；必要时包含轮转日志。
- 批次首次入队时间、路径、文件数、总大小、失败阶段、是否重试或切换过模式。
- OBS 是否实际完成整批，以及云端停留状态/百分比和持续时间。

不要附 AK/SK、认证 token、签名 URL 或完整私密数据集。日志有脱敏处理，但分享前仍应检查路径、文件名和业务元数据。

## 用户约束与交付检查

### 使用约束

- 每批一个全新直接子目录，数据写完且关闭句柄后，最后创建空标记；标记后保持不可变，不复用成功路径。
- 默认每批最多 50 个文件，按全部递归普通文件计数；更大的数据集显式配置上限。
- 源目录保持只读供工具使用，工作目录与其分离且由运行用户独占；不使用链接/junction 或手工修改历史记录。
- 上传完成前保留源数据；工具不会删除源数据，不同步删除 OBS 对象，也不保证失败批次在 OBS 中完全不可见。
- 两种真实模式切换保留同一工作目录；只为切模式换目录会丢失共用历史的效果。
- 多机器人自行隔离 OBS 前缀及上报身份；对象路径没有内置设备命名空间。
- 批次串行、文件串行，不提供并发/带宽控制、过滤规则、热加载、开机自启或云端 ACK 配置。
- 格式校验为可选结构校验，不等同于完整数据质量验收；关闭时普通文件也能上传。
- 上报允许重复和丢失，云端应按稳定 file_id 处理更新；成功三次、失败一次仅按本地调用计数。
- 最终补报期限按首次入队起 24 小时计算；若业务必须保证每次最终状态必达，需要另行设计接收确认链路。

工作副本文件在 POSIX 上以 `0600` 创建、目录以 `0700` 创建；Windows 副本目录创建时设置受保护 ACL，允许所有者、系统及管理员访问。源文件权限/ACL 不被改写。工作目录及运行登记目录仍应放在运行用户可信、可管理的位置。

### 交付前检查表

- [ ] 在计划使用的系统及 Python 环境完成安装，`ingestion start --help` 正常。
- [ ] OBS 桶、endpoint、区域、凭证和分段操作权限已通过一个真实小批次验证。
- [ ] 默认模式已配置实际机器人身份，本地 router 可用，云端收到过该身份的最终成功状态。
- [ ] 数据生产程序只在数据完成后创建空标记，数据集的直接子目录层级正确。
- [ ] 用实际客户批次确认文件数上限、工作磁盘容量及校验开关。
- [ ] 了解日志位置，能够从 JSON 状态和错误 ID 定位 OBS、上报及本地异常。
- [ ] 已验证停止/重启以及默认→仅上传→默认的操作，工作目录始终保留。
- [ ] 已明确 24 小时补报期限，以及本地发布成功和云端收到之间的边界。
- [ ] 需要时由运维独立配置开机启动及 OBS 未完成分段上传的生命周期清理策略；这些不由本工具自动设置。

## 实现与示例索引

维护本手册时，优先核对下列源码，避免把旧讨论方案写成当前行为：

- [配置与校验规则](../src/cloudrobo_ingestion/config.py)、[CLI 参数](../src/cloudrobo_ingestion/cli.py)。
- [扫描器](../src/cloudrobo_ingestion/scanner.py)、[文件清单与复制](../src/cloudrobo_ingestion/filesystem.py)、[上传子进程](../src/cloudrobo_ingestion/worker.py)。
- [调度与状态](../src/cloudrobo_ingestion/daemon.py)、[历史及补报存储](../src/cloudrobo_ingestion/store.py)、[进度发布](../src/cloudrobo_ingestion/progress.py)。
- [生命周期管理](../src/cloudrobo_ingestion/lifecycle.py)、[LeRobot 校验](../src/cloudrobo_ingestion/validation.py)。
- [core 凭证配置](../../cloudrobo-core/src/cloudrobo_core/sdk/config.py)、[OBS 客户端](../../cloudrobo-core/src/cloudrobo_core/sdk/obs_client.py)。
- [默认模式配置](../examples/ingestion.yaml)、[仅上传配置](../examples/upload-only.yaml)、[独立 Mock 开发工具](../../../tools/ingestion-mock/README.md)。
