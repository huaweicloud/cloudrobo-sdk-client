"""Observation recorder for capturing robot observation data to disk.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping

import numpy as np

from .safe_serialization import save_frames

logger = logging.getLogger(__name__)


class ObservationRecorder:
    """Record raw observations to a safe binary file for later playback.

    Usage::

        recorder = ObservationRecorder("recording.r2cr")
        recorder.record(raw_observation)
        ...
        recorder.close()
    """

    def __init__(self, file_path: str) -> None:
        self._file_path = file_path
        self._frames: List[Dict[str, Any]] = []

    def record(self, observation: Mapping[str, Any]) -> None:
        frame: Dict[str, Any] = {}
        for key, value in observation.items():
            if isinstance(value, np.ndarray):
                frame[key] = value.copy()
            else:
                frame[key] = value
        self._frames.append(frame)

    def close(self) -> None:
        save_frames(self._file_path, self._frames)
        logger.info(
            "Recording saved: %d frames to %s", len(self._frames), self._file_path
        )

    @property
    def frame_count(self) -> int:
        return len(self._frames)
