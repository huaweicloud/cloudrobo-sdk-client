"""Skill loader: SKILL.md frontmatter parsing."""
import re
from pathlib import Path
from typing import Dict

import yaml


def _parse_frontmatter(path: Path) -> Dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---", text, re.DOTALL)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}
