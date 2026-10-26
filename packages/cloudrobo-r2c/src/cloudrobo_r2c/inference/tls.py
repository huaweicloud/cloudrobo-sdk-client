"""TLS/SSL context helper for the inference transport layer.

Sole responsibility: build the self-signed-tolerant SSL context used for
``wss://`` WebSocket connections. Taken from the reference
``inference_client.py`` implementation.
"""

from __future__ import annotations

import ssl

# TLS 1.2 cipher suites used for the self-signed-tolerant SSL context.
CIPHERS = (
    "ECDHE-ECDSA-AES256-GCM-SHA384:"
    "ECDHE-RSA-AES256-GCM-SHA384:"
    "ECDHE-ECDSA-CHACHA20-POLY1305:"
    "ECDHE-RSA-CHACHA20-POLY1305"
)


def create_ssl_context() -> ssl.SSLContext:
    """Create an SSL context for ``wss://`` that accepts self-signed certs.

    Inference services typically use self-signed certificates, so hostname
    verification and certificate verification are disabled. TLS 1.2 with the
    constrained cipher list, compression disabled (BREACH mitigation) and
    server cipher preference.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers(CIPHERS)
    context.options |= ssl.OP_NO_COMPRESSION
    context.options |= ssl.OP_CIPHER_SERVER_PREFERENCE
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context
