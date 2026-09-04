import json
import logging
import re
import time
import warnings
from typing import Any, Dict, Iterator, List, Optional

import requests
from urllib3.exceptions import InsecureRequestWarning

from .apig_sdk_auth import ApigSdkSigner
from .auth import AuthManager
from .config import Config
from .exceptions import (
    AuthenticationError,
    RateLimitError,
    ResourceConflictError,
    ResourceNotFoundError,
    ServiceError,
)

logger = logging.getLogger(__name__)
traffic_logger = logging.getLogger("scripts.common.http_client.traffic")

DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1

_MASKED_SENSITIVE_HEADERS = {"authorization"}


def _mask_headers(headers: Dict[str, str]) -> Dict[str, str]:
    masked = {}
    for k, v in headers.items():
        if k.lower() in _MASKED_SENSITIVE_HEADERS and v:
            masked[k] = v[:8] + "****" if len(v) > 8 else "****"
        else:
            masked[k] = v
    return masked


def _log_request(method: str, url: str, headers: Dict, kwargs: Dict) -> None:
    log_data = {
        "direction": "request",
        "method": method,
        "url": url,
        "headers": _mask_headers(headers),
    }
    body = kwargs.get("json")
    if body is not None:
        log_data["body"] = body
    params = kwargs.get("params")
    if params:
        log_data["params"] = params
    traffic_logger.info("REQUEST %s %s | %s", method, url, json.dumps(log_data, ensure_ascii=False, default=str))


def _log_response(method: str, url: str, resp: requests.Response) -> None:
    log_data = {
        "direction": "response",
        "method": method,
        "url": url,
        "status_code": resp.status_code,
    }
    try:
        log_data["body"] = resp.json()
    except ValueError:
        log_data["body"] = resp.text[:2000] if resp.text else ""
    traffic_logger.info("RESPONSE %s %s %d | %s", method, url, resp.status_code, json.dumps(log_data, ensure_ascii=False, default=str))


def _patch_ssl_context():
    import ssl
    _orig_load_default_certs = ssl.SSLContext.load_default_certs
    def _safe_load_default_certs(self, purpose=ssl.Purpose.SERVER_AUTH):
        try:
            return _orig_load_default_certs(self, purpose)
        except ssl.SSLError:
            return None
    ssl.SSLContext.load_default_certs = _safe_load_default_certs

_patch_ssl_context()


class HttpClient:
    def __init__(
        self,
        config: Optional[Config] = None,
        auth_manager: Optional[AuthManager] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ):
        self.config = config or Config()
        self.auth_manager = auth_manager or AuthManager(self.config)
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.session = requests.Session()
        self._log_traffic = self.config.log_traffic
        self._signer = None

        # Validate credentials early
        ak = self.config.ak
        sk = self.config.sk
        if not ak or not sk:
            missing = []
            if not ak:
                missing.append("AK")
            if not sk:
                missing.append("SK")
            raise AuthenticationError(
                f"凭证缺失: {', '.join(missing)} 未配置。"
                f"请设置环境变量 HUAWEI_CLOUD_{'_'.join(missing)} 或在 ~/.cloudrobo/config.yaml 中配置"
            )

        self._signer = ApigSdkSigner(ak, sk)
        self._resolve_ssl()
        self._resolve_proxy()

    def close(self):
        if self.session:
            self.session.close()
            self.session = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def __del__(self):
        try:
            self.close()
        except Exception as e:
            logger.debug("Failed to close HttpClient in __del__: %s", e)

    def _resolve_ssl(self):
        ca_bundle = self.config.ca_bundle
        if ca_bundle:
            self._verify = ca_bundle
        elif self.config.verify_ssl is False:
            self._verify = False
            warnings.filterwarnings("ignore", category=InsecureRequestWarning)
            logger.debug("SSL verification disabled for HttpClient instance")
        else:
            self._verify = True

    def _resolve_proxy(self):
        proxies = {}
        https_proxy = self.config.https_proxy
        http_proxy = self.config.http_proxy
        username = self.config.proxy_username
        password = self.config.proxy_password
        if username and password:
            https_proxy = self._inject_proxy_auth(https_proxy, username, password)
            http_proxy = self._inject_proxy_auth(http_proxy, username, password)
        if https_proxy:
            proxies["https"] = https_proxy
        if http_proxy:
            proxies["http"] = http_proxy
        self._proxies = proxies if proxies else None
        self._no_proxy_patterns = self._parse_no_proxy(self.config.no_proxy)
        if self._proxies or self._no_proxy_patterns:
            self.session.trust_env = False

    @staticmethod
    def _parse_no_proxy(no_proxy: str) -> List[str]:
        if not no_proxy:
            return []
        patterns = []
        for p in no_proxy.split(","):
            p = p.strip()
            if p:
                patterns.append(p)
        return patterns

    def _should_bypass_proxy(self, url: str) -> bool:
        if not self._no_proxy_patterns:
            return False
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.hostname or ""
        for pattern in self._no_proxy_patterns:
            if pattern.startswith(".") and host.endswith(pattern):
                return True
            if host == pattern:
                return True
        return False

    @staticmethod
    def _inject_proxy_auth(proxy_url: str, username: str, password: str) -> str:
        if not proxy_url or "://" not in proxy_url:
            return proxy_url
        scheme, rest = proxy_url.split("://", 1)
        from urllib.parse import quote
        return f"{scheme}://{quote(username, safe='')}:{quote(password, safe='')}@{rest}"

    def _apply_auth(self, method: str, url: str, headers: Dict, kwargs: Dict) -> None:
        if self._signer:
            body = b""
            json_body = kwargs.get("json")
            if json_body is not None:
                body = json.dumps(json_body, ensure_ascii=False, default=str).encode("utf-8")
                kwargs.pop("json")
                kwargs["data"] = body
            sign_url = self._build_sign_url(url, kwargs.get("params"))
            self._signer.sign(method, sign_url, headers, body)

    @staticmethod
    def _build_sign_url(url: str, params: Optional[Dict] = None) -> str:
        if not params:
            return url
        from urllib.parse import urlencode, urlparse, urlunparse
        parsed = urlparse(url)
        existing_query = f"{parsed.query}&" if parsed.query else ""
        new_query = existing_query + urlencode(sorted(params.items()), doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    def request(self, method: str, url: str, **kwargs) -> Any:
        raw = kwargs.pop("raw", False)
        headers = kwargs.pop("headers", {})
        self._apply_auth(method, url, headers, kwargs)
        kwargs["headers"] = headers
        kwargs.setdefault("verify", self._verify)
        bypass = self._should_bypass_proxy(url)
        if bypass:
            kwargs["proxies"] = {"http": None, "https": None}
        elif self._proxies:
            kwargs.setdefault("proxies", self._proxies)
        elif self.session.trust_env:
            kwargs["proxies"] = {"http": None, "https": None}

        if self._log_traffic:
            _log_request(method, url, headers, kwargs)

        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._do_request(method, url, **kwargs)
                if self._log_traffic:
                    _log_response(method, url, resp)
                if resp.status_code == 401:
                    retried = self._retry_apig_signature(method, url, resp, kwargs)
                    if retried:
                        resp = retried
                        if self._log_traffic:
                            _log_response(method, url, resp)
                        if resp.status_code != 401:
                            return self._handle_response(resp, raw=raw)
                    raise AuthenticationError(f"APIG signature auth failed: {resp.text}")
                return self._handle_response(resp, raw=raw)
            except (AuthenticationError, ResourceNotFoundError, ResourceConflictError):
                raise
            except RateLimitError as e:
                wait = e.retry_after or (self.retry_delay * (2 ** attempt))
                logger.warning("Rate limited, waiting %.1fs", wait)
                time.sleep(wait)
            except (requests.ConnectionError, requests.Timeout) as e:
                last_exc = e
                if attempt < self.max_retries:
                    wait = self.retry_delay * (2 ** attempt)
                    logger.warning("Request failed, retrying in %.1fs: %s", wait, e)
                    time.sleep(wait)
        raise ServiceError(f"Request failed after {self.max_retries} retries: {last_exc}")

    _APGW0301_URI_PATTERN = re.compile(r"canonical_request:[^|]*\|([^|]*)")

    def _extract_server_uri(self, resp: requests.Response) -> Optional[str]:
        try:
            body = resp.json()
        except ValueError:
            return None
        if body.get("error_code") != "APIGW.0301":
            return None
        msg = body.get("error_msg", "")
        match = self._APGW0301_URI_PATTERN.search(msg)
        if not match:
            return None
        server_uri = match.group(1)
        server_uri = server_uri.replace("\\/", "/")
        return server_uri

    def _retry_apig_signature(self, method: str, url: str, resp: requests.Response, kwargs: Dict) -> Optional[requests.Response]:
        if not self._signer:
            return None
        server_uri = self._extract_server_uri(resp)
        if not server_uri:
            return None
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        client_uri = parsed.path
        if server_uri == client_uri:
            return None
        logger.info("APIG signature URI mismatch, client=%s server=%s, retrying with server URI", client_uri, server_uri)
        retry_url = urlunparse(parsed._replace(path=server_uri))
        retry_headers = {}
        retry_kwargs = {}
        body = b""
        if method.upper() != "GET":
            data = kwargs.get("data")
            if data is not None:
                body = data if isinstance(data, bytes) else b""
                retry_kwargs["data"] = data
        params = kwargs.get("params")
        sign_url = self._build_sign_url(retry_url, params)
        if params:
            from urllib.parse import urlencode
            urlencode(sorted(params.items()))
            retry_url = self._build_sign_url(retry_url, params)
        self._signer.sign(method, sign_url, retry_headers, body)
        retry_kwargs["headers"] = retry_headers
        retry_kwargs["verify"] = kwargs.get("verify", self._verify)
        if self._should_bypass_proxy(retry_url):
            retry_kwargs["proxies"] = {"http": None, "https": None}
        elif self._proxies:
            retry_kwargs["proxies"] = self._proxies
        retry_kwargs["allow_redirects"] = False
        logger.info("APIG retry URL=%s sign_url=%s", retry_url, sign_url)
        return self.session.request(method, retry_url, **retry_kwargs)

    def _do_request(self, method: str, url: str, **kwargs) -> requests.Response:
        if not self._signer:
            return self.session.request(method, url, **kwargs)
        max_redirects = kwargs.pop("max_redirects", 5)
        kwargs["allow_redirects"] = False
        resp = self.session.request(method, url, **kwargs)
        redirect_count = 0
        while resp.status_code in (301, 302, 303, 307, 308) and redirect_count < max_redirects:
            redirect_url = resp.headers.get("Location", "")
            if not redirect_url:
                break
            from urllib.parse import urljoin
            redirect_url = urljoin(url, redirect_url)
            redirect_method = "GET" if resp.status_code in (301, 302, 303) else method
            headers = kwargs.get("headers", {})
            body = kwargs.get("data", b"")
            if redirect_method == "GET":
                body = b""
                headers.pop("content-type", None)
            self._signer.sign(redirect_method, redirect_url, headers, body if isinstance(body, bytes) else b"")
            kwargs["headers"] = headers
            if redirect_method != method:
                kwargs.pop("data", None)
            url = redirect_url
            method = redirect_method
            resp = self.session.request(method, url, **kwargs)
            redirect_count += 1
        return resp

    def _handle_response(self, resp: requests.Response, raw: bool = False) -> Any:
        code = resp.status_code
        if 200 <= code < 300:
            if raw:
                return resp.content
            try:
                return resp.json()
            except ValueError:
                return resp.text
        if code == 401:
            raise AuthenticationError(f"Unauthorized: {resp.text}")
        if code == 404:
            from cloudrobo_core.models import ErrorResponse
            err = self._parse_error(resp)
            raise ResourceNotFoundError(err.error_msg or f"Not found: {resp.text}")
        if code == 409:
            from cloudrobo_core.models import ErrorResponse
            err = self._parse_error(resp)
            raise ResourceConflictError(err.error_msg or f"Conflict: {resp.text}")
        if code == 429:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    retry_after = float(retry_after)
                except (ValueError, TypeError):
                    retry_after = None
            raise RateLimitError(retry_after=retry_after)
        if code >= 500:
            err = self._parse_error(resp)
            detail = err.error_msg or resp.text[:200]
            error_msg = f"Server error {code}: {detail}"
            logger.debug("Error details", exc_info=True)
            raise ServiceError(error_msg, status_code=code)
        err = self._parse_error(resp)
        detail = err.error_msg or resp.text[:200]
        error_msg = f"Request failed ({code}): {detail}"
        logger.debug("Error details", exc_info=True)
        raise ServiceError(error_msg, status_code=code)

    def _parse_error(self, resp: requests.Response):
        from cloudrobo_core.models import ErrorResponse
        try:
            data = resp.json()
            return ErrorResponse(
                error_code=data.get("error_code", ""),
                error_msg=data.get("error_msg", ""),
            )
        except ValueError:
            return ErrorResponse()

    def get(self, url: str, params: Optional[Dict] = None, raw: bool = False, **kwargs) -> Any:
        return self.request("GET", url, params=params, raw=raw, **kwargs)

    def post(self, url: str, json: Optional[Dict] = None, raw: bool = False, **kwargs) -> Any:
        return self.request("POST", url, json=json, raw=raw, **kwargs)

    def put(self, url: str, json: Optional[Dict] = None, raw: bool = False, **kwargs) -> Any:
        return self.request("PUT", url, json=json, raw=raw, **kwargs)

    def patch(self, url: str, json: Optional[Dict] = None, raw: bool = False, **kwargs) -> Any:
        return self.request("PATCH", url, json=json, raw=raw, **kwargs)

    def delete(self, url: str, raw: bool = False, **kwargs) -> Any:
        return self.request("DELETE", url, raw=raw, **kwargs)

    MAX_PAGINATE_PAGES = 1000

    def paginate(self, url: str, params: Optional[Dict] = None,
                 page_key: str = "offset", size_key: str = "limit",
                 page_size: int = 20, items_key: str = "data") -> Iterator[Dict]:
        params = dict(params or {})
        offset = 0
        pages = 0
        while pages < self.MAX_PAGINATE_PAGES:
            params[page_key] = offset
            params[size_key] = page_size
            result = self.get(url, params=params)
            items = result.get(items_key, []) if isinstance(result, dict) else []
            for item in items:
                yield item
            total = result.get("total", 0) if isinstance(result, dict) else 0
            offset += page_size
            pages += 1
            if offset >= total or not items:
                break
