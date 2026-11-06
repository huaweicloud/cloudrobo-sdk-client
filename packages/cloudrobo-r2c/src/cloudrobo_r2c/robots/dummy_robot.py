"""Dummy SO101-like hardware adapter for local simulation and integration testing."""

from __future__ import annotations

import logging
import os
import threading
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
    # 真实相机（V4L2 / USB）采集：camera_id -> 相机控制参数（width/height/fps/brightness 等）
    _camera_specs: Dict[str, Dict[str, Any]] = field(
        default_factory=dict, init=False, repr=False
    )
    # RealSense 相机采集：camera_id -> spec（serial/width/height/fps/控制项）。
    # 用 RealSense 驱动打开（而非 V4L2 节点），可直接访问 rs.option 控制项，
    # 但同一台设备不能被 V4L2 与 RealSense 同时打开（Device or resource busy）。
    _realsense_specs: Dict[str, Dict[str, Any]] = field(
        default_factory=dict, init=False, repr=False
    )
    # RealSense 相机实例：camera_id -> RealSenseCamera（自带后台采集线程）
    _realsense_cameras: Dict[str, Any] = field(
        default_factory=dict, init=False, repr=False
    )
    # 合成帧源（随机图/静态图片/mp4 视频）：camera_id -> spec 配置
    _synthetic_sources: Dict[str, Dict[str, Any]] = field(
        default_factory=dict, init=False, repr=False
    )
    # 相机推流共享帧槽（ICameraFrameSource）：最新帧 + 采集时间戳
    _latest_frames: Dict[str, np.ndarray] = field(
        default_factory=dict, init=False, repr=False
    )
    _latest_frame_ts: Dict[str, float] = field(
        default_factory=dict, init=False, repr=False
    )
    _config_dir: str = field(default="", init=False, repr=False)
    # 真实相机（V4L2 / USB）采集：camera_id -> cv2.VideoCapture
    _camera_captures: Dict[str, Any] = field(
        default_factory=dict, init=False, repr=False
    )
    _camera_threads: Dict[str, threading.Thread] = field(
        default_factory=dict, init=False, repr=False
    )
    _camera_stop_events: Dict[str, threading.Event] = field(
        default_factory=dict, init=False, repr=False
    )

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
        (
            self._image_specs,
            self._image_files,
            self._camera_sources,
            self._camera_specs,
            self._realsense_specs,
            self._synthetic_sources,
        ) = self._resolve_image_specs(self.config.get("image_specs"))
        self._image_cache = self._load_image_files()
        # 打开真实相机（source/realsense 字段）与合成帧源
        # （h/w/c 随机图 / file 静态图 / video mp4），启动后台线程写共享帧槽
        self._open_cameras()
        self._last_observation_ts = time.monotonic()
        self._observation_id = 0
        self._connected = True
        logger.info(
            "Dummy robot connected. joints=%d images=%s files=%s cameras=%s "
            "realsense=%s synthetic=%s initial_state=%s",
            len(self._joint_names),
            list(self._image_specs.keys()),
            list(self._image_files.keys()),
            list(self._camera_sources.keys()),
            list(self._realsense_specs.keys()),
            list(self._synthetic_sources.keys()),
            self._state,
        )

    def disconnect(self) -> None:
        if not self._connected:
            logger.debug(
                "Dummy robot disconnect() ignored because adapter is not connected."
            )
            return
        logger.info("Dummy robot disconnecting. final_joint_state=%s", self._state)
        self._close_cameras()
        self._connected = False
        logger.info("Dummy robot disconnected.")

    def get_runtime(self, tick: int = 0) -> Mapping[str, Any]:
        """Collector for the state/current publish topic (CurrentState frame).

        与观测共源：joint_states（names/position/velocity/effort）+ 静态
        ``location``（可配 ``hardware.config.location``，缺省空串）。供
        ``hardware.topics`` 的 ``{project_id}/{device_id}/state/current``
        发布绑定调用。
        """
        self._ensure_connected()
        names = list(self._joint_names)
        values = list(self._state)
        frame: Dict[str, Any] = {
            "timestamp": int(time.time() * 1000),
            "location": str(self.config.get("location", "")),
            "joint_states": {
                "type": [f"J{i}" for i in range(1, len(names) + 1)],
                "names": names,
                "position": [float(v) for v in values],
                "velocity": [],
                "torque": [],
            },
        }
        return frame

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
            # 真实相机（V4L2 source / RealSense）与合成帧源（随机图/静态图/
            # mp4 视频）优先：帧由后台采集线程持续写入共享帧槽
            if (
                image_name in self._camera_sources
                or image_name in self._realsense_specs
                or image_name in self._synthetic_sources
            ):
                frame = self._latest_frames.get(image_name)
                if frame is not None:
                    images[image_name] = frame
                    continue
            if image_name in self._image_cache:
                images[image_name] = self._image_cache[image_name]
            else:
                images[image_name] = self._build_dummy_image(image_shape)

        # 更新共享帧槽（供 LiveKit 推流 ICameraFrameSource 读取）。
        # 真实相机/合成帧源帧由采集线程持续写入，此处仅兜底：尚未出帧时
        # 用本次观测的图占位，避免推流侧读到 None。不覆盖后台采集
        # 写入的时间戳（保留真实采集时刻）。
        now = time.monotonic()
        for image_name, image_data in images.items():
            if (
                image_name not in self._camera_sources
                and image_name not in self._realsense_specs
                and image_name not in self._synthetic_sources
            ):
                self._latest_frames[image_name] = image_data
                self._latest_frame_ts[image_name] = now

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

    # ── ICameraFrameSource（相机推流共享帧槽） ──────────────────────────────

    def get_latest_frame(self, camera_id: str) -> Optional[np.ndarray]:
        """返回指定相机的最近一帧 BGR 图像（H, W, 3）uint8，无帧返回 None。"""
        frame = self._latest_frames.get(camera_id)
        if frame is None:
            return None
        return frame.copy()

    def get_latest_timestamp(self, camera_id: str) -> float:
        """返回最近一帧的采集时间戳（time.monotonic()），无帧返回 0.0。"""
        return self._latest_frame_ts.get(camera_id, 0.0)

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
    ) -> tuple[
        Dict[str, List[int]],
        Dict[str, str],
        Dict[str, str],
        Dict[str, Dict[str, Any]],
        Dict[str, Dict[str, Any]],
        Dict[str, Dict[str, Any]],
    ]:
        if configured_specs is None:
            configured_specs = DEFAULT_IMAGE_SPECS

        if not isinstance(configured_specs, Mapping):
            raise ValueError(
                "image_specs must be a mapping of image_name -> {h,w,c} / "
                "{file} / {source} / {realsense} / {video}"
            )

        image_specs: Dict[str, List[int]] = {}
        image_files: Dict[str, str] = {}
        camera_sources: Dict[str, str] = {}
        camera_specs: Dict[str, Dict[str, Any]] = {}
        realsense_specs: Dict[str, Dict[str, Any]] = {}
        synthetic_sources: Dict[str, Dict[str, Any]] = {}
        for image_name, spec in configured_specs.items():
            if not isinstance(spec, Mapping):
                raise ValueError(
                    f"image_specs[{image_name!r}] must be a mapping with "
                    "h/w/c keys, 'file', 'source', 'realsense' or 'video' key"
                )

            has_file = "file" in spec
            has_dims = any(k in spec for k in ("h", "w", "c"))
            has_source = "source" in spec
            has_realsense = "realsense" in spec
            has_video = "video" in spec

            if has_realsense:
                # RealSense 相机：用 RealSense 驱动打开指定 serial 的设备。
                # 与 source/file/video/h-w-c 互斥——一台设备只能由一个驱动打开。
                if (
                    has_source or has_file or has_dims or has_video
                    or len(spec) > 1
                ):
                    raise ValueError(
                        f"image_specs[{image_name!r}]: 'realsense' is mutually "
                        "exclusive with 'source'/'file'/'video'/'h/w/c' and "
                        "sibling keys; put realsense camera settings inside "
                        "the 'realsense' block"
                    )
                rs_spec = spec["realsense"]
                if not isinstance(rs_spec, Mapping):
                    raise ValueError(
                        f"image_specs[{image_name!r}].realsense must be a mapping "
                        "(e.g. {serial: F1180540, width: 960, height: 540, fps: 30})"
                    )
                serial = str(rs_spec.get("serial", "")).strip()
                if not serial:
                    raise ValueError(
                        f"image_specs[{image_name!r}].realsense.serial must be a "
                        "non-empty RealSense serial"
                    )
                realsense_specs[str(image_name)] = {
                    str(k): v for k, v in rs_spec.items()
                }
                # 尺寸由实际首帧推断，先留空占位
                image_specs[str(image_name)] = []
                continue

            if has_source:
                # 真实相机（V4L2 / USB）：camera_id -> 设备索引或 /dev/videoN
                if has_file or has_dims or has_video:
                    raise ValueError(
                        f"image_specs[{image_name!r}]: 'source' is mutually "
                        "exclusive with 'file'/'video' and 'h/w/c'"
                    )
                # 数字索引保留为 int（cv2.VideoCapture 按索引打开），
                # 路径字符串（/dev/videoN）按路径打开
                raw_source = spec["source"]
                if isinstance(raw_source, (int, float)) and not isinstance(
                    raw_source, bool
                ):
                    camera_sources[str(image_name)] = int(raw_source)
                else:
                    camera_sources[str(image_name)] = str(raw_source).strip()
                # 其余键为相机控制参数（width/height/fps/brightness 等）
                camera_specs[str(image_name)] = {
                    str(k): v
                    for k, v in spec.items()
                    if k not in ("source", "file", "h", "w", "c")
                }
                # 尺寸由实际首帧推断，先留空占位
                image_specs[str(image_name)] = []
                continue

            if has_video:
                # 合成帧源：mp4 视频循环播放（cv2.VideoCapture 打开文件）。
                # 与 source/file/h-w-c 互斥；其余键为视频控制参数
                # （fps/loop/width/height）。
                if has_source or has_file or has_dims:
                    raise ValueError(
                        f"image_specs[{image_name!r}]: 'video' is mutually "
                        "exclusive with 'source'/'file' and 'h/w/c'"
                    )
                video_path = str(spec["video"]).strip()
                if not video_path:
                    raise ValueError(
                        f"image_specs[{image_name!r}] 'video' must be a "
                        "non-empty path"
                    )
                synthetic_sources[str(image_name)] = {
                    str(k): v
                    for k, v in spec.items()
                    if k != "video"
                }
                synthetic_sources[str(image_name)]["video"] = video_path
                # 尺寸由实际首帧推断，先留空占位
                image_specs[str(image_name)] = []
                continue

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
                # 登记为合成帧源（静态图）：后台线程按 fps 周期写帧槽
                synthetic_sources[str(image_name)] = {
                    "file": file_path,
                    "fps": int(spec.get("fps", 20) or 20),
                }
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
            # 登记为合成帧源（随机图）：后台线程按 fps 周期生成新图
            synthetic_sources[str(image_name)] = {
                "h": h,
                "w": w,
                "c": c,
                "fps": int(spec.get("fps", 20) or 20),
            }

        if not image_specs:
            raise ValueError("image_specs must contain at least one image entry")

        return (
            image_specs,
            image_files,
            camera_sources,
            camera_specs,
            realsense_specs,
            synthetic_sources,
        )

    # ── 真实相机采集（V4L2 / USB） ────────────────────────────────────────

    def _open_cameras(self) -> None:
        """打开配置的真实相机（source/realsense）与合成帧源（h/w/c 随机图、
        file 静态图、video mp4），启动后台采集线程。

        每个帧源由独立 daemon 线程持续读帧并写入共享帧槽
        （``_latest_frames`` / ``_latest_frame_ts``），推理侧
        ``get_observation()`` 与 LiveKit 推流侧 ``ICameraFrameSource`` 共享
        ——推流帧率不依赖推理循环（publish_hz）。

        真实相机支持在 ``image_specs`` 中配置额外控制参数（可选）：

        - ``width`` / ``height`` / ``fps``：请求分辨率与帧率（V4L2 可能重协商）
        - ``brightness`` / ``contrast`` / ``exposure`` / ``gain``：V4L2 控制项。
          部分 RealSense/低成本 UVC 相机默认参数会输出近全黑帧，
          需显式设置（如 RealSense RGB 节点设 brightness=128 等）。

        真实相机（source/realsense）与合成视频（video）打开失败时
        **直接抛出异常**，由 connect() 的调用方捕获并退出程序——配置了
        真实相机/视频却回退随机图没有意义，应让用户排查问题后再试。
        """
        import cv2

        # RealSense 相机优先打开：它们只应通过 RealSense 驱动访问。
        self._open_realsense_cameras()

        # 合成帧源（随机图 / mp4 视频）启动独立后台线程，持续写共享帧槽
        self._open_synthetic_sources()

        for image_name, source in self._camera_sources.items():
            cam_spec = self._camera_specs.get(image_name, {})
            try:
                cap = cv2.VideoCapture(source)
                if not cap.isOpened():
                    # 真实相机打开失败：直接报错退出，让用户排查设备问题
                    # （不回退随机图——配置了真实相机却推送随机图没有意义）。
                    if cap:
                        cap.release()
                    raise RuntimeError(
                        f"Dummy robot failed to open camera '{image_name}' "
                        f"from source {source!r}: device unavailable or busy"
                    )

                self._apply_camera_props(cap, cam_spec)

                # 记录实际首帧尺寸到 image_specs（能力上报用）
                ret, frame = cap.read()
                if ret and frame is not None:
                    h, w = frame.shape[:2]
                    self._image_specs[image_name] = [int(h), int(w), 3]
                    self._latest_frames[image_name] = frame
                    self._latest_frame_ts[image_name] = time.monotonic()
                    logger.info(
                        "Dummy robot opened camera '%s' source=%r size=%dx%d",
                        image_name,
                        source,
                        w,
                        h,
                    )
                else:
                    cap.release()
                    raise RuntimeError(
                        f"Dummy robot camera '{image_name}' source={source!r} "
                        f"opened but first read failed"
                    )

                stop_event = threading.Event()
                thread = threading.Thread(
                    target=self._camera_capture_loop,
                    args=(image_name, cap, stop_event),
                    name=f"dummy-cam-{image_name}",
                    daemon=True,
                )
                self._camera_captures[image_name] = cap
                self._camera_stop_events[image_name] = stop_event
                self._camera_threads[image_name] = thread
                thread.start()
            except Exception:
                # 打开/首帧读取失败：释放资源后向上传播，由 connect() 的
                # 调用方（cloudroboclient 主流程）捕获并退出程序。
                if "cap" in locals() and cap:
                    try:
                        cap.release()
                    except Exception:
                        logger.debug(
                            "Dummy robot camera '%s' release failed",
                            image_name, exc_info=True,
                        )
                logger.error(
                    "Dummy robot camera '%s' source=%r open failed",
                    image_name,
                    source,
                    exc_info=True,
                )
                raise

    def _open_synthetic_sources(self) -> None:
        """打开配置的合成帧源，启动独立后台线程持续写共享帧槽。

        覆盖三类非真实相机帧源（均不打开物理设备，推流帧率不依赖推理
        循环 publish_hz）：

        1. ``h/w/c`` 随机图：按 ``fps`` 周期生成新随机图（画面动态变化）
        2. ``file`` 静态图片：按 ``fps`` 周期写入同一张图（时间戳刷新）
        3. ``video`` mp4 视频：按视频帧率循环读帧（支持 loop 循环播放）

        ``fps``（可选，默认 20）：随机图/静态图的后台刷新帧率；视频默认
        用文件自带帧率。
        ``loop``（可选，默认 true）：视频播放到结尾后从头循环。

        视频文件打开失败/首帧读取失败**直接抛出异常**，由 connect() 的
        调用方捕获并退出程序（不回退随机图——配置了视频却推送随机图没有
        意义）。随机图/静态图是纯合成帧源，始终可用，不存在打开失败。
        """
        for image_name, syn_spec in self._synthetic_sources.items():
            if syn_spec.get("video"):
                # mp4 视频帧源：独立线程按视频帧率循环读帧
                self._start_video_source(image_name, syn_spec)
            elif syn_spec.get("file") or syn_spec.get("h"):
                # 静态图 / 随机图：独立线程按 fps 周期刷新帧槽
                kind = "static" if syn_spec.get("file") else "random"
                self._start_periodic_refresh(image_name, kind, syn_spec)

    def _start_periodic_refresh(
        self,
        image_name: str,
        kind: str,
        syn_spec: Dict[str, Any],
    ) -> None:
        """为随机图/静态图启动周期刷新线程（按 fps 写共享帧槽）。

        ``kind``: "random" 每次生成新随机图；"static" 重复写同一张图。
        ``syn_spec``: 该相机的合成帧源配置（fps 可选，默认 20）。
        """
        fps = int(syn_spec.get("fps", 20) or 20)
        if fps <= 0:
            fps = 20

        # 已存在（video 线程占位等）则跳过
        if image_name in self._camera_threads:
            return

        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._synthetic_refresh_loop,
            args=(image_name, kind, stop_event, fps),
            name=f"dummy-synth-{image_name}",
            daemon=True,
        )
        self._camera_stop_events[image_name] = stop_event
        self._camera_threads[image_name] = thread
        thread.start()
        logger.info(
            "Dummy robot started synthetic %s source '%s' @ %dfps",
            kind,
            image_name,
            fps,
        )

    def _synthetic_refresh_loop(
        self,
        camera_id: str,
        kind: str,
        stop_event: threading.Event,
        fps: int,
    ) -> None:
        """后台线程：周期写入随机图/静态图到共享帧槽（keep-latest）。"""
        interval = 1.0 / float(fps)
        while not stop_event.is_set():
            try:
                if kind == "random":
                    shape = self._image_specs.get(camera_id)
                    if not shape:
                        time.sleep(0.05)
                        continue
                    frame = self._build_dummy_image(shape)
                else:
                    frame = self._image_cache.get(camera_id)
                    if frame is None:
                        time.sleep(0.05)
                        continue
                now = time.monotonic()
                self._latest_frames[camera_id] = frame
                self._latest_frame_ts[camera_id] = now
            except Exception:
                logger.warning(
                    "Dummy robot synthetic source '%s' refresh error",
                    camera_id,
                    exc_info=True,
                )
            time.sleep(interval)

    def _start_video_source(self, image_name: str, syn_spec: Dict[str, Any]) -> None:
        """打开 mp4 视频并启动后台读帧线程（支持 loop 循环播放）。"""
        import cv2

        video_path = str(syn_spec.get("video", "")).strip()
        if not os.path.isabs(video_path) and self._config_dir:
            video_path = os.path.join(self._config_dir, video_path)
        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                # 视频文件打开失败：直接报错退出，让用户排查文件问题
                # （不回退随机图——配置了视频却推送随机图没有意义）。
                if cap:
                    cap.release()
                raise RuntimeError(
                    f"Dummy robot failed to open synthetic video "
                    f"'{image_name}' from {video_path!r}: "
                    f"file unavailable or undecodable"
                )

            # 记录实际首帧尺寸到 image_specs（能力上报用）
            ret, frame = cap.read()
            if ret and frame is not None:
                h, w = frame.shape[:2]
                self._image_specs[image_name] = [int(h), int(w), 3]
                self._latest_frames[image_name] = frame
                self._latest_frame_ts[image_name] = time.monotonic()
            else:
                cap.release()
                raise RuntimeError(
                    f"Dummy robot synthetic video '{image_name}' "
                    f"path={video_path!r} opened but first read failed"
                )

            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._synthetic_video_loop,
                args=(image_name, cap, stop_event, dict(syn_spec)),
                name=f"dummy-video-{image_name}",
                daemon=True,
            )
            self._camera_stop_events[image_name] = stop_event
            self._camera_threads[image_name] = thread
            thread.start()

            logger.info(
                "Dummy robot opened synthetic video '%s' path=%s size=%dx%d",
                image_name,
                video_path,
                w,
                h,
            )
        except Exception:
            # 打开/首帧读取失败：释放资源后向上传播，由 connect() 的
            # 调用方捕获并退出程序。
            if "cap" in locals() and cap:
                try:
                    cap.release()
                except Exception:
                    logger.debug(
                        "Dummy robot video '%s' release failed",
                        image_name, exc_info=True,
                    )
            logger.error(
                "Dummy robot synthetic video '%s' path=%r open failed",
                image_name,
                video_path,
                exc_info=True,
            )
            raise

    def _synthetic_video_loop(
        self,
        camera_id: str,
        cap: Any,
        stop_event: threading.Event,
        syn_spec: Dict[str, Any],
    ) -> None:
        """后台线程：按视频帧率循环读帧写入共享帧槽（keep-latest）。

        ``fps``：覆盖帧率（默认取视频自带 fps，无效时回退 20）。
        ``loop``：默认 true，播放到结尾后 seek 回 0 重新循环。
        ``width`` / ``height``：可选，resize 到目标尺寸（推流配置不一致时
        由 CameraCapture 统一 resize，这里主要用于能力上报与推理侧尺寸）。
        """
        import cv2

        fps = int(syn_spec.get("fps", 0) or 0)
        if fps <= 0:
            fps = int(cap.get(cv2.CAP_PROP_FPS) or 20)
            if fps <= 0:
                fps = 20
        interval = 1.0 / float(fps)
        loop = bool(syn_spec.get("loop", True))
        target_size = None
        tw = int(syn_spec.get("width", 0) or 0)
        th = int(syn_spec.get("height", 0) or 0)
        if tw > 0 and th > 0:
            target_size = (tw, th)

        while not stop_event.is_set():
            try:
                ret, frame = cap.read()
                if not ret or frame is None:
                    if loop:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        ret, frame = cap.read()
                        if not ret or frame is None:
                            time.sleep(0.05)
                            continue
                    else:
                        # 不循环：保持最后一帧（不再覆盖帧槽）
                        time.sleep(0.05)
                        continue

                if target_size is not None and (
                    frame.shape[1] != tw or frame.shape[0] != th
                ):
                    frame = cv2.resize(
                        frame,
                        target_size,
                        interpolation=cv2.INTER_AREA,
                    )

                now = time.monotonic()
                self._latest_frames[camera_id] = frame
                self._latest_frame_ts[camera_id] = now
            except Exception:
                logger.warning(
                    "Dummy robot synthetic video '%s' capture error",
                    camera_id,
                    exc_info=True,
                )
                time.sleep(0.05)
            time.sleep(interval)
        cap.release()

    def _open_realsense_cameras(self) -> None:
        """打开配置的 RealSense 相机（image_specs.<name>.realsense）。

        与 V4L2 路径的区别：设备通过 RealSense 驱动打开，因此可直接使用
        ``rs.option`` 控制项（exposure/gain/brightness 等）获得正确曝光的
        彩色画面——无需 V4L2 路径的 CLAHE 补偿。

        ``RealSenseCamera`` 自带后台采集线程并维护加锁帧槽，因此这里不再
        额外起线程，只把它的帧转存到适配器的共享帧槽
        （``_latest_frames`` / ``_latest_frame_ts``），供推理侧
        ``get_observation()`` 与推流侧 ``ICameraFrameSource`` 共享。

        ``realsense`` 块支持的字段：

        - ``serial``（必填）：RealSense 序列号，接受 ``F1180540`` /
          ``00000000F1180540`` / ``f1180540`` 等形式
        - ``width`` / ``height`` / ``fps``：请求分辨率与帧率；设备不支持时
          自动回退到最接近的支持模式（L515 旧固件无 640x480）
        - ``exposure`` / ``gain`` / ``brightness`` / ``contrast`` / ``gamma`` /
          ``sharpness`` / ``saturation`` / ``white_balance`` /
          ``backlight_compensation`` / ``power_line_frequency`` /
          ``enable_auto_exposure``：透传给 ``rs.option``
        - ``clahe``：是否对帧做 CLAHE 对比度增强（默认 false，RealSense
          可用 rs.option 正确曝光，无需后处理）

        打开失败（设备缺失/被占用/pyrealsense2 不可用）**直接抛出异常**，
        由 connect() 的调用方捕获并退出程序——配置了真实相机却回退随机图
        没有意义，应让用户排查问题后再试。
        """
        if not self._realsense_specs:
            return

        try:
            from cloudrobo_r2c.common.camera.realsense_camera import RealSenseCamera
        except ImportError:
            logger.error(
                "Dummy robot: pyrealsense2 not available, cannot open "
                "RealSense cameras %s",
                list(self._realsense_specs.keys()),
            )
            raise RuntimeError(
                "pyrealsense2 not available; cannot open configured "
                "RealSense cameras " + ",".join(self._realsense_specs.keys())
            )

        for image_name, rs_spec in self._realsense_specs.items():
            cam_spec = dict(rs_spec)
            serial = str(cam_spec.pop("serial", "")).strip()
            clahe_enabled = bool(cam_spec.pop("clahe", False))
            controls = {
                k: v
                for k, v in cam_spec.items()
                if k not in ("width", "height", "fps")
            }

            cam = None
            try:
                cam = RealSenseCamera(
                    serial=serial,
                    width=int(cam_spec.get("width", 640)),
                    height=int(cam_spec.get("height", 480)),
                    fps=int(cam_spec.get("fps", 30)),
                    name=image_name,
                    controls=controls,
                )
                cam.start()

                h, w = cam.height, cam.width
                self._image_specs[image_name] = [int(h), int(w), 3]
                self._realsense_cameras[image_name] = cam

                # 转存线程：把 RealSenseCamera 帧槽 → 适配器共享帧槽
                stop_event = threading.Event()
                thread = threading.Thread(
                    target=self._realsense_bridge_loop,
                    args=(image_name, cam, stop_event, clahe_enabled),
                    name=f"dummy-rs-{image_name}",
                    daemon=True,
                )
                self._camera_stop_events[image_name] = stop_event
                self._camera_threads[image_name] = thread
                thread.start()

                logger.info(
                    "Dummy robot opened RealSense camera '%s' serial=%s size=%dx%d@%d",
                    image_name, serial, w, h, cam.fps,
                )
            except Exception:
                # RealSense 相机打开失败：清理资源后向上传播，由 connect()
                # 的调用方捕获并退出程序（不回退随机图）。
                if cam is not None:
                    try:
                        cam.stop()
                    except Exception:
                        logger.debug(
                            "Dummy robot RealSense '%s' cleanup failed",
                            image_name, exc_info=True,
                        )
                self._realsense_cameras.pop(image_name, None)
                logger.error(
                    "Dummy robot RealSense camera '%s' serial=%r open failed",
                    image_name, serial, exc_info=True,
                )
                raise

    def _realsense_bridge_loop(
        self,
        camera_id: str,
        cam: Any,
        stop_event: threading.Event,
        clahe_enabled: bool,
    ) -> None:
        """把 RealSenseCamera 的帧转存到适配器共享帧槽（keep-latest）。

        适配器的共享帧槽是推理与推流共用的唯一出口，因此即使
        RealSenseCamera 自带帧槽，也要在此以适配器语义（
        ``_latest_frames`` / ``_latest_frame_ts``）暴露一份。

        ``clahe_enabled`` 时对每帧做 CLAHE 对比度增强（与 V4L2 路径一致）；
        RealSense 路径默认关闭——用 rs.option 正确设置曝光后无需后处理。
        """
        clahe = None
        if clahe_enabled:
            import cv2

            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

        while not stop_event.is_set():
            frame = cam.get_latest_frame()
            if frame is None:
                time.sleep(0.01)
                continue

            if clahe is not None:
                import cv2

                lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
                l, a, b = cv2.split(lab)
                l = clahe.apply(l)
                frame = cv2.merge((l, a, b))
                frame = cv2.cvtColor(frame, cv2.COLOR_LAB2BGR)

            self._latest_frames[camera_id] = frame
            # 保留 RealSenseCamera 的采集时刻（time.monotonic() 语义一致）
            self._latest_frame_ts[camera_id] = cam.get_latest_timestamp()
            time.sleep(0.005)

    def _apply_camera_props(self, cap: Any, cam_spec: Mapping[str, Any]) -> None:
        """将 image_specs 中的相机控制参数应用到 VideoCapture。

        RealSense RGB 节点等相机默认曝光/亮度输出近全黑帧，需显式设置
        才能得到彩色画面。请求失败静默忽略（驱动不支持该控制项时不阻断）。

        UVC 曝光控制要点：
        - ``auto_exposure``: 1=手动模式（设置后才可生效 exposure），3=自动模式
        - ``exposure``: 手动曝光值（UVC 单位为 100μs，如 100=10ms）
        - 必须先设置 ``auto_exposure=1``（手动），``exposure`` 才会生效
        """
        import cv2

        prop_map = {
            "width": cv2.CAP_PROP_FRAME_WIDTH,
            "height": cv2.CAP_PROP_FRAME_HEIGHT,
            "fps": cv2.CAP_PROP_FPS,
            "brightness": cv2.CAP_PROP_BRIGHTNESS,
            "contrast": cv2.CAP_PROP_CONTRAST,
            "exposure": cv2.CAP_PROP_EXPOSURE,
            "gain": cv2.CAP_PROP_GAIN,
        }

        # 先切换到手动曝光模式（auto_exposure=1），再设置 exposure
        if "auto_exposure" in cam_spec and cam_spec["auto_exposure"] is not None:
            try:
                cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, float(cam_spec["auto_exposure"]))
            except Exception:
                logger.debug(
                    "Dummy robot failed to set auto_exposure=%r",
                    cam_spec["auto_exposure"],
                    exc_info=True,
                )

        for key, prop in prop_map.items():
            if key in cam_spec and cam_spec[key] is not None:
                try:
                    cap.set(prop, float(cam_spec[key]))
                except Exception:
                    logger.debug(
                        "Dummy robot failed to set camera prop %r=%r",
                        key,
                        cam_spec[key],
                        exc_info=True,
                    )

    def _camera_capture_loop(
        self,
        camera_id: str,
        cap: Any,
        stop_event: threading.Event,
    ) -> None:
        """后台采集线程：持续读帧覆盖共享帧槽（keep-latest）。

        与 UR5e/Flexiv 的帧槽语义一致——槽只保留最新帧，慢消费者总是
        读到最新的一帧。任何异常都会记录并重试，直到停止。

        RealSense 515 UVC 驱动不支持 gamma/contrast 等 V4L2 控制项，
        因此在采集线程中对每帧做 CLAHE 对比度增强（LAB 色彩空间的 L
        通道自适应直方图均衡），提升画面色彩对比度。
        """
        import cv2

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

        while not stop_event.is_set():
            try:
                ret, frame = cap.read()
                if not ret or frame is None:
                    time.sleep(0.01)
                    continue

                # CLAHE 对比度增强：BGR→LAB，对 L 通道做 CLAHE，再转回 BGR
                lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
                l, a, b = cv2.split(lab)
                l = clahe.apply(l)
                frame = cv2.merge((l, a, b))
                frame = cv2.cvtColor(frame, cv2.COLOR_LAB2BGR)

                now = time.monotonic()
                self._latest_frames[camera_id] = frame
                self._latest_frame_ts[camera_id] = now
                # 推断实际尺寸（相机分辨率可能被驱动重协商）
                if self._image_specs.get(camera_id) is None:
                    h, w = frame.shape[:2]
                    self._image_specs[camera_id] = [int(h), int(w), 3]
            except Exception:
                logger.warning(
                    "Dummy robot camera '%s' capture error",
                    camera_id,
                    exc_info=True,
                )
                time.sleep(0.05)
        cap.release()

    def _close_cameras(self) -> None:
        """停止所有采集线程并释放设备（V4L2、RealSense 与合成视频）。"""
        for camera_id, stop_event in self._camera_stop_events.items():
            stop_event.set()
        for camera_id, thread in self._camera_threads.items():
            if thread and thread.is_alive():
                thread.join(timeout=2.0)
        # RealSense 设备由 RealSenseCamera 持有，需显式释放 pipeline
        for camera_id, cam in self._realsense_cameras.items():
            try:
                cam.stop()
            except Exception:
                logger.debug(
                    "Dummy robot RealSense '%s' stop failed",
                    camera_id, exc_info=True,
                )
        self._realsense_cameras.clear()
        self._camera_captures.clear()
        self._camera_threads.clear()
        self._camera_stop_events.clear()
        logger.info("Dummy robot cameras closed.")

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
