"""JWT renewal request data model (uplink: device/token_renew_request).

Topic: ``{project_id}/{robot_id}/device/token_renew_request``
Direction: edge -> cloud (SDK -> Zenoh -> r2c-gateway -> pilot-datacollector)

The edge SDK sets a local timer (at ``expires_in - renewal_before`` seconds
after JWT issuance) to proactively request JWT renewal via this topic.
The ``source`` field is auto-filled by ``session._ensure_state_source``
as ``"{project_id}/{device_id}"`` at publish time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict

from .validators import validate_type

logger = logging.getLogger(__name__)

try:
    from ..generated import token_renew_pb2
except Exception:
    token_renew_pb2 = None


@dataclass
class TokenRenewRequest:
    """device/token_renew_request payload wrapper (protobuf TokenRenewRequest)."""

    # 数据来源标识，发布时由 _ensure_state_source 填充 "{project_id}/{device_id}"
    source: str = ""
    # 当前推流 JWT 的 jti（pilot-datacollector 校验未过期且未入黑名单）
    old_jti: str = ""
    # 当前推流的 camera_id（标识续签关联的推流会话）
    camera_id: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TokenRenewRequest":
        validate_type(data, dict, "data", "TokenRenewRequest")
        return cls(
            source=str(data.get("source", "")),
            old_jti=str(data.get("old_jti", "")),
            camera_id=str(data.get("camera_id", "")),
        )

    @classmethod
    def from_protobuf(cls, payload: bytes) -> "TokenRenewRequest":
        if not token_renew_pb2:
            raise ImportError("Protobuf code not generated")

        pb = token_renew_pb2.TokenRenewRequest()
        pb.ParseFromString(payload)
        return cls(
            source=pb.source,
            old_jti=pb.old_jti,
            camera_id=pb.camera_id,
        )

    def to_protobuf(self) -> Any:
        if not token_renew_pb2:
            raise ImportError("Protobuf code not generated")

        pb = token_renew_pb2.TokenRenewRequest()
        pb.source = self.source
        pb.old_jti = self.old_jti
        pb.camera_id = self.camera_id
        return pb

    def serialize(self) -> bytes:
        return self.to_protobuf().SerializeToString()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "old_jti": self.old_jti,
            "camera_id": self.camera_id,
        }
