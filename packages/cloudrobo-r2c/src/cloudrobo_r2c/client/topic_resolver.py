"""Topic name templating: ``{project_id}/{device_id}/xxx`` placeholders.

Placeholders are resolved from the session's credential bundle identity.
"""

from __future__ import annotations

import re
from typing import Dict

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
ALLOWED_PLACEHOLDERS = ("project_id", "device_id")


class TopicResolver:
    """Resolve topic templates against a session's project/device ids."""

    def __init__(self, project_id: str, device_id: str) -> None:
        self._values: Dict[str, str] = {
            "project_id": str(project_id or ""),
            "device_id": str(device_id or ""),
        }

    def validate_template(self, template: str) -> None:
        if not template or not template.strip():
            raise ValueError("topic template must be a non-empty string")
        for name in _PLACEHOLDER_RE.findall(template):
            if name not in ALLOWED_PLACEHOLDERS:
                raise ValueError(
                    f"Unknown placeholder {{{name}}} in topic template "
                    f"{template!r}; allowed: {ALLOWED_PLACEHOLDERS}"
                )

    def resolve(self, template: str) -> str:
        self.validate_template(template)

        def _sub(match: "re.Match[str]") -> str:
            return self._values[match.group(1)]

        resolved = _PLACEHOLDER_RE.sub(_sub, template)
        return resolved
