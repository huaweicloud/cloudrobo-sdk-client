"""Route resolver for multi-route support: selects and executes routes."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from cloudrobo_r2c.core.config_mapper import ConfigDrivenMapper, TransformSpec
from cloudrobo_r2c.core.interfaces import IPolicyClient
from cloudrobo_r2c.inference.client_factory import create_policy_client
from cloudrobo_r2c.inference.r2c_config_normalizer import NormalizedConfig
from cloudrobo_r2c.inference.route_config import (
    ProtocolConfig,
    RouteConfig,
)

logger = logging.getLogger(__name__)


@dataclass
class Route:
    """Runtime route wrapper with policy client and translators."""

    config: RouteConfig
    policy_client: IPolicyClient
    r2c_to_model_mapper: Optional[ConfigDrivenMapper] = None
    model_to_r2c_mapper: Optional[ConfigDrivenMapper] = None
    default_serial_transform: Optional[List[TransformSpec]] = None
    default_deserial_transform: Optional[List[TransformSpec]] = None


class RouteResolver:
    """Selects active route and manages stop-condition checking."""

    def __init__(
        self,
        normalized_config: NormalizedConfig,
        api_key: Optional[str] = None,
    ) -> None:
        self._routes: Dict[str, Route] = {}
        self._active_route_name: str = ""
        self._iter_count: int = 0
        self._start_time: float = time.time()
        self._default_route=normalized_config.default_route
        self._build_routes(normalized_config, api_key=api_key)

    # ------------------------------------------------------------------
    # Build & select
    # ------------------------------------------------------------------

    def _build_routes(
        self,
        config: NormalizedConfig,
        api_key: Optional[str] = None,
    ) -> None:
        """Build Route objects from NormalizedConfig.

        ``api_key`` is a runtime authentication secret (never persisted to disk)
        forwarded to each route's policy client for the auth path.
        """
        for route_config in config.routes:
            kwargs: Dict[str, Any] = {"protocol": route_config.protocol}
            if api_key is not None:
                kwargs["api_key"] = api_key
            policy_client = create_policy_client(**kwargs)
            r2c_to_model_mapper: Optional[ConfigDrivenMapper] = None
            if route_config.r2c_to_model:
                r2c_to_model_mapper = ConfigDrivenMapper.from_rule_mappings(
                    route_config.r2c_to_model
                )
            model_to_r2c_mapper: Optional[ConfigDrivenMapper] = None
            if route_config.model_to_r2c:
                model_to_r2c_mapper = ConfigDrivenMapper.from_rule_mappings(
                    route_config.model_to_r2c
                )
            route = Route(
                config=route_config,
                policy_client=policy_client,
                r2c_to_model_mapper=r2c_to_model_mapper,
                model_to_r2c_mapper=model_to_r2c_mapper,
                default_serial_transform=config.default_serial_transform,
                default_deserial_transform=config.default_deserial_transform,
            )
            self._routes[route_config.name] = route

    def select_route(
        self,
        config: NormalizedConfig,
        route_name: Optional[str],
    ) -> None:
        """Select active route: --route > default_route > single route > ERROR."""
        if route_name:
            if route_name not in self._routes:
                raise ValueError(
                    f"Route {route_name!r} not found. "
                    f"Available: {list(self._routes.keys())}"
                )
            self._active_route_name = route_name
        elif len(self._routes) == 1:
            self._active_route_name = next(iter(self._routes))
        elif self._default_route:
            if self._default_route not in self._routes:
                raise ValueError(
                    f"Default route {config.default_route!r} not found."
                )
            self._active_route_name = self._default_route
        else:
            raise ValueError(
                f"Multiple routes found ({list(self._routes.keys())}) "
                "but no route specified and no default_route set."
            )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def active_route(self) -> Route:
        return self._routes[self._active_route_name]

    @property
    def active_route_name(self) -> str:
        return self._active_route_name

    @property
    def routes(self) -> Dict[str, Route]:
        return dict(self._routes)

    @property
    def iter_count(self) -> int:
        return self._iter_count

    def get_route(self, name: str) -> Route:
        if name not in self._routes:
            raise ValueError(f"Route {name!r} not found.")
        return self._routes[name]

    def set_active_route(self, name: str) -> None:
        """Switch the active route at runtime.

        The adapter reads ``active_route`` on every observation, so switching
        here takes effect on the next observations message.
        """
        if name not in self._routes:
            raise ValueError(
                f"Route {name!r} not found. Available: {list(self._routes.keys())}"
            )
        self._active_route_name = name

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close all policy clients."""
        for route in self._routes.values():
            try:
                route.policy_client.close()
            except Exception:
                logger.warning(
                    "Error closing policy client for route %r",
                    route.config.name,
                    exc_info=True,
                )
