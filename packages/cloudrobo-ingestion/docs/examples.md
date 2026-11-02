# cloudrobo-ingestion 使用示例

[模块概览](index.md) · [安装与配置](installation.md) · [CLI 命令](commands.md) · [使用示例](examples.md) · [使用手册](user-guide.md)

以下示例会真实上传 OBS。先完成[安装、凭证及 endpoint 配置](installation.md)，并使用新的测试批次路径；切换不同启动示例前先停止当前进程。

## 首次运行示例

下面以一个普通文本文件验证安装和真实上传链路。使用[默认模式完整配置](user-guide.md#默认模式完整示例)，先把桶、OBS 配置及身份改为自己的实际值；`validation.enabled` 保持 `false`。命令在 **`ingestion.yaml` 所在目录**执行。

Linux / macOS：

```bash
mkdir -p sample/batch-001
printf 'ingestion test\n' > sample/batch-001/hello.txt
touch sample/batch-001/__UPLOAD_READY__
cloudrobo ingestion start --config ingestion.yaml
cloudrobo ingestion status
```

Windows PowerShell：

```powershell
New-Item -ItemType Directory -Path .\sample\batch-001 -Force
Set-Content -Path .\sample\batch-001\hello.txt -Value 'ingestion test' -Encoding utf8
New-Item -ItemType File -Path .\sample\batch-001\__UPLOAD_READY__
cloudrobo ingestion start --config .\ingestion.yaml
cloudrobo ingestion status
```

如果当前只想验证 OBS，启动命令改成：

```bash
cloudrobo ingestion start --config ingestion.yaml --run-mode upload_only
```

确认以下结果：

1. `start` 返回进程 PID、运行模式和实际工作目录。
2. `status` 最终出现 `done` 批次计数；该计数来自工作目录的累计记录，干净环境的示例应为 1。
3. OBS 中存在 `robot-data/batch-001/hello.txt`，没有 `__UPLOAD_READY__` 对象。
4. 默认模式下另外检查 router/云端是否收到状态；仅上传模式显示上报未启用。

小批次可能很快完成，看不到中间进度是正常的。程序在没有待处理数据时仍持续运行，之后新增已标记目录也会被发现。再次测试请用 `batch-002` 等新路径，不要删除成功目录后复用 `batch-001`。

## 模式切换与修改目标地址

### 默认模式与仅上传模式切换

假设 `ingestion.yaml` 是[回流配置](user-guide.md#回流配置文件)的完整配置：

```bash
cloudrobo ingestion stop
cloudrobo ingestion start --config ingestion.yaml --run-mode upload_only

# 需要恢复进度上报时：
cloudrobo ingestion stop
cloudrobo ingestion start --config ingestion.yaml --run-mode upload_and_report
```

两次启动保持同一 `source_dir` 和 `work_dir`。已经成功的批次不因切换重新上传。

| 批次来源与切换时状态 | 切回默认模式后的行为 |
|---|---|
| 默认模式建立，之后仅上传模式完成 | 若首次入队未满 24 小时，补报保存的成功结果 |
| 仅上传模式新建且已经完成 | 不补报，也不重新上传 |
| 仅上传模式新建但尚未完成 | 纳入上报，继续处理；缺少的分类从当前配置补齐 |
| 原有成功结果还没完成三次发布 | 未过期则恢复剩余次数；过期即丢弃剩余补发 |

已有批次的分类会固定保留，配置中的分类变化通常只影响新批次；仅上传批次首次纳入上报时补齐缺失分类。切换时沿用相同 Zenoh 身份和 topic，不提供跨身份结果迁移。

### 修改 OBS 目标

```bash
cloudrobo ingestion stop
cloudrobo ingestion start --config ingestion.yaml --obs-path obs://customer-bucket/new-prefix/
```

- 已成功批次保持原实际上传地址，不会自动复制/重传到新位置；待补发结果也保留原地址。
- 未完成批次切到新地址并整批重试，新批次使用新地址；批次 UUID 和首次入队时间不变。
- 原地址上已存在的部分对象不会自动删除。旧 checkpoint 按原目标清理，清理未确认时暂停该批次。

修改 `obs_path` 不能实现历史数据迁移。只更换 OBS endpoint 而桶/前缀不变，也不能当作“重新上传所有成功历史”的开关。
