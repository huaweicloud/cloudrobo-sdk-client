# 安装指南

## 系统要求

- Python >= 3.8
- pip >= 21.0

## 安装方式

### 方式一：安装全部模块（推荐）

```bash
git clone <repository-url>
cd cloudrobo-client
pip install -r requirements-dev-editable.txt
```

以开发模式（editable）安装所有子包，代码修改即时生效。

### 方式二：从 PyPI 安装（正式发布后）

```bash
pip install hw-cloudrobo-client
```

### 方式三：单独安装模块

核心 SDK（必须）：

```bash
pip install -e packages/cloudrobo-core
```

功能模块（按需安装）：

```bash
# 资产管理
pip install -e packages/cloudrobo-asset

# 数据集处理
pip install -e packages/cloudrobo-dataset

# 模型训练
pip install -e packages/cloudrobo-train

# 模型评测
pip install -e packages/cloudrobo-eval

# 推理服务
pip install -e packages/cloudrobo-infer

# 机器人管理
pip install -e packages/cloudrobo-robot

# 智能体调度
pip install -e packages/cloudrobo-dispatch

# 工作空间
pip install -e packages/cloudrobo-workspace

# 资源管理
pip install -e packages/cloudrobo-resource

# 机器人管理
pip install -e packages/cloudrobo-robot

# 数据面 SDK（Zenoh，重型可选依赖）
pip install -e packages/cloudrobo-r2c
```

## 配置认证

### 方式一：命令行配置（推荐）

```bash
# AK/SK 自动加密存储
cloudrobo config set ak your-access-key sk your-secret-key

# 设置其他配置项
cloudrobo config set region cn-southwest-2
```

### 方式二：环境变量

```bash
export HUAWEI_CLOUD_AK="your-access-key"
export HUAWEI_CLOUD_SK="your-secret-key"
export CLOUDROBO_REGION="cn-southwest-2"
```

### 配置文件说明

`~/.cloudrobo/config.yaml` 中的 AK/SK 以加密形式存储（`ak_enc`/`sk_enc`），**不应手动编辑**。如需修改 AK/SK，请使用上述命令或环境变量。

配置文件适合编辑其他明文配置项，如 `region`、`endpoints`、`proxy` 等。

## 验证安装

```bash
# CLI 验证
cloudrobo --help

# SDK 验证
python -c "from cloudrobo_core.sdk import Config; print(Config().ak)"
```

## 安装 Skills

Skills 已从 cloudrobo-client 分离到独立仓库 `cloudrobo-skills`。

### 使用 skill install 命令

```bash
# 1. 克隆 skill 仓库
git clone <cloudrobo-skills-url> ~/cloudrobo-skills

# 2. 安装到指定平台（如 claude-code）
cloudrobo skill install --source ~/cloudrobo-skills/skills --target claude-code

# 3. 安装指定 skill（逗号分隔）
cloudrobo skill install --source ~/cloudrobo-skills/skills --target claude-code --skill-name skill1,skill2
```

## 常见问题

### 找不到 cloudrobo 命令

确保 pip 安装的 bin 目录在 PATH 中。

### 如何更新

开发模式：
```bash
pip install -r requirements-dev-editable.txt
```

PyPI 安装：
```bash
pip install --upgrade hw-cloudrobo-client
```
