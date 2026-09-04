"""Playback robot adapter that replays recorded observations.

Uses a safe custom binary format (JSON structure + raw numpy bytes) instead
of pickle — no code-execution risk during deserialization.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping

from cloudrobo_r2c.common.utils.safe_serialization import load_frames
from cloudrobo_r2c.core.interfaces import IRobotHardwareAdapter
from typing import Optional, Sequence


def create_playback_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> IRobotHardwareAdapter:
    """Entry_point factory for PlaybackRobotAdapter."""
    return PlaybackRobotAdapter(config=dict(config))


logger = logging.getLogger(__name__)


@dataclass
class PlaybackRobotAdapter(IRobotHardwareAdapter):
    """Replay recorded observations from a safe binary file.

    Configuration is read from ``playback_config``::

        hardware:
          type: playback
          playback_config:
            recording_file: "recordings/demo.r2cr"
            loop: true
    """

    config: Mapping[str, Any]

    _frames: List[Dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _index: int = field(default=0, init=False, repr=False)
    _connected: bool = field(default=False, init=False, repr=False)
    _loop: bool = field(default=True, init=False, repr=False)

    def connect(self) -> None:
        if self._connected:
            logger.debug("Playback adapter already connected; skipping.")
            return

        file_path = str(self.config.get("recording_file", ""))
        if not file_path:
            raise ValueError("playback_config.recording_file is required")

        self._frames = load_frames(file_path)

        self._loop = bool(self.config.get("loop", True))
        self._index = 0
        self._connected = True
        logger.info(
            "Playback adapter loaded %d frames from %s (loop=%s)",
            len(self._frames),
            file_path,
            self._loop,
        )

    def disconnect(self) -> None:
        self._frames.clear()
        self._index = 0
        self._connected = False
        logger.info("Playback adapter disconnected.")

    def get_observation(self) -> Mapping[str, Any]:
        if not self._connected:
            raise RuntimeError("Adapter is not connected. Call connect() first.")
        if not self._frames:
            raise RuntimeError("No frames loaded in playback adapter.")

        if self._index >= len(self._frames):
            if self._loop:
                self._index = 0
                logger.debug("Playback looped back to frame 0")
            else:
                raise RuntimeError(
                    f"Playback finished after {len(self._frames)} frames."
                )

        frame = self._frames[self._index]
        self._index += 1
        return frame

    def send_action(self, command: Mapping[str, Any]) -> None:
        pass
