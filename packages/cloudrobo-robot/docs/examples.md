# cloudrobo-robot 使用示例

## CLI 示例

### 注册机器人

```bash
cloudrobo robot create --name e3f4a5b6-c7d8-9012-efab-123456789012 --type HUMANOID --manufacturer "Mfg A" --robot-model "Model X" --workspace-id c1d2e3f4-a5b6-7890-cdef-901234567890
```

### 查询在线机器人

```bash
cloudrobo robot list --status ONLINE
```

### 查询机器人详情

```bash
cloudrobo robot show --robot-id a5b6c7d8-e9f0-1234-abcd-345678901234
```

### 更新机器人描述

```bash
cloudrobo robot update --robot-id a5b6c7d8-e9f0-1234-abcd-345678901234 --description "This is a humanoid robot for assembly tasks"
```

### 删除机器人

```bash
cloudrobo robot delete --robot-id a5b6c7d8-e9f0-1234-abcd-345678901234
```

### 导出机器人证书

```bash
cloudrobo robot export-certificate --robot-id a5b6c7d8-e9f0-1234-abcd-345678901234 --output ./certs
# 导出文件: ./certs/cert_config_My-Robot_20240101153045.zip
```

带加密密码导出:

```bash
cloudrobo robot export-certificate --robot-id a5b6c7d8-e9f0-1234-abcd-345678901234 --password "secret" --output ./certs
```

### 查询机器人最新 SDK 包信息

```bash
cloudrobo robot show-sdk
```

## SDK 示例

```python
from cloudrobo_robot.client import RobotClient
from cloudrobo_core.sdk import Config, HttpClient

config = Config()
http = HttpClient(config)
client = RobotClient(http)

# 注册机器人
robot = client.create_robot({
    "name": "e3f4a5b6-c7d8-9012-efab-123456789012",
    "type": "HUMANOID",
    "manufacturer": "Mfg A",
    "robot_model": "Model X",
    "workspace_id": "c1d2e3f4-a5b6-7890-cdef-901234567890"
})
print(f"Robot created: {robot['id']}")

# 查询机器人列表
robots = client.list_robots(status="ONLINE")
for r in robots.get("robots", []):
    print(r["name"], r["type"], r["status"])

# 查询机器人详情
robot = client.show_robot("a5b6c7d8-e9f0-1234-abcd-345678901234")
print(f"Name: {robot['name']}")
print(f"Type: {robot['type']}")
print(f"Manufacturer: {robot['manufacturer']}")

# 更新机器人信息
client.update_robot(
    robot_id="a5b6c7d8-e9f0-1234-abcd-345678901234",
    req={"description": "Updated description"}
)

# 删除机器人
client.delete_robot("a5b6c7d8-e9f0-1234-abcd-345678901234")

# 导出机器人证书（返回二进制证书内容）
cert = client.export_robot_certificate(
    robot_id="a5b6c7d8-e9f0-1234-abcd-345678901234",
    req={"password": "secret"}
)
with open("robot.cert", "wb") as f:
    f.write(cert)

# 查询机器人最新 SDK 包信息
sdk = client.show_sdk()
print(f"SDK: {sdk['file_name']} v{sdk['version']}")
```
