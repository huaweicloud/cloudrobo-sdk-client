import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from cloudrobo_core.cli.config_utils import deep_merge, ensure_user_config, USER_CONFIG_PATH

logger = logging.getLogger(__name__)

_config_loaded_logged = False
_ak_plain_warned = False
_sk_plain_warned = False

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config.yaml",
)


class Config:
    @staticmethod
    def _parse_bool(val: Any) -> Optional[bool]:
        if val is None:
            return None
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            val_lower = val.lower()
            if val_lower in ("1", "true", "yes"):
                return True
            if val_lower in ("0", "false", "no"):
                return False
            return None
        return None

    def __init__(self, config_path: Optional[str] = None):
        self._config_path = config_path or DEFAULT_CONFIG_PATH
        self._data: Dict[str, Any] = {}
        self._ak_cache: Optional[str] = None
        self._sk_cache: Optional[str] = None
        self._ak_decrypt_failed: bool = False
        self._sk_decrypt_failed: bool = False
        self._load()

    def _load(self):
        ensure_user_config()

        base_data: Dict[str, Any] = {}
        if os.path.exists(self._config_path):
            with open(self._config_path, "r", encoding="utf-8") as f:
                base_data = yaml.safe_load(f) or {}

        user_data: Dict[str, Any] = {}
        if USER_CONFIG_PATH.exists():
            try:
                with open(USER_CONFIG_PATH, "r", encoding="utf-8") as f:
                    user_data = yaml.safe_load(f) or {}
                global _config_loaded_logged
                if not _config_loaded_logged:
                    logger.debug("Merged user config from %s", USER_CONFIG_PATH)
                    _config_loaded_logged = True
            except Exception as e:
                logger.warning("Failed to load user config %s: %s", USER_CONFIG_PATH, e)

        self._data = deep_merge(base_data, user_data)

        if not self._data and not os.path.exists(self._config_path):
            if self._config_path != DEFAULT_CONFIG_PATH:
                logger.warning("Config file not found: %s, using defaults", self._config_path)

    @property
    def raw(self) -> Dict[str, Any]:
        return self._data

    @property
    def cloudrobo(self) -> Dict[str, Any]:
        return self._data.get("cloudrobo", {})

    @property
    def endpoints(self) -> Dict[str, str]:
        return self.cloudrobo.get("endpoints", {})

    def get_endpoint(self, service: str) -> str:
        endpoint = self.endpoints.get(service, "")
        env_key = f"CLOUDROBO_ENDPOINT_{service.upper().replace('-', '_')}"
        endpoint = os.environ.get(env_key, endpoint)
        if "{region}" in endpoint:
            endpoint = endpoint.replace("{region}", self.region)
        return endpoint

    @property
    def auth(self) -> Dict[str, str]:
        return self.cloudrobo.get("auth", {})

    @property
    def ak_decrypt_failed(self) -> bool:
        return self._ak_decrypt_failed

    @property
    def sk_decrypt_failed(self) -> bool:
        return self._sk_decrypt_failed

    @property
    def ak(self) -> str:
        if self._ak_cache is not None:
            return self._ak_cache
        env_val = os.environ.get("HUAWEI_CLOUD_AK")
        if env_val:
            self._ak_cache = env_val
            return env_val
        ak_enc = self.auth.get("ak_enc")
        if ak_enc:
            try:
                from cloudrobo_core.sdk.crypto import decrypt
                self._ak_cache = decrypt(ak_enc)
                return self._ak_cache
            except Exception as e:
                logger.warning("AK 解密失败: %s", e)
                self._ak_decrypt_failed = True
                self._ak_cache = ""
                return ""
        ak_plain = self.auth.get("ak", "")
        if ak_plain:
            global _ak_plain_warned
            if not _ak_plain_warned:
                logger.warning("AK 以明文存储，建议运行 cloudrobo config set ak <your-ak> 加密")
                _ak_plain_warned = True
        self._ak_cache = ak_plain
        return ak_plain

    @property
    def sk(self) -> str:
        if self._sk_cache is not None:
            return self._sk_cache
        env_val = os.environ.get("HUAWEI_CLOUD_SK")
        if env_val:
            self._sk_cache = env_val
            return env_val
        sk_enc = self.auth.get("sk_enc")
        if sk_enc:
            try:
                from cloudrobo_core.sdk.crypto import decrypt
                self._sk_cache = decrypt(sk_enc)
                return self._sk_cache
            except Exception as e:
                logger.warning("SK 解密失败: %s", e)
                self._sk_decrypt_failed = True
                self._sk_cache = ""
                return ""
        sk_plain = self.auth.get("sk", "")
        if sk_plain:
            global _sk_plain_warned
            if not _sk_plain_warned:
                logger.warning("SK 以明文存储，建议运行 cloudrobo config set sk <your-sk> 加密")
                _sk_plain_warned = True
        self._sk_cache = sk_plain
        return sk_plain

    @property
    def workspace(self) -> Dict[str, str]:
        from cloudrobo_workspace.config import load_workspace
        return load_workspace()

    @property
    def workspace_id(self) -> str:
        return self.workspace.get("workspace_id", "")

    @property
    def defaults(self) -> Dict[str, Any]:
        return self.cloudrobo.get("defaults", {})

    @property
    def proxy(self) -> Dict[str, str]:
        return self.cloudrobo.get("proxy", {})

    @property
    def http_proxy(self) -> str:
        env_val = os.environ.get("CLOUDROBO_HTTP_PROXY", "")
        if env_val:
            return env_val
        return self.proxy.get("http", "")

    @property
    def https_proxy(self) -> str:
        env_val = os.environ.get("CLOUDROBO_HTTPS_PROXY", "")
        if env_val:
            return env_val
        return self.proxy.get("https", "")

    @property
    def no_proxy(self) -> str:
        env_val = os.environ.get("CLOUDROBO_NO_PROXY", "")
        if env_val:
            return env_val
        return self.proxy.get("no_proxy", "")

    @property
    def proxy_username(self) -> str:
        return os.environ.get("CLOUDROBO_PROXY_USERNAME", self.proxy.get("username", ""))

    @property
    def proxy_password(self) -> str:
        return os.environ.get("CLOUDROBO_PROXY_PASSWORD", self.proxy.get("password", ""))

    @property
    def region(self) -> str:
        return self.cloudrobo.get("region", "cn-southwest-2")

    @property
    def verify_ssl(self) -> bool:
        env_val = os.environ.get("CLOUDROBO_VERIFY_SSL")
        if env_val is not None:
            parsed = self._parse_bool(env_val)
            if parsed is not None:
                return parsed
            # 环境变量值不合法，回退到配置文件
        parsed = self._parse_bool(self._data.get("debug", {}).get("verify_ssl"))
        if parsed is not None:
            return parsed
        return True

    @property
    def ca_bundle(self) -> str:
        env_val = os.environ.get("CLOUDROBO_CA_BUNDLE", "")
        if env_val:
            return env_val
        return self._data.get("debug", {}).get("ca_bundle", "")

    @property
    def log_traffic(self) -> bool:
        env_val = os.environ.get("CLOUDROBO_LOG_TRAFFIC")
        if env_val is not None:
            parsed = self._parse_bool(env_val)
            if parsed is not None:
                return parsed
        parsed = self._parse_bool(self._data.get("debug", {}).get("log_traffic"))
        if parsed is not None:
            return parsed
        return False

    @property
    def verbose(self) -> bool:
        env_val = os.environ.get("CLOUDROBO_VERBOSE")
        if env_val is not None:
            parsed = self._parse_bool(env_val)
            if parsed is not None:
                return parsed
        parsed = self._parse_bool(self._data.get("debug", {}).get("verbose"))
        if parsed is not None:
            return parsed
        return False

    def set_workspace_info(self, workspace_id: str, name: str, asset_catalog_id: str, default_obs_path: str):
        from cloudrobo_workspace.config import save_workspace
        save_workspace({
            "workspace_id": workspace_id,
            "name": name,
            "asset_catalog_id": asset_catalog_id,
            "default_obs_path": default_obs_path,
        })
