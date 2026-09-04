"""Robot observation data model definitions (H264 version)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Tuple, Union, Optional

from cloudrobo_r2c.common.utils import BytesEncoder
from cloudrobo_r2c.common.utils.h264_encoder import H264ImageEncoder
from .common import ExtensionValue
from .observations import (
    JointStates, EndEffectorPoses, EndEffectorStates,
    Localization, PointCloud, Observations, _parse_extensions
)

logger = logging.getLogger(__name__)


class ValidationError(ValueError):
    """Raised when input data validation fails."""
    pass

# Try to import generated Protobuf code
try:
    # Note: Now using unified observation_pb2, H264 defined in image_h264.proto
    from ..generated import observation_pb2, image_h264_pb2
except Exception:
    observation_pb2 = None
    image_h264_pb2 = None


@dataclass
class ImageShape:
    """Image shape (height, width, channels)"""
    height: int
    width: int
    channels: int

    @classmethod
    def from_tuple(cls, shape: Union[Tuple[int, int, int], List[int]]) -> ImageShape:
        """Create from tuple or list (H, W, C)."""
        if len(shape) != 3:
            raise ValueError(f"Image shape must be (H, W, C), got {shape}")
        return cls(height=shape[0], width=shape[1], channels=shape[2])

    def to_tuple(self) -> Tuple[int, int, int]:
        """Convert to tuple (H, W, C)."""
        return (self.height, self.width, self.channels)
    
    def __getitem__(self, index):
        """Support index access (for compatibility)."""
        if index == 0: return self.height
        if index == 1: return self.width
        if index == 2: return self.channels
        raise IndexError("ImageShape index out of range")
        
    def __iter__(self):
        """Support unpacking (h, w, c = shape)."""
        yield self.height
        yield self.width
        yield self.channels
    
    def __repr__(self):
        return f"ImageShape(height={self.height}, width={self.width}, channels={self.channels})"


@dataclass
class H264ImageMetadata:
    """Metadata for H264 encoded images."""
    image_keys: List[str] = field(default_factory=list)
    original_shapes: List[ImageShape] = field(default_factory=list)
    combined_shape: Union[ImageShape, None] = None
    merge_direction: str = ""
    frame_count: int = 0
    quality_preset: str = ""
    speed_preset: str = ""
    crf_used: int = 0


@dataclass
class H264ImageData:
    """H264 encoded image data main body."""
    h264_data: bytes = b""
    metadata: H264ImageMetadata = field(default_factory=H264ImageMetadata)


@dataclass
class ObservationsH264:
    """Robot observation data main body (H264 Encoded)."""
    timestamp: int
    task: str
    id: int = 0

    # H264 image data
    images: H264ImageData = field(default_factory=H264ImageData)

    # Other common fields
    joint_states: JointStates = field(default_factory=JointStates)
    end_effector_poses: EndEffectorPoses = field(default_factory=EndEffectorPoses)
    end_effector_states: EndEffectorStates = field(default_factory=EndEffectorStates)
    localization: Localization = field(default_factory=Localization)
    pointclouds: List[PointCloud] = field(default_factory=list)
    extensions: Dict[str, ExtensionValue] = field(default_factory=dict)

    @staticmethod
    def parse_joint_states(data: Dict[str, Any]) -> JointStates:
        """Parse joint_states field into JointStates model."""
        joint_states_data = data.get('joint_states', {})
        return JointStates(
            names=joint_states_data.get('names', []),
            position=joint_states_data.get('position', []),
            velocity=joint_states_data.get('velocity', []),
            torque=joint_states_data.get('torque', []),
        )

    @staticmethod
    def parse_end_effector_poses(data: Dict[str, Any]) -> EndEffectorPoses:
        """Parse end_effector_poses field into EndEffectorPoses model."""
        ee_poses_data = data.get('end_effector_poses', {})
        return EndEffectorPoses(
            names=ee_poses_data.get('names', []),
            pose=ee_poses_data.get('pose', []),
        )

    @staticmethod
    def parse_end_effector_states(data: Dict[str, Any]) -> EndEffectorStates:
        """Parse end_effector_states field into EndEffectorStates model."""
        ee_states_data = data.get('end_effector_states', {})
        return EndEffectorStates(
            names=ee_states_data.get('names', []),
            position=ee_states_data.get('position', []),
            force=ee_states_data.get('force', []),
        )

    @staticmethod
    def parse_localization(data: Dict[str, Any]) -> Localization:
        """Parse localization field into Localization model."""
        localization_data = data.get('localization', {})
        return Localization(
            odom_pose=localization_data.get('odom_pose', []),
            map_pose=localization_data.get('map_pose', []),
        )

    @staticmethod
    def parse_pointclouds(data: Dict[str, Any]) -> List[PointCloud]:
        """Parse pointclouds field into list of PointCloud models."""
        return [
            PointCloud(data=pc['data'], name=pc.get('name', ''))
            for pc in data.get('pointclouds', [])
        ]

    @classmethod
    def from_dict_and_encode_images(
        cls,
        data: Dict[str, Any],
        image_sources: Dict[str, Any],
        encoder: Optional[H264ImageEncoder] = None,
        preset: Optional[str] = None,
    ) -> ObservationsH264:
        """Construct H264 encoded ObservationsH264 directly from dict + multi-camera images.

        This method automatically completes image merging, H264 encoding, and fills all metadata returned by encoder
        (image_keys, original_shapes, combined_shape, merge_direction, frame_count,
        quality_preset, speed_preset, crf_used) into H264ImageMetadata structure,
        ensuring complete fields during to_protobuf() serialization.

        :param data: Observation base fields dict (timestamp/task/joint_states/...), no need to include images.
        :param image_sources: Dict[str, np.ndarray], multi-camera images with camera names as keys.
        :param encoder: Optional H264ImageEncoder instance, creates default config if not provided.
        :param preset: Optional encoding quality preset.
        :return: Complete ObservationsH264 instance.

        Raises:
            ValidationError: If input data is invalid
        """
        if not isinstance(data, dict):
            raise ValidationError(
                f"ObservationsH264: Input data must be a dictionary, got {type(data).__name__}"
            )

        if not isinstance(image_sources, dict):
            raise ValidationError(
                f"ObservationsH264: image_sources must be a dictionary, got {type(image_sources).__name__}"
            )

        if not image_sources:
            raise ValidationError("No images found in observation for H264 encoding")

        encoder = encoder or H264ImageEncoder()
        encoded_payload, _ = encoder.encode_images({"images": image_sources}, preset=preset)

        images_section = encoded_payload.get("images", {})
        h264_bytes = images_section.get("h264_data", b"")
        metadata_dict = images_section.get("metadata", {})

        # Explicitly construct H264ImageMetadata, fill in all fields returned by encoder
        metadata = H264ImageMetadata(
            image_keys=metadata_dict.get("image_keys", []),
            merge_direction=metadata_dict.get("merge_direction", ""),
            frame_count=metadata_dict.get("frame_count", 0),
            quality_preset=str(metadata_dict.get("quality_preset", "")),
            speed_preset=metadata_dict.get("speed_preset", ""),
            crf_used=metadata_dict.get("crf_used", 0),
        )

        # original_shapes: List[tuple] -> List[ImageShape]
        metadata.original_shapes = [
            shape if isinstance(shape, ImageShape) else ImageShape.from_tuple(tuple(shape))
            for shape in metadata_dict.get("original_shapes", [])
        ]

        # combined_shape: tuple -> ImageShape
        combined_shape_raw = metadata_dict.get("combined_shape")
        if combined_shape_raw:
            metadata.combined_shape = (
                combined_shape_raw
                if isinstance(combined_shape_raw, ImageShape)
                else ImageShape.from_tuple(tuple(combined_shape_raw))
            )

        images = H264ImageData(h264_data=h264_bytes, metadata=metadata)

        # Parse other fields (reuse from_dict logic, but images already constructed)
        joint_states = cls.parse_joint_states(data)
        ee_poses = cls.parse_end_effector_poses(data)
        ee_states = cls.parse_end_effector_states(data)
        localization = cls.parse_localization(data)
        pointclouds = cls.parse_pointclouds(data)

        return cls(
            timestamp=data['timestamp'],
            task=data['task'],
            id=data.get('id', 0),
            images=images,
            joint_states=joint_states,
            end_effector_poses=ee_poses,
            end_effector_states=ee_states,
            localization=localization,
            pointclouds=pointclouds,
            extensions=_parse_extensions(data.get('extensions', {})),
        )

    @classmethod
    def from_observations(
        cls,
        observation: Observations,
        encoder: Optional[H264ImageEncoder] = None,
        preset: Optional[str] = None,
    ) -> ObservationsH264:
        """Convert standard Observations to H264 encoded version.

        :param observation: Original observation object, must contain at least one color or depth image.
        :param encoder: Optional H264ImageEncoder instance, creates default config if not provided.
        :param preset: Optional encoding quality preset, passed to encoder.encode_images.
        """
        image_sources: Dict[str, Any] = {}
        if observation.images.color:
            image_sources.update(observation.images.color)
        if observation.images.depth:
            image_sources.update(observation.images.depth)

        if not image_sources:
            raise ValueError("No images found in observation for H264 encoding")

        encoder = encoder or H264ImageEncoder()
        encoded_payload, _ = encoder.encode_images({"images": image_sources}, preset=preset)

        images_section = encoded_payload.get("images", {})
        h264_bytes = images_section.get("h264_data", b"")
        metadata_dict = images_section.get("metadata", {})

        metadata = H264ImageMetadata(
            image_keys=metadata_dict.get("image_keys", []),
            merge_direction=metadata_dict.get("merge_direction", ""),
            frame_count=metadata_dict.get("frame_count", 0),
            quality_preset=str(metadata_dict.get('quality_preset', '')),
            speed_preset=metadata_dict.get("speed_preset", ""),
            crf_used=metadata_dict.get("crf_used", 0),
        )

        metadata.original_shapes = [
            shape if isinstance(shape, ImageShape) else ImageShape.from_tuple(tuple(shape))
            for shape in metadata_dict.get("original_shapes", [])
        ]

        combined_shape = metadata_dict.get("combined_shape")
        if combined_shape:
            metadata.combined_shape = (
                combined_shape
                if isinstance(combined_shape, ImageShape)
                else ImageShape.from_tuple(tuple(combined_shape))
            )

        images = H264ImageData(h264_data=h264_bytes, metadata=metadata)

        return cls(
            timestamp=observation.timestamp,
            task=observation.task,
            id=observation.id,
            images=images,
            joint_states=observation.joint_states,
            end_effector_poses=observation.end_effector_poses,
            end_effector_states=observation.end_effector_states,
            localization=observation.localization,
            pointclouds=observation.pointclouds,
            extensions=dict(observation.extensions),
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ObservationsH264:
        """Construct ObservationsH264 object from dict."""
        # Process H264 image data
        images_data = data.get('images', {})
        h264_data = images_data.get('h264_data', b'')
        
        # Process metadata
        metadata_dict = images_data.get('metadata', {})
        metadata = H264ImageMetadata(
            image_keys=metadata_dict.get('image_keys', []),
            merge_direction=metadata_dict.get('merge_direction', ''),
            frame_count=metadata_dict.get('frame_count', 0),
            quality_preset=metadata_dict.get('quality_preset', ''),
            speed_preset=metadata_dict.get('speed_preset', ''),
            crf_used=metadata_dict.get('crf_used', 0)
        )
        
        # Process original_shapes: List[tuple] -> List[ImageShape]
        original_shapes_data = metadata_dict.get('original_shapes', [])
        metadata.original_shapes = [
            ImageShape.from_tuple(tuple(shape)) for shape in original_shapes_data
        ]
        
        # Process combined_shape: tuple -> ImageShape
        combined_shape_data = metadata_dict.get('combined_shape')
        if combined_shape_data:
            if isinstance(combined_shape_data, (tuple, list)):
                metadata.combined_shape = ImageShape.from_tuple(combined_shape_data)
            else:
                raise ValidationError('Combined shape format error')
        
        images = H264ImageData(
            h264_data=h264_data,
            metadata=metadata
        )

        # Process joint states (reuse existing class)
        joint_states = cls.parse_joint_states(data)
        ee_poses = cls.parse_end_effector_poses(data)
        ee_states = cls.parse_end_effector_states(data)
        localization = cls.parse_localization(data)
        pointclouds = cls.parse_pointclouds(data)

        return cls(
            timestamp=data['timestamp'],
            task=data['task'],
            id=data.get('id', 0),
            images=images,
            joint_states=joint_states,
            end_effector_poses=ee_poses,
            end_effector_states=ee_states,
            localization=localization,
            pointclouds=pointclouds,
            extensions=_parse_extensions(data.get('extensions', {})),
        )

    @classmethod
    def from_pb_object(cls, pb: Any) -> ObservationsH264:
        """Construct ObservationsH264 from Protobuf object."""
        images = H264ImageData()
        if pb.HasField("h264_images"):
            img_pb = pb.h264_images
            metadata = H264ImageMetadata()
            if img_pb.HasField('metadata'):
                meta_pb = img_pb.metadata
                metadata = H264ImageMetadata(
                    image_keys=list(meta_pb.image_keys),
                    merge_direction=meta_pb.merge_direction,
                    frame_count=meta_pb.frame_count,
                    quality_preset=meta_pb.quality_preset,
                    speed_preset=meta_pb.speed_preset,
                    crf_used=meta_pb.crf_used,
                    original_shapes=[ImageShape(s.height, s.width, s.channels) for s in meta_pb.original_shapes],
                    combined_shape=ImageShape(meta_pb.combined_shape.height, meta_pb.combined_shape.width, meta_pb.combined_shape.channels)
                    if meta_pb.HasField('combined_shape') else None,
                )
            images = H264ImageData(h264_data=img_pb.h264_data, metadata=metadata)

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
    def from_protobuf(cls, payload: bytes) -> ObservationsH264:
        """Parse binary Protobuf data into ObservationsH264 object."""
        if observation_pb2 is None:
            raise ImportError("Protobuf code not generated, please run protoc to compile observation.proto")

        pb = observation_pb2.Observations()
        pb.ParseFromString(payload)
        return cls.from_pb_object(pb)

    def to_protobuf(self) -> Any:
        """Convert to Protobuf message object."""
        if observation_pb2 is None:
            raise ImportError("Protobuf code not generated, please run protoc to compile observation.proto")

        obs_pb = observation_pb2.Observations()
        obs_pb.timestamp = self.timestamp
        obs_pb.task = self.task
        obs_pb.id = self.id
        obs_pb.image_encoding = "h264"

        img_pb = obs_pb.h264_images
        img_pb.h264_data = self.images.h264_data
        meta = self.images.metadata
        meta_pb = img_pb.metadata
        meta_pb.image_keys.extend(meta.image_keys)
        meta_pb.merge_direction = meta.merge_direction
        meta_pb.frame_count = meta.frame_count
        meta_pb.quality_preset = meta.quality_preset
        meta_pb.speed_preset = meta.speed_preset
        meta_pb.crf_used = meta.crf_used
        for s in meta.original_shapes:
            shp = meta_pb.original_shapes.add()
            shp.height, shp.width, shp.channels = s.height, s.width, s.channels
        if meta.combined_shape:
            meta_pb.combined_shape.height = meta.combined_shape.height
            meta_pb.combined_shape.width = meta.combined_shape.width
            meta_pb.combined_shape.channels = meta.combined_shape.channels

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
            pc_msg = obs_pb.pointclouds.add()
            pc_msg.data = pc.data
            pc_msg.name = pc.name
        # ── extensions ─────────────────────────────────────────
        for key, ev in self.extensions.items():
            ev_pb = obs_pb.extensions[key]
            ev_pb.dtype = ev.to_proto_dtype()
            ev_pb.shape.extend(ev.shape)
            ev_pb.data = ev.data
            ev_pb.mime_type = ev.mime_type
        return obs_pb

    def serialize(self) -> bytes:
        """Serialize to binary data."""
        try:
            return self.to_protobuf().SerializeToString()
        except Exception:
            return self.to_json_bytes()

    def to_json_bytes(self) -> bytes:
        """Serialize to JSON bytes."""
        data = {"observations_h264": asdict(self)}
        json_str = json.dumps(data, cls=BytesEncoder)
        return json_str.encode('utf-8')
