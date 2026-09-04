"""Device translator factory for robot runtime selection."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from cloudrobo_r2c.core.interfaces import IDeviceTranslator, IModelTranslator
from cloudrobo_r2c.core.internal.class_loading import ensure_subclass, load_class
from cloudrobo_r2c.translators.device_translator import ConfigurableDeviceTranslator
from cloudrobo_r2c.translators.model_translator import ConfigurableModelTranslator


class DeviceTranslatorFactory:
    """Build :class:`IDeviceTranslator` implementations from ``robot_config.yaml``."""

    @classmethod
    def create_device_translator(
        cls,
        config: Mapping[str, Any],
        *,
        translator_class: Optional[str] = None,
    ) -> IDeviceTranslator:
        """Create a device translator from config.

        When *translator_class* is given it takes precedence and is injected
        into the config as ``translator.type = "custom"`` /
        ``translator.class_path = translator_class``.
        """
        if translator_class:
            config = cls._inject_translator_class(config, translator_class)

        translator_cfg = cls._extract_translator_mapping(config)
        translator_type = (
            str(translator_cfg.get("type", "configurable")).strip().lower()
        )

        if translator_type == "configurable":
            return ConfigurableDeviceTranslator.from_config(config)
        if translator_type == "custom":
            class_path = str(translator_cfg.get("class_path", "")).strip()
            if not class_path:
                raise ValueError(
                    "translator.class_path is required when translator.type is 'custom'"
                )
            translator_cls = ensure_subclass(
                load_class(class_path), IDeviceTranslator, path=class_path
            )
            custom_cfg = translator_cfg.get("custom_config")
            if custom_cfg is None:
                return translator_cls()
            if not isinstance(custom_cfg, Mapping):
                raise ValueError(
                    "translator.custom_config must be a mapping when provided"
                )
            return translator_cls(config=dict(custom_cfg))

        raise ValueError(
            "Unsupported translator.type: "
            f"{translator_type!r}. Supported: configurable, custom"
        )

    @staticmethod
    def _extract_translator_mapping(config: Mapping[str, Any]) -> Mapping[str, Any]:
        translator_cfg = config.get("translator")
        if translator_cfg is None:
            return {}
        if not isinstance(translator_cfg, Mapping):
            raise ValueError("translator must be a mapping when provided")
        return translator_cfg

    @staticmethod
    def _inject_translator_class(
        config: Mapping[str, Any],
        translator_class: str,
    ) -> dict[str, Any]:
        """Merge *translator_class* into config as a custom type translator."""
        mutable = dict(config)
        translator_section = mutable.get("translator")
        merged: dict[str, Any] = (
            dict(translator_section)
            if isinstance(translator_section, Mapping)
            else {}
        )
        merged["type"] = "custom"
        merged["class_path"] = translator_class
        merged.setdefault("custom_config", {})
        mutable["translator"] = merged
        return mutable


class ModelTranslatorFactory:
    """Build :class:`IModelTranslator` implementations from cloud config."""

    @classmethod
    def create_model_translator(
        cls,
        cloud_config: Mapping[str, Any],
        *,
        model_translator: Optional[IModelTranslator] = None,
        translator_class: Optional[str] = None,
    ) -> IModelTranslator:
        """Create a model translator.

        If *model_translator* is provided it is returned directly (injection
        path).  If *translator_class* is given it is loaded via
        ``load_class`` and validated as an ``IModelTranslator`` subclass.
        Otherwise returns the default ``ConfigurableModelTranslator``.
        """
        if model_translator is not None:
            return model_translator

        if translator_class is not None:
            path = str(translator_class).strip()
            translator_cls = ensure_subclass(
                load_class(path), IModelTranslator, path=path
            )
            return translator_cls(cloud_config)

        return ConfigurableModelTranslator(cloud_config)
