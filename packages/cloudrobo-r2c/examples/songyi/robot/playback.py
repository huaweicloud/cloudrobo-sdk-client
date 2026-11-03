"""Recorded-data playback source (回放).

Replaces the robot SDK as the data source for runtime/observation when
``record_observation`` is configured. Frames advance on a time clock
(like a video) so every consumer (observation path, runtime collector)
sees the same frame within a tick.

File format (docs/data.json):
    data[i].states.left_arm.qpos / right_arm.qpos  — 7 joint angles (rad)
    data[i].actions.left_arm.qpos / right_arm.qpos — 6 TCP pose xyzrpy (m/rad)
    data[i].actions.left_ee.qpos / right_ee.qpos   — gripper 0-10 (0=close, 10=open)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

N_ARM_JOINTS = 7
N_POSE = 6
N_ARMS = 2
N_ARM_TOTAL = N_ARM_JOINTS * N_ARMS          # 14 (J1-J14)
RUNTIME_JOINTS = 16                          # runtime 帧 16 关节（左臂7+左爪+右臂7+右爪）
GRIPPER_MIN = 0.0                            # 夹紧
GRIPPER_MAX = 10.0                           # 松开


class PlaybackError(RuntimeError):
    pass


class PlaybackSource:
    """Time-driven playback of a recorded JSON dataset."""

    def __init__(
        self,
        path: str,
        *,
        loop: bool = True,
        fps: Optional[float] = None,
    ) -> None:
        self.path = str(path)
        self.loop = bool(loop)
        self._frames, detected_fps = self._load(self.path)
        self._fps = float(fps) if fps is not None else detected_fps
        if self._fps <= 0:
            raise PlaybackError(f"invalid fps {self._fps!r} for playback {self.path!r}")
        self._t0 = time.monotonic()
        logger.info(
            "Playback loaded: %s (%d frames @ %.1ffps, loop=%s)",
            self.path,
            len(self._frames),
            self._fps,
            self.loop,
        )

    # ── loading ──────────────────────────────────────────────────────

    @staticmethod
    def _load(path: str) -> Tuple[List[Dict[str, Any]], float]:
        """Load the recorded JSON dataset.

        Returns ``(frames, detected_fps)`` where *detected_fps* comes from
        ``info.image.fps`` (recording camera rate, default 30).
        """
        file = Path(path)
        if not file.exists():
            raise PlaybackError(f"record_observation file not found: {path}")
        try:
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            raise PlaybackError(f"invalid JSON in {path}: {exc}") from exc

        frames = data.get("data") if isinstance(data, dict) else None
        if not isinstance(frames, list) or not frames:
            raise PlaybackError(f"{path}: no 'data' frames to play back")

        for index, frame in enumerate(frames):
            states = frame.get("states", {}) if isinstance(frame, dict) else {}
            for side in ("left_arm", "right_arm"):
                qpos = (states.get(side, {}) or {}).get("qpos", [])
                if len(qpos) != N_ARM_JOINTS:
                    raise PlaybackError(
                        f"{path}: frame#{index} states.{side}.qpos must have "
                        f"{N_ARM_JOINTS} joint angles, got {len(qpos)}"
                    )

        info = (data or {}).get("info", {})
        fps = (info.get("image", {}) or {}).get("fps", 30.0)
        try:
            detected_fps = float(fps)
        except (TypeError, ValueError):
            detected_fps = 30.0
        return frames, detected_fps

    def restart(self) -> None:
        self._t0 = time.monotonic()

    def frame_index(self) -> int:
        elapsed_steps = int((time.monotonic() - self._t0) * self._fps)
        if self.loop:
            return elapsed_steps % len(self._frames)
        return min(elapsed_steps, len(self._frames) - 1)

    @property
    def total_frames(self) -> int:
        return len(self._frames)

    @property
    def fps(self) -> float:
        return self._fps

    def _frame(self) -> Dict[str, Any]:
        return self._frames[self.frame_index()]

    # ── data extraction ──────────────────────────────────────────────

    def states_observation(self) -> Dict[str, List[float]]:
        """14 arm joint angles: left 7 (J1-J7) + right 7 (J8-J14), rad."""
        states = self._frame().get("states", {}) or {}
        left = [float(v) for v in states.get("left_arm", {}).get("qpos", [])]
        right = [float(v) for v in states.get("right_arm", {}).get("qpos", [])]
        return {"left": left, "right": right}

    def states_runtime(self) -> List[float]:
        """14 关节 runtime 向量：左臂7 + 右臂7（夹爪不占关节）。"""
        arms = self.states_observation()
        return arms["left"] + arms["right"]

    def actions(self) -> Dict[str, Any]:
        """Recorded actions: TCP poses xyzrpy (m/rad) + grippers 0-10."""
        actions = self._frame().get("actions", {}) or {}

        def _qpos(key: str) -> List[float]:
            return [float(v) for v in (actions.get(key, {}) or {}).get("qpos", [])]

        left_ee = _qpos("left_ee")
        right_ee = _qpos("right_ee")
        return {
            "left_pose": _qpos("left_arm"),
            "right_pose": _qpos("right_arm"),
            "left_gripper": left_ee[0] if left_ee else None,
            "right_gripper": right_ee[0] if right_ee else None,
        }

    def ee_poses(self) -> Dict[str, Any]:
        """末端位姿 6 维 xyzrpy（取当前帧录制 actions 的双臂目标位姿）。"""
        actions = self.actions()
        left = actions["left_pose"] or [0.0] * 6
        right = actions["right_pose"] or [0.0] * 6
        return {
            "names": ["left", "right"],
            "pose": [list(left[:6]), list(right[:6])],
        }

    def ee_states(self) -> Dict[str, Any]:
        """Gripper state 0-10 from the current frame's recorded actions."""
        actions = self.actions()
        left = actions["left_gripper"]
        right = actions["right_gripper"]
        return {
            "type": ["EE1", "EE2"],
            "names": ["left_ee", "right_ee"],
            "position": [
                left if left is not None else 0.0,
                right if right is not None else 0.0,
            ],
            "force": [],
        }
