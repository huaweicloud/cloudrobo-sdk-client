"""Backwards-compatible re-export of the shared RealSense camera wrapper.

The implementation moved to :mod:`cloudrobo_r2c.common.camera.realsense_camera`
so that non-ur5e adapters (e.g. the dummy adapter) can reuse it without
depending on the ur5e package. This module is kept so that existing imports
(``from cloudrobo_r2c.robots.ur5e.realsense_camera import RealSenseCamera``)
continue to work.
"""

from cloudrobo_r2c.common.camera.realsense_camera import (  # noqa: F401
    RealSenseCamera,
    normalize_serial,
)

__all__ = ["RealSenseCamera", "normalize_serial"]
