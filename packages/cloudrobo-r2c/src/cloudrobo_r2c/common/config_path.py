"""Resolution of config file paths shipped inside the installed package.

The example ``config`` directory lives inside the package at
``cloudrobo_r2c/config`` and is installed as package data, so users can
reference shipped example configs without knowing the install location.

Callers should pass the same ``config/...`` relative paths they always have;
:func:`resolve_config_path` locates them either from the current working
directory (source-checkout style) or from the installed package data.
"""

from __future__ import annotations

import os

__all__ = ["resolve_config_path"]

# Directory inside the package that holds the shipped example configs.
_PACKAGE_CONFIG_DIRNAME = "config"

# Path of the ``cloudrobo_r2c`` package directory on the installed system.
_PACKAGE_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def package_config_dir() -> str:
    """Absolute path to the ``config`` directory shipped inside the package."""
    return os.path.join(_PACKAGE_DIR, _PACKAGE_CONFIG_DIRNAME)


def resolve_config_path(path: str) -> str:
    """Resolve a ``config/...`` path to an existing file.

    Resolution order:
      1. If ``path`` is absolute and the file exists, return it.
      2. If the file exists relative to the current working directory, return it
         (supports running from a source checkout).
      3. Otherwise, look it up inside the installed package's ``config`` dir.
      4. If none of the above exists, return the package-data candidate so the
         caller can still raise a meaningful error.
    """
    if not path:
        return path

    if os.path.isabs(path):
        return os.path.abspath(path) if os.path.isfile(path) else path

    # Relative to CWD first (source checkout / user overrides).
    cwd_candidate = os.path.abspath(os.path.join(os.getcwd(), path))
    if os.path.isfile(cwd_candidate):
        return cwd_candidate

    # Fall back to the config shipped inside the installed package.
    package_candidate = os.path.abspath(os.path.join(package_config_dir(), path))

    # If a plain filename is given (e.g. "client_config.yaml"), it may also
    # live directly under the package config dir rather than under config/...
    if not os.path.isfile(package_candidate):
        direct = os.path.abspath(os.path.join(package_config_dir(), os.path.basename(path)))
        if os.path.isfile(direct):
            return direct

    return package_candidate
