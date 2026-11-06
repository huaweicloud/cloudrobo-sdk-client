"""Camera streaming message construction helpers.

相机推流相关的命令结果与告警统一复用现有 ``EventMessage``/``EventMetadata``
（``common/protos/event.proto``），通过 ``event_manager`` 单例发布到
``{project_id}/{device_id}/event`` topic。本模块提供结构化构造辅助函数。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Dict, Optional

from cloudrobo_r2c.common.models import EventMessage, EventMetadata

from .enums import EventSeverity, EventType

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def build_command_result_event(
    command: str,
    camera_id: str,
    status: str,
    error_code: str = "",
    error_msg: str = "",
    command_id: str = "",
) -> EventMessage:
    """将命令执行结果构造为 EventMessage（type="event"，瞬时事件）。

    Args:
        command: "CAMERA_START" | "CAMERA_STOP"
        camera_id: 具体相机 ID（camera_id=all 时逐路上报，此处为单路 ID）
        status: "success" | "failed"
        error_code: ErrorCode 枚举值（成功时为空）
        error_msg: 错误详情
        command_id: 命令追踪 ID（可选）
    """
    content: Dict[str, Any] = {
        "command": command,
        "camera_id": camera_id,
        "status": status,
        "error_code": error_code,
        "error_msg": error_msg,
    }
    if command_id:
        content["command_id"] = command_id

    return EventMessage(
        timestamp=_now_ms(),
        event_id=str(uuid.uuid4()),
        metadata=[
            EventMetadata(
                abstract=f"{command}_{camera_id}",
                type=EventType.EVENT,
                severity=(
                    EventSeverity.INFO
                    if status == "success"
                    else EventSeverity.MAJOR
                ),
                content=json.dumps(content, ensure_ascii=False),
                starts_at=0,  # 命令结果为瞬时事件
                ends_at=0,
            )
        ],
    )


def build_alarm_event(
    alarm_type: str,
    camera_id: str = "",
    message: str = "",
    severity: str = EventSeverity.MAJOR,
) -> EventMessage:
    """将设备告警构造为 EventMessage（type="alarm"，ends_at=0 激活中）。

    告警事件自动进入 EventManager 激活告警登记簿（active_events()）。
    """
    now_ms = _now_ms()
    return EventMessage(
        timestamp=now_ms,
        event_id=str(uuid.uuid4()),
        metadata=[
            EventMetadata(
                abstract=alarm_type,
                type=EventType.ALARM,
                severity=severity,
                content=json.dumps(
                    {
                        "alarm_type": alarm_type,
                        "camera_id": camera_id,
                        "message": message,
                    },
                    ensure_ascii=False,
                ),
                starts_at=now_ms,
                ends_at=0,  # 激活中，未解除
            )
        ],
    )


def build_alarm_clear_event(
    alarm_type: str,
    camera_id: str = "",
) -> EventMessage:
    """告警解除事件（ends_at>0 触发 EventManager 登记簿出册）。"""
    now_ms = _now_ms()
    return EventMessage(
        timestamp=now_ms,
        event_id=str(uuid.uuid4()),
        metadata=[
            EventMetadata(
                abstract=alarm_type,
                type=EventType.ALARM,
                severity=EventSeverity.INFO,
                content=json.dumps(
                    {
                        "alarm_type": alarm_type,
                        "camera_id": camera_id,
                        "action": "clear",
                    },
                    ensure_ascii=False,
                ),
                starts_at=0,
                ends_at=now_ms,  # ends_at>0 → 登记簿出册
            )
        ],
    )
