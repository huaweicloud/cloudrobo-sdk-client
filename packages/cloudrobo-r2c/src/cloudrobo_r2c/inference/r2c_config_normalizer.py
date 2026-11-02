"""Configuration normalizer for multi-route support (v3.4 schema)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlparse

from cloudrobo_r2c.core.config_mapper import (
    TransformSpec,
    _to_transform_specs_or_none,
)
from cloudrobo_r2c.inference.route_config import (
    ModelConfig,
    ProtocolConfig,
    RouteConfig,
)

logger = logging.getLogger(__name__)

_SCHEME_PATTERN = re.compile(r"^(https?|wss?)://", re.IGNORECASE)

# ``type`` 与 endpoint scheme 的兼容分类：websocket 一族（ws/wss）与
# http 一族（http/https）各自视为互通，允许加密升级（ws→wss、http→https）。
# 这样 ``type: ws`` 可搭配 ``wss://`` endpoint，``type: http`` 可搭配
# ``https://`` endpoint，无需强制 type 与 scheme 逐一匹配。
_WS_FAMILY = frozenset({"ws", "wss"})
_HTTP_FAMILY = frozenset({"http", "https"})


@dataclass
class NormalizedConfig:
    """Normalized configuration with resolved routes."""

    schema_version: str
    routes: List[RouteConfig]
    default_route: Optional[str] = None
    default_serial_transform: Optional[List[TransformSpec]] = None
    default_deserial_transform: Optional[List[TransformSpec]] = None
    model: Optional[ModelConfig] = None  # deprecated


class CloudConfigNormalizer:
    """Normalizes raw YAML config (v2.2 or v3.4) into unified RouteConfig list."""

    def normalize(
        self, raw_config: Mapping[str, Any], host_url: str = ""
    ) -> NormalizedConfig:
        if not isinstance(raw_config, Mapping):
            raise ValueError("raw_config must be a mapping")

        # Check for invalid 'models' (plural dict) field
        if "models" in raw_config and isinstance(raw_config["models"], dict):
            raise ValueError(
                "Field 'models' (plural dict) is not supported. "
                "Use 'routes' (list) instead."
            )

        # Detect schema_version
        schema_version = raw_config.get("schema_version")
        if not schema_version:
            raise ValueError("schema_version is required in config")
        schema_version = str(schema_version)

        # 版本划分规则（item 3）：
        #   v2.2 ~ v3.2（含）→ 单路由旧格式（_normalize_v22）
        #   v3.3 ~ v3.9（不含 v4.0）→ 新版 route 格式（_normalize_v34）
        #   其余（v1.x/v2.0/v2.1/v4.0/v4.1...）→ 不兼容错误
        legacy_single_route_versions = {"v2.2", "v3.0", "v3.1", "v3.2"}
        route_versions = {
            "v3.3",
            "v3.4",
            "v3.5",
            "v3.6",
            "v3.7",
            "v3.8",
            "v3.9",
        }

        if schema_version in legacy_single_route_versions:
            return self._normalize_v22(raw_config, host_url)
        elif schema_version in route_versions:
            return self._normalize_v34(raw_config, host_url, schema_version)
        else:
            raise ValueError(
                f"Unsupported schema_version: {schema_version!r}. "
                f"Supported: v2.2~v3.2 (legacy single-route), "
                f"v3.3~v3.9 (routes)."
            )

    # ------------------------------------------------------------------
    # v2.2 compatibility
    # ------------------------------------------------------------------

    def _normalize_v22(
        self, raw_config: Mapping[str, Any], host_url: str
    ) -> NormalizedConfig:
        """Normalize v2.2 config to v3.4 routes format."""

        r2c_to_model = self._extract_mappings(raw_config.get("r2c_to_model"))
        model_to_r2c = self._extract_mappings(raw_config.get("model_to_r2c"))

        # Build protocol from openpi
        protocol_dict: Optional[Dict[str, Any]] = None
        openpi = raw_config.get("openpi")
        if isinstance(openpi, Mapping) and openpi:
            protocol_dict = self._build_protocol_from_openpi(openpi)

        route_dict: Dict[str, Any] = {"name": "default"}
        if r2c_to_model:
            route_dict["r2c_to_model"] = r2c_to_model
        if model_to_r2c:
            route_dict["model_to_r2c"] = model_to_r2c
        if protocol_dict:
            route_dict["protocol"] = protocol_dict

        route = RouteConfig.from_dict(route_dict)
        route = self._resolve_route_endpoint(route, host_url)

        # Parse model (deprecated)
        model = ModelConfig.from_dict(raw_config.get("model"))
        if model:
            logger.warning(
                "Field 'model' is deprecated in v3.4 schema. "
                "It is still parsed for backward compatibility but may be removed "
                "in future versions."
            )

        return NormalizedConfig(
            schema_version="v3.4",
            routes=[route],
            default_route="default",
            model=model,
        )

    # ------------------------------------------------------------------
    # v3.3 / v3.4
    # ------------------------------------------------------------------

    def _normalize_v34(
        self,
        raw_config: Mapping[str, Any],
        host_url: str,
        schema_version: str,
    ) -> NormalizedConfig:
        """Normalize v3.3/v3.4 config."""

        raw_routes = raw_config.get("routes")
        if raw_routes is None:
            raise ValueError("'routes' field is required in v3.4 config")
        if not isinstance(raw_routes, list):
            raise ValueError("'routes' must be a list")
        if not raw_routes:
            raise ValueError("'routes' list cannot be empty")

        routes: List[RouteConfig] = []
        seen_names: set[str] = set()
        for raw_route in raw_routes:
            if not isinstance(raw_route, Mapping):
                raise ValueError(
                    f"Each route must be a mapping, got {type(raw_route)!r}"
                )
            route = RouteConfig.from_dict(raw_route)
            if route.name in seen_names:
                raise ValueError(f"Duplicate route name: {route.name!r}")
            seen_names.add(route.name)
            routes.append(route)

        # Resolve endpoints and check type/scheme
        resolved_routes = [
            self._resolve_route_endpoint(route, host_url) for route in routes
        ]

        # Parse default_route
        default_route = raw_config.get("default_route")
        if default_route is not None:
            default_route = str(default_route)
            if default_route not in seen_names:
                raise ValueError(
                    f"default_route {default_route!r} not found in routes: "
                    f"{sorted(seen_names)}"
                )

        # Parse default transforms
        default_serial_transform = self._parse_transform_list(
            raw_config.get("default_serial_transform")
        )
        default_deserial_transform = self._parse_transform_list(
            raw_config.get("default_deserial_transform")
        )

        # Parse model (deprecated)
        model = ModelConfig.from_dict(raw_config.get("model"))
        if model:
            logger.warning(
                "Field 'model' is deprecated in v3.4 schema. "
                "It is still parsed for backward compatibility but may be removed "
                "in future versions."
            )

        return NormalizedConfig(
            schema_version=schema_version,
            routes=resolved_routes,
            default_route=default_route,
            default_serial_transform=default_serial_transform,
            default_deserial_transform=default_deserial_transform,
            model=model,
        )

    # ------------------------------------------------------------------
    # Endpoint resolution
    # ------------------------------------------------------------------

    def _resolve_route_endpoint(
        self, route: RouteConfig, host_url: str
    ) -> RouteConfig:
        """Resolve endpoint template variables and check type/scheme."""
        if route.protocol is None:
            return route

        protocol = route.protocol
        resolved_endpoint = self._resolve_endpoint(protocol.endpoint, host_url)

        resolved_type = protocol.type
        if resolved_type is None:
            resolved_type = self._infer_type_from_endpoint(resolved_endpoint)
        else:
            self._check_type_scheme_consistency(resolved_type, resolved_endpoint)

        new_protocol = ProtocolConfig(
            type=resolved_type,
            origin_endpoint=protocol.endpoint,
            endpoint=resolved_endpoint,
            method=protocol.method,
            headers=dict(protocol.headers),
            timeout=protocol.timeout,
            tls=protocol.tls,
            probe=protocol.probe,
            connect_strategy=protocol.connect_strategy,
        )
        return replace(route, protocol=new_protocol)

    def _resolve_endpoint(
        self, endpoint: Optional[str], host_url: str
    ) -> str:
        """Resolve endpoint template variables and path concatenation.

        Semantics of ``host_url`` and ``endpoint``:
          - The URL host (ip/domain) and port MUST come **only** from
            ``host_url``.  ``endpoint`` must not carry its own hostname/port.
          - When ``host_url`` is **non-empty**, ``endpoint`` may be:
              * a ``{{HOST_URL}}`` template (placeholder for ``host_url``);
              * empty/``None`` (= ``host_url`` verbatim);
              * a path starting with ``/`` (concatenated onto ``host_url``);
              * a scheme+path with **no** host/port (e.g. ``ws:///api``), in
                which case ``host_url``'s authority is reused and the endpoint
                scheme is preserved.
            If ``endpoint`` carries a hostname/port → ``ValueError``, even if
            it matches ``host_url`` (host/port must not be duplicated).
          - When ``host_url`` is **empty**, backward-compatible behaviour is
            preserved: ``endpoint`` may be a full standalone URL
            (``ws://h:1``) that supplies its own host/port.
        """
        # Case 1: {{HOST_URL}} template placeholder. ``{{HOST_URL}}`` is
        # special — it is both a host placeholder AND a concatenatable path
        # anchor, so it is deliberately exempt from the host/port check that
        # runs on non-template endpoints below.
        if endpoint and "{{HOST_URL}}" in endpoint:
            return self._resolve_template_endpoint(endpoint, host_url)

        # When host_url is set, endpoint must not bring its own host/port.
        if host_url and endpoint:
            self._assert_endpoint_has_no_host_or_port(endpoint)

        # Case 3: scheme + path. When host_url is empty this is a full
        # standalone URL passed through verbatim; when host_url is set the
        # endpoint has no host (checked above) so we combine it onto
        # host_url's authority, preserving the endpoint's scheme.
        if endpoint and _SCHEME_PATTERN.match(endpoint):
            if host_url:
                return self._combine_scheme_with_host_url(endpoint, host_url)
            return endpoint

        # Case 4: Empty/None endpoint → host_url is the full endpoint
        if not endpoint:
            if not host_url:
                raise ValueError("endpoint is empty and host_url is empty")
            return host_url

        # Case 5: Path starting with / → concatenate
        if endpoint.startswith("/"):
            if not host_url:
                raise ValueError(
                    f"endpoint is a path ({endpoint!r}) but host_url is empty"
                )
            return host_url.rstrip("/") + endpoint

        # Case 6: Other → error
        raise ValueError(f"Invalid endpoint format: {endpoint!r}")

    def _resolve_template_endpoint(self, endpoint: str, host_url: str) -> str:
        """Resolve a ``{{HOST_URL}}`` template against ``host_url``.

        ``{{HOST_URL}}`` has a dual role:
          * **Host placeholder** — when it sits in the URL authority
            (preceded by ``scheme://``, e.g. ``ws://{{HOST_URL}}``), it stands
            for ``host_url``'s *authority* (``host:port``, scheme dropped).
          * **Path anchor** — when it appears without a preceding scheme
            (e.g. ``{{HOST_URL}}/api``), it stands for the *full* ``host_url``
            (scheme included) so the result keeps a scheme.

        This distinction is required because ``host_url`` already carries its
        scheme; naively replacing ``{{HOST_URL}}`` with the full ``host_url``
        would yield ``ws://ws://host:port`` when the template already supplied
        a ``ws://`` scheme prefix.
        """
        if not host_url:
            raise ValueError(
                "endpoint contains {{HOST_URL}} but host_url is empty"
            )
        marker = "{{HOST_URL}}"
        prefix = endpoint[: endpoint.find(marker)]
        if "://" in prefix:
            # host placeholder → use host_url's authority (netloc), no scheme
            hp = urlparse(host_url)
            authority = hp.netloc or hp.path
            if not authority:
                raise ValueError(
                    f"cannot extract host:port (netloc) from host_url "
                    f"{host_url!r}"
                )
            replacement = authority.rstrip("/")
        else:
            # path anchor → use full host_url (keeps scheme)
            replacement = host_url.rstrip("/")
        return endpoint.replace(marker, replacement)

    def _assert_endpoint_has_no_host_or_port(self, endpoint: str) -> None:
        """Raise if ``endpoint`` carries a hostname/port.

        Called only when ``host_url`` is set: the host/port of the final URL
        must come solely from ``host_url``, never from ``endpoint``.  The
        ``{{HOST_URL}}`` placeholder is exempt (it is substituted before this
        method runs, and kept here only defensively).
        """
        parsed = urlparse(endpoint)
        host = parsed.hostname
        if host and host.lower() == "{{host_url}}":
            # urlparse lower-cases the netloc hostname; {{HOST_URL}} is a
            # placeholder, not a real host.
            host = None
        if host:
            raise ValueError(
                f"endpoint {endpoint!r} must not contain a hostname; "
                f"host and port must come from host_url"
            )
        if parsed.port is not None:
            raise ValueError(
                f"endpoint {endpoint!r} must not contain a port; "
                f"host and port must come from host_url"
            )

    def _combine_scheme_with_host_url(
        self, endpoint: str, host_url: str
    ) -> str:
        """Combine a scheme+path endpoint (with no host) onto host_url.

        Used when ``host_url`` is set and ``endpoint`` is of the form
        ``scheme:///path`` (scheme present, authority empty).  The authority
        (host:port) is taken from ``host_url`` and the endpoint's scheme and
        path are preserved.
        """
        ep = urlparse(endpoint)
        scheme = ep.scheme  # guaranteed http/https/ws/wss by _SCHEME_PATTERN
        path = ep.path or ""
        hp = urlparse(host_url)
        authority = hp.netloc or hp.path
        if not authority:
            raise ValueError(
                f"cannot extract host:port (netloc) from host_url {host_url!r}"
            )
        return f"{scheme}://{authority}{path}"

    def _infer_type_from_endpoint(self, endpoint: str) -> str:
        """Infer protocol type from endpoint URL scheme."""
        lowered = endpoint.lower()
        if lowered.startswith("ws://"):
            return "ws"
        if lowered.startswith("wss://"):
            return "wss"
        if lowered.startswith("http://"):
            return "http"
        if lowered.startswith("https://"):
            return "https"
        raise ValueError(
            f"Cannot infer protocol type from endpoint: {endpoint!r}"
        )

    def _check_type_scheme_consistency(
        self, type_: str, endpoint: str
    ) -> None:
        """Check that protocol type is compatible with endpoint scheme.

        ``type`` and the endpoint scheme belong to the same family (ws↔wss,
        http↔https) are considered compatible, so a ``wss://`` endpoint may be
        used with ``type: ws`` and an ``https://`` endpoint with ``type: http``.
        A mismatch across families (e.g. ``type: ws`` + ``https://``) is an
        error, as is an unsupported scheme.
        """
        match = _SCHEME_PATTERN.match(endpoint)
        if match:
            scheme = match.group(1).lower()
            type_norm = type_.lower()
            compatible = (
                (type_norm in _WS_FAMILY and scheme in _WS_FAMILY)
                or (type_norm in _HTTP_FAMILY and scheme in _HTTP_FAMILY)
                or type_norm == scheme
            )
            if not compatible:
                raise ValueError(
                    f"Protocol type {type_!r} is not compatible with endpoint "
                    f"scheme in {endpoint!r} (scheme={scheme!r}); supported "
                    f"families are ws/wss and http/https"
                )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_mappings(
        field_data: Any,
    ) -> Optional[List[Mapping[str, Any]]]:
        """Extract mappings list from v2.2 field (dict with 'mappings' key or direct list)."""
        if field_data is None:
            return None
        if isinstance(field_data, Mapping):
            mappings = field_data.get("mappings")
            if mappings is None:
                return None
            if not isinstance(mappings, list):
                raise ValueError("'mappings' must be a list")
            return [dict(m) for m in mappings]
        if isinstance(field_data, list):
            return [dict(m) for m in field_data]
        return None

    @staticmethod
    def _build_protocol_from_openpi(
        openpi: Mapping[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Build protocol dict from v2.2 openpi config.

        Note: any legacy ``openpi.api_key`` field is deliberately NOT propagated.
        The api_key field was removed to avoid leaking secrets through the
        persisted config file; authentication must be supplied externally.
        """
        host = str(openpi.get("host", ""))
        if not host:
            return None
        port = openpi.get("port")

        # If host already carries a scheme, it must be one we support
        # (http/https/ws/wss). An unsupported scheme (e.g. ftp://) would
        # otherwise be mistaken for a scheme-less host and get a bogus
        # "ws://ftp://..." endpoint.
        if "://" in host and not _SCHEME_PATTERN.match(host):
            raise ValueError(
                f"Unsupported protocol scheme in openpi host {host!r}; "
                f"expected one of http, https, ws, wss"
            )

        endpoint = host
        if port is not None:
            endpoint = f"{host}:{port}"

        # Ensure endpoint has a scheme
        if not _SCHEME_PATTERN.match(endpoint):
            endpoint = f"ws://{endpoint}"

        protocol: Dict[str, Any] = {"endpoint": endpoint}
        return protocol

    @staticmethod
    def _parse_transform_list(
        data: Any,
    ) -> Optional[List[TransformSpec]]:
        """Parse a transform entry into a list of TransformSpec.

        Supports the same shorthand formats as ``config_mapper``:
          - plain string name e.g. ``"json_pack"``
          - single-key mapping e.g. ``{"json_pack": {"ensure_ascii": false}}``
          - list pipeline mixing strings and single-key mappings.

        For backward compatibility, the legacy dict-key form
        ``[{"name": ..., "config": ...}]`` (single or as list items) is also
        accepted.
        """
        if data is None:
            return None
        # Legacy dict-key form: {"name": ..., "config": ...}
        if isinstance(data, Mapping) and "name" in data:
            return [
                TransformSpec(name=str(data["name"]), config=data.get("config"))
            ]
        if (
            isinstance(data, list)
            and data
            and all(isinstance(i, Mapping) and "name" in i for i in data)
        ):
            return [
                TransformSpec(name=str(i["name"]), config=i.get("config"))
                for i in data
            ]
        return _to_transform_specs_or_none(data)


def normalize_config(
    raw_config: Mapping[str, Any], host_url: str = ""
) -> NormalizedConfig:
    """Convenience function to normalize config."""
    return CloudConfigNormalizer().normalize(raw_config, host_url)
