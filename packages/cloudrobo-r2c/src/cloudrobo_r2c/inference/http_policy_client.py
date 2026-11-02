"""HTTP(S) policy client implementation.

Serves both ``http://`` and ``https://`` endpoints: the underlying
``requests.Session`` picks the plain or TLS transport from the URL scheme of
``protocol.endpoint``. A ``type: http`` or ``type: https`` route can therefore
point at either scheme without any client-side change (encryption upgrade
http→https supported out of the box).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from cloudrobo_r2c.core.interfaces import IPolicyClient
from cloudrobo_r2c.inference.route_config import ProtocolConfig

logger = logging.getLogger(__name__)

# Content-Type used for msgpack binary request bodies (numpy-aware wire format).
_CONTENT_TYPE_MSGPACK = "application/msgpack"

# Supported endpoint schemes (used for fast, import-free validation in
# ``infer``). ``requests`` performs the actual TLS handshake for ``https://``.
_SUPPORTED_SCHEMES = ("http://", "https://")


class HTTPPolicyClient(IPolicyClient):
    """HTTP-based policy client using a lazily-created ``requests.Session``.

    - A runtime ``api_key`` is injected as a ``Bearer`` Authorization header
      in-memory only (never persisted to config).
    - ``protocol.headers`` are merged onto the session.
    - ``timeout`` is taken from ``protocol.timeout`` (defaults to 30s).
    """

    def __init__(
        self,
        protocol: ProtocolConfig,
        api_key: Optional[str] = None,
    ) -> None:
        self._protocol = protocol
        self._api_key = api_key
        self._session: Optional[Any] = None

    def _get_session(self) -> Any:
        """Lazily create and configure the requests session."""
        if self._session is None:
            try:
                import requests
            except ImportError as exc:
                raise ImportError(
                    "requests is required for HTTP policy. "
                    "Install with: pip install requests"
                ) from exc
            self._session = requests.Session()
            # Runtime api_key is injected as a Bearer Authorization header
            # in-memory only — never persisted to a config file.
            if self._api_key:
                # 防御性校验：控制字符（如 \r \n）会破坏 HTTP 头结构，拒绝注入。
                if any(ch in self._api_key for ch in "\r\n"):
                    raise ValueError(
                        "api_key must not contain control characters (\\r or \\n)"
                    )
                self._session.headers["Authorization"] = f"Bearer {self._api_key}"
            if self._protocol.headers:
                self._session.headers.update(self._protocol.headers)
        return self._session

    def connect(self) -> None:
        """Prepare/initialize the underlying session. Idempotent.

        Lazily creates the ``requests.Session`` (a no-op if already created);
        after ``close()`` the next ``connect()`` creates a fresh session.
        """
        self._get_session()

    def infer(self, payload: Any) -> Dict[str, Any]:
        """Send the payload and return the parsed response body.

        The endpoint scheme (``http://`` or ``https://``, case-insensitive)
        selects the transport — both are supported regardless of
        ``protocol.type``. An unsupported scheme raises ``ValueError`` early
        with a clear message instead of a confusing ``requests`` error.

        Returns:
            The response body parsed to a dict. JSON responses (the common
            ``application/json`` case, e.g. our VLN stub) are decoded with
            ``response.json()``. Any other content is returned raw
            (``response.content``) so that binary/encoded policies can inspect
            it. Raises on any non-2xx response.

        Note:
            ``requests`` automatically sets ``Content-Type: application/json``
            when ``json=`` is used, so JSON bodies round-trip as expected.
        """
        session = self._get_session()
        endpoint = self._protocol.endpoint or ""
        method = self._protocol.method or "POST"
        timeout = self._protocol.timeout or 30
        if endpoint and not endpoint.lower().startswith(_SUPPORTED_SCHEMES):
            raise ValueError(
                f"HTTP policy client requires an http:// or https:// endpoint, "
                f"got {endpoint!r}"
            )

        try:
            response = session.request(
                method=method,
                url=endpoint,
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.error("HTTP inference request failed: %s", "; ".join(exc.args))
            raise exc

        return self._parse_response(response)

    @staticmethod
    def _parse_response(response: Any) -> Any:
        """Parse an HTTP response body into a dict (or raw content).

        JSON bodies are decoded with ``response.json()``. If the body is
        empty or not JSON-decodable, the raw ``response.content`` is returned
        so the caller can handle it (e.g. binary policy output, empty OK from
        fire-and-forget endpoints like ``reset``/``update_obs``).
        """
        content_type = (response.headers.get("Content-Type") or "").lower()
        if "json" in content_type:
            return response.json()
        try:
            return response.json()
        except Exception:
            content = response.content
            return content if content else {}

    def close(self) -> None:
        """Close the underlying session. Idempotent."""
        if self._session is not None:
            self._session.close()
            self._session = None
