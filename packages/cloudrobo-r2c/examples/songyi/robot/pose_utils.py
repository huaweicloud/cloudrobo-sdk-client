"""Pose conversions between RPY (DucoCobot cartesian API) and R2C Pose7D.

Conventions:
- RPY: [x, y, z, Rx, Ry, Rz] — meters + radians (standard ZYX yaw-pitch-roll).
- R2C Pose7D: [x, y, z, qw, qx, qy, qz] — meters + unit quaternion (w-first).

If the SIASUN controller uses a different euler convention, adjust the
formulas here — both sides of the pipeline go through this module.
"""

from __future__ import annotations

import math
from typing import List, Sequence

_POSE7D = 7
_RPY = 6


def _clamp1(value: float) -> float:
    return max(-1.0, min(1.0, value))


def rpy_to_pose7d(pose_rpy: Sequence[float]) -> List[float]:
    """[x, y, z, Rx, Ry, Rz] -> [x, y, z, qw, qx, qy, qz]."""
    if len(pose_rpy) != _RPY:
        raise ValueError(f"rpy pose must have {_RPY} values, got {len(pose_rpy)}")
    x, y, z, roll, pitch, yaw = (float(v) for v in pose_rpy)

    cr, sr = math.cos(roll * 0.5), math.sin(roll * 0.5)
    cp, sp = math.cos(pitch * 0.5), math.sin(pitch * 0.5)
    cy, sy = math.cos(yaw * 0.5), math.sin(yaw * 0.5)

    return [
        x,
        y,
        z,
        cr * cp * cy + sr * sp * sy,
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
    ]


def pose7d_to_rpy(pose7d: Sequence[float]) -> List[float]:
    """[x, y, z, qw, qx, qy, qz] -> [x, y, z, Rx, Ry, Rz]."""
    if len(pose7d) != _POSE7D:
        raise ValueError(f"pose7d must have {_POSE7D} values, got {len(pose7d)}")
    x, y, z, qw, qx, qy, qz = (float(v) for v in pose7d)

    sin_pitch = _clamp1(2.0 * (qw * qy - qz * qx))
    roll = math.atan2(2.0 * (qw * qx + qy * qz), 1.0 - 2.0 * (qx * qx + qy * qy))
    pitch = math.asin(sin_pitch)
    yaw = math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz))

    return [x, y, z, roll, pitch, yaw]
