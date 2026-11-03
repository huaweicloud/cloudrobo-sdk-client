"""Camera streaming module (LiveKit WebRTC push).

相机推流：接收 CAMERA_START/CAMERA_STOP/RTC_TOKEN_RENEW 命令，通过 LiveKit
Client SDK 将硬件适配器共享帧槽中的相机帧推流到云端 SFU。相机设备由硬件
适配器独占持有（单一采集进程），本模块仅读取帧槽，不直接打开设备。
"""

from __future__ import annotations

from .enums import (
    CameraId,
    CommandStatus,
    CommandType,
    ErrorCode,
    EventSeverity,
    EventType,
)
from .models import (
    build_alarm_clear_event,
    build_alarm_event,
    build_command_result_event,
)

__all__ = [
    "CameraId",
    "CommandStatus",
    "CommandType",
    "ErrorCode",
    "EventSeverity",
    "EventType",
    "build_alarm_clear_event",
    "build_alarm_event",
    "build_command_result_event",
]
