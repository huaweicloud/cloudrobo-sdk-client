"""Template adapter for integrating vendor-specific robot SDKs with :class:`IRobotHardwareAdapter`."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from cloudrobo_r2c.core.interfaces import IRobotHardwareAdapter

logger = logging.getLogger(__name__)


def create_raw_sdk_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> IRobotHardwareAdapter:
    """Entry_point factory for VendorSDKHardwareAdapter.

    Expects ``raw_sdk_robot_factory`` in *extra_kwargs*.
    """
    robot_factory = extra_kwargs.get("raw_sdk_robot_factory")
    if robot_factory is None:
        raise ValueError("raw_sdk_robot_factory is required for raw_sdk adapter")
    return VendorSDKHardwareAdapter(config=dict(config), robot_factory=robot_factory)


VendorRobotFactory = Callable[[Mapping[str, Any]], Any]


@dataclass
class VendorSDKHardwareAdapter(IRobotHardwareAdapter):
    """Reusable parent/template adapter for arbitrary vendor SDK robots.

    Developers can either:

    1. Use this class directly by injecting ``robot_factory`` and keeping
       default vendor method names (``connect``, ``disconnect``,
       ``get_observation``, ``send_action``).
    2. Subclass and override method-name fields or helper methods to adapt to
       a vendor-specific API style.

    Example:
        >>> class MyVendorAdapter(VendorSDKHardwareAdapter):
        ...     connect_method_name = "initialize"
        ...     disconnect_method_name = "shutdown"
        ...     observation_method_name = "read_state"
        ...     action_method_name = "apply_command"

    ``config`` accepts arbitrary vendor configuration mappings. By default,
    ``robot_factory`` receives this mapping and returns a concrete SDK robot
    instance.
    """

    config: Mapping[str, Any]
    robot_factory: VendorRobotFactory
    connect_kwargs: Dict[str, Any] = field(default_factory=dict)

    connect_method_name: str = "connect"
    disconnect_method_name: str = "disconnect"
    observation_method_name: str = "get_observation"
    action_method_name: str = "send_action"

    _robot: Any = field(default=None, init=False, repr=False)
    _connected: bool = field(default=False, init=False, repr=False)

    def connect(self) -> None:
        """Instantiate and connect the vendor SDK robot."""
        if self._connected:
            return

        robot = self._ensure_robot_instance()
        connect = self._resolve_optional_method(robot, self.connect_method_name)
        if connect is None:
            raise TypeError(
                f"Vendor robot instance must expose callable {self.connect_method_name}()"
            )

        if self.connect_kwargs:
            connect(**self.connect_kwargs)
        else:
            connect()
        self._connected = True

    def disconnect(self) -> None:
        """Disconnect the robot if vendor SDK supports it."""
        if self._robot is None:
            self._connected = False
            return

        disconnect = self._resolve_optional_method(
            self._robot, self.disconnect_method_name
        )
        if disconnect is not None:
            disconnect()
        self._connected = False

    def get_observation(self) -> Mapping[str, Any]:
        """Fetch latest observation from vendor SDK and normalize to mapping."""
        robot = self._require_connected_robot()
        get_observation = self._resolve_optional_method(
            robot, self.observation_method_name
        )
        if get_observation is None:
            raise TypeError(
                "Vendor robot instance must expose callable "
                f"{self.observation_method_name}()"
            )

        observation = get_observation()
        return self._normalize_observation(observation)

    def send_action(self, command: Mapping[str, Any]) -> None:
        """Forward an action command to vendor SDK."""
        robot = self._require_connected_robot()
        send_action = self._resolve_optional_method(robot, self.action_method_name)
        if send_action is None:
            raise TypeError(
                f"Vendor robot instance must expose callable {self.action_method_name}(...)"
            )
        send_action(command)

    def _build_robot(self) -> Any:
        """Build vendor robot instance from ``config``.

        Subclasses may override this for more advanced initialization paths.
        """
        return self.robot_factory(self.config)

    def _normalize_observation(self, observation: Any) -> Mapping[str, Any]:
        """Normalize vendor observation payload to mapping.

        Subclasses can override for custom conversion logic.
        """
        if isinstance(observation, Mapping):
            return observation
        raise TypeError(
            "Vendor observation must be a mapping, "
            f"got {type(observation)!r}. Override _normalize_observation() if needed."
        )

    def _require_connected_robot(self) -> Any:
        if not self._connected or self._robot is None:
            raise RuntimeError("Adapter is not connected. Call connect() first.")
        return self._robot

    def _ensure_robot_instance(self) -> Any:
        if self._robot is None:
            self._robot = self._build_robot()
        return self._robot

    @staticmethod
    def _resolve_optional_method(
        instance: Any, method_name: str
    ) -> Optional[Callable[..., Any]]:
        method = getattr(instance, method_name, None)
        if method is None:
            return None
        if not callable(method):
            raise TypeError(
                f"Attribute {method_name!r} exists on vendor robot but is not callable"
            )
        return method
