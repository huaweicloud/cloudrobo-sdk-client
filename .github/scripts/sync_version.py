#!/usr/bin/env python3
"""Sync the release version from a git tag into all pyproject.toml files.

Background: versions used to be hardcoded as `version = "0.1.0"` in the root
pyproject.toml and every packages/*/pyproject.toml. The Publish-to-PyPI workflow
triggers on `v*` tags but `python -m build` only reads the hardcoded value, so
pushing a new tag still produced 0.1.0 artifacts. CI now runs this script before
building so the tag (e.g. v0.1.1-dev) drives the released version everywhere.

What it does:
  1. Normalizes the tag version to PEP 440 (v0.1.1-dev -> 0.1.1.dev0).
  2. Rewrites the `[project] version` line in the root pyproject.toml and every
     packages/*/pyproject.toml.
  3. Rewrites the root bundle's `hw-cloudrobo-*==x.y.z` pins so the main package
     depends on exactly the sub-package versions released in the same run.

Usage:
    sync_version.py <version> [--root PATH]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# PEP 440 release segment, optionally with pre/post/dev parts we accept from tags.
_PEP440_RE = re.compile(
    r"^\d+(\.\d+)*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?$"
)


def normalize(version: str) -> str:
    """Convert a tag version to its PEP 440 normalized form."""
    version = version.strip()
    # Accept common tag styles: -dev / -devN / -Dev suffixes become .devN
    version = re.sub(
        r"-(dev(?:\d+)?)$", lambda m: f".dev{m.group(1)[3:] or '0'}", version,
        flags=re.IGNORECASE,
    )
    if not _PEP440_RE.match(version):
        sys.exit(f"error: version {version!r} is not PEP 440 compatible")
    return version


def sync_file(path: Path, version: str) -> list[str]:
    """Apply version substitutions to one pyproject.toml. Returns a change log."""
    text = path.read_text(encoding="utf-8")
    changes: list[str] = []

    new_text, n = re.subn(
        r'(?m)^version = "[^"]*"$', f'version = "{version}"', text
    )
    if n != 1:
        sys.exit(f"error: {path}: expected exactly one `version = \"...\"` line, found {n}")
    if new_text != text:
        changes.append(f"version -> {version}")
    text = new_text

    # Root bundle pins sub-packages with ==; keep them in lockstep with this release.
    new_text, n = re.subn(
        r"(hw-cloudrobo-[a-z0-9-]+)==[^\",\s]+", rf"\g<1>=={version}", text
    )
    if n and new_text != text:
        changes.append(f"{n} hw-cloudrobo-*== pins -> {version}")
    text = new_text

    if changes:
        path.write_text(text, encoding="utf-8")
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="version from the git tag, without the leading 'v'")
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2],
        help="repository root (default: auto-detect from this script's location)",
    )
    args = parser.parse_args()

    version = normalize(args.version)
    targets = [args.root / "pyproject.toml", *sorted(args.root.glob("packages/*/pyproject.toml"))]
    for path in targets:
        if not path.is_file():
            sys.exit(f"error: {path} not found")
        changes = sync_file(path, version)
        status = "; ".join(changes) if changes else "already up to date"
        print(f"{path.relative_to(args.root)}: {status}")
    print(f"All pyproject.toml files synced to {version}")


if __name__ == "__main__":
    main()
