# cloudrobo-core

核心 SDK 与 CLI 框架模块，为所有功能包提供基础组件。

## 功能特性

- **Config**: 配置管理，支持环境变量、配置文件、默认值三级优先级
- **HttpClient**: HTTP 客户端封装，自动处理认证、重试和错误
- **BaseClient**: 基础客户端类，所有功能客户端的父类
- **AuthManager**: 认证管理器
- **PluginGroup**: CLI 插件动态加载器
- **异常体系**: CloudRoboError 及其子类（AuthenticationError、ResourceNotFoundError 等）
- **CLI 框架**: 基于 Click 的命令行框架，支持插件化命令组注册

## 安装

```bash
pip install -e packages/cloudrobo-core
```

## 快速开始

### SDK

```python
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)
```

### CLI

```bash
cloudrobo --help
```

## 文档导航

- [CLI 命令详情](commands.md)
- [使用示例](examples.md)
- [开发指南](development.md)
