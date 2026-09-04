# CloudRobo Robot

机器人管理模块，提供机器人注册、查询、更新、删除等功能。

## CLI 命令

```bash
# 查看帮助
cloudrobo robot --help

# 注册机器人
cloudrobo robot create --name <名称> --type <类型> --manufacturer <制造商> --robot-model <型号> --workspace-id <工作空间ID>

# 查询机器人列表
cloudrobo robot list [--name <名称>] [--status <状态>]

# 查询机器人详情
cloudrobo robot show --robot-id <机器人ID>

# 更新机器人信息
cloudrobo robot update --robot-id <机器人ID> [--name <名称>] [--description <描述>]

# 删除机器人
cloudrobo robot delete --robot-id <机器人ID>

# 导出机器人证书
cloudrobo robot export-certificate --robot-id <机器人ID> [--password <密码>] --output <导出目录>

# 查询机器人最新 SDK 包信息
cloudrobo robot show-sdk
```

## SDK 使用

```python
from cloudrobo_robot import RobotClient
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)
client = RobotClient(http)

# 注册机器人
result = client.create_robot({
    "name": "e3f4a5b6-c7d8-9012-efab-123456789012",
    "type": "HUMANOID",
    "manufacturer": "Manufacturer A",
    "robot_model": "Model X",
    "workspace_id": "c1d2e3f4-a5b6-7890-cdef-901234567890"
})

# 查询机器人列表
result = client.list_robots(name="e3f4a5b6-c7d8-9012-efab-123456789012")

# 查询机器人详情
result = client.show_robot("a5b6c7d8-e9f0-1234-abcd-345678901234")

# 更新机器人信息
result = client.update_robot("a5b6c7d8-e9f0-1234-abcd-345678901234", {"name": "updated-name"})

# 删除机器人
client.delete_robot("a5b6c7d8-e9f0-1234-abcd-345678901234")

# 导出机器人证书
cert = client.export_robot_certificate("a5b6c7d8-e9f0-1234-abcd-345678901234", {"password": "secret"})

# 查询机器人最新 SDK 包信息
result = client.show_sdk()
```
