"""JAKA control daemon — background thread that runs ``servo_j`` at a fixed frequency.

Extracted from the adapter so it can be reused independently of camera/gripper wiring.
"""

from __future__ import annotations

import logging
import os as _os
import queue
import sys as _sys
import threading
import time
from typing import Any, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Joint interpolator
# ---------------------------------------------------------------------------


class _JointInterpolator:
    """Per-joint linear ramp interpolator.

    A new target replaces the ramp immediately — interpolation starts
    from the *current* interpolated position at the moment of the call,
    not the previous target.
    """

    __slots__ = ("_start", "_target", "_start_time", "_duration")

    def __init__(self, current_joints: np.ndarray) -> None:
        arr = np.asarray(current_joints, dtype=np.float64)
        self._start = arr.copy()
        self._target = arr.copy()
        self._start_time = time.monotonic()
        self._duration = 0.0

    def set_target(self, target: np.ndarray, duration: float) -> None:
        """Ramp from *current* interpolated position to ``target``."""
        self._start = self.current()
        self._target = np.array(target, dtype=np.float64)
        self._start_time = time.monotonic()
        self._duration = max(float(duration), 0.001)

    def current(self) -> np.ndarray:
        """Evaluate interpolator at the current time."""
        return self(time.monotonic())

    def __call__(self, t: float) -> np.ndarray:
        if self._duration <= 0.0:
            return self._target.copy()
        elapsed = t - self._start_time
        if elapsed >= self._duration:
            return self._target.copy()
        alpha = min(max(elapsed / self._duration, 0.0), 1.0)
        return self._start + alpha * (self._target - self._start)


# ---------------------------------------------------------------------------
# Cartesian interpolator (linear position + Slerp rotation)
# ---------------------------------------------------------------------------


class _CartesianInterpolator:
    """Linear position + Slerp rotation ramp interpolator.

    Uses Jaka native pose format: ``[x_mm, y_mm, z_mm, rx_rad, ry_rad, rz_rad]``.
    Rotation interpolation is done via ``scipy.spatial.transform.Slerp``.
    """

    __slots__ = ("_start_pose", "_target_pose", "_start_time", "_duration")

    def __init__(self, init_pose: np.ndarray) -> None:
        arr = np.asarray(init_pose[:6], dtype=np.float64)
        self._start_pose = arr.copy()
        self._target_pose = arr.copy()
        self._start_time = time.monotonic()
        self._duration = 0.0

    def set_target(self, target_pose: np.ndarray, duration: float) -> None:
        self._start_pose = self.current()
        self._target_pose = np.array(target_pose[:6], dtype=np.float64)
        self._start_time = time.monotonic()
        self._duration = max(float(duration), 0.001)

    def current(self) -> np.ndarray:
        return self(time.monotonic())

    def __call__(self, t: float) -> np.ndarray:
        if self._duration <= 0.0:
            return self._target_pose.copy()
        elapsed = t - self._start_time
        if elapsed >= self._duration:
            return self._target_pose.copy()
        alpha = min(max(elapsed / self._duration, 0.0), 1.0)
        # Linear position
        pos = (
            self._start_pose[:3]
            + alpha * (self._target_pose[:3] - self._start_pose[:3])
        )
        # Slerp rotation (Jaka format: RPY radians)
        from scipy.spatial.transform import Rotation, Slerp

        rot_start = Rotation.from_euler("xyz", self._start_pose[3:])
        rot_end = Rotation.from_euler("xyz", self._target_pose[3:])
        slerp = Slerp([0, 1], Rotation.concatenate([rot_start, rot_end]))
        rot_interp = slerp(alpha).as_euler("xyz")
        return np.concatenate([pos, rot_interp])


# ---------------------------------------------------------------------------
# Precise wait helper
# ---------------------------------------------------------------------------


def _precise_wait(t_end: float) -> None:
    """Busy-wait helper: sleep bulk, spin remainder for low jitter."""
    slack = 0.001
    t_now = time.monotonic()
    wait = t_end - t_now
    if wait > slack:
        time.sleep(wait - slack)
    while time.monotonic() < t_end:
        pass


# ---------------------------------------------------------------------------
# Control daemon
# ---------------------------------------------------------------------------


class _JakaControlThread(threading.Thread):
    """Background daemon that runs servo at a fixed frequency.

    The main thread queues waypoints via ``servo_j()``, ``move_joints()``,
    or ``servo_cartesian()``.  The daemon drains all queued commands each
    cycle and interpolates linearly.

    Two control modes are supported:

    - **joint** (default): uses ``servo_j`` for joint-space servo.
      Cartesian targets are IK-resolved via ``kine_inverse``.
    - **cartesian**: uses ``servo_p`` for direct Cartesian servo (no IK).
      Joint-space targets are ignored (with a warning).

    The daemon caches the last-servoed joint target **and** TCP pose for
    the adapter's ``get_observation()``.
    """

    _STOP = 0
    _SERVO_J = 1
    _MOVEJ = 2
    _SERVO_CARTESIAN = 3

    def __init__(
        self,
        *,
        robot_ip: str,
        frequency: int = 125,
        max_joint_speed: float = 1.0,
        init_joints: Optional[Sequence[float]] = None,
        sdk_dir: Optional[str] = None,
        servo_move_mode: int = 0,
        do_info: Sequence[int] = (0, 0, 0),
        robot_id: int = 0,
        verbose: bool = True,
        control_mode: str = "joint",
        max_pos_speed: float = 0.25,
        max_rot_speed: float = 0.6,
    ) -> None:
        super().__init__(name="JakaControl", daemon=True)

        self._robot_ip = robot_ip
        self._frequency = int(frequency)
        self._max_joint_speed = float(max_joint_speed)
        self._init_joints = (
            np.array(init_joints[:6], dtype=np.float64)
            if init_joints is not None
            else None
        )
        self._sdk_dir = sdk_dir
        self._servo_move_mode = int(servo_move_mode)
        self._do_info = [int(v) for v in (do_info or [0, 0, 0])]
        self._robot_id = int(robot_id)
        self._verbose = verbose
        self._control_mode = str(control_mode).lower()
        self._max_pos_speed = float(max_pos_speed)
        self._max_rot_speed = float(max_rot_speed)

        self._cmd_queue: queue.Queue = queue.Queue(maxsize=256)
        self._state_lock = threading.Lock()
        self._latest_joints: Optional[np.ndarray] = None
        self._latest_tcp_pose: Optional[np.ndarray] = None
        self._ready = threading.Event()
        self._stop_event = threading.Event()
        self._connect_error: Optional[str] = None

    # -- public API (main thread) -----------------------------------------

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    def wait_ready(self, timeout: float = 15.0) -> bool:
        """Wait for the daemon startup to complete.  Returns True if ready."""
        return self._ready.wait(timeout=timeout)

    @property
    def connect_error(self) -> Optional[str]:
        return self._connect_error

    def get_cached_joints(self) -> Optional[np.ndarray]:
        """Return a copy of the last-servoed joint positions (thread-safe)."""
        with self._state_lock:
            if self._latest_joints is None:
                return None
            return self._latest_joints.copy()

    def get_cached_tcp_pose(self) -> Optional[np.ndarray]:
        """Return a copy of the last-read TCP pose (thread-safe, Cartesian mode)."""
        with self._state_lock:
            if self._latest_tcp_pose is None:
                return None
            return np.asarray(self._latest_tcp_pose, dtype=np.float64)

    def servo_j(self, target_joints: np.ndarray, duration: float) -> None:
        """Queue a joint-space servo target with ramp duration."""
        self._cmd_queue.put({
            "cmd": self._SERVO_J,
            "joints": np.asarray(target_joints[:6], dtype=np.float64),
            "duration": float(duration),
        })

    def move_joints(self, joints: np.ndarray) -> None:
        """Queue a blocking joint_move (e.g. for go_home)."""
        self._cmd_queue.put({
            "cmd": self._MOVEJ,
            "joints": np.asarray(joints[:6], dtype=np.float64),
        })

    def servo_cartesian(self, pose_6d: np.ndarray, duration: float) -> None:
        """Queue a Cartesian pose target.

        - **joint mode**: IK-resolved to joint angles in the daemon,
          then sent via ``servo_j``.
        - **cartesian mode**: sent directly via ``servo_p`` (no IK).

        Pose format: ``[x_mm, y_mm, z_mm, rx_rad, ry_rad, rz_rad]``.
        """
        self._cmd_queue.put({
            "cmd": self._SERVO_CARTESIAN,
            "pose": np.asarray(pose_6d[:6], dtype=np.float64),
            "duration": float(duration),
        })

    def stop(self) -> None:
        """Signal the daemon to exit after the current cycle."""
        self._cmd_queue.put({"cmd": self._STOP})
        self._stop_event.set()

    # -- daemon thread body -----------------------------------------------

    def run(self) -> None:
        self._preload_sdk()

        try:
            import jkrc
        except ImportError:
            self._connect_error = (
                "jkrc is required for JAKA control. "
                "Please install JAKA Python SDK."
            )
            self._ready.set()
            return

        robot = jkrc.RC(self._robot_ip)
        self._robot = robot

        if not self._startup(robot):
            return  # _connect_error already set, _ready already set

        # Read initial joints / TCP pose
        ret = robot.get_joint_position()
        init_joints = self._parse_joint_pos(ret)
        if init_joints is None:
            self._connect_error = f"Jaka get_joint_position failed: {ret}"
            self._ready.set()
            try:
                robot.logout()
            except Exception as e:
                logger.debug("logout failed during init cleanup: %s", e)
            return

        init_joints = np.array(init_joints[:6], dtype=np.float64)
        interp = _JointInterpolator(init_joints)
        with self._state_lock:
            self._latest_joints = init_joints.copy()

        # Cartesian mode: also initialise pose interpolator
        cart_interp: Optional[_CartesianInterpolator] = None
        if self._control_mode == "cartesian":
            tcp_ret = robot.get_tcp_position()
            init_pose = self._parse_tcp_pose(tcp_ret)
            if init_pose is None:
                # Fall back: use zero pose + let first command overwrite
                init_pose = np.zeros(6, dtype=np.float64)
            cart_interp = _CartesianInterpolator(init_pose)
            with self._state_lock:
                self._latest_tcp_pose = np.array(init_pose, dtype=np.float64)

        dt = 1.0 / self._frequency
        t_start = time.monotonic()
        iter_idx = 0

        try:
            while not self._stop_event.is_set():
                t_now = time.monotonic()

                if self._control_mode == "cartesian":
                    cart_interp = self._drain_commands(
                        robot, cart_interp, t_now
                    )
                else:
                    interp = self._drain_commands(robot, interp, t_now)
                if self._stop_event.is_set():
                    break

                if self._control_mode == "cartesian":
                    cmd_pose = cart_interp(t_now)
                    try:
                        robot.servo_p(cmd_pose.tolist(), move_mode=0, step_num=1)
                    except Exception as e:
                        logger.warning("servo_p failed: %s", e)

                    # Read TCP pose for feedback
                    tcp_ret = robot.get_tcp_position()
                    pose = self._parse_tcp_pose(tcp_ret)
                    with self._state_lock:
                        self._latest_joints = None  # joint cache stale
                        if pose is not None:
                            self._latest_tcp_pose = np.array(pose, dtype=np.float64)
                else:
                    cmd_joints = interp(t_now)
                    try:
                        robot.servo_j(
                            cmd_joints.tolist(),
                            self._servo_move_mode,
                            1,
                        )
                    except Exception as e:
                        if self._verbose:
                            print(
                                f"[Jaka] ERROR: servo_j call failed: {e}"
                            )

                    with self._state_lock:
                        self._latest_joints = cmd_joints.copy()

                    # Also read TCP pose for observation richness
                    tcp_ret = robot.get_tcp_position()
                    pose = self._parse_tcp_pose(tcp_ret)
                    if pose is not None:
                        with self._state_lock:
                            self._latest_tcp_pose = np.array(pose, dtype=np.float64)

                target_time = t_start + (iter_idx + 1) * dt
                _precise_wait(target_time)

                if iter_idx == 0:
                    self._ready.set()
                    if self._verbose:
                        mode_label = (
                            "Cartesian (servo_p)"
                            if self._control_mode == "cartesian"
                            else "joint (servo_j)"
                        )
                        print(
                            f"[Jaka] Connected to Jaka Mini2 at {self._robot_ip} "
                            f"({self._frequency} Hz, {mode_label})"
                        )

                iter_idx += 1
        finally:
            self._shutdown(robot)

    # -- internal helpers -------------------------------------------------

    def _preload_sdk(self) -> None:
        """Optionally preload libjakaAPI.so so ``import jkrc`` can resolve it."""
        sdk_dir = self._sdk_dir or _os.environ.get("JAKA_SDK_DIR")
        if not sdk_dir:
            return
        try:
            import ctypes as _ctypes

            _ctypes.CDLL(
                _os.path.join(sdk_dir, "libjakaAPI.so"),
                mode=_ctypes.RTLD_GLOBAL,
            )
        except Exception as e:
            logger.warning(
                "Could not preload libjakaAPI.so from %r: %s. "
                "Make sure it is on LD_LIBRARY_PATH.",
                sdk_dir, e,
            )
        if sdk_dir not in _sys.path:
            _sys.path.insert(0, sdk_dir)

    def _startup(self, robot: Any) -> bool:
        """Run login / power_on / enable_robot / servo_move_enable.

        Return ``True`` on success.  On failure, set ``_connect_error``,
        signal ``_ready``, attempt logout, and return ``False``.
        """
        steps = [
            ("login", robot.login()),
            ("power_on", robot.power_on()),
            ("enable_robot", robot.enable_robot()),
            ("servo_move_enable", robot.servo_move_enable(True)),
        ]
        for step_name, ret in steps:
            if len(ret) == 0 or int(ret[0]) != 0:
                self._connect_error = f"Jaka {step_name} failed: {ret}"
                self._ready.set()
                try:
                    robot.logout()
                except Exception as e:
                    logger.debug("logout failed during startup cleanup: %s", e)
                return False

        # Configure NLF_MMF_COMB speed-foresight filter
        try:
            robot.servo_move_use_nlf_mmf_comb(max_buf=3, kp=0.8)
        except Exception as e:
            logger.warning(
                "servo_move_use_nlf_mmf_comb not available, "
                "using default servo filter: %s",
                e,
            )

        # Optional home on startup
        if self._init_joints is not None:
            if self._verbose:
                print(f"[Jaka] Moving to init joints: {self._init_joints}")
            robot.joint_move(
                self._init_joints.tolist(),
                move_mode=0,
                is_blocking=True,
                speed=1.0,
            )
            time.sleep(0.5)

        return True

    def _drain_commands(
        self,
        robot: Any,
        interp: object,  # _JointInterpolator | _CartesianInterpolator
        t_now: float,
    ) -> object:
        """Consume all queued commands; return possibly-updated interpolator."""
        # Determine interpolator type from control mode
        cartesian = self._control_mode == "cartesian"

        while True:
            try:
                cmd = self._cmd_queue.get_nowait()
            except queue.Empty:
                break

            c = cmd["cmd"]

            if c == self._STOP:
                self._stop_event.set()
                break
            elif c == self._SERVO_J:
                if cartesian:
                    if self._verbose:
                        print(
                            "[Jaka] WARNING: SERVO_J received in Cartesian mode, "
                            "ignoring. Use Cartesian targets instead."
                        )
                else:
                    interp.set_target(cmd["joints"], cmd["duration"])  # type: ignore[union-attr]
            elif c == self._SERVO_CARTESIAN:
                if cartesian:
                    interp.set_target(cmd["pose"], cmd["duration"])  # type: ignore[union-attr]
                else:
                    target_joints = self._ik_solve(robot, cmd["pose"])
                    if target_joints is not None:
                        interp.set_target(target_joints, cmd["duration"])  # type: ignore[union-attr]
                    elif self._verbose:
                        print(
                            "[Jaka] WARNING: kine_inverse failed for "
                            f"{cmd['pose']}, skipping"
                        )
            elif c == self._MOVEJ:
                self._execute_movej(robot, cmd["joints"])
                if cartesian:
                    # Re-initialise Cartesian interpolator from TCP pose
                    tcp_ret = robot.get_tcp_position()
                    new_pose = self._parse_tcp_pose(tcp_ret)
                    if new_pose is not None:
                        interp = _CartesianInterpolator(np.array(new_pose, dtype=np.float64))
                        with self._state_lock:
                            self._latest_tcp_pose = np.array(new_pose, dtype=np.float64)
                else:
                    # Re-initialise joint interpolator at new position
                    ret = robot.get_joint_position()
                    new_joints = self._parse_joint_pos(ret)
                    if new_joints is not None:
                        new_joints = np.array(new_joints[:6], dtype=np.float64)
                        interp = _JointInterpolator(new_joints)
                        with self._state_lock:
                            self._latest_joints = new_joints.copy()

        return interp

    def _execute_movej(self, robot: Any, joints: np.ndarray) -> None:
        """Blocking joint_move: disable servo → move → re-enable servo."""
        if self._verbose:
            print(f"[Jaka] moveJ to {joints}")
        try:
            robot.servo_move_enable(False)
        except Exception as e:
            logger.debug("servo_move_enable(False) failed before moveJ: %s", e)
        robot.joint_move(joints.tolist(), move_mode=0, is_blocking=True, speed=1.0)
        robot.servo_move_enable(True)
        if self._verbose:
            print("[Jaka] moveJ complete, servo resumed")

    @staticmethod
    def _ik_solve(robot: Any, pose_6d: np.ndarray) -> Optional[np.ndarray]:
        """Convert Cartesian pose (mm, RPY rad) to joint angles via IK.

        ``pose_6d`` format: ``[x_mm, y_mm, z_mm, rx_rpy, ry_rpy, rz_rpy]``
        — same as JAKA SDK's ``servo_p`` / ``kine_inverse`` convention.
        """
        ref_joint = robot.get_joint_position()
        if len(ref_joint) < 2 or int(ref_joint[0]) != 0:
            return None
        ref = ref_joint[1]
        if hasattr(ref, "jVal"):
            ref = ref.jVal
        ret = robot.kine_inverse(pose_6d[:6].tolist(), list(ref))
        if len(ret) == 0 or int(ret[0]) != 0:
            return None
        return np.asarray(ret[1][:6], dtype=np.float64)

    def _shutdown(self, robot: Any) -> None:
        """Disable servo, logout.  Robot stays powered on."""
        try:
            robot.servo_move_enable(False)
        except Exception as e:
            logger.debug("servo_move_enable(False) failed during shutdown: %s", e)
        try:
            robot.logout()
        except Exception as e:
            logger.debug("logout failed during shutdown: %s", e)
        self._ready.set()
        if self._verbose:
            print(f"[Jaka] Disconnected from {self._robot_ip}")

    @staticmethod
    def _parse_joint_pos(ret: Any) -> Optional[List[float]]:
        if not isinstance(ret, (list, tuple)) or len(ret) == 0:
            return None
        if int(ret[0]) != 0:
            return None
        if len(ret) < 2:
            return None
        data = ret[1]
        if isinstance(data, (list, tuple)):
            return [float(v) for v in data]
        if hasattr(data, "jVal"):
            j_val = getattr(data, "jVal")
            if isinstance(j_val, (list, tuple)):
                return [float(v) for v in j_val]
        return None

    @staticmethod
    def _parse_tcp_pose(ret: Any) -> Optional[List[float]]:
        """Parse ``get_tcp_position()`` return into ``[x,y,z,rx,ry,rz]``.

        Jaka SDK returns ``(retcode, data)`` where ``data`` has
        ``tran`` (x,y,z in mm) and ``rpy`` (rx,ry,rz in rad).
        """
        if not isinstance(ret, (list, tuple)) or len(ret) == 0:
            return None
        if int(ret[0]) != 0:
            return None
        if len(ret) < 2:
            return None
        data = ret[1]
        # data.tran / data.rpy (Jaka SDK cartesian_position struct)
        if hasattr(data, "tran") and hasattr(data, "rpy"):
            xyz = getattr(data, "tran")
            rpy = getattr(data, "rpy")
            if isinstance(xyz, (list, tuple)) and isinstance(rpy, (list, tuple)):
                return [float(v) for v in xyz[:3]] + [float(v) for v in rpy[:3]]
        # Flat list fallback
        if isinstance(data, (list, tuple)) and len(data) >= 6:
            return [float(v) for v in data[:6]]
        return None
