"""Transport layer abstraction definition, ensuring interface-oriented programming."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional

from cloudrobo_r2c.common.config import ClientConfig

TransportCallback = Callable[[bytes], None]


class ITransport(ABC):
    """Defines the interface contract that all transport implementations must satisfy."""

    @abstractmethod
    def connect(self, config: ClientConfig) -> None:
        """Establish connection to remote gateway."""

    @abstractmethod
    def publish(self, topic: str, payload: bytes) -> None:
        """Send binary payload to specified topic."""

    @abstractmethod
    def subscribe(self, topic: str, callback: TransportCallback) -> None:
        """Subscribe to topic and register callback to handle received messages."""

    def connection_info(self) -> Optional[Dict[str, Any]]:
        """Return a safe (sanitized) connection summary for diagnostics.
        """
        return None

    @abstractmethod
    def close(self) -> None:
        """Close underlying connection and release resources."""