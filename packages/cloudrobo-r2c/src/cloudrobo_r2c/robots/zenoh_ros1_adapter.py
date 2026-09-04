"""Generic ROS1 hardware adapter via Zenoh transport.

Implements :class:`IRobotHardwareAdapter` by subscribing to ROS1 sensor
topics and publishing control commands through a zenoh-bridge-ros1 data
plane.  No rospy dependency — ROS1 messages are serialized/deserialized
from the binary wire format directly.

Works with any ROS1 robot behind a zenoh-bridge-ros1 instance.  Topic
names, message types, and encoder strategies are fully configurable.

Configuration (``zenoh_ros1_config`` section)::

    hardware:
      type: zenoh_ros1
      zenoh_ros1_config:
        mode: peer
        connect_endpoints: []
        namespace: ""
        rate: 30.0

        ros1_type_overrides:              # optional — extend built-in type registry
          "custom_msgs/MyMessage":
            datatype: "custom_msgs/MyMessage"
            md5: "abc123..."

        subscriptions:
          my_camera:
            ros_topic: /camera/compressed
            msg_type: sensor_msgs/CompressedImage
            store_as: camera
          my_joints:
            ros_topic: /joint_states
            msg_type: sensor_msgs/JointState
            store_as: joints

        publishers:
          my_arm:
            ros_topic: /arm/command
            msg_type: hdas_msg/motor_control
            encoder: motor_control        # motor_control | float32 | raw_bytes
          my_gripper:
            ros_topic: /gripper/command
            msg_type: std_msgs/Float32
            encoder: float32

        control_params:                    # per-publisher encoder parameters
          my_arm:
            v_des: [4.0, 4.0, 4.0, 6.0, 6.0, 6.0]
            t_ff: [0.8, 0.8, 0.8, 0.8, 0.8, 0.8]
            kp: [0.0]
            kd: [0.0]
            mode: 0
"""

from __future__ import annotations

import logging
import struct
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np

from cloudrobo_r2c.core.interfaces import IRobotHardwareAdapter


def create_zenoh_ros1_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> IRobotHardwareAdapter:
    """Entry_point factory for ZenohRos1HardwareAdapter."""
    return ZenohRos1HardwareAdapter(config=dict(config))


logger = logging.getLogger(__name__)

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]

# ═══════════════════════════════════════════════════════════════════════
#  Built-in ROS1 wire-format type descriptors
# ═══════════════════════════════════════════════════════════════════════

_BUILTIN_ROS1_TYPES: Dict[str, Dict[str, str]] = {
    "sensor_msgs/CompressedImage": {
        "datatype": "sensor_msgs/CompressedImage",
        "md5": "8f7a12909da2c9d3332d540a0977563f",
    },
    "sensor_msgs/JointState": {
        "datatype": "sensor_msgs/JointState",
        "md5": "3066dcd76a6cfaef579bd0f34173e9fd",
    },
    "sensor_msgs/Image": {
        "datatype": "sensor_msgs/Image",
        "md5": "060021388200f6f0f447d0fcd9c64743",
    },
    "std_msgs/Float32": {
        "datatype": "std_msgs/Float32",
        "md5": "73fcbf46b49191e672908e50842a83d4",
    },
    "std_msgs/Float32MultiArray": {
        "datatype": "std_msgs/Float32MultiArray",
        "md5": "6a40e0ffa6a17a503ac82f43f8722ece",
    },
    "hdas_msg/motor_control": {
        "datatype": "hdas_msg/motor_control",
        "md5": "76f26ea10e82a40ac9f0b5a1eee1c048",
    },
}


def _build_ros1_type_registry(
    overrides: Mapping[str, Any] | None,
) -> Dict[str, Dict[str, str]]:
    registry = dict(_BUILTIN_ROS1_TYPES)
    if overrides:
        for msg_type, info in overrides.items():
            if not isinstance(info, Mapping):
                raise ValueError(
                    f"ros1_type_overrides.{msg_type} must be a mapping with 'datatype' and 'md5'"
                )
            datatype = str(info.get("datatype", msg_type))
            md5 = str(info.get("md5", ""))
            if not md5:
                raise ValueError(f"ros1_type_overrides.{msg_type}.md5 is required")
            registry[str(msg_type)] = {"datatype": datatype, "md5": md5}
    return registry


def _make_zenoh_key(
    msg_type: str,
    ros_topic: str,
    namespace: str = "",
    type_registry: Dict[str, Dict[str, str]] | None = None,
) -> str:
    """Build a zenoh-bridge-ros1 data-plane key.

    Format: ``{hex(datatype)}/{md5}/{topic}`` (no namespace by default).
    When *namespace* is non-empty: ``{hex(datatype)}/{md5}/{namespace}/{topic}``.
    """
    registry = type_registry if type_registry is not None else _BUILTIN_ROS1_TYPES
    info = registry.get(msg_type)
    if info is None:
        raise ValueError(
            f"Unknown ROS1 message type {msg_type!r}. "
            f"Known types: {list(registry.keys())}. "
            f"Add custom types via ros1_type_overrides in zenoh_ros1_config."
        )
    topic = ros_topic.strip("/")
    hex_type = info["datatype"].encode().hex()
    if namespace:
        return f"{hex_type}/{info['md5']}/{namespace}/{topic}"
    return f"{hex_type}/{info['md5']}/{topic}"


# ═══════════════════════════════════════════════════════════════════════
#  ROS1 binary (de)serialization
# ═══════════════════════════════════════════════════════════════════════


def _deserialize_compressed_image(payload: bytes) -> dict:
    """Deserialize ``sensor_msgs/CompressedImage``.

    Returns ``{"format": str, "data": bytes, "frame_id": str}``.
    """
    offset = 0
    _seq, _secs, _nsecs = struct.unpack_from("<3I", payload, offset)
    offset += 12
    frame_id_len = struct.unpack_from("<I", payload, offset)[0]
    offset += 4
    frame_id = payload[offset : offset + frame_id_len].decode("utf-8")
    offset += frame_id_len
    fmt_len = struct.unpack_from("<I", payload, offset)[0]
    offset += 4
    img_format = payload[offset : offset + fmt_len].decode("utf-8")
    offset += fmt_len
    data_len = struct.unpack_from("<I", payload, offset)[0]
    offset += 4
    img_data = bytes(payload[offset : offset + data_len])
    return {"format": img_format, "data": img_data, "frame_id": frame_id}


def _compressed_image_to_ndarray(payload: bytes) -> np.ndarray | None:
    """Decode a ``CompressedImage`` payload to an RGB numpy array."""
    if cv2 is None:
        return None
    info = _deserialize_compressed_image(payload)
    arr = np.frombuffer(info["data"], np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        return None
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def _deserialize_joint_state(payload: bytes) -> dict:
    """Deserialize ``sensor_msgs/JointState``."""
    offset = 0
    _seq, _secs, _nsecs = struct.unpack_from("<3I", payload, offset)
    offset += 12
    frame_id_len = struct.unpack_from("<I", payload, offset)[0]
    offset += 4
    frame_id = payload[offset : offset + frame_id_len].decode("utf-8")
    offset += frame_id_len

    def _read_strings(data: bytes, off: int) -> tuple[list[str], int]:
        count = struct.unpack_from("<I", data, off)[0]
        off += 4
        names: list[str] = []
        for _ in range(count):
            slen = struct.unpack_from("<I", data, off)[0]
            off += 4
            names.append(data[off : off + slen].decode("utf-8"))
            off += slen
        return names, off

    def _read_f64s(data: bytes, off: int) -> tuple[list[float], int]:
        count = struct.unpack_from("<I", data, off)[0]
        off += 4
        vals = list(struct.unpack_from(f"<{count}d", data, off))
        off += count * 8
        return vals, off

    names, offset = _read_strings(payload, offset)
    position, offset = _read_f64s(payload, offset)
    velocity, offset = _read_f64s(payload, offset)
    effort, offset = _read_f64s(payload, offset)
    return {
        "names": names,
        "position": position,
        "velocity": velocity,
        "effort": effort,
        "frame_id": frame_id,
    }


def _encode_motor_control(
    p_des: list[float],
    v_des: list[float] | None = None,
    t_ff: list[float] | None = None,
    kp: list[float] | None = None,
    kd: list[float] | None = None,
    mode: int = 0,
) -> bytes:
    """Encode ``hdas_msg/motor_control`` to ROS1 wire format."""
    if v_des is None:
        v_des = [0.0] * len(p_des)
    if t_ff is None:
        t_ff = [0.0] * len(p_des)
    if kp is None:
        kp = [0.0]
    if kd is None:
        kd = [0.0]

    stamp_secs = int(time.time())
    buf = bytearray()
    buf.extend(struct.pack("<3I", 0, stamp_secs, 0))  # Header
    buf.extend(struct.pack("<I", 0))  # frame_id (empty)
    buf.extend(struct.pack("<I", 0))  # name (empty)

    def _write_f32s(arr: list[float]) -> None:
        buf.extend(struct.pack("<I", len(arr)))
        buf.extend(struct.pack(f"<{len(arr)}f", *arr))

    _write_f32s(p_des)
    _write_f32s(v_des)
    _write_f32s(kp)
    _write_f32s(kd)
    _write_f32s(t_ff)
    buf.extend(struct.pack("<B", mode))
    return bytes(buf)


def _encode_float32(value: float) -> bytes:
    return struct.pack("<f", value)


# ═══════════════════════════════════════════════════════════════════════
#  Adapter
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class ZenohRos1HardwareAdapter(IRobotHardwareAdapter):
    """Hardware adapter for ROS1 robots communicating via zenoh-bridge-ros1.

    Implements the :class:`IRobotHardwareAdapter` protocol so it can be
    used directly with :class:`SyncRobotClient` and the ``cloudroboclient``
    entry point.

    Subscriptions and publishers are declared from the ``zenoh_ros1_config``
    block.  Each publisher specifies an *encoder* that controls how the
    incoming R2C action dict is serialized:

    - ``motor_control`` — :func:`_encode_motor_control` (hdas_msg/motor_control)
    - ``float32``       — :func:`_encode_float32` (std_msgs/Float32)
    - ``raw_bytes``     — pass through raw ``bytes`` from the action value
    """

    config: Mapping[str, Any]

    _session: Any = field(default=None, init=False, repr=False)
    _subscribers: list[Any] = field(default_factory=list, init=False, repr=False)
    _publishers: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _connected: bool = field(default=False, init=False, repr=False)
    _buf: dict[str, deque] = field(default_factory=dict, init=False, repr=False)
    _msg_type_map: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _pub_meta: dict[str, dict] = field(default_factory=dict, init=False, repr=False)
    _ctrl_params: dict[str, dict] = field(default_factory=dict, init=False, repr=False)
    _rate: float = field(default=30.0, init=False, repr=False)
    _namespace: str = field(default="", init=False, repr=False)
    _mode: str = field(default="peer", init=False, repr=False)
    _endpoints: list[str] = field(default_factory=list, init=False, repr=False)
    _scouting: dict = field(default_factory=dict, init=False, repr=False)
    _type_registry: Dict[str, Dict[str, str]] = field(
        default_factory=lambda: dict(_BUILTIN_ROS1_TYPES), init=False, repr=False
    )

    def connect(self) -> None:
        if self._connected:
            logger.debug("Zenoh ROS1 adapter already connected; skipping.")
            return

        self._parse_config()

        try:
            import zenoh
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "zenoh-python is required for the Zenoh ROS1 adapter. "
                "pip install zenoh"
            ) from exc

        self._zenoh = zenoh

        zcfg = zenoh.Config()

        if self._mode not in ("client", "peer"):
            raise RuntimeError(
                "config file error, mode is required 'client' or 'peer'. "
            )

        zcfg.insert_json5("mode", f'"{self._mode}"')
        if self._endpoints:
            eps = str(self._endpoints).replace("'", '"')
            zcfg.insert_json5("connect/endpoints", eps)

        # Apply scouting config
        multicast = self._scouting.get("multicast")
        if isinstance(multicast, Mapping):
            enabled = multicast.get("enabled")
            if isinstance(enabled, bool):
                zcfg.insert_json5(
                    "scouting/multicast/enabled",
                    "true" if enabled else "false",
                )

        self._session = zenoh.open(zcfg)
        logger.info(
            "Zenoh ROS1 session opened (mode=%s, endpoints=%s)",
            self._mode,
            self._endpoints or "auto-discovery",
        )

        self._setup_subscriptions()
        self._setup_publishers()
        self._connected = True

        logger.info(
            "Zenoh ROS1 adapter connected (subscriptions=%d, publishers=%d).",
            len(self._subscribers),
            len(self._publishers),
        )

    def disconnect(self) -> None:
        for sub in self._subscribers:
            try:
                sub.undeclare()
            except Exception:
                logger.debug("Error undeclaring subscriber", exc_info=True)
        self._subscribers.clear()

        self._publishers.clear()
        self._buf.clear()

        if self._session is not None:
            self._session.close()
            self._session = None

        self._connected = False
        logger.info("Zenoh ROS1 adapter disconnected.")

    def get_observation(self) -> Mapping[str, Any]:
        if not self._connected:
            raise RuntimeError("Adapter is not connected. Call connect() first.")

        observation: Dict[str, Any] = {}

        for store_as in self._msg_type_map:
            dq = self._buf.get(store_as)
            if not dq:
                continue
            raw = dq[-1]
            msg_type = self._msg_type_map.get(store_as, "")

            if msg_type == "sensor_msgs/CompressedImage":
                info = _deserialize_compressed_image(raw)
                observation[store_as] = info["data"]

            elif msg_type == "sensor_msgs/Image":
                img = _compressed_image_to_ndarray(raw)
                if img is not None:
                    observation[store_as] = img
                else:
                    observation[store_as] = raw

            elif msg_type == "sensor_msgs/JointState":
                observation[store_as] = _deserialize_joint_state(raw)

            else:
                observation[store_as] = raw

        return observation

    def send_action(self, command: Mapping[str, Any]) -> None:
        if not self._connected:
            raise RuntimeError("Adapter is not connected. Call connect() first.")

        _ = self._zenoh  # keep import reference alive

        for pub_name, pub in self._publishers.items():
            payload = self._encode_for_publisher(pub_name, command)
            if payload is None:
                continue
            pub.put(payload)
            logger.debug("Published to %s: %d bytes", pub_name, len(payload))

    # ------------------------------------------------------------------
    #  Config parsing
    # ------------------------------------------------------------------

    def _parse_config(self) -> None:
        self._mode = str(self.config.get("mode", "peer"))
        self._rate = float(self.config.get("rate", 30.0))
        self._namespace = str(self.config.get("namespace", ""))

        endpoints = self.config.get("connect_endpoints")
        if isinstance(endpoints, list):
            self._endpoints = [str(e) for e in endpoints]

        # Scouting config
        scouting_cfg = self.config.get("scouting")
        if isinstance(scouting_cfg, Mapping):
            self._scouting = dict(scouting_cfg)
        else:
            self._scouting = {}

        # Build type registry (built-in + user overrides)
        overrides = self.config.get("ros1_type_overrides")
        if isinstance(overrides, Mapping):
            self._type_registry = _build_ros1_type_registry(overrides)
        else:
            self._type_registry = dict(_BUILTIN_ROS1_TYPES)

        # Subscriptions
        subs_cfg = self._require_mapping(
            self.config.get("subscriptions", {}), "subscriptions"
        )
        self._msg_type_map = {}
        for name, item in subs_cfg.items():
            if not isinstance(item, Mapping):
                raise ValueError(f"subscriptions.{name} must be a mapping")
            store_as = str(item.get("store_as", name))
            msg_type = str(item.get("msg_type", ""))
            if not msg_type:
                raise ValueError(f"subscriptions.{name}.msg_type is required")
            self._msg_type_map[store_as] = msg_type

        # Publishers
        pubs_cfg = self.config.get("publishers")
        if pubs_cfg is None:
            pubs_cfg = {}
        if not isinstance(pubs_cfg, Mapping):
            raise ValueError("zenoh_ros1_config.publishers must be a mapping")
        self._pub_meta = {}
        for name, item in pubs_cfg.items():
            if not isinstance(item, Mapping):
                raise ValueError(f"publishers.{name} must be a mapping")
            msg_type = str(item.get("msg_type", ""))
            if not msg_type:
                raise ValueError(f"publishers.{name}.msg_type is required")
            encoder = str(item.get("encoder", "motor_control"))
            go_home_role = str(item.get("go_home_role", "")).strip().lower() or None
            if go_home_role is not None and go_home_role not in ("arm", "gripper"):
                raise ValueError(
                    f"publishers.{name}.go_home_role must be 'arm' or "
                    f"'gripper', got {item.get('go_home_role')!r}"
                )
            self._pub_meta[name] = {
                "msg_type": msg_type,
                "encoder": encoder,
                "go_home_role": go_home_role,
            }

        # Control / encoder parameters
        ctrl_cfg = self.config.get("control_params")
        if isinstance(ctrl_cfg, Mapping):
            self._ctrl_params = {str(k): dict(v) for k, v in ctrl_cfg.items()}
        else:
            self._ctrl_params = {}

    def _setup_subscriptions(self) -> None:
        subs_cfg = self._require_mapping(
            self.config.get("subscriptions", {}), "subscriptions"
        )
        maxlen = 10
        self._buf = {}
        self._subscribers = []

        for name, item in subs_cfg.items():
            ros_topic = str(item.get("ros_topic", ""))
            msg_type = str(item.get("msg_type", ""))
            store_as = str(item.get("store_as", name))
            key = _make_zenoh_key(
                msg_type, ros_topic, self._namespace, self._type_registry
            )

            dq: deque = deque(maxlen=maxlen)
            self._buf[store_as] = dq

            sub = self._session.declare_subscriber(
                key,
                lambda sample, _dq=dq: _dq.append(sample.payload.to_bytes()),
            )
            self._subscribers.append(sub)
            logger.info("  Zenoh sub [%s] store_as=%s key=%s", name, store_as, key)

    def _setup_publishers(self) -> None:
        pubs_cfg = self.config.get("publishers")
        if pubs_cfg is None:
            pubs_cfg = {}
        if not isinstance(pubs_cfg, Mapping):
            raise ValueError("zenoh_ros1_config.publishers must be a mapping")

        self._publishers = {}
        for name, item in pubs_cfg.items():
            ros_topic = str(item.get("ros_topic", ""))
            msg_type = str(item.get("msg_type", ""))
            key = _make_zenoh_key(
                msg_type, ros_topic, self._namespace, self._type_registry
            )
            pub = self._session.declare_publisher(key)
            self._publishers[name] = pub
            logger.info("  Zenoh pub [%s] key=%s", name, key)

    # ------------------------------------------------------------------
    #  Encoder dispatch (config-driven)
    # ------------------------------------------------------------------

    def _encode_for_publisher(
        self, pub_name: str, command: Mapping[str, Any]
    ) -> bytes | None:
        meta = self._pub_meta.get(pub_name)
        if meta is None:
            logger.warning("No metadata for publisher %s", pub_name)
            return None

        encoder = meta["encoder"]

        if encoder == "motor_control":
            return self._encode_motor_control_for(pub_name, command)

        if encoder == "float32":
            return self._encode_float32_for(pub_name, command)

        if encoder == "raw_bytes":
            return self._encode_raw_bytes_for(pub_name, command)

        logger.warning(
            "Unknown encoder %r for publisher %s (supported: motor_control, float32, raw_bytes)",
            encoder,
            pub_name,
        )
        return None

    def _encode_motor_control_for(
        self, pub_name: str, command: Mapping[str, Any]
    ) -> bytes | None:
        p_des = self._extract_joint_list(command, pub_name)
        if p_des is None or len(p_des) == 0:
            return None
        params = self._ctrl_params.get(pub_name, {})
        return _encode_motor_control(
            p_des=list(p_des),
            v_des=params.get("v_des"),
            t_ff=params.get("t_ff"),
            kp=params.get("kp"),
            kd=params.get("kd"),
            mode=int(params.get("mode", 0)),
        )

    def _encode_float32_for(
        self, pub_name: str, command: Mapping[str, Any]
    ) -> bytes | None:
        val = self._extract_float(command, pub_name)
        if val is None:
            return None
        return _encode_float32(val)

    @staticmethod
    def _encode_raw_bytes_for(
        pub_name: str, command: Mapping[str, Any]
    ) -> bytes | None:
        raw = command.get(pub_name)
        if isinstance(raw, bytes):
            return raw
        if isinstance(raw, Mapping):
            data = raw.get("data")
            if isinstance(data, bytes):
                return data
        return None

    # ------------------------------------------------------------------
    #  Value extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_joint_list(
        command: Mapping[str, Any], pub_name: str
    ) -> list[float] | None:
        # Try command[pub_name] directly (e.g. command["arm_left"])
        direct = command.get(pub_name)
        if direct is not None:
            if isinstance(direct, (Sequence, np.ndarray)) and not isinstance(
                direct, (str, bytes)
            ):
                return [float(v) for v in direct]
            if isinstance(direct, Mapping):
                positions = direct.get("position")
                if isinstance(positions, (Sequence, np.ndarray)) and not isinstance(
                    positions, (str, bytes)
                ):
                    return [float(v) for v in positions]

        # Try command[pub_name + ".position"]
        key = f"{pub_name}.position"
        val = command.get(key)
        if isinstance(val, (Sequence, np.ndarray)) and not isinstance(
            val, (str, bytes)
        ):
            return [float(v) for v in val]

        return None

    @staticmethod
    def _extract_float(command: Mapping[str, Any], pub_name: str) -> float | None:
        # Try command[pub_name] directly
        direct = command.get(pub_name)
        if direct is not None:
            if isinstance(direct, (Sequence, np.ndarray)) and not isinstance(
                direct, (str, bytes)
            ):
                if len(direct) > 0:
                    return float(direct[0])
            if isinstance(direct, (int, float)):
                return float(direct)
            if isinstance(direct, Mapping):
                pos = direct.get("position")
                if pos is not None:
                    if isinstance(pos, (Sequence, np.ndarray)) and not isinstance(
                        pos, (str, bytes)
                    ):
                        if len(pos) > 0:
                            return float(pos[0])
                    if isinstance(pos, (int, float)):
                        return float(pos)

        # Try command[pub_name + ".position"]
        key = f"{pub_name}.position"
        val = command.get(key)
        if val is not None:
            if isinstance(val, (Sequence, np.ndarray)) and not isinstance(
                val, (str, bytes)
            ):
                if len(val) > 0:
                    return float(val[0])
            if isinstance(val, (int, float)):
                return float(val)

        return None

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    def ready(self) -> bool:
        """Check whether all subscribed streams have received data."""
        if not self._buf:
            return False
        return all(len(dq) > 0 for dq in self._buf.values())

    @staticmethod
    def _require_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"zenoh_ros1_config.{field_name} must be a mapping")
        return value
