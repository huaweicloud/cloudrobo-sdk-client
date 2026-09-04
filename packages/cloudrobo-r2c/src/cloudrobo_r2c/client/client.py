"""Facade for creating R2C sessions from configuration."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, Optional, Union

from cloudrobo_r2c.common.config import PROTOCOL_ZENOH, ClientConfig
from cloudrobo_r2c.common.credential_bundle import (
    BundleResourceContext,
    CredentialBundleLoader,
)
from cloudrobo_r2c.common.diagnostics import (
    ConnectionInfo,
    ConnectionStage,
    LastErrorCategory,
    sanitize_endpoints,
    sanitize_error,
)
from cloudrobo_r2c.common.exceptions import R2CConnectionError
from cloudrobo_r2c.transport import ITransport, ZenohTransport

from .session import R2CSession

logger = logging.getLogger(__name__)


class R2CClient:
    """SDK facade that selects transport implementation based on configuration."""

    @staticmethod
    def connect(
        target: Union[ClientConfig, str, os.PathLike],
        client_id: Optional[str] = None,
        private_key_password: Optional[Union[str, bytes]] = None,
    ) -> R2CSession:
        """Unified formal connection entry.

        Supported inputs:
          - ClientConfig
          - platform credential bundle zip path
          - unpacked platform credential bundle directory path
        """
        if isinstance(target, ClientConfig):
            if client_id:
                logger.warning(
                    "client_id argument is ignored when target is already a ClientConfig"
                )
            if private_key_password is not None:
                logger.warning(
                    "private_key_password argument is ignored when target is already a ClientConfig"
                )
            return R2CClient._connect_from_config(target, resource_context=None)

        if isinstance(target, (str, os.PathLike)):
            return R2CClient._connect_from_bundle_path(
                os.fspath(target),
                client_id=client_id,
                private_key_password=private_key_password,
            )

        raise TypeError(
            "R2CClient.connect() expects ClientConfig or a platform credential "
            "zip/directory path"
        )

    @staticmethod
    async def connect_async(
        target: Union[ClientConfig, str, os.PathLike],
        client_id: Optional[str] = None,
        private_key_password: Optional[Union[str, bytes]] = None,
    ) -> R2CSession:
        """Async version of connect."""
        start = time.perf_counter()
        session = await asyncio.to_thread(
            R2CClient.connect,
            target,
            client_id,
            private_key_password,
        )
        logger.debug(
            "connect_async completed in %.2f ms (project_id=%s, device_id=%s)",
            (time.perf_counter() - start) * 1000.0,
            session.project_id,
            session.device_id,
        )
        return session

    @staticmethod
    def _connect_from_bundle_path(
        path: str,
        client_id: Optional[str] = None,
        private_key_password: Optional[Union[str, bytes]] = None,
    ) -> R2CSession:
        start = time.perf_counter()
        logger.debug("Loading credential bundle from path: %s", path)
        bundle, resource_context = CredentialBundleLoader.load(path)
        try:
            config = ClientConfig.from_bundle(
                bundle,
                client_id=client_id,
                private_key_password=private_key_password,
            )
        except Exception:
            if resource_context:
                resource_context.cleanup()
            raise

        logger.debug(
            "Credential bundle loaded and config created in %.2f ms",
            (time.perf_counter() - start) * 1000.0,
        )
        return R2CClient._connect_from_config(config, resource_context=resource_context)

    @staticmethod
    def _connect_from_config(
        config: ClientConfig,
        resource_context: Optional[BundleResourceContext] = None,
    ) -> R2CSession:
        """Establish connection using given configuration and return session object."""
        start = time.perf_counter()
        endpoints = config.resolved_endpoints()
        endpoints_safe = sanitize_endpoints(endpoints)
        endpoint_pairs = list(zip(list(endpoints or []), endpoints_safe))

        info = ConnectionInfo(
            protocol=config.protocol,
            mode=config.mode,
            endpoints=endpoints_safe,
            connected=False,
            stage=ConnectionStage.INIT,
            tls_enabled=bool(config.tls and config.tls.enabled),
            mtls_enabled=bool(config.tls and config.tls.enable_mtls),
            verify_name_on_connect=(
                config.tls.verify_name_on_connect if config.tls else None
            ),
        )

        try:
            info.stage = ConnectionStage.CONFIG_VALIDATION
            validate_start = time.perf_counter()
            config.validate()
            logger.debug(
                "Client config validation completed in %.2f ms (project_id=%s, device_id=%s)",
                (time.perf_counter() - validate_start) * 1000.0,
                config.project_id,
                config.device_id,
            )
        except Exception as e:
            info.connected = False
            info.last_error_category = LastErrorCategory.CONFIG
            info.last_error = sanitize_error(str(e), endpoint_pairs=endpoint_pairs)
            if resource_context:
                resource_context.cleanup()
            message = R2CClient._build_config_validation_error_message(info, e)
            raise R2CConnectionError(message, info=info) from e

        if config.protocol == PROTOCOL_ZENOH:
            transport: ITransport = ZenohTransport()
        else:
            if resource_context:
                resource_context.cleanup()
            raise NotImplementedError(
                f"Protocol {config.protocol} is not currently supported"
            )

        try:
            connect_start = time.perf_counter()
            transport.connect(config)
            logger.debug(
                "Transport.connect completed in %.2f ms (protocol=%s)",
                (time.perf_counter() - connect_start) * 1000.0,
                config.protocol,
            )
        except R2CConnectionError as e:
            # Snapshot failure context BEFORE close(), because close() may mutate
            # the transport's ConnectionInfo stage to CLOSED.
            effective_info = R2CClient._snapshot_connection_info(e.info or info)

            try:
                transport.close()
            except Exception as close_exc:
                logger.debug(
                    "transport.close() failed after connect error: %s",
                    type(close_exc).__name__,
                )

            if resource_context:
                resource_context.cleanup()

            message = R2CClient._build_connect_failure_message(effective_info, e)
            raise R2CConnectionError(message, info=effective_info) from e
        except Exception as e:
            try:
                transport.close()
            except Exception as close_exc:
                logger.debug(
                    "transport.close() failed after connect error: %s",
                    type(close_exc).__name__,
                )

            info.stage = ConnectionStage.FAILED
            info.connected = False
            info.last_error_category = LastErrorCategory.UNKNOWN
            info.last_error = sanitize_error(str(e), endpoint_pairs=endpoint_pairs)

            if resource_context:
                resource_context.cleanup()

            message = R2CClient._build_connect_failure_message(info, e)
            raise R2CConnectionError(message, info=info) from e

        sess_info: Optional[Dict[str, Any]] = None
        try:
            sess_info = transport.connection_info()
        except Exception:
            sess_info = info.to_safe_dict()

        session = R2CSession(
            transport=transport,
            client_id=config.client_id,
            project_id=config.project_id,
            device_id=config.device_id,
            _conn_info=sess_info,
            _resource_context=resource_context,
        )
        logger.debug(
            "R2C session created in %.2f ms (project_id=%s, device_id=%s)",
            (time.perf_counter() - start) * 1000.0,
            config.project_id,
            config.device_id,
        )
        return session

    @staticmethod
    def _snapshot_connection_info(info: ConnectionInfo) -> ConnectionInfo:
        """Create an immutable snapshot of connection info for error reporting."""
        return ConnectionInfo(**info.to_safe_dict())

    @staticmethod
    def _build_config_validation_error_message(
        info: ConnectionInfo,
        error: BaseException,
    ) -> str:
        """Build a user-facing configuration validation error message."""
        detail = (info.last_error or str(error) or "unknown configuration error").strip()
        return f"Invalid client configuration: {detail}"

    @staticmethod
    def _build_connect_failure_message(
        info: ConnectionInfo,
        error: BaseException,
    ) -> str:
        """Build a user-facing connection failure message with actionable hints."""
        stage = (info.stage or ConnectionStage.FAILED).strip()
        detail = (info.last_error or str(error) or "unknown error").strip()
        category = info.last_error_category

        message = f"Failed to establish connection during {stage}."
        hint = R2CClient._hint_for_error_category(category)
        if hint:
            message += f" Possible causes: {hint}."
        message += f" Details: {detail}"
        return message

    @staticmethod
    def _hint_for_error_category(category: Optional[str]) -> str:
        """Return actionable user-facing guidance for a connection error category."""
        if category == LastErrorCategory.NETWORK:
            return (
                "check endpoint reachability, gateway availability, "
                "and network connectivity"
            )
        if category == LastErrorCategory.HANDSHAKE:
            return (
                "check CA certificate, TLS trust chain, or mTLS client "
                "certificate/private key; if using a platform credential "
                "bundle, the credential may have expired or been revoked "
                "(check the platform console for device status)"
            )
        if category == LastErrorCategory.GATEWAY:
            return "check gateway authorization or permission settings"
        if category == LastErrorCategory.CONFIG:
            return "check connection-related configuration fields"
        if category == LastErrorCategory.CONNECT:
            return (
                "check endpoint reachability, gateway availability, TLS/CA trust "
                "settings, and if mTLS is enabled, the client certificate/private "
                "key; also verify that the target host and port are correct"
            )
        if category == LastErrorCategory.UNKNOWN:
            return "check endpoint, TLS files, and gateway logs"
        return ""