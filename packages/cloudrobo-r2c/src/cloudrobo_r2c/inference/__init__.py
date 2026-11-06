"""Inference adapters for bridging R2C schema topics with policy services."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client_factory import LocalPolicyClient, create_policy_client
    from .http_policy_client import HTTPPolicyClient
    from .r2c_cloud_adapter import R2CCloudAdapter, R2CCloudAdapterConfig
    from .r2c_lerobot_policy_server import (
        R2CLeRobotPolicyServer,
        R2CLeRobotPolicyServerConfig,
    )
    from .route_config import ProtocolConfig
    from .ws_policy_client import WebSocketPolicyClient

    from cloudrobo_r2c.core.interfaces import IPolicyClient

__all__ = [
    "R2CCloudAdapter",
    "R2CCloudAdapterConfig",
    "R2CLeRobotPolicyServer",
    "R2CLeRobotPolicyServerConfig",
    "IPolicyClient",
    "WebSocketPolicyClient",
    "HTTPPolicyClient",
    "LocalPolicyClient",
    "create_policy_client",
    "ProtocolConfig",
]


def __getattr__(name: str) -> Any:
    if name in {"R2CCloudAdapter", "R2CCloudAdapterConfig"}:
        from .r2c_cloud_adapter import R2CCloudAdapter, R2CCloudAdapterConfig

        return {"R2CCloudAdapter": R2CCloudAdapter, "R2CCloudAdapterConfig": R2CCloudAdapterConfig}[name]
    if name in {"R2CLeRobotPolicyServer", "R2CLeRobotPolicyServerConfig"}:
        from .r2c_lerobot_policy_server import (
            R2CLeRobotPolicyServer,
            R2CLeRobotPolicyServerConfig,
        )

        return {
            "R2CLeRobotPolicyServer": R2CLeRobotPolicyServer,
            "R2CLeRobotPolicyServerConfig": R2CLeRobotPolicyServerConfig,
        }[name]
    if name in {"IPolicyClient"}:
        from cloudrobo_r2c.core.interfaces import IPolicyClient

        return IPolicyClient
    if name in {"WebSocketPolicyClient"}:
        from .ws_policy_client import WebSocketPolicyClient

        return WebSocketPolicyClient
    if name in {"HTTPPolicyClient"}:
        from .http_policy_client import HTTPPolicyClient

        return HTTPPolicyClient
    if name in {"LocalPolicyClient", "create_policy_client"}:
        from .client_factory import LocalPolicyClient, create_policy_client

        return {
            "LocalPolicyClient": LocalPolicyClient,
            "create_policy_client": create_policy_client,
        }[name]
    if name in {"ProtocolConfig"}:
        from .route_config import ProtocolConfig

        return ProtocolConfig
    raise AttributeError(f"module 'cloudrobo_r2c.inference' has no attribute {name!r}")
