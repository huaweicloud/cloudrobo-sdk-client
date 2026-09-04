"""Zenoh transport layer implementation."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import zenoh

from cloudrobo_r2c.common.config import (
    SUBSCRIBER_HANDLER_CALLBACK,
    SUBSCRIBER_HANDLER_FIFO,
    SUBSCRIBER_HANDLER_RING,
    ClientConfig,
)
from cloudrobo_r2c.common.diagnostics import (
    ConnectionInfo,
    ConnectionStage,
    LastErrorCategory,
    sanitize_endpoints,
    sanitize_error,
)
from cloudrobo_r2c.common.exceptions import AuthenticationError, R2CConnectionError
from cloudrobo_r2c.common.utils.logging_sanitizer import fmt_size
from cloudrobo_r2c.transport.base import ITransport, TransportCallback

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TopicDefinition:
    """Definition of a standard SDK topic."""

    message_type: str
    relative_path: str


@dataclass(frozen=True)
class SubscriberHandlerSpec:
    """Resolved subscriber strategy for a message type."""

    mode: str = SUBSCRIBER_HANDLER_CALLBACK
    capacity: Optional[int] = None


@dataclass
class SubscriberWorker:
    """Background worker draining a zenoh channel-backed subscriber."""

    topic: str
    stop_event: threading.Event
    thread: threading.Thread


TOPIC_DEFINITIONS: Dict[str, TopicDefinition] = {
    "observations": TopicDefinition(
        message_type="observations",
        relative_path="inference/observations",
    ),
    "actions": TopicDefinition(
        message_type="actions",
        relative_path="inference/actions",
    ),
    "observation": TopicDefinition(
        message_type="observation",
        relative_path="observation",
    ),
    "action_chunk": TopicDefinition(
        message_type="action_chunk",
        relative_path="action_chunk",
    ),
    "robot_meta": TopicDefinition(
        message_type="robot_meta",
        relative_path="robot_meta",
    ),
    "joint_states": TopicDefinition(
        message_type="joint_states",
        relative_path="state/joint_states",
    ),
    "end_effector_states": TopicDefinition(
        message_type="end_effector_states",
        relative_path="state/end_effector_states",
    ),
    "localization_states": TopicDefinition(
        message_type="localization_states",
        relative_path="state/localization_states",
    ),
    "imu_states": TopicDefinition(
        message_type="imu_states",
        relative_path="state/imu_states",
    ),
    "heartbeats": TopicDefinition(
        message_type="heartbeats",
        relative_path="state/heartbeats",
    ),
}


class ZenohTransport(ITransport):
    """Transport implementation for interacting with cloud via Zenoh protocol."""

    def __init__(self) -> None:
        self._session: Optional[zenoh.Session] = None
        self._publishers: Dict[str, zenoh.Publisher] = {}
        self._subscribers: List[zenoh.Subscriber] = []
        self._default_publisher_options: Dict[str, Any] = {}
        self._publisher_options_by_message: Dict[str, Dict[str, Any]] = {}
        self._subscriber_specs_by_message: Dict[str, SubscriberHandlerSpec] = {}
        self._subscriber_workers: List[SubscriberWorker] = []
        self._declared_keyexprs: Dict[str, Any] = {}
        self._endpoint_pairs: List[Tuple[str, str]] = []

        # Connectivity listener support (available on newer zenoh-python)
        self._transport_events_listener: Optional[Any] = None
        self._link_events_listener: Optional[Any] = None
        self._connectivity_listener_thread: Optional[threading.Thread] = None
        self._connectivity_listener_stop_event: Optional[threading.Event] = None
        self._connectivity_listener_enabled: bool = False
        self._disconnect_reported: bool = False
        self._initial_connect_reported: bool = False

        # Protect all mutable transport lifecycle state.
        self._state_lock = threading.RLock()

        self._conn_info: ConnectionInfo = ConnectionInfo(protocol="zenoh")
        self._last_error_raw: Optional[BaseException] = None

    def connection_info(self) -> Dict[str, Any]:
        with self._state_lock:
            return self._conn_info.to_safe_dict()

    @staticmethod
    def _classify_error(e: BaseException) -> str:
        # AuthenticationError is part of the public exception hierarchy but
        # is not currently raised by the zenoh connect path (auth failures
        # surface via string markers below). Kept as an isinstance guard so
        # future explicit raises classify correctly without code changes.
        if isinstance(e, AuthenticationError):
            return LastErrorCategory.HANDSHAKE

        msg = str(e).lower()

        if isinstance(e, ConnectionRefusedError):
            return LastErrorCategory.NETWORK
        if isinstance(e, TimeoutError):
            return LastErrorCategory.CONNECT

        network_markers = (
            "connection refused",
            "no route to host",
            "host unreachable",
            "network is unreachable",
            "temporary failure in name resolution",
            "name or service not known",
            "cannot assign requested address",
            "failed to resolve",
            "dns",
        )
        if any(marker in msg for marker in network_markers):
            return LastErrorCategory.NETWORK

        handshake_markers = (
            "handshake",
            "certificate verify failed",
            "unknown ca",
            "bad certificate",
            "expired certificate",
            "certificate expired",
            "invalid certificate",
            "private key",
            "peer did not return a certificate",
            "tls alert",
            "ssl error",
            "x509",
        )
        if any(marker in msg for marker in handshake_markers):
            return LastErrorCategory.HANDSHAKE

        gateway_markers = (
            "unauthorized",
            "forbidden",
            "permission denied",
            "access denied",
            "authentication failed",
            "not allowed",
        )
        if any(marker in msg for marker in gateway_markers):
            return LastErrorCategory.GATEWAY

        connect_markers = (
            "unable to connect to any of",
            "unable to connect",
            "timeout",
            "timed out",
            "deadline has elapsed",
        )
        if any(marker in msg for marker in connect_markers):
            return LastErrorCategory.CONNECT

        if isinstance(e, OSError):
            return LastErrorCategory.NETWORK

        return LastErrorCategory.UNKNOWN

    @staticmethod
    def _insert_json5(config: zenoh.Config, key: str, value: object) -> None:
        config.insert_json5(key, json.dumps(value))

    @staticmethod
    def _effective_endpoints(config: ClientConfig) -> Sequence[str]:
        if config.connect and config.connect.endpoints:
            return config.connect.endpoints
        return config.endpoints or []

    def _reset_connection_diagnostics(
        self, config: ClientConfig, endpoints_safe: Sequence[str]
    ) -> None:
        self._conn_info.protocol = "zenoh"
        self._conn_info.mode = config.mode
        self._conn_info.endpoints = list(endpoints_safe)
        self._conn_info.connected = False
        self._conn_info.last_error = None
        self._conn_info.last_error_category = None
        self._conn_info.stage = ConnectionStage.INIT
        self._conn_info.tls_enabled = bool(config.tls and config.tls.enabled)
        self._conn_info.mtls_enabled = bool(config.tls and config.tls.enable_mtls)
        self._conn_info.verify_name_on_connect = (
            config.tls.verify_name_on_connect if config.tls else None
        )
        self._last_error_raw = None
        self._disconnect_reported = False
        self._initial_connect_reported = False

    def _apply_endpoints(
        self, z_config: zenoh.Config, config: ClientConfig, endpoints: Sequence[str]
    ) -> None:
        if not endpoints:
            return
        endpoint_role = config.endpoint_role
        if endpoint_role == "connect":
            self._insert_json5(z_config, "connect/endpoints", list(endpoints))
            return
        self._insert_json5(z_config, "listen/endpoints", list(endpoints))

    def _apply_connect_options(
        self, z_config: zenoh.Config, config: ClientConfig
    ) -> None:
        if not config.connect:
            return
        if config.connect.exit_on_failure is not None:
            self._insert_json5(
                z_config,
                "connect/exit_on_failure",
                bool(config.connect.exit_on_failure),
            )
        if config.connect.timeout_ms is not None:
            self._insert_json5(
                z_config, "connect/timeout_ms", int(config.connect.timeout_ms)
            )

    def _apply_tls_options(self, z_config: zenoh.Config, config: ClientConfig) -> None:
        if not config.tls or not config.tls.enabled:
            return
        if config.tls.root_ca_certificate:
            self._insert_json5(
                z_config,
                "transport/link/tls/root_ca_certificate",
                config.tls.root_ca_certificate,
            )
        if config.tls.root_ca_certificate_base64:
            self._insert_json5(
                z_config,
                "transport/link/tls/root_ca_certificate_base64",
                config.tls.root_ca_certificate_base64,
            )

        self._insert_json5(
            z_config,
            "transport/link/tls/enable_mtls",
            bool(config.tls.enable_mtls),
        )

        if config.tls.connect_private_key:
            self._insert_json5(
                z_config,
                "transport/link/tls/connect_private_key",
                config.tls.connect_private_key,
            )
        if config.tls.connect_private_key_base64:
            self._insert_json5(
                z_config,
                "transport/link/tls/connect_private_key_base64",
                config.tls.connect_private_key_base64,
            )
        if config.tls.connect_certificate:
            self._insert_json5(
                z_config,
                "transport/link/tls/connect_certificate",
                config.tls.connect_certificate,
            )
        if config.tls.connect_certificate_base64:
            self._insert_json5(
                z_config,
                "transport/link/tls/connect_certificate_base64",
                config.tls.connect_certificate_base64,
            )
        if config.tls.listen_certificate:
            self._insert_json5(
                z_config,
                "transport/link/tls/listen_certificate",
                config.tls.listen_certificate,
            )
        if config.tls.listen_certificate_base64:
            self._insert_json5(
                z_config,
                "transport/link/tls/listen_certificate_base64",
                config.tls.listen_certificate_base64,
            )
        if config.tls.listen_private_key:
            self._insert_json5(
                z_config,
                "transport/link/tls/listen_private_key",
                config.tls.listen_private_key,
            )
        if config.tls.listen_private_key_base64:
            self._insert_json5(
                z_config,
                "transport/link/tls/listen_private_key_base64",
                config.tls.listen_private_key_base64,
            )
        if config.tls.verify_name_on_connect is not None:
            self._insert_json5(
                z_config,
                "transport/link/tls/verify_name_on_connect",
                bool(config.tls.verify_name_on_connect),
            )
        if config.tls.close_link_on_expiration is not None:
            self._insert_json5(
                z_config,
                "transport/link/tls/close_link_on_expiration",
                bool(config.tls.close_link_on_expiration),
            )

    def _build_zenoh_config(
        self, config: ClientConfig, effective_endpoints: Sequence[str]
    ) -> zenoh.Config:
        z_config = zenoh.Config()
        self._apply_endpoints(z_config, config, effective_endpoints)
        if config.mode:
            self._insert_json5(z_config, "mode", config.mode)
        self._apply_connect_options(z_config, config)
        self._apply_tls_options(z_config, config)
        return z_config

    def _open_session(
        self, z_config: zenoh.Config, config: ClientConfig, endpoint_count: int
    ) -> None:
        open_start = time.perf_counter()
        self._session = zenoh.open(z_config)
        logger.info(
            "Zenoh session created in %.2f ms (mode=%s, endpoints=%d)",
            (time.perf_counter() - open_start) * 1000.0,
            config.mode,
            endpoint_count,
        )

    def _handle_connect_error(
        self, error: Exception, endpoint_pairs: Sequence[tuple[str, str]]
    ) -> None:
        self._last_error_raw = error
        category = self._classify_error(error)

        # When TLS endpoints time out, the real cause is usually a
        # certificate / credential issue (expired, revoked, or platform
        # record deleted), not a network problem.  Reclassify so the
        # user-facing hint directs the user to check their credentials.
        if category == LastErrorCategory.CONNECT:
            raw_endpoints = [
                e[0] for e in (endpoint_pairs or ())
            ]
            if any(
                str(ep).startswith("tls/") for ep in raw_endpoints
            ):
                category = LastErrorCategory.HANDSHAKE

        self._conn_info.connected = False
        self._conn_info.last_error_category = category
        self._conn_info.last_error = sanitize_error(
            str(error), endpoint_pairs=list(endpoint_pairs)
        )

        raise R2CConnectionError(
            "Failed to establish Zenoh connection", info=self._conn_info
        ) from error

    def _handle_runtime_transport_error(
        self,
        operation: str,
        error: BaseException,
        topic: Optional[str] = None,
    ) -> None:
        """Record runtime transport failures after initial connect succeeds."""
        self._last_error_raw = error
        category = self._classify_error(error)

        with self._state_lock:
            if self._conn_info.stage != ConnectionStage.CLOSED:
                self._conn_info.connected = False
                self._conn_info.stage = ConnectionStage.FAILED
                self._conn_info.last_error_category = category
                self._conn_info.last_error = sanitize_error(
                    str(error),
                    endpoint_pairs=self._endpoint_pairs,
                )
            safe_info = self._conn_info.to_safe_dict()
            detail = safe_info.get("last_error") or str(error)
            self._disconnect_reported = True

        if topic:
            logger.error(
                "Zenoh runtime connection failure during %s (topic=%s, category=%s): %s | connection_info=%s",
                operation,
                topic,
                category,
                detail,
                safe_info,
            )
        else:
            logger.error(
                "Zenoh runtime connection failure during %s (category=%s): %s | connection_info=%s",
                operation,
                category,
                detail,
                safe_info,
            )

    def _supports_connectivity_listener(self) -> bool:
        if self._session is None:
            return False

        info = getattr(self._session, "info", None)
        if info is None:
            return False

        required_methods = (
            "transports",
            "links",
            "declare_transport_events_listener",
            "declare_link_events_listener",
        )
        return all(hasattr(info, name) for name in required_methods)

    def _start_connectivity_listener(self) -> None:
        if self._session is None:
            return
        if self._connectivity_listener_enabled:
            return
        if not self._supports_connectivity_listener():
            logger.warning(
                "Zenoh connectivity monitoring is NOT available: current zenoh-python "
                "runtime does not expose SessionInfo listener APIs. Connection state "
                "changes (disconnect/reconnect) will not be detected until an I/O error occurs."
            )
            return

        info = self._session.info
        try:
            self._transport_events_listener = info.declare_transport_events_listener(
                history=False
            )
            self._link_events_listener = info.declare_link_events_listener(
                history=False
            )
        except Exception as e:
            logger.warning(
                "Failed to start zenoh connectivity listeners; falling back to runtime I/O error detection only: %s",
                e,
            )
            self._transport_events_listener = None
            self._link_events_listener = None
            self._connectivity_listener_enabled = False
            return

        self._connectivity_listener_stop_event = threading.Event()
        self._connectivity_listener_thread = threading.Thread(
            target=self._connectivity_listener_loop,
            name="r2c-zenoh-connectivity-listener",
            daemon=True,
        )
        self._connectivity_listener_enabled = True
        self._connectivity_listener_thread.start()
        logger.info("Zenoh connectivity listener started")

    def _stop_connectivity_listener(self) -> None:
        stop_event = self._connectivity_listener_stop_event
        thread = self._connectivity_listener_thread
        transport_listener = self._transport_events_listener
        link_listener = self._link_events_listener

        self._connectivity_listener_enabled = False
        self._connectivity_listener_stop_event = None
        self._connectivity_listener_thread = None
        self._transport_events_listener = None
        self._link_events_listener = None

        if stop_event is not None:
            stop_event.set()

        if thread is not None:
            try:
                thread.join(timeout=1.0)
                if thread.is_alive():
                    logger.warning(
                        "Zenoh connectivity listener thread did not stop within timeout"
                    )
            except Exception as e:
                logger.warning(
                    "Failed to join zenoh connectivity listener thread: %s",
                    e,
                )

        if transport_listener is not None and hasattr(transport_listener, "undeclare"):
            try:
                transport_listener.undeclare()
            except Exception as e:
                logger.warning(
                    "Failed to undeclare zenoh transport events listener: %s",
                    e,
                )

        if link_listener is not None and hasattr(link_listener, "undeclare"):
            try:
                link_listener.undeclare()
            except Exception as e:
                logger.warning(
                    "Failed to undeclare zenoh link events listener: %s",
                    e,
                )

    def _probe_initial_connection_state(self) -> None:
        """Check current session transports/links and log connection status.

        The connectivity listener only receives events that occur AFTER it is
        registered (``history=False``).  If ``zenoh.open()`` already established
        the transport before the listener started, no PUT event will fire and
        we would never log the initial connection.  This method queries the
        current session state to fill that gap.
        """
        if self._initial_connect_reported:
            return
        if self._session is None:
            return

        info = getattr(self._session, "info", None)
        if info is None:
            return

        active_transports = self._count_active(info, "transports")
        active_links = self._count_active(info, "links")

        if active_transports > 0 or active_links > 0:
            with self._state_lock:
                self._initial_connect_reported = True
            logger.info(
                "Connected to Zenoh router successfully "
                "(transports=%d, links=%d)",
                active_transports,
                active_links,
            )
        else:
            logger.warning(
                "Zenoh session created but no active transport or link detected. "
                "Router may be unreachable Zenoh will keep retrying in the background."
            )

    @staticmethod
    def _count_active(info: Any, attr: str) -> int:
        """Count active transports or links from session info."""
        collection = getattr(info, attr, None)
        if collection is None:
            return 0
        try:
            if callable(collection):
                collection = collection()
            return sum(1 for _ in collection)
        except Exception:
            return 0

    def _try_recv_listener_event(self, listener: Any) -> Optional[Any]:
        if listener is None:
            return None
        if hasattr(listener, "try_recv"):
            return listener.try_recv()
        return None

    def _get_connectivity_event_kind(self, event: Any) -> Optional[str]:
        """Best-effort event kind extraction across zenoh-python versions."""
        candidates: List[str] = []

        kind = getattr(event, "kind", None)
        if kind is not None:
            candidates.append(str(kind))
            name = getattr(kind, "name", None)
            if name is not None:
                candidates.append(str(name))

        candidates.append(str(event))

        for text in candidates:
            normalized = text.strip().lower()
            if "delete" in normalized:
                return "DELETE"
            if "put" in normalized:
                return "PUT"

        return None

    def _mark_disconnected_from_connectivity_event(
        self,
        source: str,
        event: Any,
    ) -> None:
        event_text = str(event)

        with self._state_lock:
            if self._conn_info.stage == ConnectionStage.CLOSED:
                return

            self._conn_info.connected = False
            self._conn_info.stage = ConnectionStage.DISCONNECTED
            self._conn_info.last_error_category = LastErrorCategory.CONNECT
            self._conn_info.last_error = sanitize_error(
                event_text,
                endpoint_pairs=self._endpoint_pairs,
            )
            safe_info = self._conn_info.to_safe_dict()

            if self._disconnect_reported:
                return
            self._disconnect_reported = True

        logger.error(
            "Disconnected from Zenoh router (%s event). The session will keep retrying "
            "in the background. If the router does not recover, the robot will stop "
            "receiving actions. | event=%s",
            source,
            safe_info["last_error"],
        )

    def _mark_recovered_from_connectivity_event(
        self,
        source: str,
        event: Any,
    ) -> None:
        event_text = str(event)

        with self._state_lock:
            if self._conn_info.stage == ConnectionStage.CLOSED:
                return

            was_disconnected = (
                self._conn_info.stage == ConnectionStage.DISCONNECTED
                or not self._conn_info.connected
                or self._disconnect_reported
            )
            is_initial = not self._initial_connect_reported

            self._conn_info.connected = True
            self._conn_info.stage = ConnectionStage.READY
            self._conn_info.last_error = None
            self._conn_info.last_error_category = None
            self._disconnect_reported = False
            self._initial_connect_reported = True

        if is_initial:
            logger.info("Connected to Zenoh router successfully (%s event)", source)
        elif was_disconnected:
            logger.info(
                "Reconnected to Zenoh router after disconnection (%s event)",
                source,
            )

    def _handle_connectivity_event(self, source: str, event: Any) -> None:
        kind = self._get_connectivity_event_kind(event)
        if kind == "DELETE":
            self._mark_disconnected_from_connectivity_event(source, event)
        elif kind == "PUT":
            self._mark_recovered_from_connectivity_event(source, event)
        else:
            logger.info(
                "Unrecognized zenoh connectivity %s event: %s",
                source,
                event,
            )

    def _connectivity_listener_loop(self) -> None:
        stop_event = self._connectivity_listener_stop_event
        if stop_event is None:
            return

        logger.debug("Zenoh connectivity listener loop started")
        while not stop_event.is_set():
            try:
                event = self._try_recv_listener_event(self._transport_events_listener)
                if event is not None:
                    self._handle_connectivity_event("transport", event)

                event = self._try_recv_listener_event(self._link_events_listener)
                if event is not None:
                    self._handle_connectivity_event("link", event)

                stop_event.wait(0.1)
            except Exception as e:
                if stop_event.is_set():
                    break
                logger.warning("Zenoh connectivity listener loop error: %s", e)
                stop_event.wait(0.5)
        logger.debug("Zenoh connectivity listener loop stopped")

    def connect(self, config: ClientConfig) -> None:
        """Create Zenoh session according to given configuration.

        If a session is already open, it is closed first (including its
        subscribers and subscriber-worker threads) so that repeated
        ``connect()`` calls do not leak sessions / subscribers / worker
        threads.
        """
        start = time.perf_counter()
        effective_endpoints = self._effective_endpoints(config)
        endpoints_safe = sanitize_endpoints(effective_endpoints)
        endpoint_pairs = list(zip(list(effective_endpoints or []), endpoints_safe))
        self._endpoint_pairs = list(endpoint_pairs)

        # Tear down any pre-existing session before opening a new one. Simply
        # clearing the lists below would orphan the old session and leave the
        # subscriber-worker threads running (their stop_event never set).
        if self._session is not None:
            try:
                self.close()
            except Exception as e:
                logger.warning(
                    "Failed to close previous Zenoh session during reconnect: %s",
                    type(e).__name__,
                )

        self._reset_connection_diagnostics(config, endpoints_safe)
        self._declared_keyexprs.clear()
        self._publishers.clear()
        self._subscribers.clear()
        self._subscriber_workers.clear()
        self._default_publisher_options = {}
        self._publisher_options_by_message = {}
        self._subscriber_specs_by_message = {}
        self._stop_connectivity_listener()

        try:
            self._conn_info.stage = ConnectionStage.CONFIG_VALIDATION
            config.validate()
            z_config = self._build_zenoh_config(config, effective_endpoints)
            self._conn_info.stage = ConnectionStage.TRANSPORT_CONFIGURED

            self._conn_info.stage = ConnectionStage.OPEN_SESSION
            self._open_session(z_config, config, len(effective_endpoints))

            self._conn_info.stage = ConnectionStage.READY
            self._conn_info.connected = True
            self._conn_info.last_error = None
            self._conn_info.last_error_category = None

            self._start_connectivity_listener()

            if effective_endpoints:
                logger.info(
                    "Zenoh connecting to router(s): %s (mode=%s) waiting for connection...",
                    ", ".join(effective_endpoints),
                    config.mode,
                )
            else:
                logger.info(
                    "Zenoh scouting for routers via multicast (mode=%s) waiting for connection...",
                    config.mode,
                )

            self._probe_initial_connection_state()

            logger.debug(
                "Zenoh transport connect completed in %.2f ms",
                (time.perf_counter() - start) * 1000.0,
            )

        except Exception as e:
            self._handle_connect_error(e, endpoint_pairs)

        (
            self._default_publisher_options,
            self._publisher_options_by_message,
        ) = self._build_publisher_options(config)

    def publish(self, topic: str, payload: bytes) -> None:
        """Publish message to specified topic."""
        start = time.perf_counter()

        # Declare the publisher under the lock (it mutates _publishers), but
        # perform the blocking put() outside the lock so a slow network send
        # does not block close() (which acquires the same lock) or serialize
        # publishes across unrelated topics.
        with self._state_lock:
            if not self._session:
                raise RuntimeError("Zenoh session is not connected")

            if topic not in self._publishers:
                try:
                    declare_start = time.perf_counter()
                    publisher_options = self._resolve_publisher_options_for_topic(topic)
                    topic_ref = self._resolve_keyexpr_or_topic(topic)

                    if publisher_options:
                        try:
                            pub = self._session.declare_publisher(
                                topic_ref,
                                **publisher_options,
                            )
                        except TypeError:
                            logger.warning(
                                "Current zenoh-python does not support declare_publisher "
                                "kwargs; fallback to default publisher options."
                            )
                            pub = self._session.declare_publisher(topic_ref)
                    else:
                        pub = self._session.declare_publisher(topic_ref)

                    self._publishers[topic] = pub
                    logger.debug(
                        "Declared publisher for topic %s in %.2f ms",
                        topic,
                        (time.perf_counter() - declare_start) * 1000.0,
                    )
                except Exception as e:
                    self._handle_runtime_transport_error(
                        operation="declare_publisher",
                        error=e,
                        topic=topic,
                    )
                    raise RuntimeError(
                        f"Failed to create publisher for topic {topic}: {e}"
                    ) from e

            publisher = self._publishers[topic]

        try:
            publisher.put(payload)
            logger.debug(
                "Published topic %s in %.2f ms (%s)",
                topic,
                (time.perf_counter() - start) * 1000.0,
                fmt_size(len(payload)),
            )
        except Exception as e:
            self._handle_runtime_transport_error(
                operation="publish",
                error=e,
                topic=topic,
            )
            raise RuntimeError(f"Failed to send message to {topic}: {e}") from e

    def subscribe(self, topic: str, callback: TransportCallback) -> None:
        """Subscribe to topic and distribute messages via callback."""
        with self._state_lock:
            if not self._session:
                raise RuntimeError("Zenoh session is not connected")

            spec = self._resolve_subscriber_spec_for_topic(topic)
            topic_ref = self._resolve_keyexpr_or_topic(topic)

            if spec.mode == SUBSCRIBER_HANDLER_CALLBACK:
                try:
                    sub = self._declare_callback_subscriber(topic, topic_ref, callback)
                    self._subscribers.append(sub)
                except Exception as e:
                    self._handle_runtime_transport_error(
                        operation="declare_subscriber",
                        error=e,
                        topic=topic,
                    )
                    raise
            else:
                try:
                    sub, worker = self._declare_channel_subscriber(
                        topic, topic_ref, callback, spec
                    )
                    self._subscribers.append(sub)
                    self._subscriber_workers.append(worker)
                except Exception as e:
                    self._handle_runtime_transport_error(
                        operation="declare_subscriber",
                        error=e,
                        topic=topic,
                    )
                    raise

    def close(self) -> None:
        """Close Zenoh session and release resources."""
        start = time.perf_counter()

        with self._state_lock:
            workers = list(self._subscriber_workers)
            subscribers = list(self._subscribers)
            publishers = list(self._publishers.values())
            session = self._session

            self._subscriber_workers = []
            self._subscribers = []
            self._publishers = {}
            self._session = None
            self._default_publisher_options = {}
            self._publisher_options_by_message = {}
            self._subscriber_specs_by_message = {}
            self._declared_keyexprs = {}
            self._endpoint_pairs = []

            self._conn_info.connected = False
            self._conn_info.stage = ConnectionStage.CLOSED
            self._disconnect_reported = False

        self._stop_connectivity_listener()

        for worker in workers:
            worker.stop_event.set()

        for pub in publishers:
            try:
                pub.undeclare()
            except Exception as e:
                logger.warning("Failed to undeclare publisher during close: %s", e)

        for sub in subscribers:
            try:
                sub.undeclare()
            except Exception as e:
                logger.warning("Failed to undeclare subscriber during close: %s", e)

        for worker in workers:
            try:
                worker.thread.join(timeout=1.0)
                if worker.thread.is_alive():
                    logger.warning(
                        "Subscriber worker for topic %s did not stop within timeout",
                        worker.topic,
                    )
            except Exception as e:
                logger.warning(
                    "Failed to join subscriber worker for topic %s: %s",
                    worker.topic,
                    e,
                )

        if session:
            try:
                session.close()
            except Exception as e:
                logger.warning("Failed to close session: %s", e)

        logger.debug(
            "Zenoh transport closed in %.2f ms",
            (time.perf_counter() - start) * 1000.0,
        )

    @staticmethod
    def _build_publisher_options(
        config: ClientConfig,
    ) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
        """Build default and per-message declare_publisher kwargs from ClientConfig."""
        default_options: Dict[str, Any] = {}

        if config.publisher_reliability:
            default_options["reliability"] = ZenohTransport._resolve_zenoh_enum(
                enum_name="Reliability",
                member_name=config.publisher_reliability,
                config_key="publisher_reliability",
            )

        if config.publisher_congestion_control:
            default_options["congestion_control"] = ZenohTransport._resolve_zenoh_enum(
                enum_name="CongestionControl",
                member_name=config.publisher_congestion_control,
                config_key="publisher_congestion_control",
            )

        if config.publisher_priority:
            default_options["priority"] = ZenohTransport._resolve_zenoh_enum(
                enum_name="Priority",
                member_name=config.publisher_priority,
                config_key="publisher_priority",
            )

        options_by_message: Dict[str, Dict[str, Any]] = {}
        all_message_types = (
            set(config.publisher_reliability_by_message.keys())
            | set(config.publisher_congestion_control_by_message.keys())
            | set(config.publisher_priority_by_message.keys())
        )

        for message_type in all_message_types:
            message_options = dict(default_options)

            reliability = config.publisher_reliability_by_message.get(message_type)
            if reliability:
                message_options["reliability"] = ZenohTransport._resolve_zenoh_enum(
                    enum_name="Reliability",
                    member_name=reliability,
                    config_key=(
                        "publisher_reliability_by_message"
                        f"[{message_type}]"
                    ),
                )

            congestion_control = config.publisher_congestion_control_by_message.get(
                message_type
            )
            if congestion_control:
                message_options["congestion_control"] = (
                    ZenohTransport._resolve_zenoh_enum(
                        enum_name="CongestionControl",
                        member_name=congestion_control,
                        config_key=(
                            "publisher_congestion_control_by_message"
                            f"[{message_type}]"
                        ),
                    )
                )

            priority = config.publisher_priority_by_message.get(message_type)
            if priority:
                message_options["priority"] = (
                    ZenohTransport._resolve_zenoh_enum(
                        enum_name="Priority",
                        member_name=priority,
                        config_key=(
                            "publisher_priority_by_message"
                            f"[{message_type}]"
                        ),
                    )
                )

            options_by_message[message_type] = message_options

        return default_options, options_by_message

    @staticmethod
    def _build_subscriber_handler_specs(
        config: ClientConfig,
    ) -> Dict[str, SubscriberHandlerSpec]:
        """Build per-message subscriber handler specs from ClientConfig."""
        specs: Dict[str, SubscriberHandlerSpec] = {}
        all_message_types = set(config.subscriber_handler_by_message.keys()) | set(
            config.subscriber_handler_capacity_by_message.keys()
        )

        for message_type in all_message_types:
            mode = str(
                config.subscriber_handler_by_message.get(
                    message_type,
                    SUBSCRIBER_HANDLER_CALLBACK,
                )
            ).strip().lower()

            capacity = config.subscriber_handler_capacity_by_message.get(message_type)
            specs[message_type] = SubscriberHandlerSpec(
                mode=mode,
                capacity=(int(capacity) if capacity is not None else None),
            )

        return specs

    def _resolve_publisher_options_for_topic(self, topic: str) -> Dict[str, Any]:
        """Resolve publisher options by topic suffix (message_type)."""
        message_type = self._resolve_message_type_from_topic(topic)
        if message_type in self._publisher_options_by_message:
            return self._publisher_options_by_message[message_type]
        return self._default_publisher_options

    def _resolve_subscriber_spec_for_topic(self, topic: str) -> SubscriberHandlerSpec:
        """Resolve subscriber handler spec by topic suffix (message_type)."""
        message_type = self._resolve_message_type_from_topic(topic)
        return self._subscriber_specs_by_message.get(
            message_type,
            SubscriberHandlerSpec(),
        )

    @staticmethod
    def _resolve_message_type_from_topic(topic: str) -> str:
        """Resolve message type from topic tail."""
        return str(topic).rsplit("/", 1)[-1]

    @staticmethod
    def _resolve_zenoh_enum(enum_name: str, member_name: str, config_key: str) -> Any:
        """Resolve enum value from zenoh-python module with validation."""
        enum_cls = getattr(zenoh, enum_name, None)
        if enum_cls is None:
            raise ValueError(
                f"zenoh.{enum_name} not found. Please upgrade zenoh-python to use "
                f"{config_key}."
            )

        normalized = member_name.strip().upper()
        value = getattr(enum_cls, normalized, None)
        if value is None:
            raise ValueError(
                f"Invalid {config_key}: {member_name}. "
                f"Expected one of zenoh.{enum_name} members."
            )

        return value

    def _predeclare_keyexprs(self, config: ClientConfig) -> None:
        """Predeclare configured standard topics as KeyExpr objects."""
        if not config.predeclare_keyexpr_enabled:
            return
        if not self._session:
            raise RuntimeError("Zenoh session is not connected")

        for message_type in config.predeclare_keyexpr_by_message:
            topic = self._build_standard_topic(
                project_id=config.project_id,
                device_id=config.device_id,
                message_type=message_type,
            )
            if topic in self._declared_keyexprs:
                continue

            keyexpr = self._session.declare_keyexpr(topic)
            self._declared_keyexprs[topic] = keyexpr
            logger.debug("Predeclared key expression for topic %s", topic)

    def _resolve_keyexpr_or_topic(self, topic: str) -> Any:
        """Return declared keyexpr if cached; otherwise return the original topic string."""
        return self._declared_keyexprs.get(topic, topic)

    @staticmethod
    def _build_standard_topic(
        project_id: str,
        device_id: str,
        message_type: str,
    ) -> str:
        definition = TOPIC_DEFINITIONS.get(message_type)
        if definition is None:
            raise ValueError(f"Unsupported message type for predeclare: {message_type!r}")
        return f"{project_id}/{device_id}/{definition.relative_path}"

    def _declare_callback_subscriber(
        self,
        topic: str,
        topic_ref: Any,
        callback: TransportCallback,
    ) -> zenoh.Subscriber:
        """Declare a normal callback-based subscriber.

        Caller must hold _state_lock.
        """
        if not self._session:
            raise RuntimeError("Zenoh session is not connected")

        def _wrapper(sample: zenoh.Sample) -> None:
            start = time.perf_counter()
            try:
                payload = self._sample_payload_to_bytes(sample.payload)
                callback(payload)
                logger.debug(
                    "Processed subscriber callback for %s in %.2f ms (%s)",
                    topic,
                    (time.perf_counter() - start) * 1000.0,
                    fmt_size(len(payload)),
                )
            except Exception as e:
                logger.error("Error in message callback for %s: %s", topic, e)

        try:
            return self._session.declare_subscriber(topic_ref, _wrapper)
        except Exception as e:
            raise RuntimeError(f"Failed to subscribe to topic {topic}: {e}") from e

    def _declare_channel_subscriber(
        self,
        topic: str,
        topic_ref: Any,
        callback: TransportCallback,
        spec: SubscriberHandlerSpec,
    ) -> tuple[zenoh.Subscriber, SubscriberWorker]:
        """Declare a channel-backed subscriber and drain it in a worker thread.

        Caller must hold _state_lock.
        """
        if not self._session:
            raise RuntimeError("Zenoh session is not connected")
        if spec.capacity is None:
            raise ValueError(
                f"Subscriber handler capacity is required for topic {topic!r} "
                f"when using mode {spec.mode!r}"
            )

        try:
            if spec.mode == SUBSCRIBER_HANDLER_FIFO:
                handler = zenoh.handlers.FifoChannel(spec.capacity)
            elif spec.mode == SUBSCRIBER_HANDLER_RING:
                handler = zenoh.handlers.RingChannel(spec.capacity)
            else:
                raise ValueError(f"Unsupported subscriber handler mode: {spec.mode!r}")

            sub = self._session.declare_subscriber(topic_ref, handler)
            worker = self._create_and_start_subscriber_worker(topic, sub, callback)
            return sub, worker
        except Exception as e:
            raise RuntimeError(
                f"Failed to subscribe to topic {topic} with handler "
                f"{spec.mode}({spec.capacity}): {e}"
            ) from e

    def _create_and_start_subscriber_worker(
        self,
        topic: str,
        subscriber: zenoh.Subscriber,
        callback: TransportCallback,
    ) -> SubscriberWorker:
        """Create and start a worker for a channel-backed subscriber."""
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._subscriber_worker_loop,
            name=f"r2c-zenoh-sub-{len(self._subscriber_workers) + 1}",
            args=(topic, subscriber, callback, stop_event),
            daemon=True,
        )
        thread.start()
        return SubscriberWorker(
            topic=topic,
            stop_event=stop_event,
            thread=thread,
        )

    def _subscriber_worker_loop(
        self,
        topic: str,
        subscriber: zenoh.Subscriber,
        callback: TransportCallback,
        stop_event: threading.Event,
    ) -> None:
        """Drain samples from a handler-backed subscriber without blocking forever."""
        while not stop_event.is_set():
            try:
                sample = subscriber.try_recv()
            except Exception as e:
                if stop_event.is_set():
                    break
                self._handle_runtime_transport_error(
                    operation="subscriber.try_recv",
                    error=e,
                    topic=topic,
                )
                stop_event.wait(timeout=0.05)
                continue

            if sample is None:
                stop_event.wait(timeout=0.01)
                continue

            start = time.perf_counter()
            try:
                payload = self._sample_payload_to_bytes(sample.payload)
                callback(payload)
                logger.debug(
                    "Processed subscriber worker callback for %s in %.2f ms (%s)",
                    topic,
                    (time.perf_counter() - start) * 1000.0,
                    fmt_size(len(payload)),
                )
            except Exception as e:
                logger.error("Error in subscriber worker for %s: %s", topic, e)

    @staticmethod
    def _sample_payload_to_bytes(payload: Any) -> bytes:
        """Convert zenoh payload object to plain bytes."""
        if isinstance(payload, bytes):
            return payload
        if isinstance(payload, bytearray):
            return bytes(payload)
        if hasattr(payload, "to_bytes"):
            return payload.to_bytes()
        return bytes(payload)