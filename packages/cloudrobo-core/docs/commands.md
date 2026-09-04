# cloudrobo-core CLI 命令

## 主命令

`cloudrobo` 是所有命令的根命令。

```bash
cloudrobo [OPTIONS] COMMAND [ARGS]...
```

### 全局选项

| 选项 | 说明 |
|------|------|
| `--help` | 显示帮助信息并退出 |
| `--verbose`, `-v` | 启用详细日志输出和错误详情 |

**`-v` / `--verbose` 模式的行为差异：**

- **正常模式**：显示友好的错误提示（如"错误: SK 未配置。请运行 'cloudrobo config set sk <your-sk>' 配置"）
- **`-v` 模式**：
  - 显示 DEBUG 级别日志，包含 HTTP 请求/响应详情
  - 所有错误（已知和未知）：显示完整 Python traceback，便于调试
  - 错误提示末尾会显示"使用 --verbose 或 -v 查看详细错误信息"

**启用 verbose 模式的三种方式：**
1. 命令行参数：`cloudrobo -v` 或 `cloudrobo --verbose`
2. 环境变量：`export CLOUDROBO_VERBOSE=true`
3. 配置文件：在 `~/.cloudrobo/config.yaml` 中设置 `debug.verbose: true`

## 命令组

cloudrobo-core 注册了以下命令组入口，具体命令在各功能包中定义：

| 命令组 | 所属包 | 说明 |
|--------|--------|------|
| `asset` | cloudrobo-asset | 资产管理 |
| `dataset` | cloudrobo-dataset | 数据集处理 |
| `train` | cloudrobo-train | 模型训练 |
| `eval` | cloudrobo-eval | 模型评测 |
| `infer` | cloudrobo-infer | 推理服务 |
| `robot` | cloudrobo-robot | 机器人管理 |
| `dispatch` | cloudrobo-dispatch | 智能体调度 |
| `workspace` | cloudrobo-workspace | 工作空间 |

## 查看帮助

```bash
# 查看所有可用命令
cloudrobo --help

# 启用详细日志
cloudrobo -v asset list-repositories

# 查看特定命令组帮助
cloudrobo asset --help
cloudrobo train --help
```

## 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | 成功 |
| 1 | 一般错误 |
| 2 | 参数错误 |
| 3 | 认证失败 |
| 4 | API 调用失败 |

---

## 配置文件

默认配置文件位置：`~/.cloudrobo/config.yaml`

配置文件中的 AK/SK 以加密形式存储（`ak_enc`/`sk_enc`），**不应手动编辑**。

**设置 AK/SK 请使用：**
- `cloudrobo config set ak <your-ak> sk <your-sk>` 命令（推荐）
- 环境变量 `HUAWEI_CLOUD_AK` / `HUAWEI_CLOUD_SK`

配置文件适合编辑其他明文配置项，如 `region`、`endpoints`、`proxy` 等：

```yaml
cloudrobo:
  region: "cn-southwest-2"
  endpoints:
    cloudrobo-service: "https://custom-endpoint.example.com"
```

### 环境变量

| 环境变量 | 对应配置 | 说明 |
|---------|---------|------|
| `HUAWEI_CLOUD_AK` | `cloudrobo.auth.ak` | 覆盖配置文件中的 AK |
| `HUAWEI_CLOUD_SK` | `cloudrobo.auth.sk` | 覆盖配置文件中的 SK |
| `CLOUDROBO_SERVICE_CONFIG` | - | 指定配置文件路径 |
| `HTTP_PROXY` / `http_proxy` | `cloudrobo.proxy.http` | HTTP 代理 |
| `HTTPS_PROXY` / `https_proxy` | `cloudrobo.proxy.https` | HTTPS 代理 |
| `CLOUDROBO_HTTP_PROXY` | `cloudrobo.proxy.http` | HTTP 代理（优先级高于标准变量） |
| `CLOUDROBO_HTTPS_PROXY` | `cloudrobo.proxy.https` | HTTPS 代理（优先级高于标准变量） |
| `NO_PROXY` / `no_proxy` | `cloudrobo.proxy.no_proxy` | 不走代理的地址 |
| `CLOUDROBO_NO_PROXY` | `cloudrobo.proxy.no_proxy` | 不走代理的地址（优先级高于标准变量） |
| `CLOUDROBO_PROXY_USERNAME` | `cloudrobo.proxy.username` | 代理认证用户名 |
| `CLOUDROBO_PROXY_PASSWORD` | `cloudrobo.proxy.password` | 代理认证密码 |
| `CLOUDROBO_VERIFY_SSL` | `debug.verify_ssl` | SSL 验证开关（true/false） |
| `CLOUDROBO_CA_BUNDLE` | `debug.ca_bundle` | CA 证书包路径 |
| `CLOUDROBO_LOG_TRAFFIC` | `debug.log_traffic` | 流量日志开关（true/false） |
| `CLOUDROBO_VERBOSE` | `debug.verbose` | 详细日志开关（true/false），等同于 `-v` |
