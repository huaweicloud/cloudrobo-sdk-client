"""SDK configuration model using dataclasses for type safety and extensibility."""

from __future__ import annotations

import base64
import os
import socket
from collections.abc import Mapping as ABCMapping
from collections.abc import Sequence as ABCSequence
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence, Set, Tuple, Union

import yaml

from cloudrobo_r2c.common.credential_bundle import (
    CredentialBundle,
    pem_bytes_requires_password,
    pem_file_requires_password,
)

PROTOCOL_ZENOH = "zenoh"
ENDPOINT_ROLE = "connect"

DEFAULT_PUBLISHER_RELIABILITY = "RELIABLE"
DEFAULT_PUBLISHER_CONGESTION_CONTROL = "DROP"
DEFAULT_PUBLISHER_PRIORITY = "REAL_TIME"
DEFAULT_PUBLISHER_RELIABILITY_BY_MESSAGE = {
    "observations": "RELIABLE",
    "actions": "RELIABLE",
    "joint_states": "BEST_EFFORT",
    "end_effector_states": "BEST_EFFORT",
    "localization_states": "BEST_EFFORT",
    "imu_states": "BEST_EFFORT",
    "heartbeats": "BEST_EFFORT",
}
DEFAULT_PUBLISHER_CONGESTION_CONTROL_BY_MESSAGE = {
    "observations": "DROP",
    "actions": "DROP",
}
DEFAULT_PUBLISHER_PRIORITY_BY_MESSAGE: Dict[str, str] = {
    "observations": "REAL_TIME",
    "actions": "REAL_TIME",
    "joint_states": "DATA",
    "end_effector_states": "DATA",
    "localization_states": "DATA",
    "imu_states": "DATA",
    "heartbeats": "BACKGROUND",
}

SUBSCRIBER_HANDLER_CALLBACK = "callback"
SUBSCRIBER_HANDLER_FIFO = "fifo"
SUBSCRIBER_HANDLER_RING = "ring"
SUPPORTED_SUBSCRIBER_HANDLERS = frozenset(
    {
        SUBSCRIBER_HANDLER_CALLBACK,
        SUBSCRIBER_HANDLER_FIFO,
        SUBSCRIBER_HANDLER_RING,
    }
)

SUPPORTED_MESSAGE_TYPES = frozenset(
    {
        "observations",
        "actions",
        "observation",
        "action_chunk",
        "robot_meta",
        "joint_states",
        "end_effector_states",
        "localization_states",
        "imu_states",
        "heartbeats",
    }
)

DEFAULT_SUBSCRIBER_HANDLER_BY_MESSAGE: Dict[str, str] = {}
DEFAULT_SUBSCRIBER_HANDLER_CAPACITY_BY_MESSAGE: Dict[str, int] = {}
DEFAULT_PREDECLARE_KEYEXPR_ENABLED = False
DEFAULT_PREDECLARE_KEYEXPR_BY_MESSAGE: Tuple[str, ...] = ()

DEFAULT_PERF_CONFIG_FILENAME = "default_zenoh_perf.yaml"
BUNDLE_PERF_CONFIG_FILENAME = "perf.yaml"

ENCRYPTED_PRIVATE_KEY_PEM_HEADER = b"-----BEGIN ENCRYPTED PRIVATE KEY-----"


@dataclass(frozen=True)
class AuthenticationConfig:
    """Defines client authentication methods and credential path information."""

    method: str
    credential_path: Optional[str] = None
    description: Optional[str] = None


@dataclass(frozen=True)
class TlsConfig:
    """TLS / mTLS configuration for Zenoh connection."""

    enabled: bool = False
    root_ca_certificate: Optional[str] = None
    root_ca_certificate_base64: Optional[str] = None
    enable_mtls: bool = False
    connect_private_key: Optional[str] = None
    connect_private_key_base64: Optional[str] = None
    connect_certificate: Optional[str] = None
    connect_certificate_base64: Optional[str] = None
    listen_certificate: Optional[str] = None
    listen_certificate_base64: Optional[str] = None
    listen_private_key: Optional[str] = None
    listen_private_key_base64: Optional[str] = None
    verify_name_on_connect: Optional[bool] = None
    close_link_on_expiration: Optional[bool] = None


@dataclass(frozen=True)
class ZenohConnectConfig:
    """Zenoh connect-specific runtime configuration."""

    endpoints: Sequence[str] = field(default_factory=tuple)
    exit_on_failure: Optional[bool] = None
    timeout_ms: Optional[int] = None


@dataclass(frozen=True)
class ClientConfig:
    """Describes all parameters required for client connection."""

    project_id: str
    device_id: str
    client_id: str
    endpoint_role: str = field(default=ENDPOINT_ROLE)
    endpoints: Sequence[str] = field(default_factory=tuple)
    protocol: str = field(default=PROTOCOL_ZENOH)
    mode: str = field(default="peer")

    publisher_reliability: Optional[str] = DEFAULT_PUBLISHER_RELIABILITY
    publisher_congestion_control: Optional[str] = DEFAULT_PUBLISHER_CONGESTION_CONTROL
    publisher_priority: Optional[str] = DEFAULT_PUBLISHER_PRIORITY
    publisher_reliability_by_message: Dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_PUBLISHER_RELIABILITY_BY_MESSAGE)
    )
    publisher_congestion_control_by_message: Dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_PUBLISHER_CONGESTION_CONTROL_BY_MESSAGE)
    )
    publisher_priority_by_message: Dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_PUBLISHER_PRIORITY_BY_MESSAGE)
    )

    subscriber_handler_by_message: Dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_SUBSCRIBER_HANDLER_BY_MESSAGE)
    )
    subscriber_handler_capacity_by_message: Dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_SUBSCRIBER_HANDLER_CAPACITY_BY_MESSAGE)
    )

    predeclare_keyexpr_enabled: bool = DEFAULT_PREDECLARE_KEYEXPR_ENABLED
    predeclare_keyexpr_by_message: Tuple[str, ...] = field(
        default_factory=lambda: tuple(DEFAULT_PREDECLARE_KEYEXPR_BY_MESSAGE)
    )

    authentication: Optional[AuthenticationConfig] = None
    connect: Optional[ZenohConnectConfig] = None
    tls: Optional[TlsConfig] = None

    @classmethod
    def from_yaml(
        cls,
        path: str,
        project_id: Optional[str],
        device_id: Optional[str],
        client_id: Optional[str],
        *,
        endpoints: Optional[Sequence[str]] = None,
        mode: Optional[str] = None,
    ) -> "ClientConfig":
        """Load full client configuration from a YAML file.

        *endpoints* and *mode*, when provided, override the YAML values
        so CLI flags take precedence over file defaults.
        """
        data = cls._load_yaml_mapping_file(path, display_name=path)

        if endpoints is not None:
            raw_endpoints = list(endpoints)
        else:
            raw_endpoints = data.get("endpoints")
        endpoints = raw_endpoints if isinstance(raw_endpoints, list) else []

        auth_data = data.get("authentication")
        auth_config = None
        if auth_data:
            auth_config = AuthenticationConfig(
                method=auth_data.get("method"),
                credential_path=auth_data.get("credential_path"),
                description=auth_data.get("description"),
            )

        connect_data = data.get("connect")
        connect_config = None
        if connect_data:
            raw_connect_endpoints = connect_data.get("endpoints")
            connect_endpoints = (
                raw_connect_endpoints if isinstance(raw_connect_endpoints, list) else []
            )
            connect_config = ZenohConnectConfig(
                endpoints=tuple(connect_endpoints),
                exit_on_failure=cls._parse_bool_field(
                    connect_data.get("exit_on_failure"),
                    "connect.exit_on_failure",
                    allow_none=True,
                ),
                timeout_ms=connect_data.get("timeout_ms"),
            )

        tls_data = data.get("tls")
        tls_config = None
        if tls_data:
            tls_config = TlsConfig(
                enabled=cls._parse_bool_field(
                    tls_data.get("enabled", False),
                    "tls.enabled",
                    allow_none=False,
                ),
                root_ca_certificate=tls_data.get("root_ca_certificate"),
                root_ca_certificate_base64=tls_data.get("root_ca_certificate_base64"),
                enable_mtls=cls._parse_bool_field(
                    tls_data.get("enable_mtls", False),
                    "tls.enable_mtls",
                    allow_none=False,
                ),
                connect_private_key=tls_data.get("connect_private_key"),
                connect_private_key_base64=tls_data.get("connect_private_key_base64"),
                connect_certificate=tls_data.get("connect_certificate"),
                connect_certificate_base64=tls_data.get("connect_certificate_base64"),
                listen_certificate=tls_data.get("listen_certificate"),
                listen_certificate_base64=tls_data.get("listen_certificate_base64"),
                listen_private_key=tls_data.get("listen_private_key"),
                listen_private_key_base64=tls_data.get("listen_private_key_base64"),
                verify_name_on_connect=cls._parse_bool_field(
                    tls_data.get("verify_name_on_connect"),
                    "tls.verify_name_on_connect",
                    allow_none=True,
                ),
                close_link_on_expiration=cls._parse_bool_field(
                    tls_data.get("close_link_on_expiration"),
                    "tls.close_link_on_expiration",
                    allow_none=True,
                ),
            )

        perf_kwargs = cls._perf_kwargs_from_mapping(data)

        return cls(
            project_id=project_id if project_id else data.get("project_id"),
            device_id=device_id if device_id else data.get("device_id"),
            client_id=client_id if client_id else data.get("client_id"),
            endpoint_role=data.get("endpoint_role", ENDPOINT_ROLE),
            endpoints=tuple(endpoints),
            protocol=data.get("protocol", PROTOCOL_ZENOH),
            mode=mode if mode is not None else data.get("mode", "peer"),
            authentication=auth_config,
            connect=connect_config,
            tls=tls_config,
            **perf_kwargs,
        )

    @classmethod
    def private_key_requires_password_from_yaml(cls, path: str) -> bool:
        """Inspect YAML directly and determine whether TLS private key is encrypted."""
        data = cls._load_yaml_mapping_file(path, display_name=path)
        tls_data = data.get("tls")
        if not isinstance(tls_data, ABCMapping):
            return False

        base64_value = tls_data.get("connect_private_key_base64")
        if isinstance(base64_value, str) and base64_value.strip():
            try:
                pem_bytes = base64.b64decode(base64_value)
            except Exception as e:
                raise ValueError(
                    f"Failed to decode tls.connect_private_key_base64 from {path}"
                ) from e
            return pem_bytes_requires_password(pem_bytes)

        file_value = tls_data.get("connect_private_key")
        if isinstance(file_value, str) and file_value.strip():
            key_path = file_value.strip()
            if not os.path.isabs(key_path):
                key_path = os.path.abspath(
                    os.path.join(os.path.dirname(os.path.abspath(path)), key_path)
                )
            return pem_file_requires_password(key_path)

        return False

    @classmethod
    def from_bundle(
        cls,
        bundle: CredentialBundle,
        client_id: Optional[str] = None,
        private_key_password: Optional[Union[str, bytes]] = None,
    ) -> "ClientConfig":
        """Build ClientConfig from a platform credential bundle."""
        resolved_client_id = client_id or cls._generate_default_client_id(
            bundle.identity.robot_id
        )

        connect_cfg = ZenohConnectConfig(
            endpoints=tuple(bundle.zenoh.connect_endpoints),
            exit_on_failure=bundle.zenoh.exit_on_failure,
            timeout_ms=bundle.zenoh.timeout_ms,
        )

        tls_cfg = cls._build_tls_config_from_bundle(
            bundle,
            private_key_password=private_key_password,
        )

        perf_data = cls._load_effective_perf_mapping(bundle)
        perf_kwargs = cls._perf_kwargs_from_mapping(perf_data)

        return cls(
            project_id=bundle.identity.account_id,
            device_id=bundle.identity.robot_id,
            client_id=resolved_client_id,
            endpoint_role=ENDPOINT_ROLE,
            endpoints=tuple(bundle.zenoh.connect_endpoints),
            protocol=PROTOCOL_ZENOH,
            mode=bundle.zenoh.mode or "peer",
            authentication=AuthenticationConfig(
                method="mtls",
                credential_path=bundle.paths.base_dir,
                description="Loaded from platform credential bundle",
            ),
            connect=connect_cfg,
            tls=tls_cfg,
            **perf_kwargs,
        )

    @classmethod
    def _build_tls_config_from_bundle(
        cls,
        bundle: CredentialBundle,
        private_key_password: Optional[Union[str, bytes]] = None,
    ) -> TlsConfig:
        """Build TlsConfig from bundle, optionally decrypting encrypted key in memory."""
        tls_enabled = bool(bundle.zenoh.root_ca_certificate)

        connect_private_key, connect_private_key_base64 = (
            cls._resolve_connect_private_key_material(
                bundle.zenoh.connect_private_key,
                private_key_password=private_key_password,
            )
        )

        return TlsConfig(
            enabled=tls_enabled,
            root_ca_certificate=bundle.zenoh.root_ca_certificate,
            enable_mtls=bundle.zenoh.enable_mtls,
            connect_private_key=connect_private_key,
            connect_private_key_base64=connect_private_key_base64,
            connect_certificate=bundle.zenoh.connect_certificate,
            verify_name_on_connect=bundle.zenoh.verify_name_on_connect,
            close_link_on_expiration=bundle.zenoh.close_link_on_expiration,
        )

    @classmethod
    def _resolve_connect_private_key_material(
        cls,
        private_key_path: Optional[str],
        private_key_password: Optional[Union[str, bytes]] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Resolve connect private key material for bundle mode."""
        if not private_key_path:
            return None, None

        if not os.path.isfile(private_key_path):
            return private_key_path, None

        pem_bytes = cls._read_file_bytes(private_key_path)
        if not cls._is_encrypted_private_key_pem(pem_bytes):
            return private_key_path, None

        if private_key_password is None:
            raise ValueError(
                "Encrypted private key detected in bundle, but private_key_password was not provided"
            )

        private_key_base64 = cls._decrypt_private_key_to_base64(
            pem_bytes,
            private_key_password,
        )
        return None, private_key_base64

    @staticmethod
    def _read_file_bytes(path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    @staticmethod
    def _is_encrypted_private_key_pem(pem_bytes: bytes) -> bool:
        return pem_bytes_requires_password(pem_bytes)

    @staticmethod
    def _decode_private_key_password(
        private_key_password: Union[str, bytes],
    ) -> bytes:
        if isinstance(private_key_password, bytes):
            password_bytes = private_key_password
        elif isinstance(private_key_password, str):
            password_bytes = private_key_password.encode("utf-8")
        else:
            raise ValueError(
                "private_key_password must be str or bytes when encrypted private key is used"
            )

        if not password_bytes:
            raise ValueError(
                "private_key_password cannot be empty when encrypted private key is used"
            )

        return password_bytes

    @classmethod
    def _decrypt_private_key_to_base64(
        cls,
        encrypted_pem_bytes: bytes,
        private_key_password: Union[str, bytes],
    ) -> str:
        try:
            from cryptography.hazmat.primitives import serialization
        except ImportError as e:
            raise ValueError(
                "Encrypted private key support requires the 'cryptography' package"
            ) from e

        password_bytes = cls._decode_private_key_password(private_key_password)

        try:
            private_key = serialization.load_pem_private_key(
                encrypted_pem_bytes,
                password=password_bytes,
            )
        except (TypeError, ValueError) as e:
            raise ValueError(
                "Failed to decrypt encrypted private key: incorrect password or unsupported encrypted key format"
            ) from e

        plain_pem_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return base64.b64encode(plain_pem_bytes).decode("ascii")
    
    @staticmethod
    def _generate_default_client_id(device_id: str) -> str:
        device = str(device_id or "device").strip() or "device"
        host = socket.gethostname().strip() or "host"
        safe_host = "".join(
            ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in host
        )
        safe_device = "".join(
            ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in device
        )
        return f"python-client-{safe_device}-{safe_host}"

    def resolved_endpoints(self) -> Sequence[str]:
        """Return effective endpoints with new config taking precedence."""
        if self.connect and self.connect.endpoints:
            return tuple(self.connect.endpoints)
        return tuple(self.endpoints or ())

    @classmethod
    def _load_effective_perf_mapping(cls, bundle: CredentialBundle) -> Dict[str, Any]:
        """Load and merge built-in default perf config with optional bundle override."""
        default_perf = cls._load_default_perf_mapping()
        bundle_perf = cls._load_bundle_perf_mapping(bundle)
        return cls._merge_perf_mappings(default_perf, bundle_perf)

    @classmethod
    def _load_default_perf_mapping(cls) -> Dict[str, Any]:
        """Load SDK built-in default performance config."""
        path = cls._resolve_default_perf_yaml_path()
        if not path or not os.path.isfile(path):
            return {}
        return cls._load_yaml_mapping_file(path, display_name=path)

    @classmethod
    def _load_bundle_perf_mapping(cls, bundle: CredentialBundle) -> Dict[str, Any]:
        """Load bundle-local perf.yaml if present."""
        perf_path = getattr(bundle.paths, "perf_yaml", None)
        if not perf_path or not os.path.isfile(perf_path):
            return {}
        return cls._load_yaml_mapping_file(perf_path, display_name=perf_path)

    @classmethod
    def _resolve_default_perf_yaml_path(cls) -> Optional[str]:
        """Resolve default_zenoh_perf.yaml from the installed package data."""
        from cloudrobo_r2c.common.config_path import resolve_config_path

        return resolve_config_path(DEFAULT_PERF_CONFIG_FILENAME)

    @staticmethod
    def _load_yaml_mapping_file(path: str, display_name: str) -> Dict[str, Any]:
        """Load YAML file and ensure root is a mapping."""
        if not os.path.exists(path):
            raise FileNotFoundError(f"Config file not found: {display_name}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        data = data or {}
        if not isinstance(data, dict):
            raise ValueError(f"YAML root must be a mapping/object: {display_name}")

        return data

    @staticmethod
    def _merge_perf_mappings(
        base: Dict[str, Any],
        overlay: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Merge perf config mappings."""
        merged: Dict[str, Any] = dict(base or {})

        for key, value in (overlay or {}).items():
            base_value = merged.get(key)
            if isinstance(base_value, ABCMapping) and isinstance(value, ABCMapping):
                new_value = dict(base_value)
                new_value.update(dict(value))
                merged[key] = new_value
            else:
                merged[key] = value

        return merged

    @classmethod
    def _perf_kwargs_from_mapping(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract performance-related ClientConfig kwargs from a mapping."""
        return {
            "publisher_reliability": data.get(
                "publisher_reliability",
                DEFAULT_PUBLISHER_RELIABILITY,
            ),
            "publisher_congestion_control": data.get(
                "publisher_congestion_control",
                DEFAULT_PUBLISHER_CONGESTION_CONTROL,
            ),
            "publisher_priority": data.get(
                "publisher_priority",
                DEFAULT_PUBLISHER_PRIORITY,
            ),
            "publisher_reliability_by_message": cls._normalize_str_mapping(
                data.get(
                    "publisher_reliability_by_message",
                    DEFAULT_PUBLISHER_RELIABILITY_BY_MESSAGE,
                )
            ),
            "publisher_congestion_control_by_message": cls._normalize_str_mapping(
                data.get(
                    "publisher_congestion_control_by_message",
                    DEFAULT_PUBLISHER_CONGESTION_CONTROL_BY_MESSAGE,
                )
            ),
            "publisher_priority_by_message": cls._normalize_str_mapping(
                data.get(
                    "publisher_priority_by_message",
                    DEFAULT_PUBLISHER_PRIORITY_BY_MESSAGE,
                )
            ),
            "subscriber_handler_by_message": cls._normalize_str_mapping(
                data.get(
                    "subscriber_handler_by_message",
                    DEFAULT_SUBSCRIBER_HANDLER_BY_MESSAGE,
                )
            ),
            "subscriber_handler_capacity_by_message": cls._normalize_int_mapping(
                data.get(
                    "subscriber_handler_capacity_by_message",
                    DEFAULT_SUBSCRIBER_HANDLER_CAPACITY_BY_MESSAGE,
                )
            ),
            "predeclare_keyexpr_enabled": cls._parse_bool_field(
                data.get(
                    "predeclare_keyexpr_enabled",
                    DEFAULT_PREDECLARE_KEYEXPR_ENABLED,
                ),
                "predeclare_keyexpr_enabled",
                allow_none=False,
            ),
            "predeclare_keyexpr_by_message": cls._normalize_str_sequence(
                data.get(
                    "predeclare_keyexpr_by_message",
                    DEFAULT_PREDECLARE_KEYEXPR_BY_MESSAGE,
                )
            ),
        }

    @staticmethod
    def _normalize_str_mapping(value: Any) -> Dict[str, str]:
        """Normalize YAML mapping values into Dict[str, str]."""
        if value is None:
            return {}
        if not isinstance(value, ABCMapping):
            raise ValueError("publisher/subscriber per-message options must be a mapping")
        return {str(k): str(v) for k, v in value.items()}

    @staticmethod
    def _normalize_int_mapping(value: Any) -> Dict[str, int]:
        """Normalize YAML mapping values into Dict[str, int]."""
        if value is None:
            return {}
        if not isinstance(value, ABCMapping):
            raise ValueError("subscriber handler capacity options must be a mapping")

        out: Dict[str, int] = {}
        for k, v in value.items():
            try:
                out[str(k)] = int(v)
            except (TypeError, ValueError) as e:
                raise ValueError(
                    f"subscriber handler capacity for {k!r} must be an integer"
                ) from e
        return out

    @staticmethod
    def _normalize_str_sequence(value: Any) -> Tuple[str, ...]:
        """Normalize YAML sequences into Tuple[str, ...]."""
        if value is None:
            return ()
        if isinstance(value, (str, bytes)):
            raise ValueError("predeclare_keyexpr_by_message must be a sequence of strings")
        if not isinstance(value, ABCSequence):
            raise ValueError("predeclare_keyexpr_by_message must be a sequence of strings")
        return tuple(str(item) for item in value)

    @staticmethod
    def _parse_bool_field(
        value: Any,
        field_name: str,
        *,
        allow_none: bool,
    ) -> Optional[bool]:
        """Strictly parse a boolean field."""
        if value is None:
            if allow_none:
                return None
            raise ValueError(f"{field_name} must be a boolean, got NoneType")

        if isinstance(value, bool):
            return value

        raise ValueError(
            f"{field_name} must be a boolean, got {type(value).__name__}"
        )

    def validate(self) -> None:
        """Perform basic static validation."""
        effective_endpoints = self.resolved_endpoints()

        self._validate_required_fields()
        self._validate_protocol()
        self._validate_endpoint_role()
        self._validate_mode()
        self._validate_endpoints(effective_endpoints)
        self._validate_connect_config()
        self._validate_message_type_keys()
        self._validate_subscriber_handler_config()
        self._validate_predeclare_config()
        self._validate_tls_config(effective_endpoints)

    def _validate_required_fields(self) -> None:
        if not self.project_id:
            raise ValueError("project_id cannot be empty")
        if not self.device_id:
            raise ValueError("device_id cannot be empty")
        if not self.client_id:
            raise ValueError("client_id cannot be empty")

    def _validate_protocol(self) -> None:
        if self.protocol != PROTOCOL_ZENOH:
            raise ValueError(f"protocol not supported: {self.protocol!r}")

    def _validate_endpoint_role(self) -> None:
        if self.endpoint_role not in ("connect", "listen"):
            raise ValueError(
                f"endpoint_role must be 'connect' or 'listen', got: {self.endpoint_role!r}"
            )

    def _validate_mode(self) -> None:
        if self.mode not in ("peer", "client"):
            raise ValueError(f"mode must be 'peer' or 'client', got: {self.mode!r}")

    def _validate_endpoints(self, endpoints: Sequence[str]) -> None:
        for ep in endpoints:
            if not isinstance(ep, str) or not ep.strip():
                raise ValueError("each endpoint must be a non-empty string")

        if self.endpoint_role == "connect" and self.mode == "client" and not endpoints:
            raise ValueError(
                "endpoints cannot be empty when mode='client' and endpoint_role='connect'"
            )

    def _validate_connect_config(self) -> None:
        if not self.connect:
            return

        if self.connect.timeout_ms is not None and int(self.connect.timeout_ms) < 0:
            raise ValueError("connect.timeout_ms must be >= 0")

        if (
            self.connect.exit_on_failure is not None
            and not isinstance(self.connect.exit_on_failure, bool)
        ):
            raise ValueError("connect.exit_on_failure must be a bool")

    def _validate_message_type_keys(self) -> None:
        self._validate_mapping_keys(
            self.publisher_reliability_by_message,
            "publisher_reliability_by_message",
        )
        self._validate_mapping_keys(
            self.publisher_congestion_control_by_message,
            "publisher_congestion_control_by_message",
        )
        self._validate_mapping_keys(
            self.publisher_priority_by_message,
            "publisher_priority_by_message",
        )
        self._validate_mapping_keys(
            self.subscriber_handler_by_message,
            "subscriber_handler_by_message",
        )
        self._validate_mapping_keys(
            self.subscriber_handler_capacity_by_message,
            "subscriber_handler_capacity_by_message",
        )

    def _validate_mapping_keys(
        self,
        mapping: Dict[str, Any],
        field_name: str,
    ) -> None:
        for key in mapping.keys():
            if str(key) not in SUPPORTED_MESSAGE_TYPES:
                raise ValueError(
                    f"{field_name} contains unsupported message type: {key!r}"
                )

    def _validate_subscriber_handler_config(self) -> None:
        all_keys: Set[str] = set(self.subscriber_handler_by_message.keys()) | set(
            self.subscriber_handler_capacity_by_message.keys()
        )

        for message_type in all_keys:
            mode = str(
                self.subscriber_handler_by_message.get(
                    message_type,
                    SUBSCRIBER_HANDLER_CALLBACK,
                )
            ).strip().lower()
            capacity = self.subscriber_handler_capacity_by_message.get(message_type)

            if mode not in SUPPORTED_SUBSCRIBER_HANDLERS:
                raise ValueError(
                    f"subscriber handler for {message_type!r} must be one of "
                    f"{sorted(SUPPORTED_SUBSCRIBER_HANDLERS)}, got: {mode!r}"
                )

            if mode in (SUBSCRIBER_HANDLER_FIFO, SUBSCRIBER_HANDLER_RING):
                if capacity is None:
                    raise ValueError(
                        f"subscriber handler capacity is required when handler for "
                        f"{message_type!r} is {mode!r}"
                    )
                if int(capacity) <= 0:
                    raise ValueError(
                        f"subscriber handler capacity for {message_type!r} must be > 0"
                    )
            elif capacity is not None:
                raise ValueError(
                    f"subscriber handler capacity for {message_type!r} is only valid "
                    f"when handler is {SUBSCRIBER_HANDLER_FIFO!r} or "
                    f"{SUBSCRIBER_HANDLER_RING!r}"
                )

    def _validate_predeclare_config(self) -> None:
        if not isinstance(self.predeclare_keyexpr_enabled, bool):
            raise ValueError("predeclare_keyexpr_enabled must be a bool")

        for message_type in self.predeclare_keyexpr_by_message:
            if message_type not in SUPPORTED_MESSAGE_TYPES:
                raise ValueError(
                    "predeclare_keyexpr_by_message contains unsupported message type: "
                    f"{message_type!r}"
                )

    def _validate_tls_config(self, endpoints: Sequence[str]) -> None:
        if not self.tls:
            return

        self._validate_tls_enabled()
        self._validate_mtls(endpoints)

    def _validate_tls_enabled(self) -> None:
        if not self.tls or not self.tls.enabled:
            return

        self._validate_file_or_base64_field(
            file_value=self.tls.root_ca_certificate,
            base64_value=self.tls.root_ca_certificate_base64,
            file_field_name="tls.root_ca_certificate",
            base64_field_name="tls.root_ca_certificate_base64",
            required_when="tls.enabled=True",
        )

    def _validate_mtls(self, endpoints: Sequence[str]) -> None:
        if not self.tls or not self.tls.enable_mtls:
            return

        if not self.tls.enabled:
            raise ValueError("tls.enable_mtls=True requires tls.enabled=True")

        self._validate_mtls_files()
        self._validate_mtls_endpoints(endpoints)

    def _validate_mtls_files(self) -> None:
        if not self.tls:
            return

        if self.endpoint_role == "connect":
            self._validate_file_or_base64_field(
                file_value=self.tls.connect_certificate,
                base64_value=self.tls.connect_certificate_base64,
                file_field_name="tls.connect_certificate",
                base64_field_name="tls.connect_certificate_base64",
                required_when="tls.enable_mtls=True and endpoint_role='connect'",
            )

            self._validate_file_or_base64_field(
                file_value=self.tls.connect_private_key,
                base64_value=self.tls.connect_private_key_base64,
                file_field_name="tls.connect_private_key",
                base64_field_name="tls.connect_private_key_base64",
                required_when="tls.enable_mtls=True and endpoint_role='connect'",
            )
            return

        if self.endpoint_role == "listen":
            self._validate_file_or_base64_field(
                file_value=self.tls.listen_certificate,
                base64_value=self.tls.listen_certificate_base64,
                file_field_name="tls.listen_certificate",
                base64_field_name="tls.listen_certificate_base64",
                required_when="tls.enable_mtls=True and endpoint_role='listen'",
            )

            self._validate_file_or_base64_field(
                file_value=self.tls.listen_private_key,
                base64_value=self.tls.listen_private_key_base64,
                file_field_name="tls.listen_private_key",
                base64_field_name="tls.listen_private_key_base64",
                required_when="tls.enable_mtls=True and endpoint_role='listen'",
            )

    @staticmethod
    def _validate_file_or_base64_field(
        *,
        file_value: Optional[str],
        base64_value: Optional[str],
        file_field_name: str,
        base64_field_name: str,
        required_when: str,
    ) -> None:
        file_path = file_value.strip() if isinstance(file_value, str) else None
        base64_content = base64_value.strip() if isinstance(base64_value, str) else None

        has_file = bool(file_path)
        has_base64 = bool(base64_content)

        if has_file and has_base64:
            raise ValueError(
                f"{file_field_name} and {base64_field_name} cannot both be set"
            )

        if not has_file and not has_base64:
            raise ValueError(
                f"either {file_field_name} or {base64_field_name} is required when {required_when}"
            )

        if has_file and not os.path.isfile(file_path):
            raise ValueError(f"{file_field_name} file not found: {file_path}")

    def _validate_mtls_endpoints(self, endpoints: Sequence[str]) -> None:
        for ep in endpoints:
            if ep and not str(ep).strip().lower().startswith("tls/"):
                raise ValueError(
                    "mTLS connection requires endpoints using 'tls/' scheme"
                )