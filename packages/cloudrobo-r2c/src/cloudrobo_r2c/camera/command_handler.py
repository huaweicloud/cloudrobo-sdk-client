"""Command handler: subscribe job/command and dispatch to CameraManager.

订阅 ``{project_id}/{device_id}/job/command``，用 ``CameraCommand``
wrapper（``camera_command.proto``）反序列化，按 ``command`` 字段分发：
- ``CAMERA_START``    → CameraManager.start_streaming
- ``CAMERA_STOP``     → CameraManager.stop_streaming
- ``RTC_TOKEN_RENEW`` → CameraManager.on_token_renewed
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from cloudrobo_r2c.camera.enums import CommandType
from cloudrobo_r2c.common.models import CameraCommand

logger = logging.getLogger(__name__)


class CommandHandler:
    """订阅 job/command topic，解析并分发命令到 CameraManager。"""

    def __init__(self, session: Any, on_command: Optional[Callable[[str, CameraCommand], None]] = None):
        """Args:
            session: R2CSession（提供 transport / project_id / device_id）。
            on_command: 命令分发回调；若为 None 则使用内置的分发逻辑
                （start_streaming / stop_streaming / on_token_renewed），
                通过 ``set_dispatcher`` 注入 CameraManager。
        """
        self._session = session
        self._dispatcher: Optional[Any] = None
        self._on_command_cb = on_command

    def set_dispatcher(self, dispatcher: Any) -> None:
        """注入 CameraManager 作为命令执行方。"""
        self._dispatcher = dispatcher

    def subscribe(self) -> None:
        """订阅 {project_id}/{device_id}/job/command。"""
        topic = f"{self._session.project_id}/{self._session.device_id}/job/command"
        self._session.transport.subscribe(topic, self._on_command)
        logger.info("Subscribed to job/command topic: %s", topic)

    def _on_command(self, payload: bytes) -> None:
        """接收并解析命令（protobuf 反序列化）。"""
        logger.debug(
            "job/command RX: %d bytes raw=%s",
            len(payload),
            payload[:64].hex() if payload else "(empty)",
        )
        try:
            cmd = CameraCommand.from_protobuf(payload)
        except Exception:
            logger.error(
                "Failed to parse job/command payload (%d bytes)",
                len(payload),
                exc_info=True,
            )
            return

        logger.debug(
            "job/command RX parsed: %s",
            cmd.to_dict(),
        )

        try:
            if cmd.command == CommandType.CAMERA_START:
                if self._on_command_cb is not None:
                    self._on_command_cb(cmd.command, cmd)
                elif self._dispatcher is not None:
                    self._dispatcher.start_streaming(cmd)
                else:
                    logger.warning("No dispatcher for CAMERA_START")

            elif cmd.command == CommandType.CAMERA_STOP:
                if self._on_command_cb is not None:
                    self._on_command_cb(cmd.command, cmd)
                elif self._dispatcher is not None:
                    self._dispatcher.stop_streaming(cmd)
                else:
                    logger.warning("No dispatcher for CAMERA_STOP")

            elif cmd.command == CommandType.RTC_TOKEN_RENEW:
                # JWT 续签响应
                if self._on_command_cb is not None:
                    self._on_command_cb(cmd.command, cmd)
                elif self._dispatcher is not None:
                    self._dispatcher.on_token_renewed(cmd)
                else:
                    logger.warning("No dispatcher for RTC_TOKEN_RENEW")

            else:
                logger.warning("Unknown command: %s", cmd.command)

        except Exception:
            logger.error("Failed to handle command: %s", cmd.command, exc_info=True)
