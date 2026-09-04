"""Heartbeat and status data model definitions."""

from __future__ import annotations

import logging
from google.protobuf.timestamp_pb2 import Timestamp
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .validators import (
    ValidationError,
    validate_list_items_type,
    validate_required_fields,
    validate_string,
    normalize_proto_timestamp,
    validate_type,
)

logger = logging.getLogger(__name__)


def _validate_battery(battery_data: Dict[str, Any]) -> BatteryStatus:
    validate_type(battery_data, dict, "battery", "Heartbeat")

    percentage = battery_data.get("percentage", 0.0)
    if not isinstance(percentage, (int, float)):
        raise ValidationError(
            f"Heartbeat: Field 'battery.percentage' must be numeric, got {type(percentage).__name__}"
        )
    if not (0.0 <= percentage <= 100.0):
        raise ValidationError(
            f"Heartbeat: Field 'battery.percentage' must be between 0 and 100, got {percentage}"
        )

    voltage = battery_data.get("voltage", 0.0)
    if not isinstance(voltage, (int, float)):
        raise ValidationError(
            f"Heartbeat: Field 'battery.voltage' must be numeric, got {type(voltage).__name__}"
        )
    if voltage < 0:
        raise ValidationError(
            f"Heartbeat: Field 'battery.voltage' must be >= 0, got {voltage}"
        )

    current = battery_data.get("current", 0.0)
    if not isinstance(current, (int, float)):
        raise ValidationError(
            f"Heartbeat: Field 'battery.current' must be numeric, got {type(current).__name__}"
        )

    return BatteryStatus(percentage=percentage, voltage=voltage, current=current)


try:
    from ..generated import heartbeat_pb2
except Exception:
    heartbeat_pb2 = None


@dataclass
class BatteryStatus:
    percentage: float = 0.0
    voltage: float = 0.0
    current: float = 0.0


@dataclass
class Heartbeat:
    timestamp: Timestamp
    status: str
    mode: str
    source: str = ""
    error_code: List[int] = field(default_factory=list)
    battery: BatteryStatus = field(default_factory=BatteryStatus)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Heartbeat:
        """Construct Heartbeat object from dict.

        Args:
            data: Dictionary containing heartbeat data with required fields:
                - timestamp (int, required): Timestamp in milliseconds
                - status (str, required): Status string
                - mode (str, required): Mode string
                - error_code (list, optional): List of error codes
                - battery (dict, optional): Battery status with:
                    - percentage (float): 0-100
                    - voltage (float): >= 0
                    - current (float): any value

        Returns:
            Heartbeat object constructed from the input data

        Raises:
            ValidationError: If input data is invalid
        """
        validate_type(data, dict, "data", "Heartbeat")
        validate_required_fields(data, ["timestamp", "status", "mode"], "Heartbeat")

        timestamp = data["timestamp"]
        status = data["status"]
        mode = data["mode"]
        source = data.get("source", "")

        timestamp = normalize_proto_timestamp(timestamp, "timestamp", "Heartbeat")
        validate_string(status, "status", "Heartbeat")
        validate_string(mode, "mode", "Heartbeat")
        if source:
            validate_string(source, "source", "Heartbeat")

        error_code = data.get("error_code", [])
        if error_code:
            validate_list_items_type(error_code, int, "error_code", "Heartbeat")
        else:
            validate_type(error_code, list, "error_code", "Heartbeat")

        battery = _validate_battery(data.get("battery", {}))

        return cls(
            timestamp=timestamp,
            status=status,
            mode=mode,
            source=source,
            error_code=error_code,
            battery=battery,
        )

    @classmethod
    def from_protobuf(cls, payload: bytes) -> Heartbeat:
        if not heartbeat_pb2:
            raise ImportError("Protobuf code not generated")

        pb = heartbeat_pb2.Heartbeat()
        pb.ParseFromString(payload)

        battery = BatteryStatus(
            percentage=pb.battery.percentage,
            voltage=pb.battery.voltage,
            current=pb.battery.current,
        )

        return cls(
            timestamp=normalize_proto_timestamp(pb.timestamp, "timestamp", "Heartbeat"),
            status=pb.status,
            mode=pb.mode,
            source=getattr(pb, "source", ""),
            error_code=list(pb.error_code),
            battery=battery,
        )

    def to_protobuf(self) -> Any:
        if not heartbeat_pb2:
            raise ImportError("Protobuf code not generated")

        pb = heartbeat_pb2.Heartbeat()
        pb.timestamp.CopyFrom(
            normalize_proto_timestamp(self.timestamp, "timestamp", "Heartbeat")
        )
        pb.status = self.status
        pb.mode = self.mode
        if hasattr(pb, "source"):
            pb.source = self.source
        pb.error_code.extend(self.error_code)

        if self.battery:
            pb.battery.percentage = self.battery.percentage
            pb.battery.voltage = self.battery.voltage
            pb.battery.current = self.battery.current

        return pb

    def serialize(self) -> bytes:
        return self.to_protobuf().SerializeToString()
