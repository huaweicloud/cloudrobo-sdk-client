# 云端进度功能开发自测

独立的仓库内开发包，不随客户回流包或聚合包安装，也不参与 PyPI 发布。客户 `ingestion start`
只支持默认与仅上传模式。旧 Mock 配置和 `--mock-*` 参数已移除，不提供兼容。

从仓库根目录安装开发环境，确保工具与共享 SDK 来自同一个 checkout/提交：

```bash
python -m pip install -r requirements-dev-editable.txt
python -m cloudrobo_ingestion_mock
python -m cloudrobo_ingestion_mock --help
python -m cloudrobo_ingestion_mock --scenario functional-suite --output results.jsonl
```

默认单轮 `overview`，Ctrl+C 停止（退出码 130）。不依赖 OBS 凭证、不扫描客户目录、
不创建副本、SQLite、checkpoint、工作目录或后台实例状态；可以与真实回流进程并行运行。
终端持续输出 JSON 事件，只有显式 `--output` 才写文件，该文件会覆盖现有内容。
终端 JSON 使用 Unicode 转义，兼容 Windows 非 UTF-8 输出编码；JSONL 文件使用 UTF-8，
直接保留中文。两种输出经 JSON 解析后内容一致。
所有模拟状态仅存内存，重启生成新身份。无环境变量隐式切换开关。

## 连接与参数

默认 source 为 `r2c/local`，topic 为 `r2c/local/data_ingestion/upload_status`，
client 模式连接 `tcp/127.0.0.1:7447`。启动事件显示实际身份、topic 和连接地址。
协议没有 Mock 标记，下游按普通消息处理；可使用 CLI 显式指定测试身份。

```bash
python -m cloudrobo_ingestion_mock --scenario retry \
  --endpoint tcp/127.0.0.1:7447 --project-id r2c --device-id local
```

可用 `--zenoh-config` 读取 **R2C 格式连接 YAML**，不是回流工具的 `ingestion.yaml`。
TLS、认证、publisher 等高级选项沿用 R2C 配置规则。CLI 身份覆盖文件字段，
可重复 `--endpoint` 整体覆盖顶层及实际 connect 地址，保留 connect 超时和 TLS 等选项。
该开发入口只连接 router，不监听地址。相对 TLS/认证路径沿用 R2C 规则，按启动目录解释。

```yaml
# zenoh-test.yaml
project_id: r2c
device_id: local
client_id: cloudrobo-ingestion-mock
mode: client
endpoint_role: connect
endpoints: [tcp/127.0.0.1:7447]
```

| 参数 | 默认值与作用 |
|---|---|
| `--scenario` | `overview`，选择下表中的场景 |
| `--file-count` | 100，仅 `stress` 有效，整数 1～5,000 |
| `--time-scale` | 1，有限正数；加速正常上传阶段，最短一秒；排队、失败仍保持三秒，停滞六秒 |
| `--repeat` | 1；0 持续循环，其他非负整数指定轮数 |
| `--zenoh-config` | 可选 R2C 连接文件 |
| `--endpoint` | 可重复，覆盖连接地址 |
| `--project-id` / `--device-id` / `--client-id` | 覆盖身份 |
| `--output` | 可选 JSONL 事件文件，默认只输出终端 |

压力和协议异常不受 `--time-scale` 影响。内置时间线及数据集合固定，无随机故障；
不支持自定义场景文件。每轮批次 UUID、文件 UUID 和模拟 OBS 子路径重新生成，
同一轮的重试或乱序保持任务身份及入队时间。绝对时间取当前时间。

## 场景与云端观察点

| 场景 | 发送行为与人工检查内容 |
|---|---|
| `overview` | 三个批次、五类文件、不同大小、空文件、中文及同名文件；查看分类聚合、排队/上传/完成、多批次切换 |
| `retry` | 完成/上传中/排队 → 完成/失败/失败 → 整批重试成功；相同 UUID 允许失败恢复，已完成文件重传时也重新上传 |
| `persistent-failure` | 两轮失败和退避，保留失败结果；`task_state=0`，场景执行成功不代表模拟上传成功 |
| `variable-speed` | 慢速、快速、六秒停滞、恢复；按实际时间计算滑动窗口速率及 ETA，停滞最终归零 |
| `stress` | 指定节点数，三秒排队后持续 30 秒每秒完整快照，再发布全批成功；观察页面和消费者性能 |
| `duplicate` | 相同排队快照两次，相同成功快照两次；观察重复计数与去重 |
| `out-of-order` | 约 10% → 80% → 旧 10% → 成功 → 旧 80% → 正常成功；观察旧进度晚到的处理策略 |
| `invalid-fields` | 可解析但违反业务约束：空身份/名称/资产地址、未知状态和数据类型、负数、进度越界、NaN/Infinity、分类为空/超长、批次成功而节点失败；每个样本后发送正常成功对照 |
| `malformed` | 截断 Protobuf 和错误格式字节，经本地确认无法解析；每条之后发送正常成功对照，观察异常隔离 |
| `functional-suite` | 顺序执行 overview、retry、persistent-failure、variable-speed，不含压力或协议异常 |

压力、重复、乱序及异常必须显式选择，不混入默认演示。分类示例包括超市取货/遥操接管、
仓库巡检/自主采集、物料搬运/遥操接管。每批分类一致，与真实进度模型保持一致。

```bash
python -m cloudrobo_ingestion_mock --scenario stress --file-count 100 --output stress-100.jsonl
python -m cloudrobo_ingestion_mock --scenario stress --file-count 1000 --output stress-1000.jsonl
python -m cloudrobo_ingestion_mock --scenario stress --file-count 5000 --output stress-5000.jsonl
python -m cloudrobo_ingestion_mock --scenario malformed --output malformed.jsonl
```

## 发布结果与联调

每条 `send` / `local_published` 包含执行轮次关联用的 run ID、消息序号、场景步骤、
批次 UUID、时间、消息字节数、SHA-256、构建耗时及预期观察点，后者另含本地发布耗时。
执行结束汇总消息数、字节数和平均耗时。JSONL 不记录 TLS 密钥或完整异常业务值。

复用 Reporter / ZenohTransport。消息逐条等待本地发布调用完成，避免被快照合并覆盖；
正常场景按秒完整采样，状态变化立即发布。一次仅保留当前待发消息，上报故障时不推进到
新批次，重试超过 30 秒退出失败（非零退出码）。本地错误和运行失败也会输出事件。
正常单轮执行结束退出 0，显式循环同样可以用 Ctrl+C 停止。

本地发布成功不表示云端已经接收。工具不自动判断页面或后端行为；结合消息序号、
UUID、预期观察点和云端日志逐项验收。批次协议只有进行中/全部成功，不能表达独立的
批次失败状态或错误原因。非法样本的预期是检查云端校验、隔离及恢复，而不是要求
云端接受这些值；乱序如何处理也需结合后端约定判断。

真实链路断连需外部停止 router 或断网，随后在 30 秒内恢复，观察本地重试及云端后续处理。
模拟文件失败不代表链路故障。跨平台单元测试和本地 Zenoh 收发不能替代真实云端验收。

## 本地压力参考记录

2026-10-21，macOS 27.0.1 ARM64、12 核、Python 3.10.19；连接本机 Zenoh peer，
三个规模并发运行，同时执行全库回归测试。每个规模持续采样 30 秒、31 次完整压力快照，
三进程合计 108 条消息（包含初始排队和最终成功），本地订阅端全部收到。

| 节点数 | 最大消息字节数 | 构建及序列化 p95 | 本地发布调用/确认等待 p95 | 相邻发布间隔 p95 |
|---|---:|---:|---:|---:|
| 100 | 11,095 | 3.01 ms | 115.64 ms | 1.093 s |
| 1,000 | 108,295 | 15.66 ms | 115.33 ms | 1.095 s |
| 5,000 | 540,295 | 37.62 ms | 83.52 ms | 1.031 s |

构建耗时包括进度模型更新及 Protobuf 构建/序列化；发布耗时包含 Reporter 调度和
本地确认队列轮询，不是云端端到端时延。这是一次并发负载下的参考实测，不能代表
Windows/Linux、目标机器人或云端性能验收，也没有测量真实 OBS 吞吐。

## 同步维护与测试

本工具与主功能保留在同一仓库、同一集成分支。直接导入 ingestion 的进度模型、
Reporter、文件类型及 R2C Protobuf/transport，不复制共享实现。修改主功能接口时，
在同一个 PR 中更新工具和测试；初期只保证同一提交配套，不承诺历史 wheel 之间兼容。

开发环境已有本仓库依赖时，可单独安装 `python -m pip install --no-deps -e tools/ingestion-mock`。
需要单独运行工具测试时，安装 `python -m pip install -e 'tools/ingestion-mock[test]'`；
完整开发安装清单和 CI 已包含这个 test extra，用于测试及分发包构建检查。
正式客户仅安装 `hw-cloudrobo-ingestion` 或 `hw-cloudrobo-client`，不会自动安装本工具。
工具版本为独立开发版本，不跟随客户发布版本同步，不注册 `cloudrobo.groups` 插件。

```bash
# 全库回归包含开发工具
python -m pytest tests/ packages/ tools/ingestion-mock/tests/ -q
# 单独验证开发工具
python -m pytest tools/ingestion-mock/tests/ -q
```

现有三平台 CI 会安装并测试本工具，同时构建客户和工具的 wheel/sdist，验证客户分发包
不包含 Mock 代码或开发包依赖。普通客户测试可继续独立运行，无需安装该开发工具。
