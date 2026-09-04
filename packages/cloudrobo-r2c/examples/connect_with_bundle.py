"""Example: connect using a platform-issued credential bundle."""

from __future__ import annotations

import argparse
import json

from cloudrobo_r2c.client import R2CClient


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Connect to R2C using a platform credential bundle zip or directory"
    )
    parser.add_argument(
        "bundle_path",
        help="Path to credential bundle zip file or unpacked bundle directory",
    )
    parser.add_argument(
        "--client-id",
        default=None,
        help="Optional client_id override; if omitted, SDK auto-generates one",
    )
    args = parser.parse_args()

    session = R2CClient.connect(args.bundle_path, client_id=args.client_id)
    try:
        print("Connected.")
        print(json.dumps(session.connection_info(), indent=2, ensure_ascii=False))
    finally:
        session.close()


if __name__ == "__main__":
    main()