"""Quaternion and Euler angle math utilities (pure Python, no numpy)."""

from __future__ import annotations

import math
from typing import List, Tuple


def euler_to_quat(
    roll: float, pitch: float, yaw: float
) -> Tuple[float, float, float, float]:
    """Convert Euler angles (roll, pitch, yaw in radians) to quaternion.

    Returns:
        Tuple[float, float, float, float]: (qw, qx, qy, qz) — wxyz order.
    """
    cr = math.cos(0.5 * roll)
    sr = math.sin(0.5 * roll)
    cp = math.cos(0.5 * pitch)
    sp = math.sin(0.5 * pitch)
    cy = math.cos(0.5 * yaw)
    sy = math.sin(0.5 * yaw)

    qw = cr * cp * cy + sr * sp * sy
    qx = sr * cp * cy - cr * sp * sy
    qy = cr * sp * cy + sr * cp * sy
    qz = cr * cp * sy - sr * sp * cy

    return tuple(normalize_quat([qw, qx, qy, qz]))


def quat_to_euler(
    qw: float, qx: float, qy: float, qz: float
) -> Tuple[float, float, float]:
    """Convert quaternion (wxyz) to Euler angles (roll, pitch, yaw in radians).

    Returns:
        Tuple[float, float, float]: (roll, pitch, yaw)
    """
    qw, qx, qy, qz = normalize_quat([qw, qx, qy, qz])

    # Roll (x-axis rotation)
    sinr_cosp = 2.0 * (qw * qx + qy * qz)
    cosr_cosp = 1.0 - 2.0 * (qx * qx + qy * qy)
    roll = math.atan2(sinr_cosp, cosr_cosp)

    # Pitch (y-axis rotation)
    sinp = 2.0 * (qw * qy - qz * qx)
    if abs(sinp) >= 1.0:
        pitch = math.copysign(math.pi / 2.0, sinp)
    else:
        pitch = math.asin(sinp)

    # Yaw (z-axis rotation)
    siny_cosp = 2.0 * (qw * qz + qx * qy)
    cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
    yaw = math.atan2(siny_cosp, cosy_cosp)

    return (roll, pitch, yaw)


def normalize_quat(q: List[float]) -> List[float]:
    """Normalize a quaternion to unit length.

    If the magnitude is near zero, returns the identity quaternion [1,0,0,0].
    """
    norm = math.sqrt(q[0] ** 2 + q[1] ** 2 + q[2] ** 2 + q[3] ** 2)
    if norm < 1.0e-9:
        return [1.0, 0.0, 0.0, 0.0]
    inv = 1.0 / norm
    return [q[0] * inv, q[1] * inv, q[2] * inv, q[3] * inv]


def slerp(q1: List[float], q2: List[float], t: float) -> List[float]:
    """Spherical linear interpolation between two quaternions.

    Args:
        q1: First quaternion [qw, qx, qy, qz].
        q2: Second quaternion [qw, qx, qy, qz].
        t: Interpolation parameter in [0, 1]. t=0 returns q1, t=1 returns q2.

    Returns:
        List[float]: Interpolated quaternion [qw, qx, qy, qz].
    """
    q1 = normalize_quat(q1)
    q2 = normalize_quat(q2)

    # Compute dot product (cosine of half-angle between quaternions)
    dot = q1[0] * q2[0] + q1[1] * q2[1] + q1[2] * q2[2] + q1[3] * q2[3]

    # If dot is negative, flip q2 to take the shortest path
    if dot < 0.0:
        q2 = [-q2[0], -q2[1], -q2[2], -q2[3]]
        dot = -dot

    # If quaternions are very close, use linear interpolation
    if dot > 0.9995:
        result = [
            q1[0] + t * (q2[0] - q1[0]),
            q1[1] + t * (q2[1] - q1[1]),
            q1[2] + t * (q2[2] - q1[2]),
            q1[3] + t * (q2[3] - q1[3]),
        ]
        return normalize_quat(result)

    # Standard SLERP
    theta_0 = math.acos(dot)  # half-angle between quaternions
    sin_theta_0 = math.sin(theta_0)

    s1 = math.sin((1.0 - t) * theta_0) / sin_theta_0
    s2 = math.sin(t * theta_0) / sin_theta_0

    return [
        s1 * q1[0] + s2 * q2[0],
        s1 * q1[1] + s2 * q2[1],
        s1 * q1[2] + s2 * q2[2],
        s1 * q1[3] + s2 * q2[3],
    ]


def quat_angular_distance(q1: List[float], q2: List[float]) -> float:
    """Compute the angular distance between two quaternions in radians.

    Returns a value in [0, π].
    """
    dot = q1[0] * q2[0] + q1[1] * q2[1] + q1[2] * q2[2] + q1[3] * q2[3]
    # Clamp to [-1, 1] to avoid numerical issues
    dot = max(-1.0, min(1.0, dot))
    return 2.0 * math.acos(abs(dot))
