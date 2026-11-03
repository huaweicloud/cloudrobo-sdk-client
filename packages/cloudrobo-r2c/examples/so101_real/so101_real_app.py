"""LeRobot SO101 real-robot bridge that publishes R2C observations and executes R2C actions.

This version depends on exactly two config files:

1. ``config/client_config.yaml``
   The original R2C SDK client configuration file, loaded directly with
   ``cloudrobo_r2c.ClientConfig.from_yaml(...)``.
2. ``config/robot_config.yaml``
   A dedicated robot config file that contains:
   - the LeRobot ``RobotConfig`` payload used by ``make_robot_from_config(...)``
   - a dedicated ``joint_mapping`` section for robot-observation / action mapping
   - optional real-app runtime settings such as observation rate and termination strategy

Suggested robot_config.yaml structure::

    robot:
      type: so101_follower
      id: so101-real-001
      port: /dev/ttyACM0
      cameras:
        front:
          type: opencv
          index_or_path: 0
          width: 640
          height: 480
          fps: 30

    observation:
      joint_state_key: observation.state
      gripper_key: gripper
      ee_pose_key: ee_pose
      image_key: observation.images
      image_name_map:
        front: front

    joint_mapping:
      input:
        source_joint_names: [shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper]
        publish_joint_names: [shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper]
      output:
        action_joint_names: [shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper]
        robot_joint_names: [shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper]

    real_app:
      task_name: so101_real_task
      observation_hz: 10.0
      robot_calibrate: false
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

import cv2
import yaml

try:  # pragma: no cover - optional runtime dependency
    import numpy as np
except Exception:  # pragma: no cover
    np = None

logger = logging.getLogger(__name__)


@dataclass
class BridgeRuntimeConfig:
    task_name: str = "so101_real_task"
    observation_hz: float = 10.0
    robot_calibrate: bool = False


@dataclass
class ObservationConfig:
    joint_state_key: str = "observation.state"
    gripper_key: Optional[str] = "gripper"
    ee_pose_key: Optional[str] = "ee_pose"
    image_key: Optional[str] = "observation.images"
    image_name_map: Dict[str, str] = field(default_factory=dict)


@dataclass
class JointMappingConfig:
    input_source_joint_names: list[str] = field(default_factory=list)
    input_publish_joint_names: list[str] = field(default_factory=list)
    output_action_joint_names: list[str] = field(default_factory=list)
    output_robot_joint_names: list[str] = field(default_factory=list)


@dataclass
class ObservationSummary:
    joint_positions: list[float]
    gripper_position: Optional[float]
    end_effector_pose: list[float]
    images: Dict[str, bytes]
    raw_observation: Mapping[str, Any]


class TaskTerminationStrategy(ABC):
    @abstractmethod
    def should_stop(self, observation: ObservationSummary, elapsed_s: float) -> bool:
        """Return True when the control loop should stop."""


@dataclass
class TimeBasedTerminationStrategy(TaskTerminationStrategy):
    max_duration_s: float

    def should_stop(self, observation: ObservationSummary, elapsed_s: float) -> bool:
        return elapsed_s >= self.max_duration_s


@dataclass
class SO101LiftTaskTerminationStrategy(TaskTerminationStrategy):
    lift_height_threshold: float = 0.08
    required_gripper_position: float = 0.6
    stable_cycles: int = 3
    _stable_hits: int = 0
    _grasped: bool = False

    def should_stop(self, observation: ObservationSummary, elapsed_s: float) -> bool:
        if (
            observation.gripper_position is not None
            and observation.gripper_position >= self.required_gripper_position
        ):
            self._grasped = True

        z_value = (
            observation.end_effector_pose[2]
            if len(observation.end_effector_pose) >= 3
            else 0.0
        )
        if self._grasped and z_value >= self.lift_height_threshold:
            self._stable_hits += 1
        else:
            self._stable_hits = 0
        return self._stable_hits >= self.stable_cycles


class CompositeTerminationStrategy(TaskTerminationStrategy):
    def __init__(self, strategies: Sequence[TaskTerminationStrategy]):
        self._strategies = list(strategies)

    def should_stop(self, observation: ObservationSummary, elapsed_s: float) -> bool:
        return any(
            strategy.should_stop(observation, elapsed_s)
            for strategy in self._strategies
        )


class NoOpTerminationStrategy(TaskTerminationStrategy):
    def should_stop(self, observation: ObservationSummary, elapsed_s: float) -> bool:
        return False


@dataclass(frozen=True)
class TerminationConfig:
    max_duration_s: float = 300.0
    disable_task_stop: bool = False
    lift_height_threshold: float = 0.08
    required_gripper_position: float = 0.6
    stable_cycles: int = 3


class So101RealApp:
    def __init__(
        self,
        *,
        session: Any,
        robot: Any,
        runtime_config: BridgeRuntimeConfig,
        observation_config: ObservationConfig,
        joint_mapping: JointMappingConfig,
        termination_strategy: TaskTerminationStrategy,
    ):
        self.session = session
        self.robot = robot
        self.runtime_config = runtime_config
        self.observation_config = observation_config
        self.joint_mapping = joint_mapping
        self.termination_strategy = termination_strategy
        self._pending_actions: "queue.Queue[Any]" = queue.Queue()
        self._observation_id = 0

    def start(self) -> None:
        self.session.subscribe_actions(self._on_action)
        started_at = time.monotonic()
        period_s = 1.0 / self.runtime_config.observation_hz

        logger.info("SO101 real app started. Waiting for observations and actions.")
        while True:
            loop_started = time.monotonic()
            raw_observation = self.robot.get_observation()
            logger.info(f"raw_observation: {raw_observation.keys()}")
            summary = summarize_robot_observation(
                raw_observation, self.observation_config
            )
            payload = build_r2c_observation_payload(
                timestamp_ms=int(time.time() * 1000),
                task_name=self.runtime_config.task_name,
                observation_id=self._observation_id,
                raw_observation=raw_observation,
                joint_names=self.joint_mapping.input_publish_joint_names,
            )
            self.session.publish_observations(payload)
            self._observation_id += 1

            self._drain_actions()

            elapsed_s = time.monotonic() - started_at
            if self.termination_strategy.should_stop(summary, elapsed_s):
                logger.info("Termination strategy triggered after %.2fs.", elapsed_s)
                break

            remaining = period_s - (time.monotonic() - loop_started)
            if remaining > 0:
                time.sleep(remaining)

    def close(self) -> None:
        close_robot(self.robot)
        self.session.close()

    def _on_action(self, action: Any) -> None:
        self._pending_actions.put(action)

    def _drain_actions(self) -> None:
        try:
            while True:
                action = self._pending_actions.get_nowait()
                commands = action_to_lerobot_commands(
                    action, joint_mapping=self.joint_mapping
                )
                logger.info("Applying action id=%s with joints", action.id)
                for command in commands:
                    self.robot.send_action(command)
                    time.sleep(0.05)
        except queue.Empty:
            return


def close_robot(robot: Any) -> None:
    disconnect = getattr(robot, "disconnect", None)
    if callable(disconnect):
        disconnect()


def _lookup_nested(mapping: Mapping[str, Any], dotted_key: str) -> Any:
    current: Any = mapping
    if not dotted_key:
        return current
    for part in dotted_key.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(dotted_key)
        current = current[part]
    return current


def _to_float_list(values: Any) -> list[float]:
    if values is None:
        return []
    if np is not None and isinstance(values, np.ndarray):
        return [float(v) for v in values.reshape(-1).tolist()]
    if isinstance(values, (list, tuple)):
        return [float(v) for v in values]
    return [float(values)]


def _normalize_images(image_value: Any) -> Dict[str, bytes]:
    if image_value is None:
        return {}
    if isinstance(image_value, Mapping):
        normalized: Dict[str, bytes] = {}
        for name, value in image_value.items():
            normalized[str(name)] = _image_to_bytes(value)
        return normalized
    return {"main": _image_to_bytes(image_value)}


def _image_to_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if np is not None and isinstance(value, np.ndarray):
        return value.tobytes()
    raise TypeError(f"Unsupported image type for R2C conversion: {type(value)!r}")


def _remap_images(
    images: Dict[str, bytes], image_name_map: Mapping[str, str]
) -> Dict[str, bytes]:
    if not image_name_map:
        return images
    remapped: Dict[str, bytes] = {}
    for publish_name, source_name in image_name_map.items():
        if source_name in images:
            remapped[str(publish_name)] = images[source_name]
    return remapped


def summarize_robot_observation(
    robot_observation: Mapping[str, Any], observation_config: ObservationConfig
) -> ObservationSummary:
    return ObservationSummary(
        joint_positions=[],
        gripper_position=[],
        end_effector_pose=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
        images={},
        raw_observation=robot_observation,
    )


def build_png_bytes_from_ndarray(data: ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", data)
    if not ok:
        raise RuntimeError("Failed to encode ndarray as PNG with cv2")
    return encoded.tobytes()


def build_r2c_observation_payload(
    *,
    timestamp_ms: int,
    task_name: str,
    observation_id: int,
    raw_observation: Mapping[str, Any],
    joint_names: Sequence[str],
) -> Dict[str, Any]:
    actual_joint_names = [
        "joint_1",
        "joint_2",
        "joint_3",
        "joint_4",
        "joint_5",
        "joint_6",
    ]
    position = [
        raw_observation["shoulder_pan.pos"],
        raw_observation["shoulder_lift.pos"],
        raw_observation["elbow_flex.pos"],
        raw_observation["wrist_flex.pos"],
        raw_observation["wrist_roll.pos"],
        raw_observation["gripper.pos"],
    ]

    front = raw_observation["front"]
    front = build_png_bytes_from_ndarray(front)
    wrist = raw_observation["wrist"]
    wrist = build_png_bytes_from_ndarray(wrist)

    return {
        "timestamp": timestamp_ms,
        "task": task_name,
        "id": observation_id,
        "images": {"color": {"front": front, "wrist": wrist}, "depth": {}},
        "joint_states": {
            "names": actual_joint_names,
            "position": position,
            "velocity": [],
            "torque": [],
        },
    }


def _canonical_action_names(
    action: Any, joint_mapping: JointMappingConfig, action_width: int
) -> list[str]:
    received_names = list(
        getattr(getattr(action, "joint_states", None), "names", []) or []
    )
    if received_names:
        if len(received_names) != action_width:
            raise ValueError(
                f"R2C action joint_states.names length mismatch: {len(received_names)} vs {action_width}"
            )
        return [str(name) for name in received_names]
    if joint_mapping.output_action_joint_names:
        if len(joint_mapping.output_action_joint_names) != action_width:
            raise ValueError(
                "Configured joint_mapping.output.action_joint_names length does not match the action width: "
                f"{len(joint_mapping.output_action_joint_names)} vs {action_width}"
            )
        return list(joint_mapping.output_action_joint_names)
    raise ValueError(
        "No action joint names available; configure joint_mapping.output.action_joint_names"
    )


def action_to_lerobot_command(
    action: Any,
    *,
    joint_mapping: JointMappingConfig,
    current_gripper: Optional[float] = None,
) -> Dict[str, float]:
    positions = getattr(getattr(action, "joint_states", {}), "position", [])
    if not positions:
        raise ValueError("R2C action does not contain joint_states.position")

    first_step = positions[0]
    action_names = _canonical_action_names(action, joint_mapping, len(first_step))
    by_name = {str(name): float(value) for name, value in zip(action_names, first_step)}

    if joint_mapping.output_action_joint_names:
        canonical_source_names = joint_mapping.output_action_joint_names
    else:
        canonical_source_names = action_names

    robot_joint_names = joint_mapping.output_robot_joint_names or canonical_source_names
    if len(canonical_source_names) != len(robot_joint_names):
        raise ValueError(
            "Configured joint_mapping.output.action_joint_names and output.robot_joint_names must have the same length"
        )

    command: Dict[str, float] = {}
    for source_name, robot_name in zip(canonical_source_names, robot_joint_names):
        if source_name in by_name:
            command[str(robot_name)] = float(by_name[source_name])

    if (
        current_gripper is not None
        and "gripper" in robot_joint_names
        and "gripper" not in command
    ):
        command["gripper"] = float(current_gripper)

    if not command:
        raise ValueError("No robot command values were produced from the R2C action")
    return command


def action_to_lerobot_commands(
    action: Any,
    *,
    joint_mapping: JointMappingConfig,
    current_gripper: Optional[float] = None,
) -> list(Dict[str, float]):
    positions = action.joint_states.position
    actual_joint_names = [
        "joint_1",
        "joint_2",
        "joint_3",
        "joint_4",
        "joint_5",
        "joint_6",
    ]
    commands = []
    for position in positions:
        command = {
            "shoulder_pan.pos": position[0],
            "shoulder_lift.pos": position[1],
            "elbow_flex.pos": position[2],
            "wrist_flex.pos": position[3],
            "wrist_roll.pos": position[4],
            "gripper.pos": position[5],
        }
        commands.append(command)

    return commands


def load_yaml_config(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Top-level config must be a mapping, got {type(data)!r}")
    return data


def build_runtime_config(robot_cfg: Mapping[str, Any]) -> BridgeRuntimeConfig:
    app_cfg = robot_cfg.get("real_app") or {}
    if not isinstance(app_cfg, Mapping):
        raise ValueError("real_app must be a mapping")
    return BridgeRuntimeConfig(
        task_name=str(app_cfg.get("task_name", "so101_real_task")),
        observation_hz=float(app_cfg.get("observation_hz", 10.0)),
        robot_calibrate=bool(app_cfg.get("robot_calibrate", False)),
    )


def build_termination_config(robot_cfg: Mapping[str, Any]) -> TerminationConfig:
    app_cfg = robot_cfg.get("real_app") or {}
    return TerminationConfig(
        max_duration_s=float(app_cfg.get("max_duration_s", 300.0)),
        disable_task_stop=bool(app_cfg.get("disable_task_stop", False)),
        lift_height_threshold=float(app_cfg.get("lift_height_threshold", 0.08)),
        required_gripper_position=float(app_cfg.get("required_gripper_position", 0.6)),
        stable_cycles=int(app_cfg.get("stable_cycles", 3)),
    )


def build_termination_strategy(cfg: TerminationConfig) -> TaskTerminationStrategy:
    strategies: list[TaskTerminationStrategy] = []
    if cfg.max_duration_s > 0:
        strategies.append(
            TimeBasedTerminationStrategy(max_duration_s=cfg.max_duration_s)
        )
    if not cfg.disable_task_stop:
        strategies.append(
            SO101LiftTaskTerminationStrategy(
                lift_height_threshold=cfg.lift_height_threshold,
                required_gripper_position=cfg.required_gripper_position,
                stable_cycles=cfg.stable_cycles,
            )
        )
    if not strategies:
        return NoOpTerminationStrategy()
    if len(strategies) == 1:
        return strategies[0]
    return CompositeTerminationStrategy(strategies)


def build_observation_config(robot_cfg: Mapping[str, Any]) -> ObservationConfig:
    obs_cfg = robot_cfg.get("observation") or {}
    if not isinstance(obs_cfg, Mapping):
        raise ValueError("observation must be a mapping")
    return ObservationConfig(
        joint_state_key=str(obs_cfg.get("joint_state_key", "observation.state")),
        gripper_key=(
            None
            if obs_cfg.get("gripper_key") in (None, "")
            else str(obs_cfg.get("gripper_key"))
        ),
        ee_pose_key=(
            None
            if obs_cfg.get("ee_pose_key") in (None, "")
            else str(obs_cfg.get("ee_pose_key"))
        ),
        image_key=(
            None
            if obs_cfg.get("image_key") in (None, "")
            else str(obs_cfg.get("image_key"))
        ),
        image_name_map={
            str(k): str(v) for k, v in dict(obs_cfg.get("image_name_map") or {}).items()
        },
    )


def build_joint_mapping_config(robot_cfg: Mapping[str, Any]) -> JointMappingConfig:
    joint_cfg = robot_cfg.get("joint_mapping") or {}
    if not isinstance(joint_cfg, Mapping):
        raise ValueError("joint_mapping must be a mapping")
    input_cfg = joint_cfg.get("input") or {}
    output_cfg = joint_cfg.get("output") or {}
    if not isinstance(input_cfg, Mapping) or not isinstance(output_cfg, Mapping):
        raise ValueError(
            "joint_mapping.input and joint_mapping.output must be mappings"
        )

    source_joint_names = [str(x) for x in input_cfg.get("source_joint_names", [])]
    publish_joint_names = [
        str(x) for x in input_cfg.get("publish_joint_names", [])
    ] or list(source_joint_names)
    action_joint_names = [str(x) for x in output_cfg.get("action_joint_names", [])]
    robot_joint_names = [
        str(x) for x in output_cfg.get("robot_joint_names", [])
    ] or list(action_joint_names)

    if source_joint_names and len(source_joint_names) != len(publish_joint_names):
        raise ValueError(
            "joint_mapping.input.source_joint_names and input.publish_joint_names must have the same length"
        )
    if action_joint_names and len(action_joint_names) != len(robot_joint_names):
        raise ValueError(
            "joint_mapping.output.action_joint_names and output.robot_joint_names must have the same length"
        )

    return JointMappingConfig(
        input_source_joint_names=source_joint_names,
        input_publish_joint_names=publish_joint_names,
        output_action_joint_names=action_joint_names,
        output_robot_joint_names=robot_joint_names,
    )


def _extract_robot_config_mapping(robot_cfg: Mapping[str, Any]) -> Mapping[str, Any]:
    robot_section = robot_cfg.get("robot")
    if not isinstance(robot_section, Mapping):
        raise ValueError("robot must be a mapping")
    if not robot_section.get("type"):
        raise ValueError("robot.type is required (e.g. so101_follower)")
    return robot_section


def load_robot_config_from_yaml_mapping(robot_cfg: Mapping[str, Any]) -> Any:
    try:
        import draccus
        from lerobot.cameras.opencv.configuration_opencv import \
            OpenCVCameraConfig  # noqa: F401
        from lerobot.cameras.realsense.configuration_realsense import \
            RealSenseCameraConfig  # noqa: F401
        from lerobot.robots import bi_so_follower  # noqa: F401
        from lerobot.robots import (RobotConfig, koch_follower,
                                    make_robot_from_config, omx_follower,
                                    so_follower)
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "LeRobot and draccus must be installed in the robot environment to build RobotConfig."
        ) from exc

    robot_cfg_mapping = dict(_extract_robot_config_mapping(robot_cfg))

    if hasattr(draccus, "decode"):
        return draccus.decode(RobotConfig, robot_cfg_mapping)

    with tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as handle:
        yaml.safe_dump(robot_cfg_mapping, handle, sort_keys=False)
        temp_path = handle.name
    try:
        with open(temp_path, "r", encoding="utf-8") as handle:
            return draccus.load(RobotConfig, handle)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def make_robot_instance(robot_cfg: Mapping[str, Any]) -> Any:
    try:
        from lerobot.robots import make_robot_from_config
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "LeRobot is not installed. Please `pip install lerobot`."
        ) from exc

    robot_config = load_robot_config_from_yaml_mapping(robot_cfg)
    robot = make_robot_from_config(robot_config)
    logger.info(
        "Created robot via make_robot_from_config | type=%s | id=%s",
        getattr(robot_config, "type", None),
        getattr(robot_config, "id", None),
    )
    return robot


def load_r2c_client_config(client_config_path: str | Path) -> Any:
    from cloudrobo_r2c import ClientConfig

    return ClientConfig.from_yaml(str(client_config_path))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SO101 real robot bridge using make_robot_from_config + R2C"
    )

    # Recommended: bundle mode
    parser.add_argument(
        "--bundle",
        type=str,
        default=None,
        help="Path to the platform-issued credential bundle zip or unpacked directory (recommended)",
    )

    parser.add_argument(
        "--client-config",
        default="config/client_config.yaml",
        help="Path to the original R2C SDK client config YAML",
    )

    parser.add_argument(
        "--robot-config",
        default="config/robot_so101_config.yaml",
        help="Path to the dedicated robot config YAML containing robot + joint mapping sections",
    )
    return parser.parse_args()


def main() -> None:  # pragma: no cover
    args = parse_args()
    robot_cfg = load_yaml_config(args.robot_config)
    runtime_config = build_runtime_config(robot_cfg)
    observation_config = build_observation_config(robot_cfg)
    joint_mapping = build_joint_mapping_config(robot_cfg)
    termination_strategy = build_termination_strategy(
        build_termination_config(robot_cfg)
    )

    from cloudrobo_r2c import R2CClient

    if args.bundle:
        session = R2CClient.connect(args.bundle)
    elif args.client_config:
        session = R2CClient.connect(load_r2c_client_config(args.client_config))

    robot = make_robot_instance(robot_cfg)

    connect = getattr(robot, "connect")
    try:
        connect(calibrate=runtime_config.robot_calibrate)
    except TypeError:
        connect()

    app = So101RealApp(
        session=session,
        robot=robot,
        runtime_config=runtime_config,
        observation_config=observation_config,
        joint_mapping=joint_mapping,
        termination_strategy=termination_strategy,
    )
    try:
        app.start()
    finally:
        app.close()


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    main()
