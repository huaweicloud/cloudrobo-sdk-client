# cloudrobo-ingestion 开发与验证

[模块概览](index.md) · [使用手册](user-guide.md)

## 开发环境

在仓库根目录，按[安装文档](installation.md#从当前仓库安装)安装本地依赖，并安装测试及可选校验工具：

```bash
python -m pip install pytest ruff -e "packages/cloudrobo-ingestion[lerobot-validation]" -e "tools/ingestion-mock[test]"
```

## 检查与测试

```bash
python -m pytest packages/cloudrobo-ingestion/tests packages/cloudrobo-core/tests tools/ingestion-mock/tests -q
ruff check packages/cloudrobo-ingestion tools/ingestion-mock packages/cloudrobo-core/tests/test_obs_progress.py
```

与仓库[跨平台 workflow](../../../.github/workflows/ingestion-tests.yml)一致，CI 覆盖 Linux、Windows、macOS 的 Python 3.10/3.12。真实 OBS 测试按测试文件要求显式启用，普通回归不能代替客户环境的真实 OBS/router 联调。

## 独立 Mock 工具

[tools/ingestion-mock](../../../tools/ingestion-mock/README.md)是仓库内开发工具，支持前台模拟进度和故障场景，不随客户回流包发布。它复用进度模型和 transport，但保持脚本控制节奏，不自动套用客户成功结果的三次补发策略。测试场景和命令见该工具 README。

## 平台与验收

不依赖 Unix 信号完成生命周期控制，使用后台 subprocess、平台文件锁与认证的
loopback 控制通道。每个操作系统用户单实例，控制登记在 `~/.cloudrobo/ingestion`。
`CLOUDROBO_INGESTION_RUNTIME_DIR` 可指定独立登记位置（主要用于测试/受管理部署）；
不同登记目录会形成独立实例域。工作目录应由该用户独占，不要手动修改 SQLite。

跨平台自动化 CI 与真实 OBS/router 联调分开。性能验收需在目标机器人及客户实际
数据上测量：单个 5,000 文件批次运行 30 分钟，序列化 p95 ≤200 ms、发布间隔
p95 ≤1.2 秒、OBS 吞吐相对关闭上报下降 ≤5%。通过单元测试不代表满足这些指标。

权限测试按平台执行：Linux/macOS 检查宽松 `umask` 下副本文件 `0600`、目录 `0700`；
Windows 使用系统 PowerShell 的 `Get-Acl` 和数字 SID，验证副本不继承测试父目录的宽泛访问权限，
同时检查当前用户可读写、清理副本，源文件内容及 ACL 不变。该用例自动纳入现有 Windows
Python 3.10/3.12 CI；缺少所需系统工具时测试失败，不会静默跳过 Windows 权限检查。
Windows 副本目录在创建时通过系统 API 设置受保护的 ACL，兼容尚不支持 `mkdir(0700)`
权限处理的 Python 3.10.11；仅允许系统、管理员和所有者访问，不修改源目录的 ACL。

可复现的序列化微基准（输出机器信息、消息大小和 p95；不发送数据）：

```bash
python packages/cloudrobo-ingestion/benchmarks/serialize.py --files 5000 --iterations 100
```

扫描规模微基准（临时构造目录，结束后清理，不连接 OBS 或 Zenoh）：

```bash
python packages/cloudrobo-ingestion/benchmarks/scan.py --mode history --directories 10000
python packages/cloudrobo-ingestion/benchmarks/scan.py --mode backlog --directories 10000
python packages/cloudrobo-ingestion/benchmarks/scan.py --mode mixed --directories 10000
```

开发机实测：macOS 27.0.1 / ARM64 / 12 逻辑 CPU，Python 3.10.19。
目录和历史数据库预先构造；每个待上传目录包含一个小文件。测量从启动扫描到首轮
完成全部登记，主循环按 50 ms 间隔消费，启用 SQL 写操作计数，未执行真实上传或发布。

| 场景 | 目录数 | 首轮完成（秒） | CPU 时间（秒） | 进程峰值 RSS（MiB） | 首批清单（ms） | SQL 写操作数 |
|---|---:|---:|---:|---:|---:|---:|
| 全部为历史目录 | 1,000 | 0.060 | 0.006 | 36.38 | — | 0 |
| 全部为历史目录 | 10,000 | 0.258 | 0.036 | 40.11 | — | 0 |
| 全部为待上传目录 | 1,000 | 2.154 | 0.365 | 34.20 | 187.31 | 1,002 |
| 全部为待上传目录 | 10,000 | 21.716 | 4.097 | 38.67 | 199.15 | 10,002 |
| 历史／待上传各半 | 1,000 | 1.135 | 0.175 | 34.25 | 198.69 | 502 |
| 历史／待上传各半 | 10,000 | 11.034 | 2.096 | 39.78 | 200.42 | 5,002 |

上述场景单次主循环处理 p95 ≤14.65 ms，状态快照查询最长 2.70 ms；结果队列 ≤32、
清单窗口 ≤2、发布槽位 ≤2（本基准无待补报结果）。当前实现及自动化测试将内存发布槽位限制为当前进度和一条结果，合计 ≤2。Windows 的标准库不提供 `resource.getrusage`，脚本的 RSS 字段
在该平台输出 `null`。这些小文件测量不代表客户大数据集或真实 OBS 吞吐验收结果。
