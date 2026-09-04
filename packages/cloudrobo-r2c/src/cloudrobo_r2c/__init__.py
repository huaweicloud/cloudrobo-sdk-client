"""Top-level package for R2C Client SDK, exporting common user-facing entries."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import R2CClient, R2CSession
    from .common.config import ClientConfig
    from .sync_client import SyncRobotClient

try:
    __version__ = version("cloudrobo-r2c")
except PackageNotFoundError:
    __version__ = "unknown"

__all__ = ["R2CClient", "R2CSession", "ClientConfig", "SyncRobotClient", "__version__"]


def __getattr__(name: str) -> Any:
    if name in {"R2CClient", "R2CSession"}:
        from .client import R2CClient, R2CSession

        return {"R2CClient": R2CClient, "R2CSession": R2CSession}[name]
    if name == "ClientConfig":
        from .common.config import ClientConfig

        return ClientConfig
    if name == "SyncRobotClient":
        from .sync_client import SyncRobotClient

        return SyncRobotClient
    raise AttributeError(f"module 'cloudrobo_r2c' has no attribute '{name}'")