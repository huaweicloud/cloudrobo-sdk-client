"""Localization state model definitions."""

from __future__ import annotations

import logging
from google.protobuf.timestamp_pb2 import Timestamp
from dataclasses import dataclass, field
from typing import Any, Dict

from .common import Pose7D, PoseWithCovariance
from .validators import (
    ValidationError,
    validate_numeric_sequence,
    validate_optional_list_field,
    validate_string,
    normalize_proto_timestamp,
    validate_type,
)

logger = logging.getLogger(__name__)


def _parse_pose_with_covariance(
    data: Dict[str, Any], field_name: str
) -> PoseWithCovariance:
    payload = data.get(field_name, {})
    if not payload:
        return PoseWithCovariance(pose=Pose7D(), covariance=[])

    validate_type(payload, dict, field_name, "LocalizationState")

    pose_raw = payload.get("pose", [])
    covariance = validate_optional_list_field(
        payload, "covariance", "LocalizationState"
    )
    pose = (
        validate_numeric_sequence(
            pose_raw,
            f"{field_name}.pose",
            "LocalizationState",
            expected_length=7,
            allow_tuple=True,
        )
        if pose_raw
        else []
    )

    return PoseWithCovariance(pose=Pose7D(pose), covariance=covariance)


try:
    from ..generated import localization_state_pb2
except Exception:
    localization_state_pb2 = None


@dataclass
class LocalizationState:
    timestamp: Timestamp
    status: str
    frame_id: str
    source: str = ""
    odom_pose: PoseWithCovariance = field(
        default_factory=lambda: PoseWithCovariance(Pose7D(), [])
    )
    map_pose: PoseWithCovariance = field(
        default_factory=lambda: PoseWithCovariance(Pose7D(), [])
    )
    confidence: float = 0.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LocalizationState:
        validate_type(data, dict, "data", "LocalizationState")

        timestamp = data.get("timestamp")
        timestamp = normalize_proto_timestamp(
            timestamp, "timestamp", "LocalizationState"
        )

        status = data.get("status")
        validate_string(status, "status", "LocalizationState")

        frame_id = data.get("frame_id", "")
        if frame_id:
            validate_string(frame_id, "frame_id", "LocalizationState")
        source = data.get("source", "")
        if source:
            validate_string(source, "source", "LocalizationState")

        confidence = data.get("confidence", 0.0)
        if confidence and not isinstance(confidence, (int, float)):
            raise ValidationError(
                "LocalizationState: Field 'confidence' must be numeric"
            )

        return cls(
            timestamp=timestamp,
            status=status,
            frame_id=frame_id,
            source=source,
            odom_pose=_parse_pose_with_covariance(data, "odom_pose"),
            map_pose=_parse_pose_with_covariance(data, "map_pose"),
            confidence=confidence,
        )

    @classmethod
    def from_protobuf(cls, payload: bytes) -> LocalizationState:
        if not localization_state_pb2:
            raise ImportError("Protobuf code not generated")

        pb = localization_state_pb2.LocalizationState()
        pb.ParseFromString(payload)

        def _convert_pose_cov(pb_pc):
            return PoseWithCovariance(
                pose=Pose7D(list(pb_pc.pose.data)), covariance=list(pb_pc.covariance)
            )

        return cls(
            timestamp=normalize_proto_timestamp(
                pb.timestamp, "timestamp", "LocalizationState"
            ),
            status=pb.status,
            frame_id=pb.frame_id,
            source=getattr(pb, "source", ""),
            odom_pose=_convert_pose_cov(pb.odom_pose),
            map_pose=_convert_pose_cov(pb.map_pose),
            confidence=pb.confidence,
        )

    def to_protobuf(self) -> Any:
        if not localization_state_pb2:
            raise ImportError("Protobuf code not generated")

        pb = localization_state_pb2.LocalizationState()
        pb.timestamp.CopyFrom(
            normalize_proto_timestamp(self.timestamp, "timestamp", "LocalizationState")
        )
        pb.status = self.status
        pb.frame_id = self.frame_id
        if hasattr(pb, "source"):
            pb.source = self.source
        pb.confidence = self.confidence

        def _set_pose_cov(pb_pc, model_pc):
            if model_pc:
                pose_data = (
                    model_pc.pose.data
                    if hasattr(model_pc.pose, "data")
                    else model_pc.pose
                )
                pb_pc.pose.data.extend(pose_data)
                pb_pc.covariance.extend(model_pc.covariance)

        _set_pose_cov(pb.odom_pose, self.odom_pose)
        _set_pose_cov(pb.map_pose, self.map_pose)

        return pb

    def serialize(self) -> bytes:
        return self.to_protobuf().SerializeToString()
