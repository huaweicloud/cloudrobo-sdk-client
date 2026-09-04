"""Abstract base class for robot arm controllers."""
from abc import ABC, abstractmethod
import numpy as np


class RobotController(ABC):
    """Interface for robot arm control.

    Subclasses implement the actual communication protocol (RTDE, ROS2, etc.).
    """

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection to the robot. Return True on success."""
        ...

    @abstractmethod
    def disconnect(self):
        """Close connection and stop control loop."""
        ...

    @abstractmethod
    def get_state(self) -> dict:
        """Return latest robot state.

        Returns dict with keys:
            eef_pose: np.ndarray (6,) — [x, y, z, rx, ry, rz] axis-angle
            joint_positions: np.ndarray (6,) — joint angles in radians
            timestamp: float — monotonic time of the state reading
        """
        ...

    @abstractmethod
    def send_waypoint(self, pose_6d: np.ndarray, target_time: float):
        """Schedule a target pose at the given global time (time.time()).

        Args:
            pose_6d: (6,) array [x, y, z, rx, ry, rz] in axis-angle
            target_time: float, global timestamp when the waypoint should be reached
        """
        ...

    @abstractmethod
    def servo_to(self, pose_6d: np.ndarray, duration: float = 0.1):
        """Immediately start moving to a pose over the given duration.

        Args:
            pose_6d: (6,) array [x, y, z, rx, ry, rz]
            duration: seconds to complete the move
        """
        ...
