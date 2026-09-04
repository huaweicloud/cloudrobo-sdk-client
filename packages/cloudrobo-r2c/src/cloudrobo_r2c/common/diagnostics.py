"""Connection diagnostics models and sanitization utilities."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


class ConnectionStage:
    INIT = "INIT"
    CONFIG_VALIDATION = "CONFIG_VALIDATION"
    TRANSPORT_CONFIGURED = "TRANSPORT_CONFIGURED"
    OPEN_SESSION = "OPEN_SESSION"
    READY = "READY"
    FAILED = "FAILED"
    DISCONNECTED = "DISCONNECTED"
    CLOSED = "CLOSED"


class LastErrorCategory:
    CONFIG = "CONFIG"
    NETWORK = "NETWORK"
    GATEWAY = "GATEWAY"
    HANDSHAKE = "HANDSHAKE"
    CONNECT = "CONNECT"
    UNKNOWN = "UNKNOWN"


def now_ms() -> int:
    return int(time.time() * 1000)


_IPV4_RE = re.compile(r"^(?P<a>\d{1,3})\.(?P<b>\d{1,3})\.(?P<c>\d{1,3})\.(?P<d>\d{1,3})$")


def _mask_ipv4(host: str) -> str:
    m = _IPV4_RE.match(host)
    if m is None:
        return host

    try:
        a = int(m.group("a"))
        b = int(m.group("b"))
        c = int(m.group("c"))
        d = int(m.group("d"))
    except Exception:
        return host

    if not (0 <= a <= 255 and 0 <= b <= 255 and 0 <= c <= 255 and 0 <= d <= 255):
        return host

    return f"{a}.{b}.{c}.xxx"


def _strip_userinfo(authority: str) -> str:
    # user:pass@host:port -> host:port
    if "@" in authority:
        return authority.split("@")[-1]
    return authority


def _strip_query_fragment(s: str) -> str:
    # Remove ?query and #fragment
    s = s.split("#", 1)[0]
    s = s.split("?", 1)[0]
    return s


def _sanitize_endpoint(ep: str) -> str:
    """Sanitize endpoint string while keeping it readable for debugging.

    Typical endpoint format:
      - "tcp/192.168.1.1:7447"
      - "tcp/user:pass@host:7447?token=xxx"
      - "tls/127.0.0.1:7447"
    """
    if not ep:
        return ep
    ep = ep.strip()
    ep = _strip_query_fragment(ep)

    if "/" in ep:
        scheme, rest = ep.split("/", 1)
        scheme = scheme.strip()
        rest = rest.strip()
    else:
        scheme, rest = "", ep

    rest = _strip_userinfo(rest)

    if rest.count(":") == 1:
        host_part, port_part = rest.split(":", 1)
        host_part = host_part.strip()
        port_part = port_part.strip()
        host_part = _mask_ipv4(host_part)
        rest = f"{host_part}:{port_part}" if port_part else host_part
    else:
        rest = _mask_ipv4(rest)

    return f"{scheme}/{rest}" if scheme else rest


def sanitize_endpoints(endpoints: Sequence[str]) -> List[str]:
    return [_sanitize_endpoint(e) for e in (endpoints or [])]


_SENSITIVE_KEY_RE = r"""
(?:
    token(?:\d+)? |
    access[_-]?token |
    refresh[_-]?token |
    id[_-]?token |
    auth[_-]?token |
    pwd |
    pass(?:word)? |
    passwd |
    secret |
    api[_-]?key |
    x[_-]?api[_-]?key |
    private[_-]?key |
    credential(?:s)? |
    signature
)
"""

_VALUE_RE = r"""
(?:
    "(?:[^"\\]|\\.)*" |
    '(?:[^'\\]|\\.)*' |
    [^\s,;]+
)
"""

_SENSITIVE_KV_RE = re.compile(
    rf"(?ix)\b(?P<k>{_SENSITIVE_KEY_RE})\b\s*[:=]\s*(?P<v>{_VALUE_RE})"
)


def sanitize_error(
    message: str,
    endpoint_pairs: Optional[Sequence[Tuple[str, str]]] = None,
) -> str:
    """Sanitize error messages by redacting secrets and replacing raw endpoints with safe versions."""
    if not message:
        return message

    msg = str(message)

    if endpoint_pairs:
        for raw, safe in endpoint_pairs:
            if raw and safe and raw in msg:
                msg = msg.replace(raw, safe)

    def _redact(m: re.Match) -> str:
        k = m.group("k")
        return f"{k}=<redacted>"

    msg = _SENSITIVE_KV_RE.sub(_redact, msg)

    return msg


@dataclass
class ConnectionInfo:
    """Safe connection summary."""

    protocol: str = "zenoh"
    mode: Optional[str] = None
    endpoints: Optional[List[str]] = None
    connected: bool = False
    stage: str = ConnectionStage.INIT
    last_error: Optional[str] = None
    last_error_category: Optional[str] = None
    timestamp_ms: Optional[int] = None

    # optional diagnostics for TLS/mTLS
    tls_enabled: Optional[bool] = None
    mtls_enabled: Optional[bool] = None
    verify_name_on_connect: Optional[bool] = None

    def __post_init__(self) -> None:
        if self.endpoints is None:
            self.endpoints = []
        if self.timestamp_ms is None:
            self.timestamp_ms = now_ms()

    def to_safe_dict(self) -> Dict[str, object]:
        return {
            "protocol": self.protocol,
            "mode": self.mode,
            "endpoints": list(self.endpoints or []),
            "connected": bool(self.connected),
            "stage": self.stage,
            "last_error_category": self.last_error_category,
            "last_error": self.last_error,
            "timestamp_ms": self.timestamp_ms,
            "tls_enabled": self.tls_enabled,
            "mtls_enabled": self.mtls_enabled,
            "verify_name_on_connect": self.verify_name_on_connect,
        }