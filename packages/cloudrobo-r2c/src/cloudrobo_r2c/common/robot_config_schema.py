"""Pydantic schema for validating robot configuration YAML files.

Provides early, structured validation of ``--robot-config`` before the
configuration reaches downstream consumers.  Validation happens in two
layers:

1. **Structural** — Pydantic ``BaseModel`` checks required fields, types,
   and value ranges.
2. **Semantic** — ``field_validator`` verifies ``hardware.type`` against
   registered entry_points and known legacy types.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class _PermissiveModel(BaseModel):
    """Base that allows extra fields on every config section."""

    model_config = {"extra": "allow"}


# ── Runtime sub-sections ─────────────────────────────────────────────────────


class FusionConfig(_PermissiveModel):
    """Trajectory fusion configuration (under async_request.fusion)."""

    strategy: str = Field(
        default="replace",
        description="Fusion strategy: replace, weighted_average, nearest_neighbor",
    )
    window_size: int = Field(
        default=10, ge=0, description="Number of steps to cross-fade"
    )
    state_types: Optional[list[str]] = Field(
        default=None,
        description="Per-element physical semantics: joint_angle, position_xyz, euler, quaternion",
    )
    state_type_order: Optional[dict[str, str]] = Field(
        default=None,
        description="Element ordering: e.g. {euler: rpy, quaternion: wxyz}",
    )


class AsyncRequestConfig(_PermissiveModel):
    """Async request + fusion configuration."""

    enabled: bool = Field(default=False, description="Enable async request pipeline")
    publish_trigger_threshold: int = Field(
        default=0, ge=0, description="Queue depth threshold for preemptive publish"
    )
    fusion: FusionConfig = Field(
        default_factory=FusionConfig, description="Trajectory fusion sub-config"
    )


class HeartbeatConfig(_PermissiveModel):
    """Auto-heartbeat configuration."""

    enabled: bool = Field(default=True)
    interval_ms: int = Field(default=5000, ge=0)
    jitter_ms: int = Field(default=500, ge=0)
    status: str = Field(default="ONLINE")
    mode: str = Field(default="AUTO")


class KeyboardControlConfig(_PermissiveModel):
    """Keyboard shortcut configuration."""

    enabled: bool = Field(default=False)


# ── Runtime ──────────────────────────────────────────────────────────────────


class RuntimeConfig(_PermissiveModel):
    """Runtime / control-loop configuration."""

    publish_hz: float = Field(gt=0.0, description="Observation publish frequency (Hz)")
    max_duration_s: float = Field(
        ge=0.0,
        description="Maximum run duration in seconds; 0 = run forever",
    )
    dry_run: bool = Field(
        default=False,
        description="If true, observations are published but actions are not sent to hardware",
    )
    action_response_timeout_s: float = Field(
        gt=0.0,
        description="Maximum seconds to wait for an action before timing out",
    )
    action_response_timeout_initial_s: float = Field(
        default=3.0,
        gt=0.0,
        description="Initial timeout before backoff (seconds)",
    )
    action_response_timeout_backoff: float = Field(
        default=2.0,
        ge=1.0,
        description="Timeout backoff multiplier",
    )
    max_enqueue_actions_per_chunk: int = Field(
        default=-1,
        ge=-1,
        description="Max action steps to enqueue per chunk; -1 = unlimited",
    )
    enable_action_chunk_alignment: bool = Field(
        default=False,
        description="Align incoming chunks to last executed action",
    )
    skip_initial_observations: int = Field(
        default=1, ge=0, description="Number of initial observations to skip"
    )
    async_request: AsyncRequestConfig = Field(
        default_factory=AsyncRequestConfig,
        description="Async request pipeline configuration",
    )
    heartbeat: HeartbeatConfig = Field(
        default_factory=HeartbeatConfig,
        description="Auto-heartbeat configuration",
    )
    keyboard_control: KeyboardControlConfig = Field(
        default_factory=KeyboardControlConfig,
        description="Keyboard shortcut configuration",
    )


# ── Hardware ─────────────────────────────────────────────────────────────────


class HardwareConfig(_PermissiveModel):
    """Hardware adapter selection and adapter-specific configuration."""

    type: str = Field(description="Hardware adapter type identifier")
    config: Optional[dict[str, Any]] = Field(
        default=None,
        description="Adapter-specific configuration dictionary",
    )

    @field_validator("type")
    @classmethod
    def _validate_hardware_type(cls, v: str) -> str:
        """Normalise to lowercase and verify the type is known."""
        normalised = v.strip().lower()
        if not normalised:
            raise ValueError("hardware.type must not be empty")

        from cloudrobo_r2c.robots.robot_factory import (
            AdapterRegistry,
            LEGACY_HARDWARE_TYPES,
        )

        entry_types = AdapterRegistry.available_types()
        all_known = set(entry_types) | set(LEGACY_HARDWARE_TYPES)
        if normalised not in all_known:
            raise ValueError(
                f"Unknown hardware.type: {normalised!r}. "
                f"Available: {', '.join(sorted(all_known))}"
            )
        return normalised


# ── Translator / Mappings ────────────────────────────────────────────────────


class TranslatorConfig(_PermissiveModel):
    """Device translator selection."""

    type: str = Field(min_length=1, description="Translator type identifier")


class DeviceToR2CConfig(_PermissiveModel):
    """Upstream mapping: device observations → R2C Observations."""

    mappings: list[Any] = Field(
        min_length=1,
        description="List of mapping rules (source_path, source_paths, default, etc.)",
    )
    task: Optional[str] = Field(default=None, description="Task description")
    image_encoding: Optional[str] = Field(
        default=None,
        description="Image encoding for transport: jpeg, png, raw",
    )


class R2CToDeviceConfig(_PermissiveModel):
    """Downstream mapping: R2C Actions → device commands."""

    mappings: list[Any] = Field(
        min_length=1,
        description="List of mapping rules (target, source, source_index, etc.)",
    )


# ── Top-level ────────────────────────────────────────────────────────────────


class RobotConfig(_PermissiveModel):
    """Top-level robot configuration schema.

    Validates the five core sections.  Extra top-level keys (e.g.
    ``schema_version``) are allowed.
    """

    runtime: RuntimeConfig
    hardware: HardwareConfig
    translator: TranslatorConfig
    device_to_r2c: DeviceToR2CConfig
    r2c_to_device: R2CToDeviceConfig
