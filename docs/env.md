# CloudRobo 环境变量配置

## 配置文件路径（1个）

| 环境变量 | 配置项 | 说明 |
|---------|--------|------|
| `CLOUDROBO_SERVICE_CONFIG` | — | 指定自定义配置文件路径，替代默认的 `~/.cloudrobo/config.yaml` |

## 认证配置（2个）

| 环境变量 | 配置项 | 说明 |
|---------|--------|------|
| `HUAWEI_CLOUD_AK` | `cloudrobo.auth.ak` | 华为云 Access Key |
| `HUAWEI_CLOUD_SK` | `cloudrobo.auth.sk` | 华为云 Secret Key |

## 服务端点覆盖（1个，动态）

| 环境变量 | 配置项 | 说明 |
|---------|--------|------|
| `CLOUDROBO_ENDPOINT_{SERVICE}` | `cloudrobo.endpoints.{service}` | 覆盖服务端点地址。<br>`{SERVICE}` 为服务名大写，如 `CLOUDROBO_ENDPOINT_CLOUDROBO_SERVICE` |

## 代理配置（5个）

| 环境变量 | 配置项 | 说明 |
|---------|--------|------|
| `CLOUDROBO_HTTP_PROXY` | `cloudrobo.proxy.http` | HTTP 代理地址 |
| `CLOUDROBO_HTTPS_PROXY` | `cloudrobo.proxy.https` | HTTPS 代理地址 |
| `CLOUDROBO_NO_PROXY` | `cloudrobo.proxy.no_proxy` | 不走代理的域名列表（逗号分隔） |
| `CLOUDROBO_PROXY_USERNAME` | `cloudrobo.proxy.username` | 代理认证用户名 |
| `CLOUDROBO_PROXY_PASSWORD` | `cloudrobo.proxy.password` | 代理认证密码 |

### 代理配置示例

```bash
# 设置代理服务器
export CLOUDROBO_HTTP_PROXY=http://proxy.company.com:8080
export CLOUDROBO_HTTPS_PROXY=http://proxy.company.com:8080

# 豁免华为云域名（不走代理）
export CLOUDROBO_NO_PROXY=.myhuaweicloud.com,localhost

# 代理需要认证时
export CLOUDROBO_PROXY_USERNAME=user
export CLOUDROBO_PROXY_PASSWORD=pass
```

## 调试配置（4个）

| 环境变量 | 配置项 | 说明 |
|---------|--------|------|
| `CLOUDROBO_VERIFY_SSL` | `debug.verify_ssl` | SSL 证书验证开关<br>可选值：`true`/`false`/`yes`/`no`/`1`/`0` |
| `CLOUDROBO_CA_BUNDLE` | `debug.ca_bundle` | 自定义 CA 证书路径（.pem 文件） |
| `CLOUDROBO_LOG_TRAFFIC` | `debug.log_traffic` | HTTP 流量日志开关<br>开启后记录请求/响应的详细信息 |
| `CLOUDROBO_VERBOSE` | `debug.verbose` | 详细日志开关，等同于 `-v` 参数<br>开启后输出 DEBUG 级别日志，并在报错时显示完整 traceback |

### 调试配置示例

```bash
# 启用 SSL 验证（使用系统默认 CA 证书）
export CLOUDROBO_VERIFY_SSL=true

# 使用自定义 CA 证书
export CLOUDROBO_VERIFY_SSL=true
export CLOUDROBO_CA_BUNDLE=/path/to/company-ca-bundle.pem

# 禁用 SSL 验证（开发环境自签证书）
export CLOUDROBO_VERIFY_SSL=false

# 开启 HTTP 流量日志
export CLOUDROBO_LOG_TRAFFIC=true

# 启用 verbose 模式（三种方式任选其一）
export CLOUDROBO_VERBOSE=true
cloudrobo dataset proc list-tasks
# 或命令行参数：cloudrobo -v dataset proc list-tasks
# 或配置文件：~/.cloudrobo/config.yaml 中设置 debug.verbose: true
```

## 配置优先级

```
环境变量 > ~/.cloudrobo/config.yaml > config.yaml 默认值
```

## 总计

共 **14 个环境变量**（不含动态的 `CLOUDROBO_ENDPOINT_{SERVICE}`）：
- 配置文件：1个
- 认证：2个
- 代理：5个
- 调试：4个
- 端点：1个（动态）
