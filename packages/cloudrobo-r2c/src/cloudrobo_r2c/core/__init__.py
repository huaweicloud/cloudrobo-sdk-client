"""Core abstraction interfaces for adapter/translator-based integrations."""

from __future__ import annotations

from .config_mapper import ConfigDrivenMapper, MapperRule
from .interfaces import (
    IRobotHardwareAdapter,
    IDeviceTranslator,
    IModelTranslator,
    IValueTransformer,
)
from .transformers import DEFAULT_TRANSFORMERS

__all__ = [
    "IRobotHardwareAdapter",
    "MapperRule",
    "ConfigDrivenMapper",
    "IDeviceTranslator",
    "IModelTranslator",
    "IValueTransformer",
    "DEFAULT_TRANSFORMERS",
]
