"""Policy client factory and the local model runner.

``create_policy_client`` selects the appropriate ``IPolicyClient`` from a
``ProtocolConfig`` (None → local, ws/wss → WebSocket, http/https → HTTP).
``LocalPolicyClient`` is the renamed local runner (formerly
``LeRobotPolicyRunner``); no compatibility alias is retained.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from cloudrobo_r2c.core.interfaces import IPolicyClient
from cloudrobo_r2c.inference.http_policy_client import HTTPPolicyClient
from cloudrobo_r2c.inference.route_config import ProtocolConfig
from cloudrobo_r2c.inference.ws_policy_client import WebSocketPolicyClient

logger = logging.getLogger(__name__)


class LocalPolicyClient(IPolicyClient):
    """Local model inference runner (no network).

    Lazily loads the local model via
    ``cloudrobo_r2c.inference.r2c_lerobot_policy_server.load_model``.
    """

    def __init__(self, model_config: Optional[Any] = None) -> None:
        self._model_config = model_config
        self._model: Optional[Any] = None

    def _load_model(self) -> None:
        """Lazily load the local model."""
        if self._model is not None:
            return
        try:
            from cloudrobo_r2c.inference.r2c_lerobot_policy_server import load_model

            self._model = load_model(self._model_config)
        except ImportError as exc:
            raise ImportError(
                "Local model dependencies not available. "
                "Install with: pip install torch lerobot"
            ) from exc

    def connect(self) -> None:
        """Ensure the local model is loaded. Idempotent.

        Loads the model on first call; a no-op once loaded. After ``close()``
        the next ``connect()`` (or ``infer``) reloads the model.
        """
        self._load_model()

    def infer(self, payload: Any) -> Dict[str, Any]:
        self._load_model()
        return self._model.infer(payload)

    def close(self) -> None:
        self._model = None


def _infer_client_type(protocol: ProtocolConfig) -> Optional[str]:
    """Resolve the client type from protocol.type or the endpoint scheme."""
    client_type = protocol.type
    if client_type is None and protocol.endpoint:
        endpoint = protocol.endpoint
        if endpoint.startswith(("ws://", "wss://")):
            client_type = "ws"
        elif endpoint.startswith(("http://", "https://")):
            client_type = "http"
    return client_type


def create_policy_client(
    protocol: Optional[ProtocolConfig],
    api_key: Optional[str] = None,
) -> IPolicyClient:
    """Factory: create the appropriate policy client from a protocol config.

    ``api_key`` is a runtime-supplied authentication secret (e.g. from a masked
    stdin prompt), injected in-memory only — never persisted to config.

    Selection strategy:
    - ``protocol is None`` → ``LocalPolicyClient`` (local inference).
    - type/endpoint scheme ``ws``/``wss`` → ``WebSocketPolicyClient``.
    - type/endpoint scheme ``http``/``https`` → ``HTTPPolicyClient``.
    - ``protocol.probe`` is True but the resolved client is not ws/wss → raise
      ``ValueError`` (misconfiguration).
    - cannot determine → raise ``ValueError``.

    Raises:
        ValueError: On probe/misconfiguration or unresolved client type.
    """
    if protocol is None:
        return LocalPolicyClient()

    client_type = _infer_client_type(protocol)

    if client_type in ("ws", "wss"):
        return WebSocketPolicyClient(protocol, api_key=api_key)
    elif client_type in ("http", "https"):
        # probe 仅对 WebSocket 客户端有效：非 websocket 而 probe=True 属配置错误
        if getattr(protocol, "probe", False):
            raise ValueError(
                f"protocol.probe is only valid for WebSocket clients, "
                f"but resolved client type is {client_type!r} "
                f"(endpoint={protocol.endpoint!r})"
            )
        return HTTPPolicyClient(protocol, api_key=api_key)
    else:
        raise ValueError(
            f"Cannot determine policy client type from protocol: type={client_type!r}, "
            f"endpoint={protocol.endpoint!r}"
        )
