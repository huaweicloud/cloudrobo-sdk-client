"""Abstract interfaces inspired by the r2c_v2 draft's boundary design."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence, Union

from cloudrobo_r2c.common.models import Actions, Observations

ObservationLike = Union[Observations, Mapping[str, Any], None]
logger = logging.getLogger(__name__)


class IRobotHardwareAdapter(ABC):
    """Hardware boundary abstraction for device-specific SDKs or drivers."""

    @abstractmethod
    def connect(self) -> None:
        """Initialize the underlying hardware backend."""

    @abstractmethod
    def disconnect(self) -> None:
        """Tear down hardware resources."""

    @abstractmethod
    def get_observation(self) -> Mapping[str, Any]:
        """Read and return the latest raw observation from device backends."""

    @abstractmethod
    def send_action(self, command: Mapping[str, Any]) -> None:
        """Send a device-native action command."""

    def register_command_class(self, type_name: str, cmd_cls: type) -> None:
        """Register a command class under *type_name*.

        Registered classes are instantiated by the factory for each
        ``commands.<instance_name>`` YAML entry whose ``type`` field
        matches *type_name*.  Re-registering the same *type_name*
        overwrites the previous class.
        """
        if not hasattr(self, "_registered_command_classes"):
            setattr(self, "_registered_command_classes", {})
        getattr(self, "_registered_command_classes")[type_name] = cmd_cls

    def register_command(self, instance_name: str, command: Any) -> None:
        """Register an instantiated command under *instance_name*."""
        if not hasattr(self, "_adapter_commands"):
            setattr(self, "_adapter_commands", {})
        registry: Dict[str, Any] = getattr(self, "_adapter_commands")
        if instance_name in registry:
            logger.warning(
                "Skip duplicate command registration for %r; "
                "an implementation is already registered.",
                instance_name,
            )
            return
        registry[instance_name] = command

    def execute_command(self, command: str, **kwargs: Any) -> bool:
        """Execute a registered command by instance name.

        Returns ``True`` if the command was found and executed, ``False``
        if the command is not registered.
        """
        registry: Mapping[str, Any] = getattr(self, "_adapter_commands", {})
        cmd = registry.get(command)
        if cmd is None:
            logger.warning(
                "Command %r is not registered. Skip execution.", command
            )
            return False
        execute = getattr(cmd, "execute", None)
        if not callable(execute):
            raise TypeError(
                f"Registered command {command!r} must expose execute()"
            )
        execute(**kwargs)
        return True


class AdapterFactory(Protocol):
    """Protocol for entry_point-based adapter factory functions.

    Signature: ``create(config, **extra_kwargs) -> IRobotHardwareAdapter``.
    """

    def __call__(
        self, config: Mapping[str, Any], **extra_kwargs: Any
    ) -> IRobotHardwareAdapter: ...


class IDeviceTranslator(ABC):
    """Edge translator: raw device observation <-> R2C observation/action payload."""

    @abstractmethod
    def device_to_r2c(self, raw_device_observation: Any) -> ObservationLike:
        raise NotImplementedError

    @abstractmethod
    def r2c_to_device(self, action_step: Mapping[str, Any]) -> Any:
        raise NotImplementedError


class IModelTranslator(ABC):
    """Cloud translator: R2C observation <-> model IO."""

    @abstractmethod
    def r2c_to_model_input(self, observation: ObservationLike) -> Any:
        raise NotImplementedError

    @abstractmethod
    def model_output_to_r2c(self, model_output_tensor: Any) -> Actions:
        raise NotImplementedError


class IValueTransformer(ABC):
    """Value-level transformer used by config mapper rules."""

    @abstractmethod
    def transform(
        self, value: Any, config: Any = None, context: Any = None
    ) -> Any:
        """Transform a source value into a target-ready value.

        Args:
            value: The value to transform.
            config: Transformer-specific configuration from YAML.
            context: Optional payload dict (the full source observation)
                enabling name-based lookups via ``source_names_path``.
        """

    @staticmethod
    def validate_config(config: Any) -> None:
        """Validate *config* at load time; raise ``ValueError`` on invalid input.

        Subclasses that accept configuration override this to perform
        early schema checks.  The default implementation is a no-op.
        """
