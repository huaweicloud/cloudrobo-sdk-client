"""Config-driven translator for custom (non-Observations) topics.

Routes mapping results between message fields via config-driven mapping
rules, with skip-on-missing semantics for fields that are not yet
populated.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping

from cloudrobo_r2c.core.config_mapper import ConfigDrivenMapper

logger = logging.getLogger(__name__)


class ConfigurableTranslator:
    """Apply mapping rules to a topic payload dict.

    - Mapping targets are regular message fields (no ``_`` blackboard
      routing — the VarStore temporary-variable feature was removed).
    - When a required source (without ``default``) is still missing, the
      whole tick is skipped and a single warning line is logged (same
      semantics as ``ConfigurableDeviceTranslator``).
    """

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        name: str = "translator",
    ) -> None:
        self._name = name
        cfg = dict(config or {})
        mappings = cfg.get("mappings")
        self._mapper: ConfigDrivenMapper | None = None
        if isinstance(mappings, list) and mappings:
            self._mapper = ConfigDrivenMapper.from_rule_mappings(mappings)

    def apply(self, payload: Mapping[str, Any]) -> Dict[str, Any] | None:
        """Translate one tick of *payload*.

        Mapped fields override the input, all other fields pass through
        untouched. Returns ``None`` when the tick must be skipped due to
        missing required fields.
        """
        if self._mapper is None:
            return dict(payload)

        incomplete = self._mapper.check_completeness(payload)
        # check_completeness 返回 {"required": [...], "optional": [...]};
        # 旧版为 List[str]。统一取缺失描述列表。
        if isinstance(incomplete, dict):
            missing = list(incomplete.get("required", [])) + list(
                incomplete.get("optional", [])
            )
        else:
            missing = list(incomplete)
        if missing:
            logger.warning(
                "%s: incomplete input - skipping tick. Not ready: %s",
                self._name,
                ", ".join(missing),
            )
            return None

        overrides = self._mapper.map(payload)

        output = dict(payload)
        output.update(overrides)
        return output
