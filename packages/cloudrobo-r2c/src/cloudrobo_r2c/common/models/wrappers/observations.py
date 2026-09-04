"""Robot observation data model definitions."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any

from cloudrobo_r2c.common.utils import BytesEncoder
from .common import ExtensionValue
from .validators import (
    ValidationError, validate_required_fields, validate_type, validate_timestamp,
    validate_list_items_type, validate_string, validate_pose7d
)

logger = logging.getLogger(__name__)

try:
    from ..generated import observation_pb2
except Exception:
    observation_pb2 = None


@dataclass
class ImageGroups:
    color: Dict[str, bytes] = field(default_factory=dict)
    depth: Dict[str, bytes] = field(default_factory=dict)


@dataclass
class JointStates:
    names: List[str] = field(default_factory=list)
    position: List[float] = field(default_factory=list)
    velocity: List[float] = field(default_factory=list)
    torque: List[float] = field(default_factory=list)


@dataclass
class EndEffectorPoses:
    names: List[str] = field(default_factory=list)
    pose: List[List[float]] = field(default_factory=list)


@dataclass
class EndEffectorStates:
    names: List[str] = field(default_factory=list)
    position: List[float] = field(default_factory=list)
    force: List[float] = field(default_factory=list)


@dataclass
class Localization:
    odom_pose: List[float] = field(default_factory=list)
    map_pose: List[float] = field(default_factory=list)


@dataclass
class PointCloud:
    data: bytes
    name: str = ""


def _parse_images(images_data: Dict[str, Any]) -> ImageGroups:
    if not images_data:
        return ImageGroups()
    validate_type(images_data, dict, 'images', 'Observations')
    color = images_data.get('color', {})
    depth = images_data.get('depth', {})
    validate_type(color, dict, 'images.color', 'Observations')
    validate_type(depth, dict, 'images.depth', 'Observations')
    # Filter out None values to avoid protobuf serialization errors
    color = {k: v for k, v in color.items() if v is not None}
    depth = {k: v for k, v in depth.items() if v is not None}
    return ImageGroups(color=color, depth=depth)


def _parse_joint_states(data: Dict[str, Any]) -> JointStates:
    data = data or {}
    validate_type(data, dict, 'joint_states', 'Observations')
    names = data.get('names', [])
    position = data.get('position', [])
    velocity = data.get('velocity', [])
    torque = data.get('torque', [])
    if names:
        validate_list_items_type(names, str, 'joint_states.names', 'Observations')
    for field_name, values in [('position', position), ('velocity', velocity), ('torque', torque)]:
        if values:
            validate_list_items_type(values, (int, float), f'joint_states.{field_name}', 'Observations')
    return JointStates(names=names, position=position, velocity=velocity, torque=torque)


def _parse_ee_poses(data: Dict[str, Any]) -> EndEffectorPoses:
    data = data or {}
    validate_type(data, dict, 'end_effector_poses', 'Observations')
    names = data.get('names', [])
    pose = [validate_pose7d(p, 'end_effector_poses.pose', 'Observations') for p in data.get('pose', [])]
    if names:
        validate_list_items_type(names, str, 'end_effector_poses.names', 'Observations')
    return EndEffectorPoses(names=names, pose=pose)


def _parse_ee_states(data: Dict[str, Any]) -> EndEffectorStates:
    data = data or {}
    validate_type(data, dict, 'end_effector_states', 'Observations')
    names = data.get('names', [])
    position = data.get('position', [])
    force = data.get('force', [])
    if names:
        validate_list_items_type(names, str, 'end_effector_states.names', 'Observations')
    for field_name, values in [('position', position), ('force', force)]:
        if values:
            validate_list_items_type(values, (int, float), f'end_effector_states.{field_name}', 'Observations')
    return EndEffectorStates(names=names, position=position, force=force)


def _parse_localization(data: Dict[str, Any]) -> Localization:
    data = data or {}
    validate_type(data, dict, 'localization', 'Observations')
    odom_pose = validate_pose7d(data['odom_pose'], 'localization.odom_pose', 'Observations') if data.get('odom_pose') else []
    map_pose = validate_pose7d(data['map_pose'], 'localization.map_pose', 'Observations') if data.get('map_pose') else []
    return Localization(odom_pose=odom_pose, map_pose=map_pose)


def _parse_pointclouds(items: List[Dict[str, Any]]) -> List[PointCloud]:
    if not items:
        return []
    validate_type(items, list, 'pointclouds', 'Observations')
    pointclouds = []
    for i, pc in enumerate(items):
        validate_type(pc, dict, f'pointclouds[{i}]', 'Observations')
        if 'data' not in pc or not isinstance(pc['data'], bytes):
            raise ValidationError(f"Observations: PointCloud {i} field 'data' must be bytes")
        name = pc.get('name', '')
        if name and not isinstance(name, str):
            raise ValidationError(f"Observations: PointCloud {i} field 'name' must be str")
        pointclouds.append(PointCloud(data=pc['data'], name=name))
    return pointclouds


_VALID_EXTENSION_DTYPES = frozenset({
    "FLOAT32", "FLOAT64", "INT32", "INT64",
    "UINT8", "BOOL", "STRING", "BYTES",
})


def _parse_extensions(
    data: Dict[str, Any], *, _context: str = "Observations",
) -> Dict[str, ExtensionValue]:
    """Parse extensions dict into ``ExtensionValue`` objects.

    Each value may already be an ``ExtensionValue`` (from programmatic
    construction or mapper output), or a dict matching the
    ``ExtensionValue`` field layout.

    Validates dtype, shape, and data fields on dict inputs so that
    malformed extension values are caught at parse time rather than
    at protobuf serialization time.

    The *_context* parameter customises the error-message prefix so
    that both ``Observations`` and ``Actions`` can reuse this parser
    with the correct class name.
    """
    if not data:
        return {}
    result: Dict[str, ExtensionValue] = {}
    for key, val in data.items():
        if isinstance(val, ExtensionValue):
            result[key] = val
        elif isinstance(val, dict):
            _path = f"{_context}.extensions['{key}']"

            dtype = str(val.get("dtype", "")).strip().upper()
            if dtype not in _VALID_EXTENSION_DTYPES:
                raise ValidationError(
                    f"{_path}: unknown dtype {dtype!r}. "
                    f"Must be one of {sorted(_VALID_EXTENSION_DTYPES)}"
                )

            shape_raw = val.get("shape", [])
            if not isinstance(shape_raw, list):
                raise ValidationError(
                    f"{_path}: 'shape' must be a list, "
                    f"got {type(shape_raw).__name__}"
                )
            shape: List[int] = []
            neg_one_count = 0
            for i, dim in enumerate(shape_raw):
                if not isinstance(dim, int):
                    raise ValidationError(
                        f"{_path}: shape[{i}] must be an int, "
                        f"got {type(dim).__name__}"
                    )
                if dim < -1:
                    raise ValidationError(
                        f"{_path}: shape[{i}] must be >= -1, "
                        f"got {dim}"
                    )
                if dim == -1:
                    neg_one_count += 1
                shape.append(dim)
            if neg_one_count > 1:
                raise ValidationError(
                    f"{_path}: shape may contain at most one -1 "
                    f"(inferred dimension), got {neg_one_count}: {shape}"
                )

            data_val = val.get("data", b"")
            if not isinstance(data_val, (bytes, bytearray)):
                raise ValidationError(
                    f"{_path}: 'data' must be bytes, "
                    f"got {type(data_val).__name__}"
                )
            if isinstance(data_val, bytearray):
                data_val = bytes(data_val)

            mime_type = str(val.get("mime_type", ""))

            result[key] = ExtensionValue(
                dtype=dtype,
                shape=shape,
                data=data_val,
                mime_type=mime_type,
            )
        else:
            raise ValidationError(
                f"{_context}.extensions['{key}'] must be ExtensionValue "
                f"or dict, got {type(val).__name__}"
            )
    return result


@dataclass
class Observations:
    timestamp: int
    task: str
    id: int = 0
    images: ImageGroups = field(default_factory=ImageGroups)
    joint_states: JointStates = field(default_factory=JointStates)
    end_effector_poses: EndEffectorPoses = field(default_factory=EndEffectorPoses)
    end_effector_states: EndEffectorStates = field(default_factory=EndEffectorStates)
    localization: Localization = field(default_factory=Localization)
    pointclouds: List[PointCloud] = field(default_factory=list)
    extensions: Dict[str, "ExtensionValue"] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Observations:
        validate_type(data, dict, 'data', 'Observations')
        validate_required_fields(data, ['timestamp', 'task'], 'Observations')
        validate_timestamp(data['timestamp'], 'timestamp', 'Observations')
        validate_string(data['task'], 'task', 'Observations', allow_empty=False)
        if 'id' in data and (not isinstance(data['id'], int) or data['id'] < 0):
            raise ValidationError("Observations: Field 'id' must be >= 0")
        return cls(
            timestamp=data['timestamp'],
            task=data['task'],
            id=data.get('id', 0),
            images=_parse_images(data.get('images', {})),
            joint_states=_parse_joint_states(data.get('joint_states', {})),
            end_effector_poses=_parse_ee_poses(data.get('end_effector_poses', {})),
            end_effector_states=_parse_ee_states(data.get('end_effector_states', {})),
            localization=_parse_localization(data.get('localization', {})),
            pointclouds=_parse_pointclouds(data.get('pointclouds', [])),
            extensions=_parse_extensions(data.get('extensions', {})),
        )

    @classmethod
    def from_pb_object(cls, pb: Any) -> Observations:
        images = ImageGroups(
            color=dict(pb.raw_images.color) if pb.HasField('raw_images') else {},
            depth=dict(pb.raw_images.depth) if pb.HasField('raw_images') else {},
        )
        return cls(
            timestamp=pb.timestamp,
            task=pb.task,
            id=pb.id,
            images=images,
            joint_states=JointStates(list(pb.joint_states.names), list(pb.joint_states.position), list(pb.joint_states.velocity), list(pb.joint_states.torque)),
            end_effector_poses=EndEffectorPoses(list(pb.end_effector_poses.names), [list(pose.data) for pose in pb.end_effector_poses.pose]),
            end_effector_states=EndEffectorStates(list(pb.end_effector_states.names), list(pb.end_effector_states.position), list(pb.end_effector_states.force)),
            localization=Localization(list(pb.localization.odom_pose), list(pb.localization.map_pose)),
            pointclouds=[PointCloud(data=pc.data, name=pc.name) for pc in pb.pointclouds],
            extensions=ExtensionValue.from_proto_extensions(pb.extensions),
        )

    @classmethod
    def from_protobuf(cls, payload: bytes) -> Observations:
        if observation_pb2 is None:
            raise ImportError("Protobuf code not generated, please run protoc to compile observation.proto")
        pb = observation_pb2.Observations()
        pb.ParseFromString(payload)
        return cls.from_pb_object(pb)

    def to_protobuf(self) -> Any:
        if observation_pb2 is None:
            raise ImportError("Protobuf code not generated, please run protoc to compile observation.proto")
        obs_pb = observation_pb2.Observations()
        obs_pb.timestamp = self.timestamp
        obs_pb.task = self.task
        obs_pb.id = self.id
        obs_pb.image_encoding = "raw"
        obs_pb.raw_images.color.update(
            {k: v for k, v in self.images.color.items() if v is not None}
        )
        obs_pb.raw_images.depth.update(
            {k: v for k, v in self.images.depth.items() if v is not None}
        )
        obs_pb.joint_states.names.extend(self.joint_states.names)
        obs_pb.joint_states.position.extend(self.joint_states.position)
        obs_pb.joint_states.velocity.extend(self.joint_states.velocity)
        obs_pb.joint_states.torque.extend(self.joint_states.torque)
        obs_pb.end_effector_poses.names.extend(self.end_effector_poses.names)
        for p in self.end_effector_poses.pose:
            obs_pb.end_effector_poses.pose.add().data.extend(p.data if hasattr(p, 'data') else p)
        obs_pb.end_effector_states.names.extend(self.end_effector_states.names)
        obs_pb.end_effector_states.position.extend(self.end_effector_states.position)
        obs_pb.end_effector_states.force.extend(self.end_effector_states.force)
        obs_pb.localization.odom_pose.extend(self.localization.odom_pose)
        obs_pb.localization.map_pose.extend(self.localization.map_pose)
        for pc in self.pointclouds:
            msg = obs_pb.pointclouds.add()
            msg.data = pc.data
            msg.name = pc.name
        # ── extensions ─────────────────────────────────────────
        for key, ev in self.extensions.items():
            ev_pb = obs_pb.extensions[key]
            ev_pb.dtype = ev.to_proto_dtype()
            ev_pb.shape.extend(ev.shape)
            ev_pb.data = ev.data
            ev_pb.mime_type = ev.mime_type
        return obs_pb

    def serialize(self) -> bytes:
        try:
            return self.to_protobuf().SerializeToString()
        except Exception:
            logger.warning("Using JSON serialization fallback")
            return self.to_json_bytes()

    def to_json_bytes(self) -> bytes:
        data = {"observations": asdict(self)}
        return json.dumps(data, cls=BytesEncoder).encode('utf-8')
