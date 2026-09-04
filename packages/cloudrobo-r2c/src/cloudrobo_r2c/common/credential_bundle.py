"""Platform credential bundle loader for mTLS-based connection bootstrap."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Union

from cloudrobo_r2c.common.exceptions import CredentialBundleError


PathLike = Union[str, os.PathLike]


def pem_bytes_requires_password(pem_bytes: bytes) -> bool:
    """Best-effort detection for encrypted PEM private keys."""
    text = pem_bytes.decode("utf-8", errors="ignore")
    head = text[:4096]
    normalized = head.upper()

    if "-----BEGIN ENCRYPTED PRIVATE KEY-----" in normalized:
        return True

    if "PROC-TYPE: 4,ENCRYPTED" in normalized:
        return True

    if "DEK-INFO:" in normalized:
        return True

    return False


def pem_file_requires_password(path: str | os.PathLike) -> bool:
    file_path = Path(path)
    if not file_path.is_file():
        return False
    return pem_bytes_requires_password(file_path.read_bytes())


@dataclass(frozen=True)
class BundlePaths:
    """Resolved file paths for a credential bundle."""

    base_dir: str
    device_info_json: str
    zenoh_json: str
    ca_pem: str
    cert_pem: str
    key_pem: str
    perf_yaml: Optional[str] = None


@dataclass(frozen=True)
class DeviceIdentity:
    """Identity information extracted from device_info.json."""

    account_id: str
    robot_id: str
    permission_role: Optional[str] = None


@dataclass(frozen=True)
class ZenohBundleConfig:
    """Zenoh runtime configuration extracted from zenoh.json."""

    mode: str
    connect_endpoints: Tuple[str, ...]
    exit_on_failure: Optional[bool] = None
    timeout_ms: Optional[int] = None

    root_ca_certificate: Optional[str] = None
    enable_mtls: bool = False
    connect_private_key: Optional[str] = None
    connect_certificate: Optional[str] = None
    verify_name_on_connect: Optional[bool] = None
    close_link_on_expiration: Optional[bool] = None


@dataclass(frozen=True)
class CredentialBundle:
    """Normalized result produced by the platform credential bundle loader."""

    paths: BundlePaths
    identity: DeviceIdentity
    zenoh: ZenohBundleConfig

    def requires_private_key_password(self) -> bool:
        """Return whether the bundle's client private key is encrypted."""
        if self.zenoh.connect_private_key:
            return pem_file_requires_password(self.zenoh.connect_private_key)
        return False


@dataclass
class BundleResourceContext:
    """Owns temporary resources created while loading a credential bundle."""

    temp_dir: Optional[str] = None
    temp_dir_obj: Optional[tempfile.TemporaryDirectory] = None

    @classmethod
    def create(cls, prefix: str = "r2c_bundle_") -> "BundleResourceContext":
        temp_dir_obj = tempfile.TemporaryDirectory(prefix=prefix)
        return cls(
            temp_dir=temp_dir_obj.name,
            temp_dir_obj=temp_dir_obj,
        )

    def cleanup(self) -> None:
        if self.temp_dir_obj is not None:
            try:
                self.temp_dir_obj.cleanup()
            finally:
                self.temp_dir_obj = None
                self.temp_dir = None
            return

        if self.temp_dir and os.path.isdir(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            self.temp_dir = None


class CredentialBundleLoader:
    """Load credential bundle from a zip package or an unpacked directory."""

    DEVICE_INFO_NAME = "device_info.json"
    ZENOH_CONFIG_NAME = "zenoh.json"

    DEFAULT_CA_NAME = "ca.pem"
    DEFAULT_CERT_NAME = "server_cert.pem"
    DEFAULT_KEY_NAME = "server_key.pem"
    PERF_CONFIG_NAME = "perf.yaml"

    @classmethod
    def load(
        cls,
        path: PathLike,
    ) -> tuple[CredentialBundle, Optional[BundleResourceContext]]:
        """Load bundle from zip file or directory.

        Returns:
            (bundle, resource_context)
        """
        path_str = os.fspath(path)
        if not path_str:
            raise CredentialBundleError("Credential bundle path cannot be empty")

        if os.path.isdir(path_str):
            bundle = cls._load_from_directory(path_str)
            return bundle, None

        if os.path.isfile(path_str) and path_str.lower().endswith(".zip"):
            return cls._load_from_zip(path_str)

        if os.path.isfile(path_str):
            raise CredentialBundleError(
                f"Unsupported credential bundle file type: {path_str!r}. Expected .zip file or directory."
            )

        raise CredentialBundleError(f"Credential bundle path not found: {path_str}")

    @classmethod
    def _load_from_zip(
        cls,
        zip_path: str,
    ) -> tuple[CredentialBundle, BundleResourceContext]:
        if not os.path.exists(zip_path):
            raise CredentialBundleError(f"Credential bundle zip not found: {zip_path}")

        if not zipfile.is_zipfile(zip_path):
            raise CredentialBundleError(f"Invalid zip file: {zip_path}")

        resource_context = BundleResourceContext.create(prefix="r2c_bundle_")
        temp_dir = resource_context.temp_dir
        if not temp_dir:
            resource_context.cleanup()
            raise CredentialBundleError(
                "Failed to create temporary directory for credential bundle"
            )

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                cls._safe_extract_zip(zf, temp_dir)

            bundle = cls._load_from_directory(temp_dir)
            return bundle, resource_context
        except Exception:
            resource_context.cleanup()
            raise

    @classmethod
    def _safe_extract_zip(cls, zf: zipfile.ZipFile, dest_dir: str) -> None:
        """Extract zip safely to avoid path traversal."""
        dest_path = Path(dest_dir).resolve()

        for member in zf.infolist():
            member_path = dest_path / member.filename
            try:
                member_path_resolved = member_path.resolve()
            except FileNotFoundError:
                member_path.parent.mkdir(parents=True, exist_ok=True)
                member_path_resolved = member_path.resolve()

            if (
                dest_path not in member_path_resolved.parents
                and member_path_resolved != dest_path
            ):
                raise CredentialBundleError(
                    f"Unsafe zip entry detected: {member.filename}"
                )

        zf.extractall(dest_dir)

    @classmethod
    def _load_from_directory(cls, base_dir: str) -> CredentialBundle:
        if not os.path.isdir(base_dir):
            raise CredentialBundleError(
                f"Credential bundle directory not found: {base_dir}"
            )

        paths = cls._resolve_required_files(base_dir)
        identity = cls._parse_device_info(paths.device_info_json)
        zenoh_cfg = cls._parse_zenoh_json(paths.zenoh_json, base_dir, paths)

        return CredentialBundle(
            paths=paths,
            identity=identity,
            zenoh=zenoh_cfg,
        )

    @classmethod
    def _resolve_required_files(cls, base_dir: str) -> BundlePaths:
        base_dir = os.path.abspath(base_dir)

        device_info_json = cls._find_single_file(base_dir, cls.DEVICE_INFO_NAME)
        zenoh_json = cls._find_single_file(base_dir, cls.ZENOH_CONFIG_NAME)

        ca_pem = cls._find_optional_cert_file(
            base_dir,
            explicit_relative_candidates=(
                cls.DEFAULT_CA_NAME,
                f"certs/{cls.DEFAULT_CA_NAME}",
            ),
        )
        cert_pem = cls._find_optional_cert_file(
            base_dir,
            explicit_relative_candidates=(
                cls.DEFAULT_CERT_NAME,
                f"certs/{cls.DEFAULT_CERT_NAME}",
            ),
        )
        key_pem = cls._find_optional_cert_file(
            base_dir,
            explicit_relative_candidates=(
                cls.DEFAULT_KEY_NAME,
                f"certs/{cls.DEFAULT_KEY_NAME}",
            ),
        )
        perf_yaml = cls._find_optional_file(base_dir, cls.PERF_CONFIG_NAME)

        if not ca_pem:
            raise CredentialBundleError("Missing certificate file: ca.pem")
        if not cert_pem:
            raise CredentialBundleError("Missing certificate file: server_cert.pem")
        if not key_pem:
            raise CredentialBundleError("Missing certificate file: server_key.pem")

        return BundlePaths(
            base_dir=base_dir,
            device_info_json=device_info_json,
            zenoh_json=zenoh_json,
            ca_pem=ca_pem,
            cert_pem=cert_pem,
            key_pem=key_pem,
            perf_yaml=perf_yaml,
        )

    @classmethod
    def _find_single_file(cls, base_dir: str, filename: str) -> str:
        direct = os.path.join(base_dir, filename)
        if os.path.isfile(direct):
            return os.path.abspath(direct)

        matches = []
        for root, _, files in os.walk(base_dir):
            if filename in files:
                matches.append(os.path.abspath(os.path.join(root, filename)))

        if not matches:
            raise CredentialBundleError(
                f"Required file not found in credential bundle: {filename}"
            )

        matches.sort()
        return matches[0]

    @classmethod
    def _find_optional_file(cls, base_dir: str, filename: str) -> Optional[str]:
        direct = os.path.join(base_dir, filename)
        if os.path.isfile(direct):
            return os.path.abspath(direct)

        matches = []
        for root, _, files in os.walk(base_dir):
            if filename in files:
                matches.append(os.path.abspath(os.path.join(root, filename)))

        if not matches:
            return None

        matches.sort()
        return matches[0]

    @classmethod
    def _find_optional_cert_file(
        cls,
        base_dir: str,
        explicit_relative_candidates: tuple[str, ...],
    ) -> Optional[str]:
        for rel in explicit_relative_candidates:
            p = os.path.join(base_dir, rel)
            if os.path.isfile(p):
                return os.path.abspath(p)

        target_name = os.path.basename(explicit_relative_candidates[0])
        matches = []
        for root, _, files in os.walk(base_dir):
            if target_name in files:
                matches.append(os.path.abspath(os.path.join(root, target_name)))

        if not matches:
            return None

        matches.sort()
        return matches[0]

    @classmethod
    def _parse_device_info(cls, path: str) -> DeviceIdentity:
        data = cls._load_json(path, "device_info.json")

        account_id = str(data.get("account_id") or "").strip()
        robot_id = str(data.get("robot_id") or "").strip()
        permission_role = data.get("permission_role")

        if not account_id:
            raise CredentialBundleError(
                "device_info.json missing required field: account_id"
            )
        if not robot_id:
            raise CredentialBundleError(
                "device_info.json missing required field: robot_id"
            )

        return DeviceIdentity(
            account_id=account_id,
            robot_id=robot_id,
            permission_role=(
                str(permission_role).strip() if permission_role is not None else None
            ),
        )

    @classmethod
    def _parse_zenoh_json(
        cls,
        path: str,
        base_dir: str,
        bundle_paths: BundlePaths,
    ) -> ZenohBundleConfig:
        data = cls._load_json(path, "zenoh.json")

        mode = str(data.get("mode") or "").strip() or "peer"

        connect_data = data.get("connect") or {}
        raw_endpoints = connect_data.get("endpoints") or []
        if not isinstance(raw_endpoints, list):
            raise CredentialBundleError(
                "zenoh.json field connect.endpoints must be a list"
            )

        endpoints = tuple(str(ep).strip() for ep in raw_endpoints if str(ep).strip())
        exit_on_failure = cls._parse_optional_bool(
            connect_data.get("exit_on_failure"),
            "connect.exit_on_failure",
        )
        timeout_ms = connect_data.get("timeout_ms")

        tls_data = ((data.get("transport") or {}).get("link", {}).get("tls", {}))

        root_ca_certificate = cls._resolve_cert_path(
            tls_data.get("root_ca_certificate"),
            base_dir=base_dir,
            bundle_paths=bundle_paths,
            fallback_path=bundle_paths.ca_pem,
        )
        connect_private_key = cls._resolve_cert_path(
            tls_data.get("connect_private_key"),
            base_dir=base_dir,
            bundle_paths=bundle_paths,
            fallback_path=bundle_paths.key_pem,
        )
        connect_certificate = cls._resolve_cert_path(
            tls_data.get("connect_certificate"),
            base_dir=base_dir,
            bundle_paths=bundle_paths,
            fallback_path=bundle_paths.cert_pem,
        )

        enable_mtls = cls._parse_optional_bool(
            tls_data.get("enable_mtls"),
            "transport.link.tls.enable_mtls",
        )
        if enable_mtls is None:
            enable_mtls = False

        verify_name_on_connect = cls._parse_optional_bool(
            tls_data.get("verify_name_on_connect"),
            "transport.link.tls.verify_name_on_connect",
        )
        close_link_on_expiration = cls._parse_optional_bool(
            tls_data.get("close_link_on_expiration"),
            "transport.link.tls.close_link_on_expiration",
        )

        return ZenohBundleConfig(
            mode=mode,
            connect_endpoints=endpoints,
            exit_on_failure=exit_on_failure,
            timeout_ms=int(timeout_ms) if timeout_ms is not None else None,
            root_ca_certificate=root_ca_certificate,
            enable_mtls=enable_mtls,
            connect_private_key=connect_private_key,
            connect_certificate=connect_certificate,
            verify_name_on_connect=verify_name_on_connect,
            close_link_on_expiration=close_link_on_expiration,
        )

    @classmethod
    def _resolve_cert_path(
        cls,
        raw_path: Optional[object],
        *,
        base_dir: str,
        bundle_paths: BundlePaths,
        fallback_path: str,
    ) -> Optional[str]:
        if raw_path is None or str(raw_path).strip() == "":
            return os.path.abspath(fallback_path) if fallback_path else None

        raw_path_str = str(raw_path).strip()

        if os.path.isabs(raw_path_str) and os.path.isfile(raw_path_str):
            return os.path.abspath(raw_path_str)

        candidate = os.path.abspath(os.path.join(base_dir, raw_path_str))
        if os.path.isfile(candidate):
            return candidate

        normalized_name = os.path.basename(raw_path_str)
        fallback_name = os.path.basename(fallback_path)

        if normalized_name == fallback_name and os.path.isfile(fallback_path):
            return os.path.abspath(fallback_path)

        for root, _, files in os.walk(base_dir):
            if normalized_name in files:
                return os.path.abspath(os.path.join(root, normalized_name))

        raise CredentialBundleError(
            f"Referenced certificate file not found: {raw_path_str!r}"
        )

    @staticmethod
    def _parse_optional_bool(
        value: object,
        field_name: str,
    ) -> Optional[bool]:
        """Strictly parse an optional boolean field from zenoh.json."""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        raise CredentialBundleError(
            f"zenoh.json field {field_name} must be a boolean, got {type(value).__name__}"
        )

    @classmethod
    def _load_json(cls, path: str, display_name: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise CredentialBundleError(f"Invalid JSON in {display_name}: {e}") from e
        except OSError as e:
            raise CredentialBundleError(f"Failed to read {display_name}: {e}") from e

        if not isinstance(data, dict):
            raise CredentialBundleError(f"{display_name} must contain a JSON object")

        return data


def bundle_requires_private_key_password(path: PathLike) -> bool:
    """Return whether the bundle's client private key is encrypted."""
    bundle, resource_context = CredentialBundleLoader.load(path)
    try:
        return bundle.requires_private_key_password()
    finally:
        if resource_context is not None:
            resource_context.cleanup()