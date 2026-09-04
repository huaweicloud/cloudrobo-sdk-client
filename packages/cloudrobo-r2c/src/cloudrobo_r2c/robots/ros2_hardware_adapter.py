"""ROS2-backed implementation of :class:`IRobotHardwareAdapter`.

Configuration (strict schema)::

    {
        "ros2": {
            "node_name": "r2c_hw_adapter",
            "spin_timeout_sec": 0.05,
            "auto_spin": True,
            "subscriptions": {
                "joint_states": {
                    "topic": "/joint_states",
                    "msg_type": "sensor_msgs.msg.JointState",
                    "qos": 10,
                    "store_as": "joint_states",
                    "store_raw_message": False,
                    "max_update_hz": 60,
                    "include_fields": ["name", "position", "velocity", "effort"],
                    "field_aliases": {"name": "names"},
                    "transforms": ["ros_message_to_mapping"]
                }
            },
            "command_publishers": {
                "arm": {
                    "topic": "/arm_controller/command",
                    "msg_type": "trajectory_msgs.msg.JointTrajectory",
                    "qos": 10
                }
            },
            "command_services": {
                "arm_driver": {
                    "service": "/arm_driver/set_joint_positions",
                    "srv_type": "custom_interfaces.srv.SetJointPositions",
                    "timeout_sec": 1.0
                }
            },
            "default_command_publisher": "arm",
            "default_command_service": "arm_driver",
            "action_targets": {"arm": "arm"},
            "service_targets": {"arm_service": "arm_driver"}
        }
    }
"""

from __future__ import annotations

import importlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from cloudrobo_r2c.common.utils import summarize_observation_for_log
from cloudrobo_r2c.core.config_mapper import TransformSpec
from cloudrobo_r2c.core.interfaces import IRobotHardwareAdapter
from cloudrobo_r2c.core.internal.class_loading import SecurityError, validate_module_security
from cloudrobo_r2c.core.transformers import build_transformer_registry


def create_ros2_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> IRobotHardwareAdapter:
    """Entry_point factory for Ros2HardwareAdapter."""
    return Ros2HardwareAdapter(config={"ros2": dict(config)})


logger = logging.getLogger(__name__)


_RCLPY_CONTEXT_LOCK = threading.Lock()
_RCLPY_CONTEXT_REF_COUNTS: Dict[int, int] = {}


@dataclass(frozen=True)
class SubscriptionConfig:
    topic: str
    msg_type: str
    qos: int = 10
    store_as: str = ""
    include_fields: List[str] = field(default_factory=list)
    field_aliases: Mapping[str, str] = field(default_factory=dict)
    max_update_hz: float = 0.0
    store_raw_message: bool = False
    transforms: List[TransformSpec] = field(default_factory=list)


@dataclass(frozen=True)
class CommandPublisherConfig:
    topic: str
    msg_type: str
    qos: int = 10


@dataclass(frozen=True)
class CommandServiceConfig:
    service: str
    srv_type: str
    timeout_sec: float = 1.0


@dataclass(frozen=True)
class ActionClientConfig:
    action_name: str
    action_type: str
    joint_names: List[str] = field(default_factory=list)
    time_from_start_sec: float = 5.0


@dataclass(frozen=True)
class Ros2AdapterConfig:
    node_name: str
    spin_timeout_sec: float
    auto_spin: bool
    subscriptions: Mapping[str, SubscriptionConfig]
    command_publishers: Mapping[str, CommandPublisherConfig]
    command_services: Mapping[str, CommandServiceConfig]
    action_clients: Mapping[str, ActionClientConfig]
    default_command_publisher: Optional[str]
    default_command_service: Optional[str]
    action_targets: Mapping[str, str]
    service_targets: Mapping[str, str]
    observation_sync: "ObservationSyncConfig"
    init_joints: "InitJointsConfig"


@dataclass(frozen=True)
class InitJointsEntry:
    publisher: str = ""
    message: Mapping[str, Any] = field(default_factory=dict)
    joints: List[float] = field(default_factory=list)
    joint_names: List[str] = field(default_factory=list)
    time_from_start_sec: float = 3.0


@dataclass(frozen=True)
class InitJointsConfig:
    enabled: bool = False
    delay_sec: float = 1.0
    entries: List[InitJointsEntry] = field(default_factory=list)


@dataclass(frozen=True)
class ObservationSyncConfig:
    enabled: bool = False
    queue_size: int = 20
    slop_sec: float = 0.05
    allow_headerless: bool = True


@dataclass
class Ros2HardwareAdapter(IRobotHardwareAdapter):
    """Hardware adapter backed by configurable ROS2 subscriptions/publishers."""

    config: Mapping[str, Any]
    rclpy_module: Optional[Any] = None
    message_filters_module: Optional[Any] = None
    message_type_resolver: Optional[Callable[[str], Any]] = None

    _node: Any = field(default=None, init=False, repr=False)
    _connected: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )
    _spin_thread: Optional[threading.Thread] = field(
        default=None, init=False, repr=False
    )
    _stop_spin: threading.Event = field(
        default_factory=threading.Event, init=False, repr=False
    )

    _latest_streams: Dict[str, Any] = field(
        default_factory=dict, init=False, repr=False
    )
    _latest_timestamp_ns: int = field(default=0, init=False, repr=False)

    _command_publishers: Dict[str, Any] = field(
        default_factory=dict, init=False, repr=False
    )
    _command_services: Dict[str, Any] = field(
        default_factory=dict, init=False, repr=False
    )
    _action_clients: Dict[str, Any] = field(
        default_factory=dict, init=False, repr=False
    )
    _adapter_config: Optional[Ros2AdapterConfig] = field(
        default=None, init=False, repr=False
    )
    _subscription_transformers: Dict[str, Any] = field(
        default_factory=build_transformer_registry, init=False, repr=False
    )
    _sync_subscribers: List[Any] = field(default_factory=list, init=False, repr=False)
    _sync_synchronizer: Optional[Any] = field(default=None, init=False, repr=False)

    # -- transport registry (public, for Ros2GoHomeCommand) --------------

    _TRANSPORT_KIND_TO_FIELD = {
        "action": "action_clients",
        "service": "command_services",
        "topic": "command_publishers",
    }

    def get_transport_targets(self, kind: str) -> Mapping[str, Any]:
        """Return the configured target registry for a transport ``kind``.

        ``kind`` is one of ``"action"`` / ``"service"`` / ``"topic"``
        (matching the ``ros2:`` block's ``kind``). Returns the adapter's
        mapping of target name → config for that transport; an empty
        mapping if the adapter is unconfigured.

        Exposed so :class:`Ros2GoHomeCommand` can validate ``target``
        without reaching into ``adapter._adapter_config``.
        """
        kind_field = self._TRANSPORT_KIND_TO_FIELD.get(kind)
        if kind_field is None:
            raise ValueError(
                f"Unknown transport kind {kind!r}; expected one of "
                f"{sorted(self._TRANSPORT_KIND_TO_FIELD)}."
            )
        if self._adapter_config is None:
            return {}
        return getattr(self._adapter_config, kind_field)

    def move_to(
        self,
        *,
        pose_euler: Optional[Sequence[float]] = None,
        pose_quat: Optional[Sequence[float]] = None,
        joints: Optional[Sequence[float]] = None,
    ) -> None:
        """Not supported — ROS2 go_home is dispatched by :class:`Ros2GoHomeCommand`.

        The ROS2 adapter routes arm motion through one of three transports
        (action / service / topic), chosen per-call via the ``ros2:``
        sub-block of the ``go_home`` YAML preset. There is no single
        hard-coded payload this method could emit, so the typed-interface
        ``move_to`` is intentionally not implemented. Use the
        ``commands.go_home`` preset (handled by
        :class:`~cloudrobo_r2c.robots.commands.ros2.Ros2GoHomeCommand`) — see
        ADR-0005.
        """
        raise NotImplementedError(
            "Ros2HardwareAdapter.move_to() is not implemented. ROS2 go_home "
            "is dispatched by Ros2GoHomeCommand via the 'ros2:' sub-block of "
            "the commands.go_home YAML preset (see ADR-0005)."
        )


    def connect(self) -> None:
        if self._connected:
            logger.debug("ROS2 adapter already connected; skipping connect()")
            return

        logger.info("Connecting ROS2 hardware adapter")
        self._adapter_config = self._parse_config(self.config)
        rclpy = self._get_rclpy_module()

        logger.debug(
            "Initializing ROS2 node '%s' (auto_spin=%s, spin_timeout_sec=%.3f)",
            self._adapter_config.node_name,
            self._adapter_config.auto_spin,
            self._adapter_config.spin_timeout_sec,
        )
        self._acquire_rclpy_context(rclpy)
        self._node = rclpy.create_node(self._adapter_config.node_name)

        self._setup_subscriptions(self._adapter_config)
        self._setup_command_publishers(self._adapter_config)
        self._setup_command_services(self._adapter_config)
        self._setup_action_clients(self._adapter_config)

        if self._adapter_config.auto_spin:
            self._stop_spin.clear()
            self._spin_thread = threading.Thread(
                target=self._spin_loop,
                args=(rclpy, self._adapter_config.spin_timeout_sec),
                name=f"{self._adapter_config.node_name}_spin",
                daemon=True,
            )
            self._spin_thread.start()
            logger.debug(
                "Started ROS2 spin thread '%s' with timeout %.3fs",
                self._spin_thread.name,
                self._adapter_config.spin_timeout_sec,
            )

        self._connected = True
        logger.info(
            "ROS2 adapter connected (subscriptions=%d, publishers=%d, services=%d, actions=%d)",
            len(self._adapter_config.subscriptions),
            len(self._adapter_config.command_publishers),
            len(self._adapter_config.command_services),
            len(self._adapter_config.action_clients),
        )

        self._publish_init_joints(self._adapter_config)

    def disconnect(self) -> None:
        if not self._connected:
            logger.debug("ROS2 adapter already disconnected; skipping disconnect()")
            return

        logger.info("Disconnecting ROS2 hardware adapter")
        self._connected = False
        self._stop_spin.set()

        if self._spin_thread is not None:
            self._spin_thread.join(timeout=2.0)
            logger.debug("ROS2 spin thread joined")
            self._spin_thread = None

        rclpy = self._get_rclpy_module()
        node = self._node

        self._node = None
        self._command_publishers = {}
        self._command_services = {}
        self._action_clients = {}
        self._sync_subscribers = []
        self._sync_synchronizer = None
        self._adapter_config = None

        if node is not None:
            destroy_node = getattr(node, "destroy_node", None)
            if callable(destroy_node):
                destroy_node()
                logger.debug("ROS2 node destroyed")

        self._release_rclpy_context(rclpy)
        logger.info("ROS2 hardware adapter disconnected")

    def _acquire_rclpy_context(self, rclpy: Any) -> None:
        module_key = id(rclpy)
        should_init = False
        with _RCLPY_CONTEXT_LOCK:
            current_count = _RCLPY_CONTEXT_REF_COUNTS.get(module_key, 0)
            if current_count == 0:
                should_init = True
            _RCLPY_CONTEXT_REF_COUNTS[module_key] = current_count + 1

        if should_init:
            logger.debug("Initializing shared rclpy context")
            rclpy.init(args=None)

    def _release_rclpy_context(self, rclpy: Any) -> None:
        module_key = id(rclpy)
        should_shutdown = False
        with _RCLPY_CONTEXT_LOCK:
            current_count = _RCLPY_CONTEXT_REF_COUNTS.get(module_key, 0)
            if current_count <= 1:
                _RCLPY_CONTEXT_REF_COUNTS.pop(module_key, None)
                should_shutdown = True
            else:
                _RCLPY_CONTEXT_REF_COUNTS[module_key] = current_count - 1

        if should_shutdown:
            shutdown = getattr(rclpy, "shutdown", None)
            if callable(shutdown):
                logger.debug("Shutting down shared rclpy context")
                shutdown()

    def get_observation(self) -> Mapping[str, Any]:
        if not self._connected:
            raise RuntimeError("Adapter is not connected. Call connect() first.")

        with self._lock:
            obs = {
                "streams": dict(self._latest_streams),
                "timestamp_ns": self._latest_timestamp_ns,
            }

            logger.debug(
                "device observation: %s",
                summarize_observation_for_log(obs),
            )
            return obs

    def send_action(self, command: Mapping[str, Any]) -> None:
        logger.debug("send_action command %s", summarize_observation_for_log(command))
        if not self._connected:
            raise RuntimeError("Adapter is not connected. Call connect() first.")
        if not isinstance(command, Mapping):
            raise ValueError("command must be a mapping")
        if self._adapter_config is None:
            raise RuntimeError("Adapter configuration is not initialized")

        commands_to_publish = self._normalize_commands(command)
        for command_item in commands_to_publish:
            raw_payload = command_item.get("message")
            if raw_payload is None:
                raw_payload = command_item.get("data", command_item)

            transport_kind, transport_name = self._pick_command_transport(
                command_item, self._adapter_config
            )
            if transport_kind == "publisher":
                self._publish_command(
                    publisher_name=transport_name,
                    raw_payload=raw_payload,
                    cfg=self._adapter_config,
                )
                continue

            self._call_command_service(
                service_name=transport_name,
                raw_payload=raw_payload,
                cfg=self._adapter_config,
            )

    def _normalize_commands(
        self, command: Mapping[str, Any]
    ) -> List[Mapping[str, Any]]:
        """Normalize command payload into a list for publisher/service dispatch.

        Supported formats:
        1) Single command (backward compatible):
           {"target": "...", "data": {...}} or {"publisher": "...", "message": ...}
        2) Batch command list:
           {"commands": [{"publisher": "arm", "data": {...}}, ...]}
        3) Publisher keyed command map:
           {"commands_by_publisher": {"arm": {...}, "gripper": {...}}}
        4) Service keyed command map:
           {"commands_by_service": {"joint_move": {...}}}

        Multiple batch formats can be combined in one payload. For example,
        ``commands_by_publisher`` + ``commands_by_service`` in the same command.
        """
        normalized: List[Mapping[str, Any]] = []

        commands = command.get("commands")
        if commands is not None:
            if not isinstance(commands, list) or not commands:
                raise ValueError("command.commands must be a non-empty list")
            for item in commands:
                if not isinstance(item, Mapping):
                    raise ValueError("each command.commands item must be a mapping")
                normalized.append(item)

        commands_by_publisher = command.get("commands_by_publisher")
        if commands_by_publisher is not None:
            if (
                not isinstance(commands_by_publisher, Mapping)
                or not commands_by_publisher
            ):
                raise ValueError(
                    "command.commands_by_publisher must be a non-empty mapping"
                )
            for publisher_name, payload in commands_by_publisher.items():
                if not isinstance(payload, Mapping):
                    raise ValueError(
                        "each command.commands_by_publisher value must be a mapping"
                    )
                item = dict(payload)
                item.setdefault("publisher", str(publisher_name))
                normalized.append(item)

        commands_by_service = command.get("commands_by_service")
        if commands_by_service is not None:
            if not isinstance(commands_by_service, Mapping) or not commands_by_service:
                raise ValueError(
                    "command.commands_by_service must be a non-empty mapping"
                )
            for service_name, payload in commands_by_service.items():
                if not isinstance(payload, Mapping):
                    raise ValueError(
                        "each command.commands_by_service value must be a mapping"
                    )
                item = dict(payload)
                item.setdefault("service", str(service_name))
                normalized.append(item)

        if normalized:
            return normalized

        return [command]

    def _pick_command_transport(
        self, command: Mapping[str, Any], cfg: Ros2AdapterConfig
    ) -> Tuple[str, str]:
        explicit_publisher = command.get("publisher")
        if isinstance(explicit_publisher, str) and explicit_publisher:
            return "publisher", explicit_publisher

        explicit_service = command.get("service")
        if isinstance(explicit_service, str) and explicit_service:
            return "service", explicit_service

        target = command.get("target")
        if isinstance(target, str):
            if target in cfg.action_targets:
                return "publisher", cfg.action_targets[target]
            if target in cfg.service_targets:
                return "service", cfg.service_targets[target]

        if cfg.default_command_publisher:
            return "publisher", cfg.default_command_publisher
        if cfg.default_command_service:
            return "service", cfg.default_command_service

        if len(self._command_publishers) == 1 and not self._command_services:
            return "publisher", next(iter(self._command_publishers.keys()))
        if len(self._command_services) == 1 and not self._command_publishers:
            return "service", next(iter(self._command_services.keys()))

        raise ValueError(
            "Unable to choose command transport: provide command.publisher/command.service, "
            "or configure ros2.default_command_publisher/default_command_service/"
            "action_targets/service_targets."
        )

    def _publish_command(
        self, publisher_name: str, raw_payload: Any, cfg: Ros2AdapterConfig
    ) -> None:
        logger.debug(
            "Publishing to ROS2 command publisher '%s' (keys=%s)",
            publisher_name,
            list(raw_payload.keys()) if isinstance(raw_payload, Mapping) else type(raw_payload).__name__,
        )
        if raw_payload is None:
            logger.debug("No command payload to publish")
            return
        publisher = self._command_publishers.get(publisher_name)
        if publisher is None:
            raise ValueError(
                f"No ROS2 command publisher found for {publisher_name!r}. "
                "Check ros2.command_publishers/default_command_publisher/action_targets."
            )

        command_cfg = cfg.command_publishers[publisher_name]
        message_type = self._resolve_message_type(command_cfg.msg_type)
        if isinstance(raw_payload, message_type):
            ros_message = raw_payload
            logger.debug(
                "Command payload is already ROS message type '%s'",
                command_cfg.msg_type,
            )
        elif isinstance(raw_payload, Mapping):
            ros_message = self._mapping_to_ros_message(message_type, raw_payload)
            logger.debug(
                "Converted mapping payload to ROS message type '%s' with keys=%s",
                command_cfg.msg_type,
                list(raw_payload.keys()),
            )
        else:
            raise ValueError(
                "publisher command payload must be a ROS message or mapping"
            )

        publisher.publish(ros_message)
        logger.debug(
            "Published ROS2 action via '%s' to topic '%s'",
            publisher_name,
            command_cfg.topic,
        )

    def _publish_init_joints(self, cfg: Ros2AdapterConfig) -> None:
        """Publish initial joint positions on startup.

        Called at the end of :meth:`connect` after all publishers are
        created and the spin thread is running.  Supports two modes:

        * ``message`` — a raw dict that is converted to the publisher's
          ROS message type via :meth:`_mapping_to_ros_message`.
        * ``joints`` + ``joint_names`` — a
          ``trajectory_msgs/JointTrajectory`` is built with a single
          waypoint (like :meth:`_build_action_goal` does for action
          goals).
        """
        if not cfg.init_joints.enabled:
            return

        time.sleep(cfg.init_joints.delay_sec)
        logger.info(
            "Publishing init_joints for %d entries (delay_sec=%.1f)",
            len(cfg.init_joints.entries),
            cfg.init_joints.delay_sec,
        )

        for i, entry in enumerate(cfg.init_joints.entries):
            publisher = self._command_publishers.get(entry.publisher)
            if publisher is None:
                logger.error(
                    "init_joints entry #%d: publisher %r not found; skipping",
                    i,
                    entry.publisher,
                )
                continue

            pub_cfg = cfg.command_publishers[entry.publisher]

            if entry.message:
                # ---- raw message mode ----
                msg_type = self._resolve_message_type(pub_cfg.msg_type)
                try:
                    ros_message = self._mapping_to_ros_message(
                        msg_type, entry.message
                    )
                except Exception:
                    logger.exception(
                        "init_joints entry #%d: failed to convert message "
                        "to %s; skipping",
                        i,
                        pub_cfg.msg_type,
                    )
                    continue
            elif entry.joints:
                # ---- JointTrajectory mode ----
                try:
                    from builtin_interfaces.msg import Duration  # type: ignore[import-not-found]
                    from trajectory_msgs.msg import (  # type: ignore[import-not-found]
                        JointTrajectory as _JointTrajectory,
                        JointTrajectoryPoint as _JointTrajectoryPoint,
                    )
                except ImportError:
                    logger.exception(
                        "init_joints entry #%d: trajectory_msgs not "
                        "installed; cannot build JointTrajectory",
                        i,
                    )
                    continue

                joint_names = entry.joint_names
                if not joint_names:
                    # Try to inherit from action_clients
                    action_clients_cfg: Mapping[str, ActionClientConfig] = (
                        cfg.action_clients
                    )
                    for ac_name, ac_cfg in action_clients_cfg.items():
                        if ac_name == entry.publisher or any(
                            t == entry.publisher
                            for t in cfg.action_targets.values()
                            if t == ac_name
                        ):
                            joint_names = list(ac_cfg.joint_names)
                            logger.debug(
                                "init_joints entry #%d: inherited joint_names "
                                "from action_clients.%s: %s",
                                i,
                                ac_name,
                                joint_names,
                            )
                            break

                duration = Duration(sec=int(entry.time_from_start_sec))
                point = _JointTrajectoryPoint(
                    positions=[float(v) for v in entry.joints],
                    time_from_start=duration,
                )
                ros_message = _JointTrajectory(
                    joint_names=joint_names if joint_names else [],
                    points=[point],
                )
            else:
                logger.warning(
                    "init_joints entry #%d: no message or joints; skipping",
                    i,
                )
                continue

            publisher.publish(ros_message)
            logger.info(
                "init_joints entry #%d: published to publisher '%s' "
                "(topic '%s', msg_type '%s')",
                i,
                entry.publisher,
                pub_cfg.topic,
                pub_cfg.msg_type,
            )

    def _call_command_service(
        self, service_name: str, raw_payload: Any, cfg: Ros2AdapterConfig
    ) -> None:
        logger.debug("Selected ROS2 command service '%s'", service_name)
        client = self._command_services.get(service_name)
        if client is None:
            raise ValueError(
                f"No ROS2 command service found for {service_name!r}. "
                "Check ros2.command_services/default_command_service/service_targets."
            )

        service_cfg = cfg.command_services[service_name]
        service_type = self._resolve_message_type(service_cfg.srv_type)
        request_type = getattr(service_type, "Request", None)
        if request_type is None:
            raise ValueError(
                f"ROS2 service type {service_cfg.srv_type!r} has no Request class"
            )

        if isinstance(raw_payload, request_type):
            request = raw_payload
        elif isinstance(raw_payload, Mapping):
            request = self._mapping_to_ros_message(request_type, raw_payload)
        else:
            raise ValueError("service command payload must be a ROS request or mapping")

        wait_for_service = getattr(client, "wait_for_service", None)
        if callable(wait_for_service) and not wait_for_service(
            timeout_sec=service_cfg.timeout_sec
        ):
            raise RuntimeError(
                f"ROS2 service {service_cfg.service!r} not available within "
                f"{service_cfg.timeout_sec}s"
            )

        future = client.call_async(request)
        self._wait_for_future(future, timeout_sec=service_cfg.timeout_sec)
        logger.debug(
            "Called ROS2 command service '%s' at '%s'",
            service_name,
            service_cfg.service,
        )

    def _wait_for_future(self, future: Any, timeout_sec: float) -> None:
        rclpy = self._get_rclpy_module()
        spin_until_future_complete = getattr(rclpy, "spin_until_future_complete", None)
        if callable(spin_until_future_complete) and self._node is not None:
            spin_until_future_complete(self._node, future, timeout_sec=timeout_sec)

        if hasattr(future, "done") and callable(future.done) and not future.done():
            raise RuntimeError(
                f"ROS2 service call did not complete within {timeout_sec}s"
            )

        if hasattr(future, "exception") and callable(future.exception):
            error = future.exception()
            if error is not None:
                raise RuntimeError("ROS2 service call failed") from error

    def _send_action_goal(
        self,
        *,
        target: str,
        joints: Sequence[float],
        timeout_sec: float,
    ) -> None:
        """Send a goal to a configured action server and block for the result.

        Used by :class:`Ros2GoHomeCommand` for ``kind: action``
        dispatch. The goal is assembled from the
        ``ros2.action_clients.<target>`` config (joint_names,
        time_from_start_sec) and the runtime ``joints`` list.
        """
        if not self._connected:
            raise RuntimeError("Adapter is not connected. Call connect() first.")
        if self._adapter_config is None:
            raise RuntimeError("Adapter configuration is not initialized")
        if target not in self._action_clients:
            available = ", ".join(sorted(self._action_clients.keys())) or "(none)"
            raise ValueError(
                f"action client {target!r} not found. Available: {available}"
            )

        cfg = self._adapter_config.action_clients[target]
        client = self._action_clients[target]

        goal = self._build_action_goal(cfg, joints)
        goal_future = client.send_goal_async(goal)
        goal_handle = self._wait_for_future_value(goal_future, timeout_sec)
        if goal_handle is None or not getattr(goal_handle, "accepted", False):
            raise RuntimeError(f"ROS2 action server {target!r} rejected the goal")

        result_future = goal_handle.get_result_async()
        result = self._wait_for_future_value(result_future, timeout_sec)
        status = getattr(result, "status", None)
        if status != 4:
            raise RuntimeError(
                f"ROS2 action {target!r} did not succeed (status={status})"
            )

    def _wait_for_future_value(self, future: Any, timeout_sec: float) -> Any:
        """Block on a future (using ``spin_until_future_complete``) and
        return its result. Re-raises on exception.
        """
        rclpy = self._get_rclpy_module()
        spin_until_future_complete = getattr(rclpy, "spin_until_future_complete", None)
        if callable(spin_until_future_complete) and self._node is not None:
            spin_until_future_complete(self._node, future, timeout_sec=timeout_sec)

        if hasattr(future, "done") and callable(future.done) and not future.done():
            raise TimeoutError(f"ROS2 action did not complete within {timeout_sec}s")
        if hasattr(future, "exception") and callable(future.exception):
            error = future.exception()
            if error is not None:
                raise RuntimeError("ROS2 action call failed") from error
        return future.result() if hasattr(future, "result") else None

    def _build_action_goal(
        self,
        cfg: ActionClientConfig,
        joints: Sequence[float],
    ) -> Any:
        """Assemble a goal for a known action type.

        Currently supports ``control_msgs/action/FollowJointTrajectory``
        with a single trajectory point. Other action types fall
        through to a no-op goal (the action server is expected to
        accept whatever the user wires up via ``send_action`` for
        those).
        """
        action_type = self._resolve_message_type(cfg.action_type)
        type_name = getattr(action_type, "__qualname__", str(action_type))

        if "FollowJointTrajectory" in type_name:
            from builtin_interfaces.msg import Duration as _Duration  # type: ignore[import-not-found]
            from trajectory_msgs.msg import (  # type: ignore[import-not-found]
                JointTrajectory as _JointTrajectory,
                JointTrajectoryPoint as _JointTrajectoryPoint,
            )

            duration = _Duration(sec=int(cfg.time_from_start_sec))
            point = _JointTrajectoryPoint(
                positions=[float(v) for v in joints[: len(cfg.joint_names)]],
                time_from_start=duration,
            )
            return _JointTrajectory(
                joint_names=list(cfg.joint_names),
                points=[point],
            )
        # Unknown action type: best-effort pass-through (a mapping
        # that rclpy will coerce if the message supports it).
        return {"joints": list(joints)}

    def _setup_subscriptions(self, cfg: Ros2AdapterConfig) -> None:
        if cfg.observation_sync.enabled and len(cfg.subscriptions) > 1:
            self._setup_synchronized_subscriptions(cfg)
            return

        for stream_name, item in cfg.subscriptions.items():
            msg_type = self._resolve_message_type(item.msg_type)
            store_as = item.store_as or stream_name
            logger.debug(
                "Creating ROS2 subscription '%s': topic='%s', msg_type='%s', qos=%d, store_as='%s'",
                stream_name,
                item.topic,
                item.msg_type,
                item.qos,
                store_as,
            )
            callback = self._build_stream_callback(
                store_as,
                item.include_fields,
                item.field_aliases,
                item.max_update_hz,
                item.store_raw_message,
                item.transforms,
            )
            self._node.create_subscription(msg_type, item.topic, callback, item.qos)

    def _setup_synchronized_subscriptions(self, cfg: Ros2AdapterConfig) -> None:
        message_filters = self._get_message_filters_module()
        subscriber_cls = getattr(message_filters, "Subscriber", None)
        sync_cls = getattr(message_filters, "ApproximateTimeSynchronizer", None)
        if not callable(subscriber_cls) or not callable(sync_cls):
            raise RuntimeError(
                "ROS2 message_filters module does not provide Subscriber/ApproximateTimeSynchronizer"
            )

        stream_items: List[Tuple[str, SubscriptionConfig]] = list(
            cfg.subscriptions.items()
        )
        prepared_streams: List[Tuple[str, Callable[[Any], Any]]] = []
        subscribers: List[Any] = []

        for stream_name, item in stream_items:
            msg_type = self._resolve_message_type(item.msg_type)
            store_as = item.store_as or stream_name
            logger.debug(
                "Creating synchronized ROS2 subscription '%s': topic='%s', msg_type='%s', qos=%d, store_as='%s'",
                stream_name,
                item.topic,
                item.msg_type,
                item.qos,
                store_as,
            )
            subscribers.append(subscriber_cls(self._node, msg_type, item.topic))
            prepared_streams.append(
                (
                    store_as,
                    self._prepare_stream_processor(
                        item.include_fields,
                        item.field_aliases,
                        item.max_update_hz,
                        item.store_raw_message,
                        item.transforms,
                    ),
                )
            )

        synchronizer = sync_cls(
            subscribers,
            queue_size=cfg.observation_sync.queue_size,
            slop=cfg.observation_sync.slop_sec,
            allow_headerless=cfg.observation_sync.allow_headerless,
        )
        synchronizer.registerCallback(
            self._build_synchronized_callback(prepared_streams)
        )

        self._sync_subscribers = subscribers
        self._sync_synchronizer = synchronizer
        logger.info(
            "Enabled ApproximateTimeSynchronizer for %d streams (queue_size=%d, slop_sec=%.3f, allow_headerless=%s)",
            len(stream_items),
            cfg.observation_sync.queue_size,
            cfg.observation_sync.slop_sec,
            cfg.observation_sync.allow_headerless,
        )

    def _setup_command_publishers(self, cfg: Ros2AdapterConfig) -> None:
        self._command_publishers = {}
        for publisher_name, item in cfg.command_publishers.items():
            msg_type = self._resolve_message_type(item.msg_type)
            publisher = self._node.create_publisher(msg_type, item.topic, item.qos)
            self._command_publishers[publisher_name] = publisher
            logger.debug(
                "Created ROS2 command publisher '%s': topic='%s', msg_type='%s', qos=%d",
                publisher_name,
                item.topic,
                item.msg_type,
                item.qos,
            )

    def _setup_command_services(self, cfg: Ros2AdapterConfig) -> None:
        self._command_services = {}
        for service_name, item in cfg.command_services.items():
            srv_type = self._resolve_message_type(item.srv_type)
            service_client = self._node.create_client(srv_type, item.service)
            self._command_services[service_name] = service_client
            logger.debug(
                "Created ROS2 command service client '%s': service='%s', srv_type='%s', timeout=%.3f",
                service_name,
                item.service,
                item.srv_type,
                item.timeout_sec,
            )

    def _setup_action_clients(self, cfg: Ros2AdapterConfig) -> None:
        """Create ``rclpy.action.ActionClient`` handles for each action.

        The action is connected lazily inside
        :meth:`_send_action_goal`; here we just construct the
        ``ActionClient`` objects so the user can introspect them.
        """
        self._action_clients = {}
        for action_name, item in cfg.action_clients.items():
            action_type = self._resolve_message_type(item.action_type)
            client = self._build_action_client(action_type, item.action_name)
            self._action_clients[action_name] = client
            logger.debug(
                "Created ROS2 action client '%s': action='%s', action_type='%s'",
                action_name,
                item.action_name,
                item.action_type,
            )

    def _build_action_client(self, action_type: Any, action_name: str) -> Any:
        """Build an ``rclpy.action.ActionClient`` for the given type.

        Returns the constructed client. Falls back to a stub object
        (with the same interface methods returning futures) when
        ``rclpy.action`` is unavailable, so unit tests can run without
        the ROS2 runtime.
        """
        rclpy = self._get_rclpy_module()
        action_module = getattr(rclpy, "action", None)
        client_cls = (
            getattr(action_module, "ActionClient", None) if action_module else None
        )
        if not callable(client_cls):
            return _StubActionClient(action_type, action_name)
        if self._node is None:
            raise RuntimeError("ROS2 node is not initialised")
        return client_cls(self._node, action_type, action_name)

    def _build_stream_callback(
        self,
        stream_name: str,
        include_fields: List[str],
        field_aliases: Mapping[str, str],
        max_update_hz: float,
        store_raw_message: bool,
        transforms: List[TransformSpec],
    ) -> Callable[[Any], None]:
        process_message = self._prepare_stream_processor(
            include_fields,
            field_aliases,
            max_update_hz,
            store_raw_message,
            transforms,
        )

        _update_count = [0]  # mutable counter for throttle

        def _callback(msg: Any) -> None:
            filtered = process_message(msg)
            if filtered is None:
                return
            now_ns = time.time_ns()
            with self._lock:
                self._latest_streams[stream_name] = filtered
                self._latest_timestamp_ns = now_ns
            # Throttle: only log every 100th update to avoid DEBUG spam
            # at stream frequency (camera @ 30 Hz → 1 log / 3.3 s).
            _update_count[0] += 1
            if _update_count[0] % 100 == 0:
                logger.debug(
                    "Updated stream '%s' (update #%d, keys=%s)",
                    stream_name,
                    _update_count[0],
                    list(filtered.keys() if isinstance(filtered, Mapping) else []),
                )

        return _callback

    def _build_synchronized_callback(
        self,
        streams: List[Tuple[str, Callable[[Any], Any]]],
    ) -> Callable[..., None]:
        _update_count = [0]

        def _callback(*messages: Any) -> None:
            if len(messages) != len(streams):
                logger.warning(
                    "Synchronized callback message count mismatch: expected=%d actual=%d",
                    len(streams),
                    len(messages),
                )
                return

            synced_payloads: Dict[str, Any] = {}
            for (stream_name, process_message), msg in zip(streams, messages):
                processed = process_message(msg)
                if processed is None:
                    return
                synced_payloads[stream_name] = processed

            now_ns = time.time_ns()
            with self._lock:
                self._latest_streams.update(synced_payloads)
                self._latest_timestamp_ns = now_ns

            _update_count[0] += 1
            if _update_count[0] % 100 == 0:
                logger.debug(
                    "Updated synchronized streams %s (update #%d)",
                    list(synced_payloads.keys()),
                    _update_count[0],
                )

        return _callback

    def _prepare_stream_processor(
        self,
        include_fields: List[str],
        field_aliases: Mapping[str, str],
        max_update_hz: float,
        store_raw_message: bool,
        transforms: List[TransformSpec],
    ) -> Callable[[Any], Optional[Any]]:
        min_interval_sec = (1.0 / max_update_hz) if max_update_hz > 0 else 0.0
        last_update_monotonic: Optional[float] = None

        def _process(msg: Any) -> Optional[Any]:
            nonlocal last_update_monotonic
            if min_interval_sec > 0.0:
                now_monotonic = time.perf_counter()
                if (
                    last_update_monotonic is not None
                    and now_monotonic - last_update_monotonic < min_interval_sec
                ):
                    return None
                last_update_monotonic = now_monotonic

            payload: Any = (
                msg if store_raw_message else self._ros_message_to_mapping(msg)
            )
            payload = self._apply_subscription_transforms(payload, transforms)
            return self._project_fields(payload, include_fields, field_aliases)

        return _process

    @staticmethod
    def _project_fields(
        payload: Any,
        include_fields: List[str],
        field_aliases: Mapping[str, str],
    ) -> Any:
        if not isinstance(payload, Mapping):
            if include_fields or field_aliases:
                logger.warning(
                    "include_fields/field_aliases are ignored for non-mapping payloads"
                )
            return payload

        if include_fields:
            selected = {k: payload[k] for k in include_fields if k in payload}
        else:
            selected = dict(payload)

        result: Dict[str, Any] = {}
        for key, value in selected.items():
            mapped_key = field_aliases.get(key, key)
            result[mapped_key] = value
        return result

    def _apply_subscription_transforms(
        self, value: Any, transforms: List[TransformSpec]
    ) -> Any:
        transformed = value
        for transform_spec in transforms:
            transformer = self._subscription_transformers.get(transform_spec.name)
            if transformer is None:
                raise ValueError(
                    f"Unknown ros2 subscription transform: {transform_spec.name!r}"
                )
            if hasattr(transformer, "transform"):
                transformed = transformer.transform(transformed, transform_spec.config)
            else:
                transformed = transformer(transformed, transform_spec.config)
        return transformed

    def _spin_loop(self, rclpy: Any, spin_timeout_sec: float) -> None:
        logger.debug("ROS2 spin loop started")
        spin_once = getattr(rclpy, "spin_once", None)
        if not callable(spin_once):
            logger.warning(
                "rclpy.spin_once unavailable; ROS2 callbacks may not be processed"
            )
            return

        while not self._stop_spin.is_set():
            node = self._node
            if node is None:
                break
            try:
                spin_once(node, timeout_sec=spin_timeout_sec)
            except Exception:
                logger.exception("ROS2 spin_once failed")
                time.sleep(spin_timeout_sec)
        logger.debug("ROS2 spin loop stopped")

    def _resolve_message_type(self, dotted_path: str) -> Any:
        if self.message_type_resolver is not None:
            logger.debug(
                "Resolving ROS2 message type via custom resolver: %s", dotted_path
            )
            return self.message_type_resolver(dotted_path)

        if "." not in dotted_path:
            raise ValueError(f"Invalid ROS2 message type path: {dotted_path!r}")

        module_path, class_name = dotted_path.rsplit(".", 1)
        validate_module_security(module_path)

        logger.debug(
            "Resolving ROS2 message type '%s' from module '%s'", class_name, module_path
        )
        module = importlib.import_module(module_path)
        loaded = getattr(module, class_name, None)
        if loaded is None:
            raise ValueError(
                f"ROS2 message type {class_name!r} not found in module {module_path!r}"
            )
        if not isinstance(loaded, type):
            raise TypeError(
                f"ROS2 message type path {dotted_path!r} does not resolve to a class"
            )
        return loaded

    @staticmethod
    def _require_mapping(
        value: Any, field_name: str, *, non_empty: bool = False
    ) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"{field_name} must be a mapping")
        if non_empty and not value:
            raise ValueError(f"{field_name} must be a non-empty mapping")
        return value

    @staticmethod
    def _normalize_optional_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError(f"{field_name} must be a mapping")
        return value

    @staticmethod
    def _parse_default_transport_name(
        raw_value: Any,
        *,
        field_name: str,
        available_names: Mapping[str, Any],
        expected_mapping_field: str,
    ) -> Optional[str]:
        if raw_value is None:
            return None
        transport_name = str(raw_value)
        if transport_name not in available_names:
            raise ValueError(f"{field_name} must exist in {expected_mapping_field}")
        return transport_name

    @staticmethod
    def _parse_target_mapping(
        raw_value: Any,
        field_name: str,
        transport_field: str,
        available_names: Mapping[str, Any],
    ) -> Dict[str, str]:
        if not isinstance(raw_value, Mapping):
            raise ValueError(f"{field_name} must be a mapping")
        parsed = {str(k): str(v) for k, v in raw_value.items()}
        for mapped_name in parsed.values():
            if mapped_name not in available_names:
                raise ValueError(
                    f"every {field_name} value must exist in {transport_field}"
                )
        return parsed

    def _parse_subscriptions(
        self, subscriptions_cfg: Mapping[str, Any]
    ) -> Dict[str, SubscriptionConfig]:
        subscriptions: Dict[str, SubscriptionConfig] = {}
        for stream_name, raw_item in subscriptions_cfg.items():
            if not isinstance(raw_item, Mapping):
                raise ValueError("each ros2.subscriptions item must be a mapping")
            topic = str(raw_item.get("topic", "")).strip()
            msg_type = str(raw_item.get("msg_type", "")).strip()
            if not topic or not msg_type:
                raise ValueError(
                    f"subscriptions.{stream_name} requires topic and msg_type"
                )
            subscriptions[str(stream_name)] = SubscriptionConfig(
                topic=topic,
                msg_type=msg_type,
                qos=int(raw_item.get("qos", 10)),
                store_as=str(raw_item.get("store_as", "")).strip(),
                include_fields=self._to_str_list(raw_item.get("include_fields")),
                field_aliases=self._to_str_map(raw_item.get("field_aliases")),
                max_update_hz=self._parse_max_update_hz(raw_item.get("max_update_hz")),
                store_raw_message=bool(raw_item.get("store_raw_message", False)),
                transforms=self._to_transform_specs(raw_item.get("transforms")),
            )
        return subscriptions

    def _parse_command_publishers(
        self, command_publishers_cfg: Mapping[str, Any]
    ) -> Dict[str, CommandPublisherConfig]:
        command_publishers: Dict[str, CommandPublisherConfig] = {}
        for publisher_name, raw_item in command_publishers_cfg.items():
            if not isinstance(raw_item, Mapping):
                raise ValueError("each ros2.command_publishers item must be a mapping")
            topic = str(raw_item.get("topic", "")).strip()
            msg_type = str(raw_item.get("msg_type", "")).strip()
            if not topic or not msg_type:
                raise ValueError(
                    f"command_publishers.{publisher_name} requires topic and msg_type"
                )
            command_publishers[str(publisher_name)] = CommandPublisherConfig(
                topic=topic,
                msg_type=msg_type,
                qos=int(raw_item.get("qos", 10)),
            )
        return command_publishers

    def _parse_command_services(
        self, command_services_cfg: Mapping[str, Any]
    ) -> Dict[str, CommandServiceConfig]:
        command_services: Dict[str, CommandServiceConfig] = {}
        for service_name, raw_item in command_services_cfg.items():
            if not isinstance(raw_item, Mapping):
                raise ValueError("each ros2.command_services item must be a mapping")
            service = str(raw_item.get("service", "")).strip()
            srv_type = str(raw_item.get("srv_type", "")).strip()
            if not service or not srv_type:
                raise ValueError(
                    f"command_services.{service_name} requires service and srv_type"
                )
            command_services[str(service_name)] = CommandServiceConfig(
                service=service,
                srv_type=srv_type,
                timeout_sec=float(raw_item.get("timeout_sec", 1.0)),
            )
        return command_services

    def _parse_action_clients(
        self, action_clients_cfg: Mapping[str, Any]
    ) -> Dict[str, ActionClientConfig]:
        action_clients: Dict[str, ActionClientConfig] = {}
        for action_name, raw_item in action_clients_cfg.items():
            if not isinstance(raw_item, Mapping):
                raise ValueError("each ros2.action_clients item must be a mapping")
            ros2_action_name = str(raw_item.get("action_name", "")).strip()
            action_type = str(raw_item.get("action_type", "")).strip()
            if not ros2_action_name or not action_type:
                raise ValueError(
                    f"action_clients.{action_name} requires action_name and action_type"
                )
            raw_joint_names = raw_item.get("joint_names", [])
            if not isinstance(raw_joint_names, list):
                raise ValueError(
                    f"action_clients.{action_name}.joint_names must be a list"
                )
            action_clients[str(action_name)] = ActionClientConfig(
                action_name=ros2_action_name,
                action_type=action_type,
                joint_names=[str(j) for j in raw_joint_names],
                time_from_start_sec=float(raw_item.get("time_from_start_sec", 5.0)),
            )
        return action_clients

    def _parse_config(self, cfg: Mapping[str, Any]) -> Ros2AdapterConfig:
        logger.debug("Parsing ROS2 adapter configuration")
        ros2_cfg = cfg.get("ros2", cfg)
        ros2_cfg = self._require_mapping(ros2_cfg, "ros2")

        subscriptions_cfg = self._require_mapping(
            ros2_cfg.get("subscriptions"), "ros2.subscriptions", non_empty=True
        )
        command_publishers_cfg = self._normalize_optional_mapping(
            ros2_cfg.get("command_publishers", {}), "ros2.command_publishers"
        )
        command_services_cfg = self._normalize_optional_mapping(
            ros2_cfg.get("command_services", {}), "ros2.command_services"
        )

        if not command_publishers_cfg and not command_services_cfg:
            raise ValueError(
                "ros2.command_publishers or ros2.command_services must provide at least one command transport"
            )

        subscriptions = self._parse_subscriptions(subscriptions_cfg)
        command_publishers = self._parse_command_publishers(command_publishers_cfg)
        command_services = self._parse_command_services(command_services_cfg)
        action_clients_cfg = self._normalize_optional_mapping(
            ros2_cfg.get("action_clients", {}), "ros2.action_clients"
        )
        action_clients = self._parse_action_clients(action_clients_cfg)

        default_command_publisher = self._parse_default_transport_name(
            ros2_cfg.get("default_command_publisher"),
            field_name="ros2.default_command_publisher",
            available_names=command_publishers,
            expected_mapping_field="ros2.command_publishers",
        )
        default_command_service = self._parse_default_transport_name(
            ros2_cfg.get("default_command_service"),
            field_name="ros2.default_command_service",
            available_names=command_services,
            expected_mapping_field="ros2.command_services",
        )
        action_targets = self._parse_target_mapping(
            ros2_cfg.get("action_targets", {}),
            "ros2.action_targets",
            "ros2.command_publishers",
            command_publishers,
        )
        service_targets = self._parse_target_mapping(
            ros2_cfg.get("service_targets", {}),
            "ros2.service_targets",
            "ros2.command_services",
            command_services,
        )
        observation_sync = self._parse_observation_sync_config(
            ros2_cfg.get("observation_sync"),
            subscription_count=len(subscriptions),
        )
        init_joints = self._parse_init_joints(
            ros2_cfg.get("init_joints"),
            available_publishers=command_publishers,
            default_publisher=default_command_publisher,
        )

        parsed_config = Ros2AdapterConfig(
            node_name=str(ros2_cfg.get("node_name", "r2c_hw_adapter")),
            spin_timeout_sec=float(ros2_cfg.get("spin_timeout_sec", 0.05)),
            auto_spin=bool(ros2_cfg.get("auto_spin", True)),
            subscriptions=subscriptions,
            command_publishers=command_publishers,
            command_services=command_services,
            action_clients=action_clients,
            default_command_publisher=default_command_publisher,
            default_command_service=default_command_service,
            action_targets=action_targets,
            service_targets=service_targets,
            observation_sync=observation_sync,
            init_joints=init_joints,
        )
        logger.info(
            "Parsed ROS2 config: node='%s', subscriptions=%d, publishers=%d, services=%d, actions=%d, "
            "default_publisher=%s, default_service=%s, auto_spin=%s, observation_sync(enabled=%s, queue_size=%d, slop_sec=%.3f)",
            parsed_config.node_name,
            len(parsed_config.subscriptions),
            len(parsed_config.command_publishers),
            len(parsed_config.command_services),
            len(parsed_config.action_clients),
            parsed_config.default_command_publisher,
            parsed_config.default_command_service,
            parsed_config.auto_spin,
            parsed_config.observation_sync.enabled,
            parsed_config.observation_sync.queue_size,
            parsed_config.observation_sync.slop_sec,
        )
        return parsed_config

    def _get_rclpy_module(self) -> Any:
        if self.rclpy_module is not None:
            logger.debug("Using injected rclpy module")
            return self.rclpy_module

        try:
            logger.debug("Importing rclpy module")
            return importlib.import_module("rclpy")
        except ImportError as exc:  # pragma: no cover - optional runtime dependency
            raise RuntimeError(
                "ROS2 dependency 'rclpy' is not installed. Please install ROS2 Python packages."
            ) from exc

    def _get_message_filters_module(self) -> Any:
        if self.message_filters_module is not None:
            logger.debug("Using injected message_filters module")
            return self.message_filters_module

        try:
            logger.debug("Importing message_filters module")
            return importlib.import_module("message_filters")
        except ImportError as exc:  # pragma: no cover - optional runtime dependency
            raise RuntimeError(
                "ROS2 dependency 'message_filters' is not installed. "
                "Please install ROS2 Python packages."
            ) from exc

    def _ros_message_to_mapping(self, msg: Any) -> Mapping[str, Any]:
        if isinstance(msg, Mapping):
            return dict(msg)

        fields = getattr(msg, "__slots__", None)
        if fields is None:
            payload = getattr(msg, "__dict__", None)
            if isinstance(payload, Mapping):
                return dict(payload)
            return {"value": msg}

        result: Dict[str, Any] = {}
        for field_name in fields:
            clean_name = field_name[1:] if field_name.startswith("_") else field_name
            value = getattr(msg, field_name, getattr(msg, clean_name, None))
            result[clean_name] = self._normalize_value(value)
        return result

    def _normalize_value(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {k: self._normalize_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._normalize_value(v) for v in value]
        if isinstance(value, tuple):
            return [self._normalize_value(v) for v in value]
        if hasattr(value, "__slots__") or hasattr(value, "__dict__"):
            return self._ros_message_to_mapping(value)
        return value

    def _mapping_to_ros_message(
        self, message_type: Any, data: Mapping[str, Any]
    ) -> Any:
        msg = message_type()
        for key, value in data.items():
            if not hasattr(msg, key):
                continue
            current_value = getattr(msg, key)
            if isinstance(value, Mapping) and (
                hasattr(current_value, "__slots__")
                or hasattr(current_value, "__dict__")
            ):
                nested = self._mapping_to_ros_message(type(current_value), value)
                setattr(msg, key, nested)
                continue
            setattr(msg, key, value)
        return msg

    @staticmethod
    def _to_str_list(value: Any) -> List[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("include_fields must be a list of strings")
        return [str(item) for item in value]

    @staticmethod
    def _to_str_map(value: Any) -> Mapping[str, str]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("field_aliases must be a mapping")
        return {str(k): str(v) for k, v in value.items()}

    @staticmethod
    def _parse_max_update_hz(value: Any) -> float:
        if value is None:
            return 0.0
        parsed = float(value)
        if parsed <= 0:
            raise ValueError("max_update_hz must be > 0 when provided")
        return parsed

    @staticmethod
    def _to_transform_specs(value: Any) -> List[TransformSpec]:
        if value is None:
            return []
        raw_items = value if isinstance(value, list) else [value]
        transforms: List[TransformSpec] = []
        for item in raw_items:
            if isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    transforms.append(TransformSpec(name=stripped))
                continue
            if isinstance(item, Mapping):
                if len(item) != 1:
                    raise ValueError(
                        "each ros2 subscription transform mapping must have exactly one key"
                    )
                transform_name = str(next(iter(item.keys()))).strip()
                transforms.append(
                    TransformSpec(name=transform_name, config=item[transform_name])
                )
                continue
            raise ValueError("ros2 subscription transforms must be str or mapping")
        return transforms

    @staticmethod
    def _resolve_init_joints_publisher(
        publisher: str,
        index: int,
        available_publishers: Mapping[str, Any],
        default_publisher: Optional[str],
    ) -> str:
        """Resolve the publisher for an init_joints entry.

        If *publisher* is empty, falls back to *default_publisher*
        or the sole available publisher.  Raises ValueError when
        it cannot be resolved or the resolved publisher is not
        registered in *available_publishers*.
        """
        if not publisher:
            if default_publisher:
                return default_publisher
            if len(available_publishers) == 1:
                return next(iter(available_publishers.keys()))
            raise ValueError(
                f"ros2.init_joints.entries[{index}]: 'publisher' is required "
                "when multiple command_publishers exist and no "
                "default_command_publisher is configured"
            )

        if publisher not in available_publishers:
            raise ValueError(
                f"ros2.init_joints.entries[{index}]: publisher {publisher!r} "
                f"not found in ros2.command_publishers"
            )
        return publisher

    @staticmethod
    def _parse_init_joints_entry_message(
        raw_message: Any, index: int
    ) -> Mapping[str, Any]:
        """Parse the optional ``message`` field of an init_joints entry."""
        if raw_message is None:
            return {}
        if not isinstance(raw_message, Mapping):
            raise ValueError(
                f"ros2.init_joints.entries[{index}].message must be a mapping"
            )
        return dict(raw_message)

    @staticmethod
    def _parse_init_joints_entry_joints(
        raw_entry: Mapping[str, Any],
        index: int,
        has_joints: bool,
    ) -> Tuple[List[float], List[str], float]:
        """Parse joints / joint_names / time_from_start_sec from a raw init_joints entry."""
        if not has_joints:
            return [], [], 3.0

        raw_joints = raw_entry["joints"]
        if not isinstance(raw_joints, list):
            raise ValueError(
                f"ros2.init_joints.entries[{index}].joints must be a list"
            )
        joints = [float(v) for v in raw_joints]

        raw_names = raw_entry.get("joint_names", [])
        if not isinstance(raw_names, list):
            raise ValueError(
                f"ros2.init_joints.entries[{index}].joint_names must be a list"
            )
        joint_names = [str(n) for n in raw_names]

        time_from_start_sec = float(raw_entry.get("time_from_start_sec", 3.0))
        return joints, joint_names, time_from_start_sec

    @staticmethod
    def _parse_single_init_joints_entry(
        raw_entry: Any,
        index: int,
        available_publishers: Mapping[str, Any],
        default_publisher: Optional[str],
    ) -> InitJointsEntry:
        """Parse a single init_joints entry from raw config."""
        if not isinstance(raw_entry, Mapping):
            raise ValueError(
                f"ros2.init_joints.entries[{index}] must be a mapping"
            )

        publisher = Ros2HardwareAdapter._resolve_init_joints_publisher(
            str(raw_entry.get("publisher", "")).strip(),
            index,
            available_publishers,
            default_publisher,
        )

        has_message = "message" in raw_entry
        has_joints = "joints" in raw_entry

        if has_message and has_joints:
            raise ValueError(
                f"ros2.init_joints.entries[{index}]: cannot specify both "
                "'message' and 'joints' — use one or the other"
            )
        if not has_message and not has_joints:
            raise ValueError(
                f"ros2.init_joints.entries[{index}]: must specify either "
                "'message' or 'joints'"
            )

        message = Ros2HardwareAdapter._parse_init_joints_entry_message(
            raw_entry.get("message"), index
        )
        joints, joint_names, time_from_start_sec = (
            Ros2HardwareAdapter._parse_init_joints_entry_joints(
                raw_entry, index, has_joints
            )
        )

        return InitJointsEntry(
            publisher=publisher,
            message=message,
            joints=joints,
            joint_names=joint_names,
            time_from_start_sec=time_from_start_sec,
        )

    @staticmethod
    def _parse_init_joints(
        value: Any,
        *,
        available_publishers: Mapping[str, Any],
        default_publisher: Optional[str],
    ) -> InitJointsConfig:
        if value is None:
            return InitJointsConfig()
        if not isinstance(value, Mapping):
            raise ValueError("ros2.init_joints must be a mapping")

        enabled = bool(value.get("enabled", False))
        delay_sec = float(value.get("delay_sec", 1.0))
        raw_entries = value.get("entries")
        if not enabled or raw_entries is None:
            return InitJointsConfig(enabled=enabled, delay_sec=delay_sec)

        if not isinstance(raw_entries, list):
            raise ValueError("ros2.init_joints.entries must be a list")
        if not raw_entries:
            raise ValueError(
                "ros2.init_joints.entries must not be empty when enabled=true"
            )

        entries = [
            Ros2HardwareAdapter._parse_single_init_joints_entry(
                raw_entry, i, available_publishers, default_publisher
            )
            for i, raw_entry in enumerate(raw_entries)
        ]

        return InitJointsConfig(
            enabled=enabled, delay_sec=delay_sec, entries=entries
        )

    @staticmethod
    def _parse_observation_sync_config(
        value: Any, *, subscription_count: int
    ) -> ObservationSyncConfig:
        if value is None:
            return ObservationSyncConfig(enabled=subscription_count > 1)
        if not isinstance(value, Mapping):
            raise ValueError("ros2.observation_sync must be a mapping")

        enabled = bool(value.get("enabled", subscription_count > 1))
        queue_size = int(value.get("queue_size", 20))
        slop_sec = float(value.get("slop_sec", 0.05))
        allow_headerless = bool(value.get("allow_headerless", True))

        if queue_size <= 0:
            raise ValueError("ros2.observation_sync.queue_size must be > 0")
        if slop_sec < 0:
            raise ValueError("ros2.observation_sync.slop_sec must be >= 0")

        return ObservationSyncConfig(
            enabled=enabled,
            queue_size=queue_size,
            slop_sec=slop_sec,
            allow_headerless=allow_headerless,
        )


class _StubActionClient:
    """Stand-in for ``rclpy.action.ActionClient`` when rclpy is missing.

    The unit tests run without a live ROS2 runtime; this stub gives
    them a real object to introspect (so the adapter's
    ``send_goal_async`` calls can be inspected) without trying to
    import the real client. It records every goal sent.
    """

    def __init__(self, action_type: Any, action_name: str) -> None:
        self._action_type = action_type
        self._action_name = action_name
        self.sent_goals: List[Any] = []

    def send_goal_async(self, goal: Any) -> Any:
        self.sent_goals.append(goal)
        return _CompletedFuture(goal)


class _CompletedFuture:
    """Minimal completed-future shim for the action client stub.

    The real ``rclpy`` returns a future whose ``result()`` is the
    goal handle (for ``send_goal_async``) or the wrapped response
    (for ``get_result_async``). For testing we just hand back the
    payload as the result.
    """

    def __init__(self, value: Any) -> None:
        self._value = value

    def done(self) -> bool:
        return True

    def result(self) -> Any:
        return self._value

    def exception(self) -> Optional[BaseException]:
        return None
