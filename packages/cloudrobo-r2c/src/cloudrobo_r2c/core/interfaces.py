"""Abstract interfaces inspired by the r2c_v2 draft's boundary design."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Mapping,
    Protocol,
    Union,
    runtime_checkable,
)

if TYPE_CHECKING:
    # 仅类型检查时导入。运行时导入会拉入 cloudrobo_r2c.common 包初始化
    # (common/__init__ → schema → core.config_mapper),与 core 包自身
    # 的初始化形成循环导入("cloudrobo_r2c.core 作为首个导入"场景)。
    # 直接从 wrapper 子模块导入:models/__init__ 用动态 globals() 重导出,
    # mypy 无法解析其属性。
    import numpy as np
    from cloudrobo_r2c.common.models.wrappers.action import Actions
    from cloudrobo_r2c.common.models.wrappers.observations import Observations

ObservationLike = Union["Observations", Mapping[str, Any], None]
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


class IPolicyClient(ABC):
    """Cloud-side inference client boundary abstraction.

    Implementations (WebSocket / HTTP / local runner) send a payload to an
    inference service and return the response as a dict. Lives beside
    ``IModelTranslator`` as part of the cloud-side model boundary.

    Lifecycle contract: ``connect`` must be idempotent (safe to call multiple
    times and after ``close``); ``infer`` must not be called before ``connect``;
    ``close`` releases the underlying resources and is also idempotent.
    """

    @abstractmethod
    def connect(self) -> None:
        """Establish/prepare the underlying connection. Idempotent."""
        raise NotImplementedError

    @abstractmethod
    def infer(self, payload: Any) -> Dict[str, Any]:
        """Send payload to the inference service and return the response."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Clean up underlying resources."""
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


@runtime_checkable
class ICameraFrameSource(Protocol):
    """可选接口：硬件适配器若实现此协议，则向 LiveKit 推流侧共享相机帧。

    相机设备仍由适配器独占打开（单一采集进程），推理侧 ``get_observation()``
    与推流侧 ``CameraCapture`` 共享同一帧槽。实现方应在内部加锁并返回副本
    （copy），避免与采集线程的写入竞争——从根源上避免 V4L2/RealSense 设备
    独占冲突。

    ``@runtime_checkable`` 允许 ``isinstance(adapter, ICameraFrameSource)``
    运行时检查（协议为鸭子类型，不要求显式继承）。
    """

    def get_latest_frame(
        self, camera_id: str
    ) -> Optional["np.ndarray"]:
        """返回指定相机的最近一帧 BGR 图像（H, W, 3）uint8。

        相机不存在或尚无帧时返回 None。
        """

    def get_latest_timestamp(self, camera_id: str) -> float:
        """返回最近一帧的采集时间戳（time.monotonic()）。无帧返回 0.0。"""
