"""A1Z + G1Z gripper hardware adapter for R2C SDK.

Single-arm 6-DOF robot with G1Z gripper, driven through the GALAXEA-A1Z
Python SDK via SocketCAN.  Camera frames are captured through a dedicated
per-camera background thread (ADR-0006) to guarantee freshness regardless
of the consumer's read cadence.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from cloudrobo_r2c.core.interfaces import IRobotHardwareAdapter

logger = logging.getLogger(__name__)

logging.getLogger("can.interfaces.socketcan").setLevel(logging.WARNING)

# ── Camera freshness thresholds (ADR-0006) ──────────────────────────────────
_STALE_THRESHOLD_MS = 200.0
_BROKEN_THRESHOLD_S = 1.0
_STALE_WARNING_DEBOUNCE_S = 5.0
_CAPTURE_THREAD_JOIN_TIMEOUT_S = 2.0

# ── A1Z defaults ───────────────────────────────────────────────────────────
_DEFAULT_CAN_CHANNEL = "can0"
_DEFAULT_CONTROL_FREQ_HZ = 250
_DEFAULT_GRIPPER_MAX_TORQUE = 2.0  # Nm
_DEFAULT_GRIPPER_OPEN_WIDTH = 1.0   # normalised 0..1
_DEFAULT_GRIPPER_CLOSE_WIDTH = 0.0  # normalised 0..1

# Recognised send_action command keys
_RECOGNISED_ACTION_KEYS = {"joint_positions", "gripper", "gripper_action"}

# ── Camera slot (thread-safe single-producer single-consumer) ──────────────


@dataclass
class _CameraSlot:
    """Thread-safe slot holding the freshest frame from one camera.

    Producer (capture thread) calls :meth:`put` on every successful
    ``cap.read()``.  Consumer (``get_observation``) calls :meth:`get`
    to read the latest payload.
    """

    name: str
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )
    _payload: Tuple[Optional[np.ndarray], float, Optional[str]] = field(
        default=(None, 0.0, None), init=False, repr=False
    )
    _warned: bool = field(default=False, init=False, repr=False)
    _last_stale_warning: float = field(default=0.0, init=False, repr=False)

    def put(
        self,
        frame: Optional[np.ndarray],
        timestamp: float,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._payload = (frame, timestamp, error)

    def get(self) -> Tuple[Optional[np.ndarray], float, Optional[str]]:
        with self._lock:
            return self._payload


# ── Factory ─────────────────────────────────────────────────────────────────


def create_a1z_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> IRobotHardwareAdapter:
    """Entry_point factory for A1ZHardwareAdapter."""
    return A1ZHardwareAdapter(config=dict(config))


# ── Adapter ─────────────────────────────────────────────────────────────────


@dataclass
class A1ZHardwareAdapter(IRobotHardwareAdapter):
    """Hardware adapter for the GALAXEA A1Z 6-DOF arm with G1Z gripper.

    YAML config keys (all under ``hardware.config``)::

        can_channel:           str   = "can0"
        control_freq_hz:       int   = 250
        gripper_max_torque:    float = 2.0      # Nm
        gripper_open_width:    float = 1.0       # normalised
        gripper_close_width:   float = 0.0       # normalised
        cameras:               dict  = {}        # {name: {source, width, height, fps}}
        commands:              dict              # standard r2c command blocks
    """

    config: Mapping[str, Any]

    _robot: Any = field(default=None, init=False, repr=False)
    _bus: Any = field(default=None, init=False, repr=False)
    _connected: bool = field(default=False, init=False, repr=False)

    # Camera plumbing (ADR-0006 capture-thread pattern)
    _camera_captures: Dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _camera_slots: Dict[str, _CameraSlot] = field(default_factory=dict, init=False, repr=False)
    _camera_threads: Dict[str, threading.Thread] = field(default_factory=dict, init=False, repr=False)
    _camera_stop_events: Dict[str, threading.Event] = field(default_factory=dict, init=False, repr=False)
    _connect_time: float = field(default=0.0, init=False, repr=False)

    # ── Command registration ──────────────────────────────────────────────

    def __post_init__(self) -> None:
        from cloudrobo_r2c.robots.commands.a1z import A1ZGoHomeCommand, A1ZEstopCommand

        self.register_command_class("go_home", A1ZGoHomeCommand)
        self.register_command_class("estop", A1ZEstopCommand)

    # ── IRobotHardwareAdapter core ────────────────────────────────────────

    def connect(self) -> None:
        if self._connected:
            logger.debug("Already connected, skipping connect()")
            return

        can_channel = self.config.get("can_channel", _DEFAULT_CAN_CHANNEL)
        control_freq_hz = int(self.config.get("control_freq_hz", _DEFAULT_CONTROL_FREQ_HZ))
        gripper_max_torque = float(self.config.get("gripper_max_torque", _DEFAULT_GRIPPER_MAX_TORQUE))

        logger.info(
            "A1Z: initialising on %s @ %d Hz, gripper max_torque=%.1f Nm",
            can_channel,
            control_freq_hz,
            gripper_max_torque,
        )

        from a1z.robots.get_robot import get_a1z_robot

        self._robot = get_a1z_robot(
            can_channel=can_channel,
            control_freq_hz=control_freq_hz,
            with_gripper=True,
            gripper_max_torque=gripper_max_torque,
            zero_gravity_mode=False,
        )
        self._bus = getattr(self._robot, "_bus", None)
        self._robot.start()
        logger.info("A1Z: control loop running")

        self._open_cameras()
        self._connect_time = time.monotonic()
        self._connected = True
        logger.info("A1Z adapter connected successfully")

    def disconnect(self) -> None:
        if not self._connected:
            logger.debug("Already disconnected, skipping disconnect()")
            return

        # Stop capture threads first, then release cameras
        for stop_event in self._camera_stop_events.values():
            stop_event.set()
        for name, thread in self._camera_threads.items():
            thread.join(timeout=_CAPTURE_THREAD_JOIN_TIMEOUT_S)
            if thread.is_alive():
                logger.warning(
                    "Camera capture thread %r did not exit within %.1fs",
                    name,
                    _CAPTURE_THREAD_JOIN_TIMEOUT_S,
                )

        self._release_cameras()

        if self._robot is not None:
            logger.info("A1Z: stopping control loop")
            self._robot.stop()
            self._robot = None

        self._bus = None
        self._connected = False
        logger.info("A1Z adapter disconnected")

    def get_observation(self) -> Mapping[str, Any]:
        self._ensure_connected()

        joint_pos = self._robot.get_joint_pos()  # 7D: [j1..j6, gripper_norm]
        result: Dict[str, Any] = {
            "joint_positions": np.asarray(joint_pos, dtype=np.float32),
        }

        # Camera frames via capture-thread slots (ADR-0006)
        if self._camera_captures:
            camera_meta: Dict[str, Dict[str, Any]] = {}
            now = time.monotonic()
            for name in self._camera_captures:
                slot = self._camera_slots.get(name)
                thread = self._camera_threads.get(name)

                frame, timestamp, error = (
                    slot.get() if slot is not None else (None, 0.0, None)
                )

                if error is not None:
                    camera_meta[name] = {
                        "timestamp": 0.0, "age_ms": 0.0,
                        "stale": False, "not_ready": False, "error": error,
                    }
                    continue

                if slot is None or thread is None or not thread.is_alive():
                    camera_meta[name] = {
                        "timestamp": 0.0, "age_ms": 0.0,
                        "stale": False, "not_ready": True,
                        "error": "capture thread not running",
                    }
                    continue

                if timestamp == 0.0:
                    if not slot._warned:
                        elapsed = now - self._connect_time
                        if elapsed > _BROKEN_THRESHOLD_S:
                            logger.warning("Camera %r broken: no frame after %.1fs", name, elapsed)
                        else:
                            logger.warning("Camera %r not yet ready", name)
                        slot._warned = True
                    camera_meta[name] = {
                        "timestamp": 0.0, "age_ms": 0.0,
                        "stale": False, "not_ready": True, "error": None,
                    }
                    continue

                age_ms = (now - timestamp) * 1000.0
                stale = age_ms > _STALE_THRESHOLD_MS
                if stale and (now - slot._last_stale_warning) > _STALE_WARNING_DEBOUNCE_S:
                    logger.warning(
                        "Camera %r frame is %.0f ms old (threshold %.0f ms)",
                        name, age_ms, _STALE_THRESHOLD_MS,
                    )
                    slot._last_stale_warning = now

                result[name] = frame
                camera_meta[name] = {
                    "timestamp": timestamp, "age_ms": age_ms,
                    "stale": stale, "not_ready": False, "error": None,
                }

            result["camera_meta"] = camera_meta

        return result

    def send_action(self, command: Mapping[str, Any]) -> None:
        self._ensure_connected()

        dry_run = self.config.get("dry_run", False)
        if dry_run:
            logger.info("[DRY_RUN] action: %s", {k: v for k, v in command.items()})
            return

        # ── resolve joint positions ──────────────────────────────────
        # Accept three forms (in priority order):
        #   1. "joint_positions": [j0..j5, gripper]  — full 7D array
        #   2. "joint_positions": [j0..j5]            — 6D array
        #   3. "joint_0".."joint_5"                   — per-joint scalars
        #        (from r2c_to_device per-index mappings)
        if "joint_positions" in command:
            target = np.asarray(command["joint_positions"], dtype=np.float64)
            self._robot.command_joint_pos(target)
        elif any(f"joint_{i}" in command for i in range(6)):
            joints = np.array(
                [float(command.get(f"joint_{i}", 0.0)) for i in range(6)],
                dtype=np.float64,
            )
            self._robot.command_joint_pos(joints)

        # ── gripper ───────────────────────────────────────────────────
        # Priority: gripper_action > gripper > per-joint gripper
        if "gripper_action" in command:
            action = str(command["gripper_action"]).strip().lower()
            if action == "open":
                self._robot.command_gripper(1.0)
            elif action == "close":
                self._robot.command_gripper(0.0)
            else:
                logger.warning("A1Z: unknown gripper_action %r, ignoring", action)
        elif "gripper" in command:
            norm = self._width_to_norm(float(command["gripper"]))
            self._robot.command_gripper(norm)

    # ── typed interface (used by GoHomeCommand) ──────────────────────────

    def move_to(
        self,
        *,
        pose_euler: Optional[Sequence[float]] = None,
        pose_quat: Optional[Sequence[float]] = None,
        joints: Optional[Sequence[float]] = None,
    ) -> None:
        """Move the arm to a target.  ``joints`` only; pose keys are rejected."""
        self._ensure_connected()

        if pose_euler is not None or pose_quat is not None:
            raise ValueError(
                "A1Z move_to: pose_euler / pose_quat are not supported "
                "(A1Z has no built-in IK). Use 'joints' as a list of "
                "6 floats in radians."
            )
        if joints is None:
            raise ValueError(
                "A1Z move_to: exactly one of pose_euler / pose_quat / joints "
                "is required"
            )

        joints_list = [float(v) for v in joints]
        if len(joints_list) != 6:
            raise ValueError(
                f"A1Z move_to: joints must have 6 elements, got {len(joints_list)}"
            )

        speed = float(self.config.get("move_speed", 0.5))
        self._robot.move_joints(np.asarray(joints_list, dtype=np.float64), speed=speed)

    def set_gripper(
        self,
        *,
        width: Optional[float] = None,
        action: Optional[str] = None,
    ) -> None:
        """Move the gripper to a target width or perform an open/close action."""
        self._ensure_connected()

        if (width is None) == (action is None):
            raise ValueError(
                "set_gripper() requires exactly one of width or action"
            )

        if action is not None:
            normalised = action.strip().lower()
            if normalised == "open":
                self._robot.command_gripper(1.0)
            elif normalised == "close":
                self._robot.command_gripper(0.0)
            else:
                raise ValueError(
                    f"set_gripper action must be 'open' or 'close', got {action!r}"
                )
            return

        norm = self._width_to_norm(float(width))
        self._robot.command_gripper(norm)

    def estop(self) -> None:
        """Engage soft emergency stop."""
        self._ensure_connected()
        self._robot.estop()
        logger.warning("A1Z: ESTOP engaged")

    def release(self) -> None:
        """Release the estop latch."""
        self._ensure_connected()
        self._robot.release()
        logger.info("A1Z: ESTOP released")

    @property
    def is_estopped(self) -> bool:
        if self._robot is None:
            return False
        return bool(getattr(self._robot, "is_estopped", False))

    # ── internal helpers ──────────────────────────────────────────────────

    def _ensure_connected(self) -> None:
        if not self._connected or self._robot is None:
            raise RuntimeError("A1Z adapter is not connected. Call connect() first.")

    def _width_to_norm(self, width: float) -> float:
        """Map a physical width (metres or arbitrary) to normalised [0, 1]."""
        open_w = float(self.config.get("gripper_open_width", _DEFAULT_GRIPPER_OPEN_WIDTH))
        close_w = float(self.config.get("gripper_close_width", _DEFAULT_GRIPPER_CLOSE_WIDTH))
        span = open_w - close_w
        if abs(span) < 1e-9:
            return 0.5
        norm = (width - close_w) / span
        return float(np.clip(norm, 0.0, 1.0))

    # ── Camera management (ADR-0006 capture-thread pattern) ────────────────

    def _open_cameras(self) -> None:
        import cv2

        cameras = self.config.get("cameras") or {}
        if not isinstance(cameras, Mapping):
            return

        for name, cam_config in cameras.items():
            if not isinstance(cam_config, Mapping):
                logger.warning("Camera %r config is not a mapping, skipping", name)
                continue

            source = cam_config.get("source", 0)
            cap = cv2.VideoCapture(
                int(source) if isinstance(source, int) else str(source), cv2.CAP_V4L2
            )
            if not cap.isOpened():
                logger.warning("Failed to open camera %r (source=%s), skipping", name, source)
                continue

            # MJPEG mode for direct JPEG byte access
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))
            if "width" in cam_config:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(cam_config["width"]))
            if "height" in cam_config:
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(cam_config["height"]))
            if "fps" in cam_config:
                cap.set(cv2.CAP_PROP_FPS, float(cam_config["fps"]))
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            # Check if MJPEG was accepted
            fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
            is_mjpg = (fourcc == cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))
            if is_mjpg:
                cap.set(cv2.CAP_PROP_CONVERT_RGB, 1)

            # Flush stale frames
            for _ in range(5):
                cap.read()

            self._camera_captures[name] = cap

            slot = _CameraSlot(name=name)
            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._capture_loop,
                args=(name, cap, slot, stop_event, is_mjpg),
                name=f"a1z-cam-{name}",
                daemon=True,
            )
            self._camera_slots[name] = slot
            self._camera_stop_events[name] = stop_event
            self._camera_threads[name] = thread
            thread.start()
            logger.info(
                "A1Z camera %r opened: source=%s (%s)",
                name, source, "MJPEG" if is_mjpg else "raw",
            )

    def _capture_loop(
        self,
        name: str,
        cap: Any,
        slot: _CameraSlot,
        stop_event: threading.Event,
        is_mjpg: bool,
    ) -> None:
        """Continuously drain the camera into the slot for freshness.

        For MJPEG cameras the raw buffer is returned so the translator
        can encode once to JPEG in the data pipeline — no decode/re-encode
        round-trip.  For non-MJPEG cameras we return the BGR ndarray.
        """
        try:
            while not stop_event.is_set():
                ok, buf = cap.read()
                if ok and buf is not None:
                    slot.put(buf, time.monotonic())
        except Exception as exc:
            logger.exception("A1Z capture thread %r died", name)
            slot.put(None, 0.0, str(exc))

    def _release_cameras(self) -> None:
        import cv2

        for name, cap in self._camera_captures.items():
            try:
                cap.release()
                logger.info("A1Z camera %r released", name)
            except Exception as e:
                logger.warning("Failed to release camera %r: %s", name, e)
        self._camera_captures.clear()
        self._camera_slots.clear()
        self._camera_threads.clear()
        self._camera_stop_events.clear()
