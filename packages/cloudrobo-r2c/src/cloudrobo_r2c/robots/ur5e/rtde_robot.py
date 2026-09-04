"""UR5e robot controller via RTDE protocol."""
import time
import threading
import queue
import enum
import numpy as np
import scipy.interpolate as si
import scipy.spatial.transform as st

from cloudrobo_r2c.robots.ur5e.robot_base import RobotController


# ---------------------------------------------------------------------------
# Precise timing utility (inlined to avoid extra dependency)
# ---------------------------------------------------------------------------

def _precise_wait(t_end: float, slack_time: float = 0.001, time_func=time.monotonic):
    """Wait until monotonic time reaches t_end with minimal jitter."""
    t_start = time_func()
    t_wait = t_end - t_start
    if t_wait > 0:
        t_sleep = t_wait - slack_time
        if t_sleep > 0:
            time.sleep(t_sleep)
        while time_func() < t_end:
            pass


# ---------------------------------------------------------------------------
# Pose trajectory interpolator
# ---------------------------------------------------------------------------

def _pose_distance(a, b):
    """Return (position_distance, rotation_distance) between two 6D poses."""
    pos_dist = np.linalg.norm(b[:3] - a[:3])
    rot_a = st.Rotation.from_rotvec(a[3:])
    rot_b = st.Rotation.from_rotvec(b[3:])
    rot_dist = (rot_b * rot_a.inv()).magnitude()
    return pos_dist, rot_dist


class PoseTrajectoryInterpolator:
    """Interpolates 6D poses over time: linear position + Slerp rotation."""

    def __init__(self, times: np.ndarray, poses: np.ndarray):
        times = np.asarray(times, dtype=np.float64)
        poses = np.asarray(poses, dtype=np.float64)

        if len(times) == 1:
            self._single = True
            self._times = times
            self._poses = poses
        else:
            self._single = False
            pos = poses[:, :3]
            rot = st.Rotation.from_rotvec(poses[:, 3:])
            self._pos_interp = si.interp1d(times, pos, axis=0, assume_sorted=True)
            self._rot_interp = st.Slerp(times, rot)

    @property
    def times(self):
        return self._times if self._single else self._pos_interp.x

    @property
    def poses(self):
        if self._single:
            return self._poses
        pos = self._pos_interp(self._pos_interp.x)
        rot = self._rot_interp(self._pos_interp.x).as_rotvec()
        return np.hstack([pos, rot])

    def trim(self, start_t: float, end_t: float):
        """Return a new interpolator covering only [start_t, end_t]."""
        t = self.times
        keep = t[(start_t < t) & (t < end_t)]
        all_t = np.unique(np.concatenate([[start_t], keep, [end_t]]))
        return PoseTrajectoryInterpolator(all_t, self(all_t))

    def schedule_waypoint(self, pose, schedule_time, max_pos_speed=np.inf,
                          max_rot_speed=np.inf, curr_time=None,
                          last_waypoint_time=None):
        """Schedule a waypoint at the given schedule_time, with speed limits."""
        if curr_time is not None and schedule_time <= curr_time:
            return self

        start_time = max(curr_time or self.times[0], self.times[0])
        end_time = min(schedule_time, self.times[-1])
        if last_waypoint_time is not None:
            end_time = max(last_waypoint_time, curr_time or 0)
        end_time = min(end_time, schedule_time)
        start_time = min(start_time, end_time)

        trimmed = self.trim(start_time, end_time)
        end_pose = trimmed(end_time)
        pos_d, rot_d = _pose_distance(end_pose, pose)
        duration = max(schedule_time - end_time, pos_d / max_pos_speed, rot_d / max_rot_speed)

        new_times = np.append(trimmed.times, [end_time + duration])
        new_poses = np.vstack([trimmed.poses, pose])
        return PoseTrajectoryInterpolator(new_times, new_poses)

    def __call__(self, t):
        scalar = np.isscalar(t)
        t_arr = np.atleast_1d(t)
        if self._single:
            out = np.tile(self._poses[0], (len(t_arr), 1))
        else:
            t_arr = np.clip(t_arr, self.times[0], self.times[-1])
            out = np.zeros((len(t_arr), 6))
            out[:, :3] = self._pos_interp(t_arr)
            out[:, 3:] = self._rot_interp(t_arr).as_rotvec()
        return out[0] if scalar else out


# ---------------------------------------------------------------------------
# RTDE Robot Controller
# ---------------------------------------------------------------------------

class _Command(enum.Enum):
    STOP = 0
    SERVOL = 1
    SCHEDULE_WAYPOINT = 2
    MOVEJ = 3


class RTDEUR5eController(RobotController, threading.Thread):
    """UR5e robot arm controlled via RTDE protocol.

    Runs a high-frequency control loop (500 Hz) in a background thread.
    The main thread sends waypoints and reads state through thread-safe APIs.
    """

    def __init__(
        self,
        robot_ip: str,
        frequency: int = 500,
        lookahead_time: float = 0.1,
        gain: int = 300,
        max_pos_speed: float = 0.25,
        max_rot_speed: float = 0.6,
        tcp_offset: float = 0.21,
        init_joints: np.ndarray = None,
        verbose: bool = True,
    ):
        threading.Thread.__init__(self, name="RTDEControl", daemon=True)
        self.robot_ip = robot_ip
        self.frequency = frequency
        self.lookahead_time = lookahead_time
        self.gain = gain
        self.max_pos_speed = max_pos_speed
        self.max_rot_speed = max_rot_speed
        self.tcp_offset = tcp_offset
        self.init_joints = init_joints
        self.verbose = verbose

        self._cmd_queue = queue.Queue(maxsize=256)
        self._state_lock = threading.Lock()
        self._latest_state: dict = {}
        self._ready = threading.Event()
        self._stop_event = threading.Event()

    # ---- public RobotController API ----

    def connect(self) -> bool:
        try:
            import rtde_control
            import rtde_receive
            self._rtde_c_mod = rtde_control
            self._rtde_r_mod = rtde_receive
        except ImportError:
            raise RuntimeError(
                "rtde_control/rtde_receive not installed. "
                "Install with: pip install ur-rtde"
            )
        self.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError(f"Failed to connect to UR5e at {self.robot_ip}")
        return True

    def disconnect(self):
        self._cmd_queue.put({"cmd": _Command.STOP.value})
        self._stop_event.set()
        if self.is_alive() and self._ready.is_set():
            self.join(timeout=3)

    def get_state(self) -> dict:
        with self._state_lock:
            return dict(self._latest_state)

    def send_waypoint(self, pose_6d: np.ndarray, target_time: float):
        pose_6d = np.asarray(pose_6d, dtype=np.float64)
        self._cmd_queue.put({
            "cmd": _Command.SCHEDULE_WAYPOINT.value,
            "target_pose": pose_6d,
            "target_time": target_time,
        })

    def servo_to(self, pose_6d: np.ndarray, duration: float = 0.1):
        pose_6d = np.asarray(pose_6d, dtype=np.float64)
        self._cmd_queue.put({
            "cmd": _Command.SERVOL.value,
            "target_pose": pose_6d,
            "duration": duration,
        })

    def move_joints(self, joints: np.ndarray, speed: float = 0.5, accel: float = 1.4):
        """Move to target joint positions via blocking moveJ, then resume servoL."""
        joints = np.asarray(joints, dtype=np.float64)
        self._cmd_queue.put({
            "cmd": _Command.MOVEJ.value,
            "joints": joints,
            "speed": speed,
            "accel": accel,
        })

    @property
    def is_ready(self):
        return self._ready.is_set()

    # ---- control loop (runs in background thread) ----

    def run(self):
        rtde_c = self._rtde_c_mod.RTDEControlInterface(hostname=self.robot_ip)
        rtde_r = self._rtde_r_mod.RTDEReceiveInterface(hostname=self.robot_ip)

        try:
            rtde_c.setTcp([0, 0, self.tcp_offset, 0, 0, 0])

            if self.init_joints is not None:
                if self.verbose:
                    print(f"Moving to init joints: {self.init_joints}")
                rtde_c.moveJ(self.init_joints.tolist(), 0.5, 1.4)

            curr_pose = np.array(rtde_r.getActualTCPPose())
            curr_t = time.monotonic()
            interp = PoseTrajectoryInterpolator(
                times=np.array([curr_t]),
                poses=np.array([curr_pose]),
            )
            last_wp_time = curr_t

            dt = 1.0 / self.frequency
            t_start = time.monotonic()
            iter_idx = 0
            keep_running = True
            cube_diag = np.linalg.norm([1, 1, 1])

            while keep_running and not self._stop_event.is_set():
                t_now = time.monotonic()

                cmd_pose = interp(t_now)
                rtde_c.servoL(
                    cmd_pose, 0.5, 0.5, dt,
                    self.lookahead_time, self.gain,
                )

                state = {
                    "eef_pose": np.array(rtde_r.getActualTCPPose()),
                    "joint_positions": np.array(rtde_r.getActualQ()),
                    "joint_velocities": np.array(rtde_r.getActualQd()),
                    "timestamp": time.time(),
                }
                with self._state_lock:
                    self._latest_state = state

                try:
                    cmd = self._cmd_queue.get_nowait()
                except queue.Empty:
                    cmd = None

                if cmd is not None:
                    c = cmd["cmd"]
                    if c == _Command.STOP.value:
                        keep_running = False
                        break
                    elif c == _Command.SERVOL.value:
                        target = cmd["target_pose"]
                        dur = float(cmd["duration"])
                        t_insert = t_now + dt + dur
                        interp = interp.schedule_waypoint(
                            target, t_insert,
                            max_pos_speed=self.max_pos_speed * cube_diag,
                            max_rot_speed=self.max_rot_speed * cube_diag,
                            curr_time=t_now + dt,
                        )
                        last_wp_time = t_insert
                    elif c == _Command.SCHEDULE_WAYPOINT.value:
                        target = cmd["target_pose"]
                        target_time_global = float(cmd["target_time"])
                        target_time_mono = time.monotonic() - time.time() + target_time_global
                        interp = interp.schedule_waypoint(
                            target, target_time_mono,
                            max_pos_speed=self.max_pos_speed * cube_diag,
                            max_rot_speed=self.max_rot_speed * cube_diag,
                            curr_time=t_now + dt,
                            last_waypoint_time=last_wp_time,
                        )
                        last_wp_time = target_time_mono
                    elif c == _Command.MOVEJ.value:
                        joints = cmd["joints"]
                        speed = float(cmd["speed"])
                        accel = float(cmd["accel"])
                        if self.verbose:
                            print(f"[RTDE] moveJ to {joints}")
                        rtde_c.servoStop()
                        rtde_c.moveJ(joints.tolist(), speed, accel)
                        curr_pose = np.array(rtde_r.getActualTCPPose())
                        curr_t = time.monotonic()
                        interp = PoseTrajectoryInterpolator(
                            times=np.array([curr_t]),
                            poses=np.array([curr_pose]),
                        )
                        last_wp_time = curr_t
                        if self.verbose:
                            print(f"[RTDE] moveJ complete, resuming servoL")

                _precise_wait(t_start + (iter_idx + 1) * dt)

                if iter_idx == 0:
                    self._ready.set()
                    if self.verbose:
                        print(f"[RTDE] Connected to UR5e at {self.robot_ip}")
                iter_idx += 1

        except Exception as e:
            if self.verbose:
                print(f"[RTDE] Error: {e}")
            raise
        finally:
            rtde_c.servoStop()
            rtde_c.stopScript()
            rtde_c.disconnect()
            rtde_r.disconnect()
            self._ready.set()
            if self.verbose:
                print(f"[RTDE] Disconnected from {self.robot_ip}")
