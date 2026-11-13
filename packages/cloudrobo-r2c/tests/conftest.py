"""Pytest bootstrap for the cloudrobo-r2c test suite.

Installs the fake ``a2d_sdk`` modules before any test module is imported so
``cloudrobo_r2c.robots.agibot_g01.agibot_g01_client`` binds the fakes at import
time regardless of test collection order (no GDK environment required). The
mock adapter tests do not use GDK, but they still import the real adapter
module chain.
"""

import sys
from pathlib import Path

_TESTS_DIR = str(Path(__file__).resolve().parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from fake_a2d_sdk import install_fake_a2d_sdk  # noqa: E402

install_fake_a2d_sdk()
