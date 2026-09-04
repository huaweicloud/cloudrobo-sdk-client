"""Regression tests for AdapterRegistry entry-point resolution.

Verifies that when multiple installed distributions register adapters under the
same name in the shared ``r2c_sdk.adapters`` group, the registry always prefers
the factory defined inside the current ``cloudrobo_r2c`` package.  Otherwise a
stale sibling distribution (e.g. the legacy ``r2c_sdk`` / ``hw-r2c-sdk``
package) shadows ours)Skip, and the factory returns an adapter built from a
*different* ``IRobotHardwareAdapter`` class, breaking the isinstance check in
:meth:`RobotFactory._try_entry_point_adapter`.
"""

from __future__ import annotations

from importlib.metadata import EntryPoint

import pytest

from cloudrobo_r2c.robots.robot_factory import AdapterRegistry


def _ep(name: str, value: str) -> EntryPoint:
    """Build a minimal EntryPoint whose `.value` we can inspect."""
    ep = EntryPoint(name=name, value=value, group="r2c_sdk.adapters")
    return ep


@pytest.fixture(autouse=True)
def _clean_registry():
    AdapterRegistry.reset()
    yield
    AdapterRegistry.reset()


def test_is_ours_prefers_current_package(monkeypatch):
    # A duplicate name: one from our package, one from a legacy sibling dist.
    ours = _ep("dummy", "cloudrobo_r2c.robots.dummy_robot:create_dummy_adapter")
    legacy = _ep("dummy", "r2c_sdk.robots.dummy_robot:create_dummy_adapter")

    # Make the legacy entry point appear *after* ours to ensure the flat-map
    # overwrite (old behavior) would have picked the legacy one.
    monkeypatch.setattr(
        "cloudrobo_r2c.robots.robot_factory.entry_points",
        lambda group: [ours, legacy],
    )

    AdapterRegistry._ensure_scanned()
    chosen = AdapterRegistry._entry_points["dummy"]
    assert chosen.value.startswith("cloudrobo_r2c.")


def test_unique_third_party_entry_point_kept(monkeypatch):
    # A uniquely-named adapter provided by a different package (not ours, not a
    # duplicate) must still be honored.
    third_party = _ep(
        "my_vendor", "my_vendor.robots.adapter:create_adapter"
    )
    monkeypatch.setattr(
        "cloudrobo_r2c.robots.robot_factory.entry_points",
        lambda group: [third_party],
    )

    AdapterRegistry._ensure_scanned()
    assert AdapterRegistry._entry_points["my_vendor"].value == (
        "my_vendor.robots.adapter:create_adapter"
    )


def test_is_ours_handles_missing_value():
    ep = _ep("weird", "")
    assert AdapterRegistry._is_ours(ep) is False

    ep2 = _ep("ours", "cloudrobo_r2c.robots.x:create_x")
    assert AdapterRegistry._is_ours(ep2) is True
