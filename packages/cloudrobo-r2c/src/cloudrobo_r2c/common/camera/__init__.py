"""Shared camera drivers usable by any hardware adapter.

Currently holds the RealSense capture wrapper (``RealSenseCamera``), which
keeps the device in a background capture thread and exposes the latest frame
via ``get_latest_frame()``. Adapters own the device; the LiveKit streaming
side only reads the shared frame slot (see ``ICameraFrameSource``).
"""

from __future__ import annotations

from .realsense_camera import RealSenseCamera

__all__ = ["RealSenseCamera"]
