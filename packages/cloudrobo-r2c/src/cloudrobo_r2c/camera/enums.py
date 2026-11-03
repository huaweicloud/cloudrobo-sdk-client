"""Camera streaming enums (command types, camera ids, error codes, event types).

Values align with the cloud-side ``/v1/robots-monitor/{robot_id}/command``
enumeration and the ``event.proto`` Metadata fields.
"""

from __future__ import annotations

from enum import Enum


class CommandType(str, Enum):
    """端侧支持的命令类型（复用 /v1/robots-monitor/{robot_id}/command 枚举）。"""

    CAMERA_START = "CAMERA_START"
    CAMERA_STOP = "CAMERA_STOP"
    RTC_TOKEN_RENEW = "RTC_TOKEN_RENEW"  # 新增：JWT 续签响应命令


class CameraId(str, Enum):
    """相机方位标识。"""

    FRONT = "front"
    LEFT_FRONT = "left_front"
    RIGHT_FRONT = "right_front"
    WRIST = "wrist"
    LEFT_WRIST = "left_wrist"
    RIGHT_WRIST = "right_wrist"
    ALL = "all"


class ErrorCode(str, Enum):
    """端侧错误码枚举。"""

    CAMERA_NOT_FOUND = "CAMERA_NOT_FOUND"
    CAMERA_BUSY = "CAMERA_BUSY"
    ENCODER_INIT_FAILED = "ENCODER_INIT_FAILED"
    WSS_CONNECT_FAILED = "WSS_CONNECT_FAILED"
    JWT_INVALID = "JWT_INVALID"
    JWT_EXPIRED = "JWT_EXPIRED"
    PUBLISH_TIMEOUT = "PUBLISH_TIMEOUT"
    RESOLUTION_NOT_SUPPORTED = "RESOLUTION_NOT_SUPPORTED"
    RESOURCE_INSUFFICIENT = "RESOURCE_INSUFFICIENT"
    ALREADY_ACTIVE = "ALREADY_ACTIVE"  # 幂等命中，非错误
    NOT_ACTIVE = "NOT_ACTIVE"  # 幂等命中，非错误
    BITRATE_EXCEEDED = "BITRATE_EXCEEDED"  # 被服务端踢除
    CAMERA_START_FAILED = "CAMERA_START_FAILED"
    CAMERA_STOP_FAILED = "CAMERA_STOP_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class CommandStatus(str, Enum):
    """命令执行状态。"""

    SUCCESS = "success"
    FAILED = "failed"


class EventType(str, Enum):
    """事件类型，映射到 EventMetadata.type 字段。"""

    EVENT = "event"  # 普通事件（如命令执行结果）
    ALARM = "alarm"  # 告警事件（如码率超限、JWT 过期等）


class EventSeverity(str, Enum):
    """事件级别，映射到 EventMetadata.severity 字段。"""

    CRITICAL = "Critical"
    MAJOR = "Major"
    MINOR = "Minor"
    INFO = "Info"
