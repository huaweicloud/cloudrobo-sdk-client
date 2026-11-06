"""Camera streaming command data model (downlink: job/command topic).

Topic: ``{project_id}/{robot_id}/job/command``
Direction: cloud -> edge (pilot-datacollector -> r2c-gateway -> Zenoh -> SDK)

One ``CameraCommand`` message carries three command types distinguished by
the ``command`` field, each using a different field subset:

- ``CAMERA_START``    — start camera streaming (carries JWT + wss URL)
- ``CAMERA_STOP``     — stop camera streaming
- ``RTC_TOKEN_RENEW`` — JWT renewal response (carries new JWT)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict

from .validators import validate_type

logger = logging.getLogger(__name__)

try:
    from ..generated import camera_command_pb2
except Exception:
    camera_command_pb2 = None

# Known camera command types
CAMERA_START = "CAMERA_START"
CAMERA_STOP = "CAMERA_STOP"
RTC_TOKEN_RENEW = "RTC_TOKEN_RENEW"


@dataclass
class CameraCommand:
    """job/command topic payload wrapper (protobuf CameraCommand)."""

    command: str = ""
    # "all" | "front" | "left_front" | "right_front" | "wrist" | "left_wrist" | "right_wrist"
    camera_id: str = "all"
    command_id: str = ""
    token: str = ""
    url: str = ""
    room: str = ""
    expires_in: int = 0
    renewal_before: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CameraCommand":
        validate_type(data, dict, "data", "CameraCommand")
        return cls(
            command=str(data.get("command", "")),
            camera_id=str(data.get("camera_id", "all")),
            command_id=str(data.get("command_id", "")),
            token=str(data.get("token", "")),
            url=str(data.get("url", "")),
            room=str(data.get("room", "")),
            expires_in=int(data.get("expires_in", 0)),
            renewal_before=int(data.get("renewal_before", 0)),
        )

    @classmethod
    def from_protobuf(cls, payload: bytes) -> "CameraCommand":
        if not camera_command_pb2:
            raise ImportError("Protobuf code not generated")

        pb = camera_command_pb2.CameraCommand()
        pb.ParseFromString(payload)
        return cls(
            command=pb.command,
            camera_id=pb.camera_id,
            command_id=pb.command_id,
            token=pb.token,
            url=pb.url,
            room=pb.room,
            expires_in=pb.expires_in,
            renewal_before=pb.renewal_before,
        )

    def to_protobuf(self) -> Any:
        if not camera_command_pb2:
            raise ImportError("Protobuf code not generated")

        pb = camera_command_pb2.CameraCommand()
        pb.command = self.command
        pb.camera_id = self.camera_id
        pb.command_id = self.command_id
        pb.token = self.token
        pb.url = self.url
        pb.room = self.room
        pb.expires_in = int(self.expires_in)
        pb.renewal_before = int(self.renewal_before)
        return pb

    def serialize(self) -> bytes:
        return self.to_protobuf().SerializeToString()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "camera_id": self.camera_id,
            "command_id": self.command_id,
            "token": self.token,
            "url": self.url,
            "room": self.room,
            "expires_in": self.expires_in,
            "renewal_before": self.renewal_before,
        }
