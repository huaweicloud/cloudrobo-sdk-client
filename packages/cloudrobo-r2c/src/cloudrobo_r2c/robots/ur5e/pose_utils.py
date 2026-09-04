"""Pose utilities: conversions between 6D pose, position+rotation, and 4x4 matrices."""
import numpy as np
import scipy.spatial.transform as st


def pos_rot_to_mat(pos, rot):
    """Convert position + scipy Rotation to 4x4 homogeneous matrix."""
    shape = pos.shape[:-1]
    mat = np.zeros(shape + (4, 4), dtype=pos.dtype)
    mat[..., :3, 3] = pos
    mat[..., :3, :3] = rot.as_matrix()
    mat[..., 3, 3] = 1
    return mat


def mat_to_pos_rot(mat):
    """Convert 4x4 homogeneous matrix to position + scipy Rotation."""
    pos = mat[..., :3, 3]
    rot = st.Rotation.from_matrix(mat[..., :3, :3])
    return pos, rot


def pos_rot_to_pose(pos, rot):
    """Convert position + scipy Rotation to 6D pose [x, y, z, rx, ry, rz] (axis-angle)."""
    pose = np.zeros(pos.shape[:-1] + (6,), dtype=pos.dtype)
    pose[..., :3] = pos
    pose[..., 3:] = rot.as_rotvec()
    return pose


def pose_to_pos_rot(pose):
    """Convert 6D pose [x, y, z, rx, ry, rz] (axis-angle) to position + scipy Rotation."""
    pos = pose[..., :3]
    rot = st.Rotation.from_rotvec(pose[..., 3:])
    return pos, rot


def pose_to_mat(pose):
    """Convert 6D pose to 4x4 homogeneous matrix."""
    return pos_rot_to_mat(*pose_to_pos_rot(pose))


def mat_to_pose(mat):
    """Convert 4x4 homogeneous matrix to 6D pose."""
    return pos_rot_to_pose(*mat_to_pos_rot(mat))


def apply_delta_pose(pose, delta_pose):
    """Apply delta to a 6D pose: position adds, rotation composes (delta * current)."""
    new_pose = np.zeros_like(pose)
    new_pose[:3] = pose[:3] + delta_pose[:3]
    rot = st.Rotation.from_rotvec(pose[3:])
    drot = st.Rotation.from_rotvec(delta_pose[3:])
    new_pose[3:] = (drot * rot).as_rotvec()
    return new_pose


def invert_pose(pose):
    """Invert a 6D pose."""
    mat = pose_to_mat(pose)
    inv_mat = np.linalg.inv(mat)
    return mat_to_pose(inv_mat)
