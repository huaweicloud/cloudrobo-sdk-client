# 隙锋接入华为 CloudRobo —— 额外安装组件清单（交付物 #5）

> 范围：在**客户开发电脑（上位机，可含 WSL2）**上部署「隙锋桥」所需的全部软件/凭证/网络组件。
> 桥跑在开发电脑上、不装进机器人本体（产品形态）。

## 1. 运行环境

| 组件 | 版本/要求 | 说明 |
|---|---|---|
| 操作系统 | Linux x86_64（含 Windows WSL2） | pypilot wheel 为 `linux_x86_64`，仅支持该架构 |
| Python | **3.10**（cp310） | **硬约束**：真机模式的 pypilot 只有 cp310 wheel；建议用 conda env（如 `py310`） |
| pip | ≥ 23 | 安装 wheel / editable 包 |

## 2. 核心 Python 包

| 包 | 版本 | 来源 | 何时需要 |
|---|---|---|---|
| **hw-cloudrobo-r2c** | 0.1.58 | 华为提供（含本 `cloudrobo_r2c.robots.xifeng` 子包） | **必装**（mock + real 都要） |
| **pypilot** | **2.0.0.260612.0**（cp310 wheel） | 隙锋提供 `pypilot-2.0.0.260612.0-cp310-cp310-linux_x86_64.whl` | **仅真机模式**（`source: real`）；mock 模式不需要 |

> mock 模式（合成数据 / 连真云做链路验证）**无需 pypilot**，只装 hw-cloudrobo-r2c 即可。

## 3. hw-cloudrobo-r2c 的传递依赖（随其自动安装）

来自 `packages/cloudrobo-r2c/pyproject.toml`，pip 会自动拉取：

| 依赖 | 版本约束 |
|---|---|
| eclipse-zenoh | ==1.9.0（传输层，mTLS + zenoh peer/client） |
| protobuf | >=5.29,<7 |
| PyYAML | >=6.0 |
| av | ==15.1.0（H264 编码路径用；本桥默认走 JPEG，但仍随包安装） |
| cryptography | >=42,<46（mTLS） |
| numpy | ==1.26.2（Python 3.10）；>=2.0,<2.5（Python≥3.12） |
| opencv-python-headless | ==4.11.0.86（Python 3.10）；==4.12.0.88（Python≥3.12） |

> 隙锋桥子包 `cloudrobo_r2c.robots.xifeng` 不引入新依赖（仅用到已随 r2c_sdk 安装的 `numpy`）。
> 目前不支持 RGB 推流，桥不采集图像，故 opencv 仅作为 r2c_sdk 传递依赖存在、本桥未使用。

## 4. 凭证与网络

| 组件 | 说明 |
|---|---|
| CloudRobo 设备 bundle | 在 CloudRobo 控制台「运行管理 > 机器人 > 创建机器人」下载的 `cert_xxx.zip`（含 ca/server_cert/server_key/device_info/zenoh），R2C mTLS 接入用。**连真云必需** |
| 出网到华为云 | 能访问 `cloudrobo-r2c.cn-southwest-2.myhuaweicloud.com:7447`（贵阳节点，TLS） |
| 到机器人的局域网 | **仅真机模式**：开发电脑与隙锋机器人同网段；机器人调试口为 **RJ45 千兆（1000Mbps，CAT5e+）**（用户手册 §电气接口）。WSL2 访问局域网设备需端口转发或 mirrored 网络（Win11 24H2+） |

## 5. 安装步骤

```bash
# (1) 建 Python 3.10 环境（conda 示例）
conda create -n py310 python=3.10 -y && conda activate py310

# (2) 装华为 R2C SDK（editable，含 xifeng 子包）
pip install -e /path/to/packages/cloudrobo-r2c

# (3) 仅真机模式：装隙锋 pypilot wheel
pip install /path/to/pypilot-2.0.0.260612.0-cp310-cp310-linux_x86_64.whl

# (4) 自检
python -c "import r2c_sdk, cloudrobo_r2c.robots.xifeng; print('r2c_sdk + xifeng OK')"
python -m pytest tests/xifeng/ -v      # 27 passed（真机模式另需 pypilot）
```

## 6. 不需要单独安装的

- 上行调度 / chunk 拆分 / 心跳 / zenoh 客户端 / protobuf 序列化 —— 均由 hw-cloudrobo-r2c 内置。
- 隙锋桥适配器 —— 已随 hw-cloudrobo-r2c 的 `cloudrobo_r2c.robots.xifeng` 子包一并安装，**无需独立 pip 包**。

---
> 版本基线：hw-cloudrobo-r2c 0.1.58 + pypilot 2.0.0.260612.0 + Python 3.10（2026-06-15 验证）。
> 升级 SDK/wheel 时，请同步核对本清单第 2/3 节的版本约束。
