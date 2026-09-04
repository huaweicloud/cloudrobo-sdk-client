"""JAKA robot hardware adapter implementing :class:`IRobotHardwareAdapter`.

Runs a background daemon thread (default 125 Hz) for servo control.

Two control modes are supported:

- **joint** (default): uses ``servo_j`` for joint-space servo.
  Cartesian targets are IK-resolved via ``kine_inverse``.
- **cartesian**: uses ``servo_p`` for direct Cartesian servo without IK.

Design decisions (see grill-with-docs session):
* Control daemon thread with queue-bound waypoints — ``send_action()``
  is non-blocking.
* Cartesian mode uses ``servo_p`` natively; joint mode uses ``servo_j``.
* Per-joint/per-pose linear ramp interpolation — new targets override the
  ramp from the current interpolated position.
* Daemon caches last servo target and TCP pose; main thread reads cameras
  and gripper independently — no concurrent ``jkrc.RC`` access.
"""

from __future__ import annotations

import logging
import os as _os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np

from cloudrobo_r2c.core.interfaces import IRobotHardwareAdapter
from cloudrobo_r2c.robots.jaka.jaka_robot import _JakaControlThread
from cloudrobo_r2c.robots.jaka.opencv_camera import OpenCVCamera
from cloudrobo_r2c.robots.jaka.step_motor_gripper import StepMotorGripper


def create_jaka_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> IRobotHardwareAdapter:
    """Entry_point factory for JakaHardwareAdapter."""
    return JakaHardwareAdapter(config={"jaka": dict(config)})


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hardware adapter
# ---------------------------------------------------------------------------


@dataclass
class JakaHardwareAdapter(IRobotHardwareAdapter):
    """JAKA adapter with a background 125 Hz servo daemon.

    Config keys (all under ``hardware.config`` / ``jaka``)::

        ip: "192.168.1.101"      # robot IP
        control_mode: "joint"    # "joint" (default) | "cartesian" (servo_p direct)
        frequency: 125           # daemon loop Hz
        max_joint_speed: 1.0     # rad/s — joint mode ramp duration
        max_pos_speed: 0.25      # m/s — Cartesian mode max linear speed
        max_rot_speed: 0.6       # rad/s — Cartesian mode max angular speed
        sdk_dir: null            # optional path to JAKA SDK for libjakaAPI.so preload
        servo_move_mode: 0       # servo_j absolute-position mode
        do_info: [0, 0, 0]       # digital output state for servo_j
        init_joints: null        # optional home-on-connect joint positions
        robot_id: 0              # JAKA SDK robot identifier
        cameras: {}              # optional OpenCV camera specs
        gripper: null            # optional step-motor gripper config
    """

    config: Mapping[str, Any]

    _ctrl: Optional[_JakaControlThread] = field(default=None, init=False, repr=False)
    _connected: bool = field(default=False, init=False, repr=False)
    _cameras: Dict[str, OpenCVCamera] = field(
        default_factory=dict, init=False, repr=False
    )
    _gripper: Optional[StepMotorGripper] = field(default=None, init=False, repr=False)

    # -- IRobotHardwareAdapter --------------------------------------------

    def move_to(
        self,
        *,
        pose_euler: Optional[Sequence[float]] = None,
        pose_quat: Optional[Sequence[float]] = None,
        joints: Optional[Sequence[float]] = None,
    ) -> None:
        """Move the robot to an absolute joint target (go_home semantics).

        Only ``joints`` is supported on the typed interface — the joint
        list is forwarded to the daemon via :meth:`joint_move` (a blocking
        ``moveJ`` that pauses servo, moves, and re-enables servo).

        ``pose_euler`` / ``pose_quat`` are **not** handled here: they
        require JAKA-specific unit conversion (m → mm) and an IK /
        direct-drive dispatch on ``control_mode``. Use the
        ``commands.go_home`` YAML preset (handled by
        :class:`~cloudrobo_r2c.robots.commands.jaka.JakaGoHomeCommand`) to
        command a Cartesian home — see ADR-0007.
        """
        self._validate_move_to_inputs(pose_euler, pose_quat, joints)
        if joints is None:
            raise NotImplementedError(
                "JakaHardwareAdapter.move_to() only supports the 'joints' "
                "input. For pose_euler / pose_quat targets, use the "
                "commands.go_home YAML preset (JakaGoHomeCommand) instead."
            )
        self.joint_move([float(v) for v in joints[:6]], is_block=True)

    def connect(self) -> None:
        if self._connected:
            logger.debug("JAKA adapter already connected; skipping connect().")
            return

        cfg = self._robot_cfg
        ip = str(cfg.get("ip", "192.168.1.101"))

        # Resolve sdk_dir: config → env → None (rely on LD_LIBRARY_PATH)
        sdk_dir = cfg.get("sdk_dir") or _os.environ.get("JAKA_SDK_DIR")

        self._ctrl = _JakaControlThread(
            robot_ip=ip,
            frequency=int(cfg.get("frequency", 125)),
            max_joint_speed=float(cfg.get("max_joint_speed", 1.0)),
            init_joints=cfg.get("init_joints"),
            sdk_dir=sdk_dir,
            servo_move_mode=int(cfg.get("servo_move_mode", 0)),
            do_info=cfg.get("do_info", [0, 0, 0]),
            robot_id=int(cfg.get("robot_id", 0)),
            verbose=True,
            control_mode=str(cfg.get("control_mode", "joint")),
            max_pos_speed=float(cfg.get("max_pos_speed", 0.25)),
            max_rot_speed=float(cfg.get("max_rot_speed", 0.6)),
        )
        self._ctrl.start()

        if not self._ctrl.wait_ready(timeout=15):
            raise RuntimeError(f"Timeout connecting to Jaka Mini2 at {ip} (15 s)")
        if self._ctrl.connect_error is not None:
            raise RuntimeError(self._ctrl.connect_error)

        self._setup_cameras()
        self._setup_gripper()
        self._connected = True

    def disconnect(self) -> None:
        if self._ctrl is not None and self._ctrl.is_alive():
            self._ctrl.stop()
            self._ctrl.join(timeout=3)
        self._ctrl = None

        for camera in self._cameras.values():
            camera.stop()
        self._cameras.clear()

        if self._gripper is not None:
            self._gripper.disconnect()
            self._gripper = None

        self._connected = False

    def get_observation(self) -> Mapping[str, Any]:
        self._require_connected()

        # Joint positions from daemon cache
        joints: List[float]
        if self._ctrl is not None:
            cached = self._ctrl.get_cached_joints()
            joints = cached.tolist() if cached is not None else [0.0] * 6
        else:
            joints = [0.0] * 6

        observation: Dict[str, Any] = {
            "joint_names": [f"joint_{i}" for i in range(1, 7)],
            "joint_positions": joints[:6],
        }
        for idx, value in enumerate(joints[:6], start=1):
            observation[f"joint_{idx}"] = float(value)

        # TCP pose from daemon cache (available in both modes)
        if self._ctrl is not None:
            tcp_pose = self._ctrl.get_cached_tcp_pose()
            if tcp_pose is not None:
                observation["tcp_pose"] = np.asarray(
                    tcp_pose, dtype=np.float32
                ).tolist()

        # Camera frames (background-thread capture, non-blocking)
        for name, camera in self._cameras.items():
            frame = camera.get_latest_frame()
            if frame is not None:
                observation[name] = frame

        # Gripper (independent serial port — no RC access needed)
        if self._gripper is not None:
            gripper_percent = self._gripper.get_percent()
            if gripper_percent is not None:
                observation["gripper"] = gripper_percent

        return observation

    def send_action(self, command: Mapping[str, Any]) -> None:
        self._require_connected()
        ctrl = self._ctrl
        control_mode = str(self._robot_cfg.get("control_mode", "joint")).lower()

        # Joint path (default) or Cartesian path
        if control_mode == "cartesian":
            cart_target = self._extract_cartesian_target(command)
            if cart_target is not None:
                duration = self._compute_duration_for_cartesian(ctrl, cart_target)
                ctrl.servo_cartesian(cart_target, duration)
        else:
            joint_target = self._extract_joint_target(command)
            if joint_target is not None:
                duration = self._compute_duration(ctrl, joint_target)
                ctrl.servo_j(np.array(joint_target), duration)

        # Gripper (main thread, independent serial port)
        gripper_target = self._extract_gripper_target(command)
        if gripper_target is not None and self._gripper is not None:
            self._gripper.set_percent(gripper_target)

    def joint_move(self, joint_pos: Sequence[float], *, is_block: bool = False) -> None:
        """Queue a blocking joint_move via the daemon.

        The daemon pauses servo, performs the move, re-enables servo,
        and re-initialises the interpolator at the new position.

        Note: *is_block* is ignored — the daemon always uses blocking
        ``joint_move`` internally.  The method is non-blocking for the
        caller (it just queues the command).
        """
        self._require_connected()
        ctrl = self._ctrl
        ctrl.move_joints(np.array(joint_pos[:6]))

    def servo_cartesian(self, pose_6d: Sequence[float]) -> None:
        """Move the TCP to a 6D pose via the daemon's ``servo_cartesian`` (直驱).

        ``pose_6d`` is forwarded to the daemon unchanged — it must be in
        the JAKA SDK convention ``[x_mm, y_mm, z_mm, rx, ry, rz]`` (the
        public R2C schema uses metres, so callers in
        :class:`~cloudrobo_r2c.robots.commands.jaka.JakaGoHomeCommand` convert
        before calling this method).

        Effective behaviour depends on the adapter's ``control_mode``:

        - ``"joint"`` (default) — the daemon IK-resolves the pose via
          ``kine_inverse`` and rides on ``servo_j``. This is *not* 直驱
          from the application's perspective, even though the daemon
          method is called ``servo_cartesian``.
        - ``"cartesian"`` — the daemon forwards directly to
          ``servo_p`` (no IK). This is the real 直驱 path.

        See ADR-0007.
        """
        self._require_connected()
        ctrl = self._ctrl
        duration = self._compute_duration_for_cartesian(ctrl, pose_6d)
        ctrl.servo_cartesian(np.asarray(pose_6d[:6], dtype=np.float64), duration)

    def set_gripper(
        self,
        *,
        width: Optional[float] = None,
        action: Optional[str] = None,
    ) -> None:
        """Move or actuate the JAKA stepper-motor gripper.

        Exactly one of ``width`` or ``action`` must be supplied; passing
        both or neither raises :class:`ValueError`.

        - ``action='open'`` / ``'close'`` is forwarded to
          :meth:`gripper_control`, which maps to ``set_percent(100.0)``
          / ``set_percent(0.0)``.
        - ``width=<float>`` is **not supported** on the JAKA stepper-motor
          gripper and raises :class:`NotImplementedError` (the gripper
          has no physical metre unit — see ADR-0007).

        The string ``action`` is case- and whitespace-insensitive.
        """
        if (width is None) == (action is None):
            raise ValueError("set_gripper() requires exactly one of width or action")
        if width is not None:
            raise NotImplementedError(
                "JakaHardwareAdapter.set_gripper(width=...) is not supported. "
                "JAKA's stepper-motor gripper has no physical metre unit. "
                "Use action='open' or action='close' instead."
            )
        action_norm = action.strip().lower()
        if action_norm == "open":
            self.gripper_control("OPEN")
            return
        if action_norm == "close":
            self.gripper_control("CLOSE")
            return
        raise ValueError(
            f"set_gripper action must be 'open' or 'close', got {action!r}"
        )

    def gripper_control(self, action: str) -> None:
        """Open or close the gripper.

        ``action`` must be ``"OPEN"`` or ``"CLOSE"`` (case-insensitive).
        """
        if self._gripper is None:
            raise RuntimeError("Gripper is not configured")
        action = action.upper()
        if action == "OPEN":
            self._gripper.set_percent(100.0)
        elif action == "CLOSE":
            self._gripper.set_percent(0.0)
        else:
            raise ValueError(
                f"gripper_control action must be OPEN or CLOSE, got {action!r}"
            )

    # -- IK (public, for JakaGoHomeCommand) -------------------------------

    def kine_inverse(self, pose_6d_m: Sequence[float]) -> Optional[List[float]]:
        """Resolve a 6D Cartesian pose to joint angles via the JAKA SDK.

        ``pose_6d_m`` is in the public R2C schema convention
        ``[x, y, z, rx, ry, rz]`` with position in **metres** and rotation
        in radians. The JAKA SDK ``kine_inverse`` expects millimetres, so
        the conversion is performed here; the reference joint set is read
        from the robot's current position.

        Returns a list of 6 joint angles (radians) on success, or
        ``None`` if the SDK reports an IK failure (the caller decides
        policy — e.g. :class:`JakaGoHomeCommand` raises).

        Note: this reads the daemon's internal ``_robot`` handle, which
        is initialised during :meth:`connect`. The JAKA SDK calls are
        not thread-safe with the daemon's servo loop, so this is intended
        for sporadic go_home use, not high-frequency control.
        """
        self._require_connected()
        ctrl = self._ctrl
        robot = ctrl._robot  # type: ignore[attr-defined]
        if robot is None:
            raise RuntimeError("JAKA daemon robot handle is not initialised")
        pose_mm = np.array(
            [
                float(pose_6d_m[0]) * 1000.0,
                float(pose_6d_m[1]) * 1000.0,
                float(pose_6d_m[2]) * 1000.0,
                float(pose_6d_m[3]),
                float(pose_6d_m[4]),
                float(pose_6d_m[5]),
            ],
            dtype=np.float64,
        )
        joints = ctrl._ik_solve(robot, pose_mm)  # type: ignore[attr-defined]
        if joints is None:
            return None
        return [float(v) for v in joints[:6]]

    # -- config helper ----------------------------------------------------

    @property
    def _robot_cfg(self) -> Mapping[str, Any]:
        robot_cfg = self.config.get("jaka")
        if robot_cfg is None:
            robot_cfg = self.config
        if not isinstance(robot_cfg, Mapping):
            raise ValueError("jaka config must be a mapping")
        return robot_cfg

    # -- duration computation ---------------------------------------------

    def _compute_duration(
        self, ctrl: _JakaControlThread, target_joints: Sequence[float]
    ) -> float:
        """Compute ramp duration from max_joint_speed and current position."""
        cached = ctrl.get_cached_joints()
        if cached is None or len(cached) < 6:
            return 0.1
        max_delta = max(
            abs(float(t) - float(c)) for t, c in zip(target_joints[:6], cached[:6])
        )
        max_speed = float(self._robot_cfg.get("max_joint_speed", 1.0))
        duration = max_delta / max(max_speed, 0.001)
        return max(duration, 0.02)  # floor 20 ms

    def _compute_duration_for_cartesian(
        self, ctrl: _JakaControlThread, cart_target: Sequence[float]
    ) -> float:
        """Compute ramp duration using current TCP pose and speed limits.

        Falls back to 0.2 s when TCP pose is not yet available.
        """
        cached = ctrl.get_cached_tcp_pose()
        if cached is None:
            return 0.2  # fallback before first TCP read

        # Position delta (mm)
        pos_delta = float(
            np.linalg.norm(np.array(cart_target[:3], dtype=np.float64) - cached[:3])
        )
        max_pos_speed = (
            float(self._robot_cfg.get("max_pos_speed", 0.25)) * 1000.0
        )  # m/s → mm/s
        duration_pos = pos_delta / max(max_pos_speed, 0.001)

        # Rotation delta (rad) — approximate max abs diff
        rot_delta = max(
            abs(float(t) - float(c)) for t, c in zip(cart_target[3:], cached[3:])
        )
        max_rot_speed = float(self._robot_cfg.get("max_rot_speed", 0.6))
        duration_rot = rot_delta / max(max_rot_speed, 0.001)

        return max(duration_pos, duration_rot, 0.02)

    # -- target extraction ------------------------------------------------

    @staticmethod
    def _extract_joint_target(command: Mapping[str, Any]) -> Optional[List[float]]:
        if "joint_target" in command:
            payload = command.get("joint_target")
            if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
                return [float(v) for v in payload]

        if "joint_positions" in command:
            payload = command.get("joint_positions")
            if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
                return [float(v) for v in payload]

        joint_states = command.get("joint_states")
        if isinstance(joint_states, Mapping):
            positions = joint_states.get("position")
            if isinstance(positions, Sequence) and not isinstance(
                positions, (str, bytes)
            ):
                if (
                    positions
                    and isinstance(positions[0], Sequence)
                    and not isinstance(positions[0], (str, bytes))
                ):
                    return [float(v) for v in positions[0]]
                return [float(v) for v in positions]

        return None

    @staticmethod
    def _extract_cartesian_target(
        command: Mapping[str, Any],
    ) -> Optional[List[float]]:
        """Extract a 6D Cartesian pose ``[x_mm, y_mm, z_mm, rx, ry, rz]``.
        The convention matches JAKA SDK's RPY-in-radians input.
        """
        for key in ("tcp_pose", "eef_pose", "cartesian_target"):
            payload = command.get(key)
            if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
                return [float(v) for v in payload[:6]]

        pose_section = command.get("eef_state")
        if isinstance(pose_section, Mapping):
            pose = pose_section.get("pose")
            if isinstance(pose, Sequence) and not isinstance(pose, (str, bytes)):
                return [float(v) for v in pose[:6]]

        return None

    @staticmethod
    def _extract_gripper_target(command: Mapping[str, Any]) -> Optional[float]:
        for key in ("gripper", "gripper_percent"):
            value = command.get(key)
            if value is not None:
                return float(value)

        gripper_section = command.get("gripper_state")
        if isinstance(gripper_section, Mapping):
            value = gripper_section.get("position")
            if isinstance(value, Sequence) and value:
                return float(value[0])
            if value is not None:
                return float(value)
        return None

    # -- cameras / gripper setup ------------------------------------------

    def _setup_cameras(self) -> None:
        cameras_cfg = self._robot_cfg.get("cameras", {})
        if cameras_cfg is None:
            return
        if not isinstance(cameras_cfg, Mapping):
            raise ValueError("cameras must be a mapping")

        for name, spec in cameras_cfg.items():
            if not isinstance(spec, Mapping):
                raise ValueError(f"cameras[{name!r}] must be a mapping")
            if str(spec.get("type", "opencv")).lower() != "opencv":
                raise ValueError(
                    f"Only camera type 'opencv' is supported, got {spec.get('type')!r}"
                )
            camera = OpenCVCamera(
                name=str(name),
                index_or_path=spec.get("index_or_path", 0),
                width=int(spec.get("width", 640)),
                height=int(spec.get("height", 480)),
                fps=int(spec.get("fps", 25)),
            )
            camera.start()
            self._cameras[str(name)] = camera

    def _setup_gripper(self) -> None:
        gripper_cfg = self._robot_cfg.get("gripper")
        if gripper_cfg is None:
            return
        if not isinstance(gripper_cfg, Mapping):
            raise ValueError("gripper must be a mapping")

        serial_port = gripper_cfg.get("serial_port")
        if not serial_port:
            raise ValueError("gripper.serial_port is required when gripper is enabled")

        self._gripper = StepMotorGripper(
            port=str(serial_port),
            baudrate=int(gripper_cfg.get("baudrate", 115200)),
            timeout_s=float(gripper_cfg.get("timeout_s", 2.0)),
            motor_id=int(gripper_cfg.get("motor_id", 1)),
            grip_angle_open=int(gripper_cfg.get("grip_angle_open", 361)),
            grip_angle_closed=int(gripper_cfg.get("grip_angle_closed", 19070)),
            mode=int(gripper_cfg.get("mode", 0)),
            direction=int(gripper_cfg.get("direction", 0)),
            sub_divide=int(gripper_cfg.get("sub_divide", 16)),
            speed=int(gripper_cfg.get("speed", 1200)),
        )
        self._gripper.connect()

    # -- guards -----------------------------------------------------------

    def _require_connected(self) -> None:
        if not self._connected or self._ctrl is None:
            raise RuntimeError("Adapter is not connected. Call connect() first.")
        if not self._ctrl.is_alive():
            raise RuntimeError(
                "JAKA control daemon has stopped unexpectedly. "
                "Call disconnect() then connect() to restart."
            )
