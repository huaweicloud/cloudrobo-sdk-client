"""Dummy SO101-like hardware adapter for local simulation and integration testing."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from itertools import zip_longest
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np

from cloudrobo_r2c.common.utils.logging_sanitizer import summarize_observation_for_log
from cloudrobo_r2c.core.interfaces import IRobotHardwareAdapter
from cloudrobo_r2c.robots.commands.base import AdapterCommand


def create_dummy_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> IRobotHardwareAdapter:
    """Entry_point factory for DummyRobotHardwareAdapter.

    Args:
        config: Adapter configuration mapping (``hardware.config``).
        **extra_kwargs: Reserved for runtime dependency injection; supports
            ``_config_dir`` for resolving relative image file paths.
    """
    cfg = dict(config)
    _config_dir = extra_kwargs.get("_config_dir", "")
    if _config_dir:
        cfg["_config_dir"] = _config_dir
    return DummyRobotHardwareAdapter(config=cfg)


logger = logging.getLogger(__name__)


DEFAULT_SO101_JOINT_NAMES: List[str] = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
]

DEFAULT_IMAGE_SPECS: Dict[str, Dict[str, int]] = {
    "front": {"h": 480, "w": 640, "c": 3},
}


class _GetStateCommand(AdapterCommand):
    """Read and log the current joint state of the dummy robot."""

    def execute(self, **kwargs: Any) -> None:
        obs = self.adapter.get_observation()
        names = self.adapter.config.get("joint_names", [])
        if names:
            logger.info("[get_state] ── current joint state ──────────────────────")
            for name in names:
                val = obs.get(name)
                if isinstance(val, (int, float)):
                    logger.info("[get_state]   %-20s → % 8.4f rad (% 6.1f°)",
                                name, val, val * 57.2958)
                else:
                    logger.info("[get_state]   %-20s → %s", name, val)
            logger.info("[get_state] ─────────────────────────────────────────────")
        else:
            logger.info("[get_state] current joints: %s", obs)


@dataclass
class DummyRobotHardwareAdapter(IRobotHardwareAdapter):
    """Software-only robot adapter that emulates SO101-like I/O payloads.

    Observation payload includes both nested and flattened keys frequently seen in
    SO101/LeRobot integrations (for example ``observation.state`` and
    ``observation.images.front``).

    Action payload supports both raw-device style commands and R2C action-like
    mappings (``joint_states.position`` trajectory).
    """

    config: Mapping[str, Any] = field(default_factory=dict)

    _connected: bool = field(default=False, init=False, repr=False)
    _joint_names: List[str] = field(
        default_factory=lambda: list(DEFAULT_SO101_JOINT_NAMES),
        init=False,
        repr=False,
    )
    _state: List[float] = field(default_factory=list, init=False, repr=False)
    _target_state: dict[str, float] = field(
        default_factory=dict, init=False, repr=False
    )
    _last_observation_ts: float = field(default=0.0, init=False, repr=False)
    _observation_id: int = field(default=0, init=False, repr=False)
    _image_specs: Dict[str, List[int]] = field(
        default_factory=dict, init=False, repr=False
    )
    _image_files: Dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _image_cache: Dict[str, np.ndarray] = field(
        default_factory=dict, init=False, repr=False
    )
    _config_dir: str = field(default="", init=False, repr=False)

    def __post_init__(self) -> None:
        from cloudrobo_r2c.robots.commands.dummy import GoHomeCommand
        self.register_command_class("go_home", GoHomeCommand)
        self.register_command_class("get_state", _GetStateCommand)

    def connect(self) -> None:
        if self._connected:
            logger.debug(
                "Dummy robot connect() ignored because adapter is already connected."
            )
            return

        logger.info(
            "Dummy robot connecting with config keys=%s", sorted(self.config.keys())
        )
        joint_names = self.config.get("joint_names")
        if isinstance(joint_names, Sequence) and not isinstance(
            joint_names, (str, bytes)
        ):
            normalized = [str(name) for name in joint_names]
            if normalized:
                self._joint_names = normalized

        initial_state = self._normalize_joint_positions(
            self.config.get("initial_joint_positions"),
            expected_dim=len(self._joint_names),
        )
        if initial_state is None:
            initial_state = [0.0] * len(self._joint_names)

        self._state = list(initial_state)

        names = self._joint_names
        values = list(initial_state)
        self._target_state = dict(
            zip_longest(names, values[: len(names)], fillvalue=0.0)
        )

        self._config_dir = str(self.config.get("_config_dir", ""))
        self._image_specs, self._image_files = self._resolve_image_specs(
            self.config.get("image_specs")
        )
        self._image_cache = self._load_image_files()
        self._last_observation_ts = time.monotonic()
        self._observation_id = 0
        self._connected = True
        logger.info(
            "Dummy robot connected. joints=%d images=%s files=%s initial_state=%s",
            len(self._joint_names),
            list(self._image_specs.keys()),
            list(self._image_files.keys()),
            self._state,
        )

    def disconnect(self) -> None:
        if not self._connected:
            logger.debug(
                "Dummy robot disconnect() ignored because adapter is not connected."
            )
            return
        logger.info("Dummy robot disconnecting. final_joint_state=%s", self._state)
        self._connected = False
        logger.info("Dummy robot disconnected.")

    def get_observation(self) -> Mapping[str, Any]:
        self._ensure_connected()
        logger.debug(
            "Dummy robot preparing observation. observation_id=%d current_state=%s",
            self._observation_id + 1,
            self._state,
        )

        self._step_simulation()

        images: Dict[str, np.ndarray] = {}
        for image_name, image_shape in self._image_specs.items():
            if image_name in self._image_cache:
                images[image_name] = self._image_cache[image_name]
            else:
                images[image_name] = self._build_dummy_image(image_shape)

        names = list(self._joint_names)
        values = list(self._state)
        obs: Dict[str, Any] = dict(
            zip_longest(names, values[: len(names)], fillvalue=0.0)
        )
        for image_name, image_data in images.items():
            obs[f"{image_name}"] = image_data

        # ── simulated extra fields for extension mechanism demo ─────
        obs["task_instruction"] = self.config.get(
            "task_instruction", "pick the pen into the box"
        )
        obs["episode_id"] = max(self._observation_id // 100, 1)
        obs["model_confidence"] = 0.85 + 0.10 * (self._observation_id % 20) / 20.0
        obs["step_index"] = self._observation_id

        self._observation_id += 1
        logger.info(
            "Dummy robot observation captured. id=%d joints=%d images=%s",
            self._observation_id,
            len(self._joint_names),
            list(self._image_specs.keys()),
        )
        logger.debug(
            "Dummy robot observation generated id=%d obs=%s",
            self._observation_id,
            summarize_observation_for_log(obs),
        )
        return obs

    def move_to(
        self,
        *,
        pose_euler: Optional[Sequence[float]] = None,
        pose_quat: Optional[Sequence[float]] = None,
        joints: Optional[Sequence[float]] = None,
    ) -> None:
        """Move the simulated robot to a home configuration.

        Dummy adapter only supports joint targets; pose inputs raise
        :class:`NotImplementedError`.
        """
        self._validate_move_to_inputs(pose_euler, pose_quat, joints)
        if joints is None:
            raise NotImplementedError(
                "DummyRobotHardwareAdapter.move_to() only supports the "
                "'joints' input. Provide joints=[j1, j2, ...] in the "
                "commands.go_home block."
            )

        target = [float(v) for v in joints]
        names = self.config.get("joint_names", [])
        if names and len(names) == len(target):
            for name, val in zip(names, target):
                logger.info(
                    "[go_home] joint %s → %.4f rad (%.1f°)",
                    name, val, val * 57.2958,
                )
            logger.info(
                "[go_home] moving %d joints to home positions: %s",
                len(target),
                [f"{v:.4f}" for v in target],
            )
        else:
            logger.info(
                "[go_home] moving %d joints to home positions: %s",
                len(target),
                [f"{v:.4f}" for v in target],
            )
        self.send_action({"joint_target": target})

    def set_gripper(
        self,
        *,
        width: Optional[float] = None,
        action: Optional[str] = None,
    ) -> None:
        """Log gripper request — dummy adapter has no physical gripper."""
        if width is not None:
            logger.info(
                "[go_home] gripper → %.4f m (dummy: logged, no hardware action)",
                width,
            )
        if action is not None:
            logger.info(
                "[go_home] gripper action → %s (dummy: logged, no hardware action)",
                action,
            )

    def send_action(self, command: Mapping[str, Any]) -> None:
        self._ensure_connected()
        logger.debug("Dummy robot received raw action command=%s", command)

        if not isinstance(command, Mapping):
            raise ValueError("command must be a mapping")

        target = self._resolve_target_state(command)
        if target is None:
            raise ValueError("Unable to parse joint target from command. ")

        self._target_state = target
        logger.info(
            "Dummy robot message received(action). keys=%s",
            sorted(self._target_state.keys()),
        )
        logger.info(
            "Dummy robot action received. target_state=%s", dict(self._target_state)
        )

        # Commands carrying joint_target / joint_positions (go_home, move_to)
        # should instantly reset the joints to the target rather than
        # drifting through _step_simulation.  Otherwise the targets are
        # immediately overwritten by cloud actions in the sync-client loop
        # and never reached.
        if "joint_target" in command or "joint_positions" in command:
            self._snap_state_to_target()

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("Adapter is not connected. Call connect() first.")

    def _snap_state_to_target(self) -> None:
        """Instantly snap ``_state`` to ``_target_state``.

        Used by go_home / move_to so the joints are immediately at the
        commanded position, bypassing the incremental ``_step_simulation``
        interpolation that is designed for streaming cloud actions.
        """
        for i, joint_name in enumerate(self._joint_names):
            target = self._target_state.get(joint_name, self._state[i])
            self._state[i] = target
        logger.info(
            "Dummy robot state snapped to target: %s",
            [f"{v:.4f}" for v in self._state],
        )

    def _step_simulation(self) -> None:
        now = time.monotonic()
        dt = max(now - self._last_observation_ts, 0.0)
        self._last_observation_ts = now

        max_speed = float(self.config.get("max_joint_speed_rad_s", 2.5))
        max_step = max_speed * dt

        if max_step <= 0.0:
            return

        for i, joint_name in enumerate(self._joint_names):
            current = self._state[i]
            target = self._target_state.get(joint_name, current)
            delta = target - current
            if abs(delta) <= max_step:
                self._state[i] = target
            else:
                self._state[i] = current + (max_step if delta > 0 else -max_step)
        logger.debug(
            "Dummy robot simulation stepped. dt=%.6fs max_step=%.6f state=%s target=%s",
            dt,
            max_step,
            self._state,
            self._target_state,
        )

    @staticmethod
    def _normalize_joint_positions(
        payload: Any,
        *,
        expected_dim: int,
    ) -> Optional[List[float]]:
        if payload is None:
            return None

        candidate = payload
        if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes)):
            candidate_list = list(candidate)
            if (
                candidate_list
                and isinstance(candidate_list[0], Sequence)
                and not isinstance(candidate_list[0], (str, bytes))
            ):
                candidate_list = list(candidate_list[0])
            if len(candidate_list) != expected_dim:
                raise ValueError(
                    f"Joint target dimension mismatch: got {len(candidate_list)}, expected {expected_dim}"
                )
            return [float(v) for v in candidate_list]

        return None

    def _resolve_target_state(
        self, command: Mapping[str, Any]
    ) -> Optional[dict[str, float]]:
        expected_dim = len(self._joint_names)

        for key in ("joint_target", "joint_positions"):
            normalized = self._normalize_joint_positions(
                command.get(key),
                expected_dim=expected_dim,
            )
            if normalized is not None:
                return dict(zip(self._joint_names, normalized))

        joint_states = command.get("joint_states")
        if isinstance(joint_states, Mapping):
            normalized = self._normalize_joint_positions(
                joint_states.get("position"),
                expected_dim=expected_dim,
            )
            if normalized is not None:
                return dict(zip(self._joint_names, normalized))

        direct_values: dict[str, float] = {}
        for joint_name in self._joint_names:
            if joint_name in command:
                direct_values[joint_name] = float(command[joint_name])
                continue
            base_joint_name = joint_name.removesuffix(".pos")
            if base_joint_name in command:
                direct_values[joint_name] = float(command[base_joint_name])
                continue
            pos_joint_name = (
                joint_name if joint_name.endswith(".pos") else f"{joint_name}.pos"
            )
            if pos_joint_name in command:
                direct_values[joint_name] = float(command[pos_joint_name])

        if direct_values:
            merged_target = dict(self._target_state)
            merged_target.update(direct_values)
            return merged_target

        return None

    @staticmethod
    def _build_dummy_image(image_shape: List[int]) -> np.ndarray:
        h, w, c = image_shape
        return np.random.randint(0, 256, size=(h, w, c), dtype=np.uint8)

    @staticmethod
    def _resolve_image_specs(
        configured_specs: Any,
    ) -> tuple[Dict[str, List[int]], Dict[str, str]]:
        if configured_specs is None:
            configured_specs = DEFAULT_IMAGE_SPECS

        if not isinstance(configured_specs, Mapping):
            raise ValueError(
                "image_specs must be a mapping of image_name -> {h,w,c} or {file}"
            )

        image_specs: Dict[str, List[int]] = {}
        image_files: Dict[str, str] = {}
        for image_name, spec in configured_specs.items():
            if not isinstance(spec, Mapping):
                raise ValueError(
                    f"image_specs[{image_name!r}] must be a mapping with h/w/c keys or file key"
                )

            has_file = "file" in spec
            has_dims = any(k in spec for k in ("h", "w", "c"))

            if has_file:
                if has_dims:
                    raise ValueError(
                        f"image_specs[{image_name!r}]: 'file' and 'h/w/c' are mutually exclusive"
                    )
                file_path = str(spec["file"]).strip()
                if not file_path:
                    raise ValueError(
                        f"image_specs[{image_name!r}] 'file' must be a non-empty path"
                    )
                image_files[str(image_name)] = file_path
                image_specs[str(image_name)] = []  # placeholder, filled after load
                continue

            try:
                h = int(spec["h"])
                w = int(spec["w"])
                c = int(spec["c"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"image_specs[{image_name!r}] must provide integer h/w/c"
                ) from exc

            if h <= 0 or w <= 0 or c <= 0:
                raise ValueError(
                    f"image_specs[{image_name!r}] must have positive h/w/c values"
                )

            image_specs[str(image_name)] = [h, w, c]

        if not image_specs:
            raise ValueError("image_specs must contain at least one image entry")

        return image_specs, image_files

    def _load_image_files(self) -> Dict[str, np.ndarray]:
        import cv2

        cache: Dict[str, np.ndarray] = {}
        for image_name, file_path in self._image_files.items():
            if not os.path.isabs(file_path) and self._config_dir:
                resolved = os.path.join(self._config_dir, file_path)
            else:
                resolved = file_path

            img = cv2.imread(resolved, cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError(
                    f"Failed to load image file for '{image_name}': {resolved}"
                )

            h, w, c = img.shape
            self._image_specs[image_name] = [h, w, c]
            cache[image_name] = img
            logger.info(
                "Dummy robot loaded image '%s' from %s shape=(%d,%d,%d)",
                image_name,
                resolved,
                h,
                w,
                c,
            )

        return cache
