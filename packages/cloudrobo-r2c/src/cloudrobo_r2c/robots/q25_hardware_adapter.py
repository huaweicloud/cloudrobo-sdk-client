"""Q25 Ultra hardware adapter for R2C SDK.

Adapts the Q25 Ultra mobile robot (ROS2-based) to the
:class:`IRobotHardwareAdapter` interface.

This adapter does **not** directly control the robot's motion. It is
responsible for two things only:
1. Retrieving camera images from the Q25 robot via ROS2.
2. Forwarding trajectory commands from the CloudAdapter to the Q25 robot
   via ROS2 ``nav_msgs/msg/Path``.

Communication Protocol
----------------------
1. On startup, send a ``call_method=reset`` observation.
2. Wait for CloudAdapter to reply with an action carrying
   ``call_method=reset`` and ``reset_status=0``.
3. Thereafter, send ``call_method=infer`` observations.
4. The observation's ``timestamp`` is taken from the ROS2 image message
   header stamp, **not** generated locally.
5. Block observation publishing until the CloudAdapter sends the next
   action (i.e. only one outstanding infer at a time).
6. Before forwarding the action to Q25, inject a ``timestamp`` field
   carrying the previously-sent observation timestamp.
7. Publish the modified action as a JSON string on the ROS2 trajectory
   topic, then allow the next observation to be sent.

ROS2 Trajectory Payload (``nav_msgs/Path``)
-------------------------------------------
Each waypoint ``[x, y, sin_yaw, cos_yaw]`` maps to a ``PoseStamped``:
- ``pose.position.x`` / ``pose.position.y``
- ``pose.orientation.z = sin_yaw``, ``pose.orientation.w = cos_yaw``

Configuration (``hardware.custom_config``)
-------------------------------------------
.. code-block:: yaml

    image_topic: "/camera/image_raw"
    trajectory_topic: "/track/cloud_trajectory"
    image_width: 224
    image_height: 224
    jpeg_quality: 90
    use_mock_image: false
    dry_run: true
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

import numpy as np

from cloudrobo_r2c.core.interfaces import IRobotHardwareAdapter

logger = logging.getLogger(__name__)


def create_q25_ros2_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> IRobotHardwareAdapter:
    """Entry_point factory for Q25HardwareAdapter (ROS2)."""
    return Q25HardwareAdapter(config=dict(config))


# ---------------------------------------------------------------------------
# Optional dependency guards
# ---------------------------------------------------------------------------

try:
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
except ImportError:
    rclpy = None  # type: ignore[assignment]
    Node = None  # type: ignore[assignment,misc]

try:
    from sensor_msgs.msg import Image as RosImage
    from nav_msgs.msg import Path as RosPath
    from geometry_msgs.msg import PoseStamped
except ImportError:
    RosImage = None  # type: ignore[assignment,misc]
    RosPath = None  # type: ignore[assignment,misc]
    PoseStamped = None  # type: ignore[assignment,misc]

try:
    from cv_bridge import CvBridge
except ImportError:
    CvBridge = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_IMAGE_TOPIC = "/camera/image_raw"
DEFAULT_TRAJECTORY_TOPIC = "/track/cloud_trajectory"
DEFAULT_IMAGE_WIDTH = 224
DEFAULT_IMAGE_HEIGHT = 224
DEFAULT_JPEG_QUALITY = 90

# Trajectory constants
TRAJECTORY_NUM_WAYPOINTS = 8
TRAJECTORY_DIMS = 4  # x, y, sin_yaw, cos_yaw


# ---------------------------------------------------------------------------
# Internal ROS2 node
# ---------------------------------------------------------------------------


class _Q25RosNode(Node):
    """Minimal ROS2 node: subscribes to camera, publishes trajectory string."""

    def __init__(self, cfg: Mapping[str, Any]) -> None:
        super().__init__("q25_hardware_adapter")

        self._lock = threading.Lock()

        # Latest image data (thread-safe)
        self.latest_jpeg_bytes: Optional[bytes] = None
        self.latest_image_timestamp_ms: int = 0

        # Resolve config
        image_topic = str(cfg.get("image_topic", DEFAULT_IMAGE_TOPIC))
        self._trajectory_topic = str(
            cfg.get("trajectory_topic", DEFAULT_TRAJECTORY_TOPIC)
        )
        self._jpeg_quality = int(cfg.get("jpeg_quality", DEFAULT_JPEG_QUALITY))
        self._image_width = int(cfg.get("image_width", DEFAULT_IMAGE_WIDTH))
        self._image_height = int(cfg.get("image_height", DEFAULT_IMAGE_HEIGHT))
        self._use_mock_image = bool(cfg.get("use_mock_image", False))

        # QoS
        qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )

        if self._use_mock_image:
            logger.info("Mock image mode enabled — generating solid gray 224x224 images")
            self._mock_counter = 0
            self._generate_mock_image()
            # Update mock image at ~10 Hz
            self.create_timer(0.1, self._mock_image_timer_callback)
        else:
            # Image subscriber (only when not in mock mode)
            if RosImage is not None:
                self._img_sub = self.create_subscription(
                    RosImage, image_topic, self._image_callback, qos
                )

        # Trajectory publisher (nav_msgs/Path)
        if RosPath is not None and PoseStamped is not None:
            self._traj_pub = self.create_publisher(
                RosPath, self._trajectory_topic, 10
            )

        # CvBridge (only needed for real ROS image decoding)
        self._cv_bridge = CvBridge() if CvBridge is not None and not self._use_mock_image else None

        logger.info(
            "Q25 ROS2 node initialised: image=%s trajectory=%s use_mock=%s",
            image_topic,
            self._trajectory_topic,
            self._use_mock_image,
        )

    # ------------------------------------------------------------------
    # Mock image (when use_mock_image = true)
    # ------------------------------------------------------------------

    def _generate_mock_image(self) -> None:
        """Generate a solid gray 224x224 JPEG as mock camera frame."""
        from PIL import Image  # type: ignore[import-untyped]
        import io

        target_size = self._image_width  # square
        arr = np.full((target_size, target_size, 3), 128, dtype=np.uint8)
        img = Image.fromarray(arr, mode="RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self._jpeg_quality)
        jpeg_bytes = buf.getvalue()

        now_ms = int(time.time() * 1000)
        with self._lock:
            self.latest_jpeg_bytes = jpeg_bytes
            self.latest_image_timestamp_ms = now_ms

    def _mock_image_timer_callback(self) -> None:
        """Timer callback to periodically update the mock image timestamp."""
        now_ms = int(time.time() * 1000)
        with self._lock:
            self.latest_image_timestamp_ms = now_ms

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _image_callback(self, msg: RosImage) -> None:
        """Convert ROS Image -> numpy -> JPEG bytes. Extract timestamp."""
        import cv2

        # Extract ROS header timestamp (seconds -> milliseconds)
        stamp_s = float(msg.header.stamp.sec)
        stamp_ns = float(msg.header.stamp.nanosec)
        timestamp_ms = int(stamp_s * 1000 + stamp_ns / 1_000_000)

        try:
            if self._cv_bridge is not None:
                cv_image = self._cv_bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
            else:
                # Manual decoding when cv_bridge is not available
                h, w = msg.height, msg.width
                step = msg.step
                if step == w * 3:
                    cv_image = np.frombuffer(msg.data, dtype=np.uint8).reshape(h, w, 3)
                else:
                    cv_image = np.frombuffer(msg.data, dtype=np.uint8).reshape(
                        h, step, 3
                    )[:, :w, :]
                if msg.encoding == "rgb8":
                    cv_image = cv2.cvtColor(cv_image, cv2.COLOR_RGB2BGR)
        except Exception as exc:
            logger.warning("Failed to decode image: %s", exc)
            return

        # Resize & centre-crop to target dimensions
        processed = self._preprocess_frame(cv_image)

        # JPEG encode
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality]
        ok, jpeg_buf = cv2.imencode(".jpg", processed, encode_params)
        if not ok:
            logger.warning("JPEG encode failed")
            return

        with self._lock:
            self.latest_jpeg_bytes = jpeg_buf.tobytes()
            self.latest_image_timestamp_ms = timestamp_ms

    # ------------------------------------------------------------------
    # Image preprocessing
    # ------------------------------------------------------------------

    def _preprocess_frame(self, frame: np.ndarray) -> np.ndarray:
        """Resize by short edge and centre-crop to target size."""
        import cv2

        target_size = self._image_width  # square
        h, w = frame.shape[:2]
        scale = target_size / min(h, w)
        new_h = int(h * scale)
        new_w = int(w * scale)
        resized = cv2.resize(frame, (new_w, new_h))
        start_x = (new_w - target_size) // 2
        start_y = (new_h - target_size) // 2
        return resized[start_y : start_y + target_size, start_x : start_x + target_size]

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def get_image(self) -> tuple[Optional[bytes], int]:
        """Return the latest JPEG frame and its ROS timestamp (ms).

        Returns
        -------
        tuple[Optional[bytes], int]
            (jpeg_bytes, timestamp_ms). If no frame is available yet,
            jpeg_bytes will be None and timestamp_ms will be 0.
        """
        with self._lock:
            if self.latest_jpeg_bytes is None:
                return None, 0
            return bytes(self.latest_jpeg_bytes), self.latest_image_timestamp_ms

    def publish_trajectory_path(
        self, waypoints: list[list[float]], timestamp_ms: int
    ) -> None:
        """Publish a trajectory as a ``nav_msgs/Path`` message.

        Each waypoint ``[x, y, sin_yaw, cos_yaw]`` is converted to a
        ``PoseStamped`` where:
        - ``pose.position.x`` = x
        - ``pose.position.y`` = y
        - ``pose.orientation.z`` = sin_yaw
        - ``pose.orientation.w`` = cos_yaw

        Parameters
        ----------
        waypoints:
            List of 8 waypoints, each ``[x, y, sin_yaw, cos_yaw]``.
        timestamp_ms:
            The observation timestamp to associate with this trajectory.
        """
        if RosPath is None or PoseStamped is None:
            logger.warning("nav_msgs not available; cannot publish trajectory Path")
            return

        msg = RosPath()
        # Fill header with the observation timestamp
        stamp_sec = timestamp_ms // 1000
        stamp_nsec = (timestamp_ms % 1000) * 1_000_000
        msg.header.stamp.sec = stamp_sec
        msg.header.stamp.nanosec = stamp_nsec
        msg.header.frame_id = "base_link"

        poses = []
        for i, wp in enumerate(waypoints):
            if len(wp) < 4:
                continue
            x, y, sin_yaw, cos_yaw = wp[0], wp[1], wp[2], wp[3]
            ps = PoseStamped()
            ps.header.stamp.sec = stamp_sec
            ps.header.stamp.nanosec = stamp_nsec + i  # tiny offset to preserve order
            ps.header.frame_id = "base_link"
            ps.pose.position.x = float(x)
            ps.pose.position.y = float(y)
            ps.pose.position.z = 0.0
            ps.pose.orientation.x = 0.0
            ps.pose.orientation.y = 0.0
            ps.pose.orientation.z = float(sin_yaw)
            ps.pose.orientation.w = float(cos_yaw)
            poses.append(ps)

        msg.poses = poses
        self._traj_pub.publish(msg)

        logger.info(
            "Published trajectory Path to %s: timestamp=%d, waypoints=%d",
            self._trajectory_topic,
            timestamp_ms,
            len(poses),
        )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@dataclass
class Q25HardwareAdapter(IRobotHardwareAdapter):
    """Q25 Ultra mobile robot hardware adapter.

    Does **not** directly control the robot. Exchanges data between the
    CloudAdapter and the Q25 robot via ROS2 + R2C protocol.

    State machine
    -------------
    INIT -> send reset obs -> wait for reset ack -> ready
    READY -> send infer obs (timestamp from ROS2 image stamp)
          -> wait for CloudAdapter action
          -> inject timestamp, publish to ROS2
          -> loop back to READY
    """

    config: Mapping[str, Any]

    # ROS2 internals
    _ros_node: Optional[_Q25RosNode] = field(default=None, init=False, repr=False)
    _executor: Any = field(default=None, init=False, repr=False)
    _spin_thread: Optional[threading.Thread] = field(
        default=None, init=False, repr=False
    )
    _connected: bool = field(default=False, init=False, repr=False)

    # Protocol state
    _call_id: float = field(default=0.0, init=False, repr=False)
    _sent_obs_timestamp_ms: int = field(default=0, init=False, repr=False)
    _reset_done: threading.Event = field(
        default_factory=threading.Event, init=False, repr=False
    )
    _reset_sent: bool = field(default=False, init=False, repr=False)
    _action_received: threading.Event = field(
        default_factory=threading.Event, init=False, repr=False
    )
    _waiting_for_infer: bool = field(default=False, init=False, repr=False)

    # ------------------------------------------------------------------
    # IRobotHardwareAdapter
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Initialise ROS2 node and wait for first camera frame."""
        if self._connected:
            logger.debug("Already connected")
            return

        if rclpy is None:
            raise ImportError(
                "rclpy is required for Q25HardwareAdapter. "
                "Install with: pip install rclpy"
            )

        if RosPath is None:
            raise ImportError(
                "ROS2 nav_msgs package is required. "
                "Install ROS2 Humble and source the setup script."
            )

        dry_run = self.config.get("dry_run", False)
        logger.info("Q25 adapter connecting (dry_run=%s)...", dry_run)

        if not rclpy.ok():
            rclpy.init()

        self._ros_node = _Q25RosNode(self.config)

        from rclpy.executors import SingleThreadedExecutor

        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self._ros_node)
        self._spin_thread = threading.Thread(
            target=self._executor.spin, daemon=True, name="q25-ros-spin"
        )
        self._spin_thread.start()

        # Wait briefly for the first image (skip in mock mode — image is instant)
        if not self._ros_node._use_mock_image:
            logger.info("Waiting for first camera frame...")
            for _ in range(50):
                jpeg_bytes, ts = self._ros_node.get_image()
                if jpeg_bytes is not None and ts > 0:
                    logger.info("First camera frame received (timestamp=%d)", ts)
                    break
                time.sleep(0.1)
            else:
                logger.warning("No camera frame received after 5s; continuing anyway")
        else:
            logger.info("Mock image mode — skipping camera frame wait")

        self._call_id = random.random()
        self._reset_sent = False
        self._reset_done.clear()
        self._waiting_for_infer = False
        self._action_received.set()  # allow first obs to go through
        self._sent_obs_timestamp_ms = 0

        self._connected = True
        logger.info("Q25 adapter connected (call_id=%.6f)", self._call_id)

    def disconnect(self) -> None:
        """Shut down ROS2 node and release resources."""
        if not self._connected:
            return

        logger.info("Q25 adapter disconnecting...")

        if self._executor is not None and self._ros_node is not None:
            try:
                self._executor.remove_node(self._ros_node)
            except Exception as exc:
                logger.debug("Failed to remove ROS node from executor: %s", exc)
            self._executor.shutdown()

        if self._spin_thread is not None:
            self._spin_thread.join(timeout=5.0)
            self._spin_thread = None

        if self._ros_node is not None:
            try:
                self._ros_node.destroy_node()
            except Exception as exc:
                logger.debug("Failed to destroy ROS node: %s", exc)
            self._ros_node = None

        self._executor = None
        self._connected = False
        logger.info("Q25 adapter disconnected")

    def get_observation(self) -> Mapping[str, Any]:
        """Return the latest observation, blocking if needed per protocol.

        **Blocking behaviour**:
        - If the previous infer obs has not yet been answered by an action
          from the CloudAdapter, this method **blocks** until the action
          arrives (via :meth:`send_action`).
        - The first call sends a ``call_method=reset`` obs and blocks
          until the CloudAdapter acknowledges the reset.

        Returns
        -------
        dict
            Keys:
            - ``front``: JPEG bytes (for ``images.color.image`` mapping)
            - ``call_method``: float, < 0.5 = reset, >= 0.5 = infer
            - ``call_id``: float, session-unique ID
            - ``task_id``: float, fixed to 10.0
            - ``reset_status``: float, always 0.0
        """
        self._ensure_connected()

        if self._ros_node is None:
            raise RuntimeError("ROS2 node not initialised")

        jpeg_bytes, image_ts_ms = self._ros_node.get_image()

        # ========================
        if not self._reset_sent:
            self._reset_sent = True
            self._action_received.clear()

            timestamp_ms = image_ts_ms if image_ts_ms > 0 else int(time.time() * 1000)
            self._sent_obs_timestamp_ms = timestamp_ms

            obs: Dict[str, Any] = {
                "call_method": 0.0,
                "call_id": self._call_id,
                "task_id": 10.0,
            }

            if jpeg_bytes is not None:
                obs["front"] = jpeg_bytes
            else:
                obs["front"] = _MINIMAL_JPEG

            log_obs = {
                k: (
                    f"<{len(v)} bytes>"
                    if k == "front" and isinstance(v, bytes)
                    else v
                )
                for k, v in obs.items()
            }
            logger.info("[OBS RESET] timestamp=%d %s", timestamp_ms, log_obs)

            # Return obs immediately WITHOUT blocking.
            # SyncRobotClient will publish it, then cloud adapter sends reset ack
            # back, and send_action() sets _reset_done to allow infer flow.
            return obs

        # ========================
        # State: wait for reset ack before proceeding to infer
        # ========================
        if not self._reset_done.is_set():
            logger.info("Waiting for CloudAdapter reset ack...")
            self._action_received.wait()
            logger.info("Reset ack received")

        # ========================
        # State: INFER (block until previous action is consumed)
        # ========================
        if self._waiting_for_infer:
            logger.debug(
                "Waiting for CloudAdapter action (last obs timestamp=%d)...",
                self._sent_obs_timestamp_ms,
            )
            self._action_received.wait()
            logger.debug("Action received, proceeding with next obs")

        timestamp_ms = image_ts_ms if image_ts_ms > 0 else int(time.time() * 1000)
        self._sent_obs_timestamp_ms = timestamp_ms
        self._waiting_for_infer = True
        self._action_received.clear()

        obs = {
            "call_method": 1.0,
            "call_id": self._call_id,
            "task_id": 10.0,
        }

        if jpeg_bytes is not None:
            obs["front"] = jpeg_bytes
        else:
            obs["front"] = _MINIMAL_JPEG

        log_obs = {
            k: (
                f"<{len(v)} bytes>"
                if k == "front" and isinstance(v, bytes)
                else v
            )
            for k, v in obs.items()
        }
        logger.info("[OBS INFER] timestamp=%d %s", timestamp_ms, log_obs)

        return obs

    def send_action(self, command: Mapping[str, Any]) -> None:
        """Process an action from the CloudAdapter."""
        self._ensure_connected()

        dry_run = self.config.get("dry_run", False)

        # Parse trajectory from joint_states.position
        joint_states = command.get("joint_states", {})
        position = joint_states.get("position", []) if isinstance(joint_states, Mapping) else []

        # --- Log raw received action data ---
        log_cmd = _summarize_mapping(command)
        logger.info("[ACT RAW] %s", log_cmd)

        # ========================
        # Check for reset ack
        # ========================
        if self._try_handle_reset_ack(command):
            return

        # ========================
        # Normal infer action
        # ========================
        flat_positions = self._extract_flat_positions(position)
        if flat_positions is None:
            return

        obs_timestamp = self._sent_obs_timestamp_ms

        logger.info(
            "[ACT INFER] timestamp=%d waypoints=%d dry_run=%s",
            obs_timestamp,
            len(flat_positions) // TRAJECTORY_DIMS,
            dry_run,
        )

        if dry_run:
            self._log_dry_run_waypoints(flat_positions)
        else:
            waypoints = [
                flat_positions[i : i + TRAJECTORY_DIMS]
                for i in range(0, len(flat_positions), TRAJECTORY_DIMS)
            ]
            if self._ros_node is not None:
                self._ros_node.publish_trajectory_path(waypoints, obs_timestamp)

        # Unblock get_observation for the next cycle
        self._waiting_for_infer = False
        self._action_received.set()

    def _try_handle_reset_ack(self, command: Mapping[str, Any]) -> bool:
        """Check if the action is a reset acknowledgement and handle it.

        Returns True if the action was handled as a reset ack (caller should return).
        """
        ee_states = command.get("end_effector_states", {})
        if not isinstance(ee_states, Mapping):
            return False
        ee_position = ee_states.get("position", [])
        if not ee_position or self._reset_done.is_set():
            return False

        ee_position_flat = self._flatten_position(ee_position)
        call_method = float(ee_position_flat[0]) if len(ee_position_flat) > 0 else 0.0
        reset_status = float(ee_position_flat[1]) if len(ee_position_flat) > 1 else 0.0

        if call_method < 0.5 and reset_status == 0.0:
            logger.info("[ACT RESET ACK] Reset acknowledged by CloudAdapter")
            self._reset_done.set()
            self._action_received.set()
            return True
        elif call_method < 0.5 and reset_status != 0.0:
            logger.error("[ACT RESET FAILED] reset_status=%s", reset_status)
            self._reset_sent = False
            self._action_received.set()
            return True

        return False

    def _extract_flat_positions(self, position: Any) -> Optional[list[float]]:
        """Extract and validate flat position list from joint_states.position.

        Returns the flattened position list if valid, None otherwise.
        """
        if not position or len(position) < TRAJECTORY_NUM_WAYPOINTS * TRAJECTORY_DIMS:
            logger.debug(
                "No valid trajectory in command: joint_states.position=%s", position
            )
            self._waiting_for_infer = False
            self._action_received.set()
            return None

        flat_positions = self._flatten_position(position)

        if len(flat_positions) < TRAJECTORY_NUM_WAYPOINTS * TRAJECTORY_DIMS:
            logger.debug("Insufficient trajectory data: %d values", len(flat_positions))
            self._waiting_for_infer = False
            self._action_received.set()
            return None

        return flat_positions

    @staticmethod
    def _flatten_position(position: Any) -> list[float]:
        """Flatten a nested position list to a single level."""
        result = list(position)
        if result and isinstance(result[0], (list, tuple)):
            result = list(result[0])
        return result

    @staticmethod
    def _log_dry_run_waypoints(flat_positions: list[float]) -> None:
        """Log waypoints in dry-run mode."""
        for i in range(0, len(flat_positions), TRAJECTORY_DIMS):
            pt = flat_positions[i : i + TRAJECTORY_DIMS]
            if len(pt) >= 4:
                logger.info(
                    "[DRY_RUN] Waypoint %d: x=%.3f, y=%.3f, sin=%.3f, cos=%.3f",
                    i // TRAJECTORY_DIMS,
                    pt[0], pt[1], pt[2], pt[3],
                )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("Adapter is not connected. Call connect() first.")


# ---------------------------------------------------------------------------
# Minimal JPEG placeholder
# ---------------------------------------------------------------------------


def _make_placeholder_jpeg(
    width: int = 224, height: int = 224, quality: int = 90
) -> bytes:
    """Generate a random-colour JPEG placeholder of the given size."""
    from PIL import Image  # type: ignore[import-untyped]

    import io

    arr = np.random.randint(0, 256, (height, width, 3), dtype=np.uint8)
    img = Image.fromarray(arr, mode="RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


_MINIMAL_JPEG = _make_placeholder_jpeg()


# ---------------------------------------------------------------------------
# Logging helper — summarize mapping for log (images show byte count only)
# ---------------------------------------------------------------------------


def _summarize_mapping(mapping: Mapping[str, Any], _depth: int = 0) -> Mapping[str, Any]:
    """Recursively summarise a mapping for logging; images show ``<N bytes>``."""
    if _depth > 5:
        return {"...": "..."}
    result: Dict[str, Any] = {}
    for k, v in mapping.items():
        if isinstance(v, bytes):
            result[k] = f"<{len(v)} bytes>"
        elif isinstance(v, Mapping):
            result[k] = _summarize_mapping(v, _depth + 1)
        elif isinstance(v, (list, tuple)):
            if v and isinstance(v[0], (list, tuple)):
                result[k] = [
                    _summarize_mapping({"item": x}, _depth + 1)["item"]
                    if isinstance(x, Mapping)
                    else (f"<{len(x)} floats>" if len(x) > 10 else x)
                    for x in v
                ]
            elif len(v) > 10:
                result[k] = f"<{len(v)} floats>"
            else:
                result[k] = v
        else:
            result[k] = v
    return result
