"""SDK custom exception system providing unified error semantics."""

from __future__ import annotations

from typing import Optional

from cloudrobo_r2c.common.diagnostics import ConnectionInfo


class R2CSDKError(Exception):
    """Base class for all SDK-related exceptions, for unified catching."""


class R2CConnectionError(R2CSDKError):
    """Thrown when connection establishment or transport layer issues occur.

    Carries a sanitized ConnectionInfo for diagnostics.
    """

    def __init__(self, message: str, info: Optional[ConnectionInfo] = None):
        super().__init__(message)
        self.info: Optional[ConnectionInfo] = info

    def connection_info(self) -> dict:
        return self.info.to_safe_dict() if self.info else {}


class AuthenticationError(R2CSDKError):
    """Thrown when authentication fails or credentials are unavailable."""


class CredentialBundleError(R2CSDKError):
    """Thrown when the platform credential bundle is invalid or cannot be parsed."""