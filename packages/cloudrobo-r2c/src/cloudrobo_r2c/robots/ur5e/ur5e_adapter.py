"""UR5e RTDE hardware adapter implementing IRobotHardwareAdapter.

Wires together RTDEUR5eController, DHGripper, and RealSenseCamera
into a single adapter callable by cloudroboclient via SyncRobotClient.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np
from scipy.spatial.transform import Rotation

from cloudrobo_r2c.core.interfaces import IRobotHardwareAdapter
from cloudrobo_r2c.robots.ur5e.rtde_robot import RTDEUR5eController


def create_ur5e_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> IRobotHardwareAdapter:
    """Entry_point factory for UR5eHardwareAdapter."""
    return UR5eHardwareAdapter(config=dict(config))


from cloudrobo_r2c.robots.ur5e.dh_gripper import DHGripper
from cloudrobo_r2c.robots.ur5e.realsense_camera import RealSenseCamera
from cloudrobo_r2c.robots.ur5e.pose_utils import apply_delta_pose

logger = logging.getLogger(__name__)


@dataclass
class UR5eHardwareAdapter(IRobotHardwareAdapter):
    """UR5e RTDE hardware adapter.

    Configuration is read from the ``ur5e_config`` section of the robot config YAML::

        hardware:
          type: ur5e_rtde
          ur5e_config:
            action_mode: "absolute"    # "absolute" (default) or "delta"
            robot:
              robot_ip: "192.168.5.152"
              frequency: 500
              ...
            gripper:
              port: "/dev/ttyUSBDH_"
              ...
            cameras:
              - name: "wrist"
                serial: "..."
                ...

    ``action_mode`` controls how joint position values from cloud actions are
    interpreted:

    - ``"absolute"`` (default): values are treated as absolute target joint
      angles (radians) and sent directly to the robot.
    - ``"delta"``: values are treated as increments.  The adapter reads the
      current joint positions and adds the deltas before commanding the robot.
      Use this when the inference model outputs delta actions (e.g. ACT).
    """

    config: Mapping[str, Any]

    _robot: Optional[RTDEUR5eController] = field(default=None, init=False, repr=False)
    _gripper: Optional[DHGripper] = field(default=None, init=False, repr=False)
    _cameras: List[RealSenseCamera] = field(
        default_factory=list, init=False, repr=False
    )
    _connected: bool = field(default=False, init=False, repr=False)
    _camera_names: List[str] = field(default_factory=list, init=False, repr=False)
    _action_mode: str = field(default="absolute", init=False, repr=False)
    _commanded_eef_pose: Optional[np.ndarray] = field(
        default=None, init=False, repr=False
    )

    def __post_init__(self) -> None:
        from cloudrobo_r2c.robots.commands.generic import GoHomeCommand
        self.register_command_class("go_home", GoHomeCommand)

    # ------------------------------------------------------------------
    # IRobotHardwareAdapter
    # ------------------------------------------------------------------

    def move_to(
        self,
        *,
        pose_euler: Optional[Sequence[float]] = None,
        pose_quat: Optional[Sequence[float]] = None,
        joints: Optional[Sequence[float]] = None,
    ) -> None:
        """Move the robot to an absolute target (go_home semantics).

        go_home must always reach an absolute configuration, so this
        method bypasses :meth:`send_action`'s ``action_mode`` delta
        logic and commands the robot directly:

        - ``joints`` (N floats) → ``robot.move_joints`` (absolute moveJ).
        - ``pose_euler`` (6 floats, ``[x, y, z, roll, pitch, yaw]`` in
          metres + radians — the cross-adapter RPY contract) → RPY is
          converted to axis-angle (rotation vector) before being sent to
          ``robot.send_waypoint`` (absolute servoL), because the UR5e
          RTDE servoL path consumes axis-angle, not RPY.
          ``_commanded_eef_pose`` is updated to the axis-angle pose so
          subsequent delta inference actions are relative to it.
        - ``pose_quat`` is not supported (no quat→axis-angle conversion
          at this layer) → :class:`NotImplementedError`; use
          ``pose_euler`` instead.

        Note: the inference path (:meth:`send_action` with ``eef_pose``)
        consumes axis-angle directly and is unaffected — only the go_home
        ``pose_euler`` input is interpreted as RPY to honour the typed
        interface contract.
        """
        self._validate_move_to_inputs(pose_euler, pose_quat, joints)
        if self._robot is None:
            raise RuntimeError("Adapter is not connected. Call connect() first.")

        if joints is not None:
            self._robot.move_joints(
                np.array([float(v) for v in joints], dtype=np.float64)
            )
        elif pose_euler is not None:
            vals = [float(v) for v in pose_euler[:6]]
            x, y, z, roll, pitch, yaw = vals
            # Cross-adapter contract: pose_euler rotation is RPY (radians).
            # UR5e servoL consumes axis-angle, so convert here.
            rotvec = Rotation.from_euler("xyz", [roll, pitch, yaw]).as_rotvec()
            eef = np.array(
                [x, y, z, float(rotvec[0]), float(rotvec[1]), float(rotvec[2])],
                dtype=np.float64,
            )
            self._robot.send_waypoint(eef, time.time() + 0.1)
            self._commanded_eef_pose = eef
        else:
            raise NotImplementedError(
                "UR5eHardwareAdapter.move_to() does not support pose_quat "
                "(no quat→axis-angle conversion). Use "
                "pose_euler=[x,y,z,roll,pitch,yaw] instead."
            )

    def set_gripper(
        self,
        *,
        width: Optional[float] = None,
        action: Optional[str] = None,
    ) -> None:
        """Actuate the DH gripper.

        - ``action="open"``  → ``max_width`` (fully open).
        - ``action="close"`` → ``0.0`` (fully closed).
        - ``width`` (metres) → forwarded verbatim to ``set_position``.

        Logs a warning and returns when no gripper is configured.

        Exactly one of ``width`` or ``action`` must be supplied (matching
        the JAKA / Flexiv adapters); passing both or neither raises
        :class:`ValueError`.
        """
        if (width is None) == (action is None):
            raise ValueError(
                "set_gripper() requires exactly one of width or action to be "
                f"supplied, got width={width!r}, action={action!r}."
            )
        if self._gripper is None:
            logger.warning("UR5e set_gripper: no gripper configured; skipping.")
            return
        if action is not None:
            normalized = action.strip().lower()
            if normalized == "open":
                self._gripper.set_position(self._gripper.max_width)
            elif normalized == "close":
                self._gripper.set_position(0.0)
            else:
                raise ValueError(
                    f"set_gripper action must be 'open' or 'close', got {action!r}."
                )
            return
        if width is not None:
            self._gripper.set_position(float(width))

    def connect(self) -> None:
        if self._connected:
            logger.debug("UR5e adapter already connected; skipping.")
            return

        self._action_mode = (
            str(self.config.get("action_mode", "absolute")).strip().lower()
        )
        if self._action_mode not in ("absolute", "delta"):
            raise ValueError(
                f"action_mode must be 'absolute' or 'delta', got: {self._action_mode!r}"
            )
        logger.info("UR5e action_mode: %s", self._action_mode)

        robot_cfg = self._robot_cfg
        gripper_cfg = self._gripper_cfg
        cameras_cfg = self._cameras_cfg

        # 1. Robot
        init_joints = None
        if "init_joints" in robot_cfg:
            init_joints = np.array(robot_cfg["init_joints"], dtype=np.float64)

        self._robot = RTDEUR5eController(
            robot_ip=str(robot_cfg["robot_ip"]),
            frequency=int(robot_cfg.get("frequency", 500)),
            lookahead_time=float(robot_cfg.get("lookahead_time", 0.1)),
            gain=int(robot_cfg.get("gain", 300)),
            max_pos_speed=float(robot_cfg.get("max_pos_speed", 0.25)),
            max_rot_speed=float(robot_cfg.get("max_rot_speed", 0.6)),
            tcp_offset=float(robot_cfg.get("tcp_offset", 0.21)),
            init_joints=init_joints,
            verbose=bool(robot_cfg.get("verbose", True)),
        )
        logger.info("Connecting to UR5e at %s ...", robot_cfg["robot_ip"])
        self._robot.connect()
        logger.info("UR5e connected.")

        init_state = self._robot.get_state()
        if init_state and "eef_pose" in init_state:
            self._commanded_eef_pose = np.asarray(
                init_state["eef_pose"], dtype=np.float64
            )
            logger.info("Initial eef_pose: %s", self._commanded_eef_pose)

        # 2. Gripper
        if gripper_cfg:
            self._gripper = DHGripper(
                port=str(gripper_cfg.get("port", "/dev/ttyUSBDH_")),
                baudrate=int(gripper_cfg.get("baudrate", 115200)),
                max_width=float(gripper_cfg.get("max_width", 0.08)),
                max_speed=float(gripper_cfg.get("max_speed", 0.07273)),
                max_force=float(gripper_cfg.get("max_force", 140.0)),
            )
            logger.info("Connecting DH gripper at %s ...", gripper_cfg.get("port"))
            self._gripper.connect()
            self._gripper.initialize()
            logger.info("DH gripper connected and initialized.")

        # 3. Cameras
        for cam_cfg in cameras_cfg:
            cam = RealSenseCamera(
                serial=str(cam_cfg.get("serial", "")),
                width=int(cam_cfg.get("width", 640)),
                height=int(cam_cfg.get("height", 480)),
                fps=int(cam_cfg.get("fps", 30)),
                name=str(cam_cfg["name"]),
            )
            cam.start()
            self._cameras.append(cam)
            self._camera_names.append(cam.name)
        if cameras_cfg:
            logger.info(
                "Started %d RealSense camera(s): %s",
                len(self._cameras),
                self._camera_names,
            )

        self._connected = True
        logger.info("UR5eHardwareAdapter connected.")

    def disconnect(self) -> None:
        for cam in self._cameras:
            try:
                cam.stop()
            except Exception:
                logger.debug("Error stopping camera %s", cam.name, exc_info=True)
        self._cameras.clear()
        self._camera_names.clear()

        if self._gripper is not None:
            try:
                self._gripper.disconnect()
            except Exception:
                logger.debug("Error disconnecting gripper", exc_info=True)
            self._gripper = None

        if self._robot is not None:
            try:
                self._robot.disconnect()
            except Exception:
                logger.debug("Error disconnecting robot", exc_info=True)
            self._robot = None

        self._connected = False
        logger.info("UR5eHardwareAdapter disconnected.")

    def get_observation(self) -> Mapping[str, Any]:
        if not self._connected:
            raise RuntimeError("Adapter is not connected. Call connect() first.")

        observation: Dict[str, Any] = {}

        # Robot state
        if self._robot is not None:
            state = self._robot.get_state()
            if state:
                eef = state.get("eef_pose")
                joints = state.get("joint_positions")
                vel = state.get("joint_velocities")
                if joints is not None:
                    observation["joint_positions"] = np.asarray(
                        joints, dtype=np.float32
                    )
                if vel is not None:
                    observation["joint_velocities"] = np.asarray(vel, dtype=np.float32)
                if eef is not None:
                    observation["eef_pose"] = np.asarray(eef, dtype=np.float32)
                if self._commanded_eef_pose is not None:
                    observation["commanded_eef_pose"] = np.asarray(
                        self._commanded_eef_pose, dtype=np.float32
                    )
                observation["timestamp"] = state.get("timestamp", 0.0)

        # Gripper state
        if self._gripper is not None:
            gs = self._gripper.get_state()
            observation["gripper_position"] = float(gs.get("position", 0.0))

        # Camera frames
        for cam in self._cameras:
            frame = cam.get_latest_frame()
            if frame is not None:
                observation[cam.name] = frame

        return observation

    def send_action(self, command: Mapping[str, Any]) -> None:
        """Send a device-native action command.

        Supported keys in the command dict:

        - ``eef_pose``: 6D eef target [x,y,z,rx,ry,rz] axis-angle, sent via send_waypoint
        - ``joint_positions``: list of 6 joint angles (rad), sent via move_joints
        - ``gripper_position``: float 0.0–max_width (m), sent via set_position
        - ``gripper_percent``: float 0–100, converted to position using max_width

        In ``delta`` mode, eef_pose and joint_positions are treated as increments
        added to the current state.
        """
        # 1. EEF pose — Cartesian control via waypoint + servoL
        eef_target = self._extract_eef_target(command)
        if eef_target is not None and self._robot is not None:
            eef_target = np.asarray(eef_target, dtype=np.float64)
            if self._action_mode == "delta":
                if self._commanded_eef_pose is not None:
                    eef_target = apply_delta_pose(self._commanded_eef_pose, eef_target)
                else:
                    state = self._robot.get_state()
                    curr_eef = state.get("eef_pose")
                    if curr_eef is not None:
                        eef_target = apply_delta_pose(
                            np.asarray(curr_eef, dtype=np.float64), eef_target
                        )
            self._commanded_eef_pose = eef_target
            target_time = time.time() + 0.1
            self._robot.send_waypoint(eef_target, target_time)
        else:
            # 2. Joint positions (only if no eef_pose)
            joint_target = self._extract_joint_target(command)
            if joint_target is not None:
                if self._action_mode == "delta" and self._robot is not None:
                    state = self._robot.get_state()
                    current_joints = state.get("joint_positions")
                    if current_joints is not None and len(current_joints) > 0:
                        n = min(len(current_joints), len(joint_target))
                        joint_target = [
                            float(current_joints[i]) + float(joint_target[i])
                            for i in range(n)
                        ]
                if self._robot is not None:
                    self._robot.move_joints(np.array(joint_target, dtype=np.float64))

        # 3. Gripper target (always processed)
        gripper_target = self._extract_gripper_target(command)
        if gripper_target is not None and self._gripper is not None:
            self._gripper.set_position(float(gripper_target))

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    @property
    def _robot_cfg(self) -> Mapping[str, Any]:
        cfg = self.config.get("robot")
        if not isinstance(cfg, Mapping):
            raise ValueError("ur5e_config.robot must be a mapping")
        return cfg

    @property
    def _gripper_cfg(self) -> Mapping[str, Any]:
        cfg = self.config.get("gripper")
        if cfg is None:
            return {}
        if not isinstance(cfg, Mapping):
            raise ValueError("ur5e_config.gripper must be a mapping")
        return cfg

    @property
    def _cameras_cfg(self) -> List[Mapping[str, Any]]:
        cfg = self.config.get("cameras")
        if cfg is None:
            return []
        if not isinstance(cfg, list):
            raise ValueError("ur5e_config.cameras must be a list")
        return cfg

    # ------------------------------------------------------------------
    # Action extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_eef_target(command: Mapping[str, Any]) -> Optional[List[float]]:
        """Extract 6D eef pose target from command dict.

        Checks ``eef_pose`` first, then ``action`` (7D model output
        where first 6 dims are eef delta).
        """
        if "eef_pose" in command:
            payload = command["eef_pose"]
            if isinstance(payload, (Sequence, np.ndarray)) and not isinstance(
                payload, (str, bytes)
            ):
                vals = [float(v) for v in payload]
                if len(vals) >= 6:
                    return vals[:6]

        if "action" in command:
            payload = command["action"]
            if isinstance(payload, (Sequence, np.ndarray)) and not isinstance(
                payload, (str, bytes)
            ):
                vals = [float(v) for v in payload]
                if len(vals) >= 6:
                    return vals[:6]

        return None

    @staticmethod
    def _extract_joint_target(command: Mapping[str, Any]) -> Optional[List[float]]:
        if "joint_positions" in command:
            payload = command["joint_positions"]
            if isinstance(payload, (Sequence, np.ndarray)) and not isinstance(
                payload, (str, bytes)
            ):
                return [float(v) for v in payload]

        if "joint_target" in command:
            payload = command["joint_target"]
            if isinstance(payload, (Sequence, np.ndarray)) and not isinstance(
                payload, (str, bytes)
            ):
                return [float(v) for v in payload]

        joint_states = command.get("joint_states")
        if isinstance(joint_states, Mapping):
            positions = joint_states.get("position")
            if isinstance(positions, (Sequence, np.ndarray)) and not isinstance(
                positions, (str, bytes)
            ):
                if (
                    positions
                    and isinstance(positions[0], (Sequence, np.ndarray))
                    and not isinstance(positions[0], (str, bytes))
                ):
                    return [float(v) for v in positions[0]]
                return [float(v) for v in positions]

        return None

    def _extract_gripper_target(self, command: Mapping[str, Any]) -> Optional[float]:
        if "gripper_position" in command:
            return float(command["gripper_position"])

        if "gripper_percent" in command:
            pct = float(command["gripper_percent"])
            max_w = self._gripper.max_width if self._gripper else 0.08
            return pct / 100.0 * max_w

        if "gripper" in command:
            return float(command["gripper"])

        gripper_section = command.get("gripper_state")
        if isinstance(gripper_section, Mapping):
            value = gripper_section.get("position")
            if isinstance(value, (Sequence, np.ndarray)) and not isinstance(
                value, (str, bytes)
            ):
                if len(value) > 0:
                    return float(value[0])
            elif value is not None:
                return float(value)

        return None
