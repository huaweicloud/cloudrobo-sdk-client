# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

WORKSPACE_CONFIG_DIR = Path.home() / ".cloudrobo"
WORKSPACE_PATH = WORKSPACE_CONFIG_DIR / "workspace.json"


def load_workspace() -> dict[str, Any]:
    if WORKSPACE_PATH.exists():
        try:
            return json.loads(WORKSPACE_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to load workspace.json: %s", e)
    return {}


def save_workspace(data: dict[str, Any]) -> None:
    WORKSPACE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(WORKSPACE_PATH, 0o600)
    except OSError:
        pass
