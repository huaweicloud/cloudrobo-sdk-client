"""Camera capability reporting data model (uplink: device/camera_capability).

Topic: ``{project_id}/{robot_id}/device/camera_capability``
Direction: edge -> cloud (SDK -> Zenoh -> r2c-gateway -> pilot-datacollector)

The edge SDK enumerates local camera devices and reports capabilities to the
cloud, which stores them in the ``t_robot_camera_capability`` table.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .validators import validate_type

logger = logging.getLogger(__name__)

try:
    from ..generated import camera_capability_pb2
except Exception:
    camera_capability_pb2 = None


@dataclass
class CameraCapability:
    """Single camera capability entry (protobuf CameraCapability)."""

    camera_id: str = ""
    name: str = ""
    resolution: str = ""
    fps: int = 0
    codec: str = "h264"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CameraCapability":
        validate_type(data, dict, "data", "CameraCapability")
        return cls(
            camera_id=str(data.get("camera_id", "")),
            name=str(data.get("name", "")),
            resolution=str(data.get("resolution", "")),
            fps=int(data.get("fps", 0)),
            codec=str(data.get("codec", "h264")),
        )

    @classmethod
    def from_protobuf(cls, pb: Any) -> "CameraCapability":
        return cls(
            camera_id=pb.camera_id,
            name=pb.name,
            resolution=pb.resolution,
            fps=pb.fps,
            codec=pb.codec,
        )

    def to_protobuf(self, pb: Any) -> Any:
        pb.camera_id = self.camera_id
        pb.name = self.name
        pb.resolution = self.resolution
        pb.fps = int(self.fps)
        pb.codec = self.codec
        return pb

    def to_dict(self) -> Dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "name": self.name,
            "resolution": self.resolution,
            "fps": self.fps,
            "codec": self.codec,
        }


@dataclass
class CameraCapabilityReport:
    """Camera capability report payload (protobuf CameraCapabilityReport)."""

    device_id: str = ""
    cameras: List[CameraCapability] = field(default_factory=list)
    timestamp: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CameraCapabilityReport":
        validate_type(data, dict, "data", "CameraCapabilityReport")
        raw_cameras = data.get("cameras", [])
        if not isinstance(raw_cameras, list):
            raise TypeError(
                "CameraCapabilityReport: Field 'cameras' must be a list, "
                f"got {type(raw_cameras).__name__}"
            )
        return cls(
            device_id=str(data.get("device_id", "")),
            cameras=[CameraCapability.from_dict(c) for c in raw_cameras],
            timestamp=int(data.get("timestamp", 0)),
        )

    @classmethod
    def from_protobuf(cls, payload: bytes) -> "CameraCapabilityReport":
        if not camera_capability_pb2:
            raise ImportError("Protobuf code not generated")

        pb = camera_capability_pb2.CameraCapabilityReport()
        pb.ParseFromString(payload)
        return cls(
            device_id=pb.device_id,
            cameras=[CameraCapability.from_protobuf(c) for c in pb.cameras],
            timestamp=pb.timestamp,
        )

    def to_protobuf(self) -> Any:
        if not camera_capability_pb2:
            raise ImportError("Protobuf code not generated")

        pb = camera_capability_pb2.CameraCapabilityReport()
        pb.device_id = self.device_id
        pb.timestamp = int(self.timestamp)
        for cam in self.cameras:
            cam.to_protobuf(pb.cameras.add())
        return pb

    def serialize(self) -> bytes:
        return self.to_protobuf().SerializeToString()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "cameras": [c.to_dict() for c in self.cameras],
            "timestamp": self.timestamp,
        }
