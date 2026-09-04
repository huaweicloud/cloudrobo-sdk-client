# 使用示例

## 机器人端：发布 Observation

```python
import time
import numpy as np
from cloudrobo_r2c import R2CClient, ClientConfig

config = ClientConfig.from_yaml("config/client_config.yaml")
client = R2CClient.connect(config)

def on_action_received(action):
    print(f"Received action: {action.joint_states.position}")

client.subscribe_actions(on_action_received)

img = np.zeros((480, 640, 3), dtype=np.uint8)
while True:
    obs_data = {
        "timestamp": int(time.time() * 1000),
        "task": "pick_and_place_01",
        "id": 0,
        "images": {"front_cam": img},
        "joint_states": {
            "names": ["joint_1", "joint_2", "joint_3"],
            "position": [0.0, 1.57, -1.57],
            "velocity": [0.1, 0.0, 0.0],
            "torque": [1.2, 0.5, 0.0],
        },
        "localization": {"odom_pose": [], "map_pose": []},
    }
    client.publish_observations(obs_data, image_encode="h264")
    time.sleep(1.0)
```

## 云端：订阅 Observation & 发布 Action

```python
from cloudrobo_r2c import R2CClient, ClientConfig
from cloudrobo_r2c.common.models import Observations, Actions

config = ClientConfig.from_yaml("config/client_config.yaml")
client = R2CClient.connect(config)

def on_observation_received(obs: Observations):
    print(f"Timestamp: {obs.timestamp}")
    if obs.images.color:
        for cam_name, img in obs.images.color.items():
            print(f"  {cam_name}: {img.shape if isinstance(img, np.ndarray) else len(img)} bytes")

    action = Actions.from_dict({
        "timestamp": int(time.time() * 1000),
        "chunk_size": 5,
        "joint_states": {
            "names": ["joint_1", "joint_2", "joint_3"],
            "position": [[0.1, 1.6, -1.5]],
        },
    })
    client.publish_actions(action, target_device_id="robot-001")

client.subscribe_observations(on_observation_received, target_device_id="robot-001")
```

## CLI 示例

以下示例假设当前目录为 `packages/cloudrobo-r2c/`：

```bash
# 使用证书 bundle 连接
cloudrobo r2c client --bundle config/certs/cert_xxx.zip --robot-config config/robot_dummy_config.yaml

# 指定 Zenoh 端点
cloudrobo r2c client --project-id test-tenant --device-id device-001 --endpoints tcp/127.0.0.1:7447 --mode client

# 录制观测数据
cloudrobo r2c client --client-config config/client_config.yaml --record observations.pkl --duration 60
```

## 更多示例

完整示例代码见 `examples/` 目录，包括：

- `action_publisher.py` / `action_subscriber.py`：Action 发布/订阅
- `connect_with_bundle.py`：证书 bundle 连接
- `azureloong_cloud_adapter.py`：AzureLoong 云端适配器
- `a1z_cloud_adapter.py`：A1Z 云端适配器
