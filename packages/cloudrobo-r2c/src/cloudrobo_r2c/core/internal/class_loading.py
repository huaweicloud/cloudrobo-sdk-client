"""Utilities for dynamically loading user-provided classes."""

from __future__ import annotations

import importlib
from typing import Any, Sequence, Type, TypeVar, Optional

T = TypeVar("T")

# Built-in and standard-library modules that are dangerous to import from
# user-controlled configuration.  Blocked to prevent arbitrary code execution
# through ``hardware.class_path`` or ``translator.class_path``.
_DENIED_ROOT_MODULES: frozenset = frozenset(
    {
        # direct code execution / process control
        "os",
        "sys",
        "subprocess",
        "builtins",
        "importlib",
        "shutil",
        "ctypes",
        "socket",
        "pickle",
        "code",
        "compileall",
        "pdb",
        "pty",
        "signal",
        "tempfile",
        "webbrowser",
        "http",
        # file-system helpers
        "pathlib",
        "glob",
        "fnmatch",
        "fileinput",
        "linecache",
        # shell / interactive
        "cmd",
        "codeop",
        "readline",
        "rlcompleter",
        # networking
        "urllib",
        "ftplib",
        "http",
        "smtplib",
        "poplib",
        "imaplib",
        "nntplib",
        "telnetlib",
        "xmlrpc",
        "asyncio",
        # multi-processing / threading escape
        "multiprocessing",
        "threading",
        "concurrent",
        # debug / introspection
        "inspect",
        "traceback",
        "bdb",
        "faulthandler",
        "trace",
        "tracemalloc",
        "sysconfig",
        "dis",
        "gc",
        # serialisation / marshal
        "marshal",
        "shelve",
        "dbm",
    }
)

# Root-module prefixes that indicate C-extension / private internals.
_DENIED_PREFIXES: tuple = ("_",)


class SecurityError(ValueError):
    """Raised when a module path violates security policy."""


def load_class(
    dotted_path: str,
    *,
    allowed_prefixes: Optional[Sequence[str]] = None,
) -> Type[Any]:
    """Load and return class object from ``module.submodule.ClassName`` path.

    Args:
        dotted_path: Fully-qualified ``pkg.mod.Cls`` string.
        allowed_prefixes: If set, only module paths starting with one of these
            prefixes are permitted.  For example ``["my_robot_pkg."]``.

    Raises:
        SecurityError: If the module path is denied by security policy.
        ValueError: If *dotted_path* is malformed or the class is missing.
        TypeError: If the resolved object is not a class.
    """
    if "." not in dotted_path:
        raise ValueError(
            "Class path must be in 'module.submodule.ClassName' format, "
            f"got: {dotted_path!r}"
        )

    module_path, class_name = dotted_path.rsplit(".", 1)
    validate_module_security(module_path, allowed_prefixes)

    module = importlib.import_module(module_path)
    loaded = getattr(module, class_name, None)
    if loaded is None:
        raise ValueError(f"Class {class_name!r} not found in module {module_path!r}")
    if not isinstance(loaded, type):
        raise TypeError(f"{dotted_path!r} does not resolve to a class")
    return loaded


def ensure_subclass(candidate: Type[Any], expected: Type[T], *, path: str) -> Type[T]:
    """Validate dynamically loaded class inherits from expected interface."""
    if not issubclass(candidate, expected):
        raise TypeError(
            f"Class {path!r} must inherit from {expected.__name__}, "
            f"got {candidate.__name__}"
        )
    return candidate


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------


def validate_module_security(
    module_path: str,
    allowed_prefixes: Optional[Sequence[str]] = None,
) -> None:
    root = module_path.split(".")[0]

    if root in _DENIED_ROOT_MODULES:
        raise SecurityError(
            f"Module {root!r} is blocked for security reasons. "
            f"Use a custom adapter module in your project package instead."
        )

    for prefix in _DENIED_PREFIXES:
        if root.startswith(prefix):
            raise SecurityError(
                f"Module {root!r} starts with reserved prefix {prefix!r}. "
                f"This module path is not allowed."
            )

    if allowed_prefixes:
        if not any(module_path.startswith(p) for p in allowed_prefixes):
            raise SecurityError(
                f"Module {module_path!r} is not in the allowed prefix list: "
                f"{list(allowed_prefixes)}"
            )
