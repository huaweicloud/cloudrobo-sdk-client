"""Bounded OBS diagnostics without credentials, signed queries or traceback locals."""

import logging
import os
import re
import traceback
from collections import deque


def redact(value, secrets=(), limit=2000):
    text = str(value)
    for secret in secrets:
        if isinstance(secret, str) and secret:
            text = text.replace(secret, "[REDACTED]")
    # SDK messages may contain signed URLs or request headers/XML.
    scheme = r"(?:https?://|(?:tcp|tls|udp|quic)/)"
    text = re.sub(r"(" + scheme + r")[^/\s]+@", r"\1[REDACTED]@", text)
    text = re.sub(r"(" + scheme + r"[^\s?'\"<>]+)\?[^\s'\"<>]*", r"\1?[REDACTED]", text)
    text = re.sub(r"(?im)(authorization\s*[:=]\s*).*", r"\1[REDACTED]", text)
    sensitive = r"(?:access[_-]?key(?:[_-]?id)?|secret[_-]?key|security[_-]?token|token|signature|password|ak|sk)"
    text = re.sub(
        r"(?i)([\"']?" + sensitive + r"[\"']?\s*[:=]\s*)[\"']?[^\s,;\"'<>]+[\"']?",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?is)(<" + sensitive + r">).*?(</" + sensitive + r">)",
        r"\1[REDACTED]\2",
        text,
    )
    return text[:limit]


def credential_values(client=None):
    return tuple(
        value
        for value in (
            getattr(client, "access_key", None),
            getattr(client, "secret_key", None),
            os.environ.get("HUAWEI_CLOUD_AK"),
            os.environ.get("HUAWEI_CLOUD_SK"),
            os.environ.get("HUAWEI_CLOUD_SECURITY_TOKEN"),
        )
        if isinstance(value, str) and value
    )


def exception_details(exc, secrets=()):
    details = dict(exception_type=type(exc).__name__, message=redact(exc, secrets))
    for name in ("errno", "winerror"):
        value = getattr(exc, name, None)
        if isinstance(value, int):
            details[name] = value
    # Source lines and locals can contain secrets; retain only stack locations.
    details["stack"] = [
        dict(file=redact(f.filename, secrets), line=f.lineno, function=redact(f.name, secrets))
        for f in traceback.extract_tb(exc.__traceback__)[-20:]
    ]
    cause = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    seen, causes = {id(exc)}, []
    while cause is not None and id(cause) not in seen and len(causes) < 4:
        seen.add(id(cause))
        item = dict(exception_type=type(cause).__name__, message=redact(cause, secrets))
        for name in ("errno", "winerror"):
            value = getattr(cause, name, None)
            if isinstance(value, int):
                item[name] = value
        causes.append(item)
        cause = cause.__cause__ or (None if cause.__suppress_context__ else cause.__context__)
    if causes:
        details["causes"] = causes
    return details


def response_details(response, secrets=()):
    details = dict(message="OBS upload returned no response")
    if response is not None:
        details = {
            key: redact(getattr(response, attr, ""), secrets)
            for key, attr in (
                ("http_status", "status"),
                ("error_code", "errorCode"),
                ("request_id", "requestId"),
                ("message", "errorMessage"),
            )
        }
    return details


class UploadLogCapture:
    """Retain SDK failure reasons lost by its multipart exception wrappers."""

    def __init__(self, delegate, secrets):
        self.delegate, self.secrets = delegate, secrets
        self.messages = deque(maxlen=5)

    def log(self, level, message, *args, **kwargs):
        if level >= logging.WARNING:
            message_text = message % args if args else message
            self.messages.append(redact(message_text, self.secrets, limit=1500))
        self.delegate.log(level, message, *args, **kwargs)

    def close(self):
        self.delegate.close()
