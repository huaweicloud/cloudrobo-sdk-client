from __future__ import annotations

import argparse
import getpass
import logging
import os
from typing import Optional

from cloudrobo_r2c import R2CClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def resolve_password(
    direct_password: Optional[str],
    password_env: Optional[str],
    prompt: bool,
) -> Optional[str]:
    if direct_password:
        return direct_password

    if password_env:
        value = os.environ.get(password_env)
        if not value:
            raise ValueError(
                f"Environment variable {password_env!r} is not set or is empty"
            )
        return value

    if prompt:
        return getpass.getpass("Encrypted private key password: ")

    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Connect to R2C using a bundle that may contain an encrypted "
            "server_key.pem."
        )
    )
    parser.add_argument(
        "--bundle",
        default="cert_0.zip",
        help="Path to the credential bundle zip or directory",
    )
    parser.add_argument(
        "--private-key-password",
        default=None,
        help=(
            "Password for encrypted server_key.pem. "
            "Avoid using this on shared shells if shell history is a concern."
        ),
    )
    parser.add_argument(
        "--private-key-password-env",
        default=None,
        help="Read private key password from environment variable name",
    )
    parser.add_argument(
        "--prompt-password",
        action="store_true",
        help="Prompt for private key password interactively",
    )

    args = parser.parse_args()

    password = resolve_password(
        direct_password=args.private_key_password,
        password_env=args.private_key_password_env,
        prompt=args.prompt_password,
    )

    logger.info("Connecting with bundle: %s", args.bundle)

    session = None
    try:
        session = R2CClient.connect(
            args.bundle,
            private_key_password=password,
        )

        logger.info(
            "Connected successfully: project_id=%s device_id=%s",
            session.project_id,
            session.device_id,
        )

        if hasattr(session, "connection_info"):
            try:
                logger.info("Connection info: %s", session.connection_info())
            except Exception:
                logger.info("Connection established (connection_info unavailable)")
        else:
            logger.info("Connection established")

    finally:
        if session is not None and hasattr(session, "close"):
            try:
                session.close()
                logger.info("Session closed")
            except Exception as e:
                logger.warning("Failed to close session cleanly: %s", e)


if __name__ == "__main__":
    main()