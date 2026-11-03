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

## 事件上报（EventManager）

```python
from cloudrobo_r2c.client.event_manager import event_manager

# 在适配器/告警模块任意位置调用；限频 2Hz，超频事件缓存补发
event_manager.publish({
    "event_id": "battery_low",
    "event_type": "robot.alarm",
    "level": "WARNING",
    "message": "battery below 10%",
    "starts_at": int(time.time() * 1000),
})
```

## 自定义 Topic（hardware.topics）

```yaml
hardware:
  type: dummy
  topics:
    "{project_id}/{device_id}/state/current":
      proto: "cloudrobo_r2c.common.models:RunTimeMessage"
      direction: publish
      rate_hz: 2
      collector: get_runtime
```

## 相机推流

```yaml
# robot config 片段（完整示例见 config/robot_dummy_realcam_config.yaml）
camera_streaming:
  enabled: true
  camera_device_map:
    front: front
    wrist: wrist
  default_width: 640
  default_height: 480
  default_fps: 20
```

相机推流需要安装 `hw-cloudrobo-r2c[camera]`；云端通过 `job/command` 下发 `CAMERA_START` 后开始经 LiveKit 推流。

## 数据面转发（forwarder）

```bash
cloudrobo r2c client --bundle config/<cert-config.zip> \
  --robot-config config/robot_dummy_realcam_config.yaml \
  --forwarder-config config/forwarder_config.yaml
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
- `observation_publisher.py` / `observation_subscriber.py`：Observation 发布/订阅
- `heartbeat_publisher.py`：心跳发布
- `command_publisher.py` / `event_publisher.py` / `mock_taskprocess_pub.py`：命令、事件、任务进度发布
- `topic_recorder.py` / `topic_replayer.py`：话题录制与回放
- `connect_with_bundle.py` / `connect_bundle_encrypted_key.py`：证书 bundle 连接
- `jaka_app.py` / `q25_cloud_adapter.py` / `so101_app.py` / `s05_cloud_adapter.py` / `xifeng_cloud_adapter.py`：各机器人型号适配
- `ros_adapter/`、`so101_real/`、`songyi/`、`ur5e_transformer/`、`third_party_adapter/`：完整接入与扩展示例
