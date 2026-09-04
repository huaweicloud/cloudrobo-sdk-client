"""IMU state model definitions."""

from __future__ import annotations

import logging
from google.protobuf.timestamp_pb2 import Timestamp
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .common import Quaternion, Vector3
from .validators import (
    validate_numeric_sequence,
    validate_optional_list_field,
    validate_string,
    normalize_proto_timestamp,
    validate_type,
)

logger = logging.getLogger(__name__)

try:
    from ..generated import imu_state_pb2
except Exception:
    imu_state_pb2 = None


@dataclass
class IMUState:
    timestamp: Timestamp
    name: str
    source: str = ""
    orientation: Quaternion = field(default_factory=Quaternion)
    angular_velocity: Vector3 = field(default_factory=Vector3)
    linear_acceleration: Vector3 = field(default_factory=Vector3)
    magnetic_field: Vector3 = field(default_factory=Vector3)

    orientation_covariance: List[float] = field(default_factory=list)
    angular_velocity_covariance: List[float] = field(default_factory=list)
    linear_acceleration_covariance: List[float] = field(default_factory=list)
    magnetic_field_covariance: List[float] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> IMUState:
        """Construct IMUState object from dict."""
        validate_type(data, dict, "data", "IMUState")

        timestamp = data.get("timestamp")
        timestamp = normalize_proto_timestamp(timestamp, "timestamp", "IMUState")

        name = data.get("name")
        validate_string(name, "name", "IMUState")
        source = data.get("source", "")
        if source:
            validate_string(source, "source", "IMUState")

        def _vec(f: str, default: List[float], size: int) -> List[float]:
            value = data.get(f, default)
            if not value:
                return list(default)
            return validate_numeric_sequence(
                value, f, "IMUState", expected_length=size, allow_tuple=True
            )

        covariances = {
            k: validate_optional_list_field(data, k, "IMUState")
            for k in [
                "orientation_covariance",
                "angular_velocity_covariance",
                "linear_acceleration_covariance",
                "magnetic_field_covariance",
            ]
        }

        return cls(
            timestamp=timestamp,
            name=name,
            source=source,
            orientation=Quaternion(*_vec("orientation", [0, 0, 0, 1], 4)),
            angular_velocity=Vector3(*_vec("angular_velocity", [0, 0, 0], 3)),
            linear_acceleration=Vector3(*_vec("linear_acceleration", [0, 0, 0], 3)),
            magnetic_field=Vector3(*_vec("magnetic_field", [0, 0, 0], 3)),
            orientation_covariance=covariances["orientation_covariance"],
            angular_velocity_covariance=covariances["angular_velocity_covariance"],
            linear_acceleration_covariance=covariances[
                "linear_acceleration_covariance"
            ],
            magnetic_field_covariance=covariances["magnetic_field_covariance"],
        )

    @classmethod
    def from_protobuf(cls, payload: bytes) -> IMUState:
        if not imu_state_pb2:
            raise ImportError("Protobuf code not generated")

        pb = imu_state_pb2.IMUState()
        pb.ParseFromString(payload)

        orient = Quaternion(
            x=pb.orientation.x,
            y=pb.orientation.y,
            z=pb.orientation.z,
            w=pb.orientation.w,
        )
        ang_vel = Vector3(
            x=pb.angular_velocity.x, y=pb.angular_velocity.y, z=pb.angular_velocity.z
        )
        lin_acc = Vector3(
            x=pb.linear_acceleration.x,
            y=pb.linear_acceleration.y,
            z=pb.linear_acceleration.z,
        )
        mag_field = Vector3(
            x=pb.magnetic_field.x, y=pb.magnetic_field.y, z=pb.magnetic_field.z
        )

        return cls(
            timestamp=normalize_proto_timestamp(pb.timestamp, "timestamp", "IMUState"),
            name=pb.name,
            source=getattr(pb, "source", ""),
            orientation=orient,
            angular_velocity=ang_vel,
            linear_acceleration=lin_acc,
            magnetic_field=mag_field,
            orientation_covariance=list(pb.orientation_covariance),
            angular_velocity_covariance=list(pb.angular_velocity_covariance),
            linear_acceleration_covariance=list(pb.linear_acceleration_covariance),
            magnetic_field_covariance=list(pb.magnetic_field_covariance),
        )

    def to_protobuf(self) -> Any:
        if not imu_state_pb2:
            raise ImportError("Protobuf code not generated")

        pb = imu_state_pb2.IMUState()
        pb.timestamp.CopyFrom(
            normalize_proto_timestamp(self.timestamp, "timestamp", "IMUState")
        )
        pb.name = self.name
        if hasattr(pb, "source"):
            pb.source = self.source

        if self.orientation:
            pb.orientation.x = self.orientation.x
            pb.orientation.y = self.orientation.y
            pb.orientation.z = self.orientation.z
            pb.orientation.w = self.orientation.w

        if self.angular_velocity:
            pb.angular_velocity.x = self.angular_velocity.x
            pb.angular_velocity.y = self.angular_velocity.y
            pb.angular_velocity.z = self.angular_velocity.z

        if self.linear_acceleration:
            pb.linear_acceleration.x = self.linear_acceleration.x
            pb.linear_acceleration.y = self.linear_acceleration.y
            pb.linear_acceleration.z = self.linear_acceleration.z

        if self.magnetic_field:
            pb.magnetic_field.x = self.magnetic_field.x
            pb.magnetic_field.y = self.magnetic_field.y
            pb.magnetic_field.z = self.magnetic_field.z

        pb.orientation_covariance.extend(self.orientation_covariance)
        pb.angular_velocity_covariance.extend(self.angular_velocity_covariance)
        pb.linear_acceleration_covariance.extend(self.linear_acceleration_covariance)
        pb.magnetic_field_covariance.extend(self.magnetic_field_covariance)

        return pb

    def serialize(self) -> bytes:
        return self.to_protobuf().SerializeToString()
