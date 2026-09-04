import logging
import os
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict

import yaml

logger = logging.getLogger(__name__)

USER_CONFIG_DIR = Path.home() / ".cloudrobo"
USER_CONFIG_PATH = USER_CONFIG_DIR / "config.yaml"

_FIELDS_TO_CLEAR = [
    ("cloudrobo", "auth", "ak"),
    ("cloudrobo", "auth", "sk"),
    ("cloudrobo", "proxy", "http"),
    ("cloudrobo", "proxy", "https"),
    ("cloudrobo", "proxy", "no_proxy"),
    ("debug", "ca_bundle"),
]


def _load_project_config() -> Dict[str, Any]:
    try:
        config_file = files("cloudrobo_core").joinpath("config.yaml")
        with open(str(config_file), "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except (FileNotFoundError, yaml.YAMLError) as e:
        logger.debug("Failed to load project config via importlib: %s", e)
    fallback = Path(__file__).resolve()
    for _ in range(6):
        fallback = fallback.parent
        candidate = fallback / "config.yaml"
        if candidate.exists():
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except (FileNotFoundError, yaml.YAMLError) as e:
                logger.debug("Failed to load fallback config %s: %s", candidate, e)
            continue
    return {}


def _generate_user_template() -> str:
    data = _load_project_config()
    for path in _FIELDS_TO_CLEAR:
        node = data
        for key in path[:-1]:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                node = None
                break
        if node is not None and isinstance(node, dict):
            node[path[-1]] = ""
    header = "# CloudRobo 用户配置（优先级高于工程目录 config/config.yaml）\n"
    header += "# 在此填入你的 AK/SK，无需修改工程目录下的配置\n\n"
    return header + yaml.dump(data, allow_unicode=True, default_flow_style=False)


def ensure_user_config() -> Path:
    if not USER_CONFIG_PATH.exists():
        USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        USER_CONFIG_PATH.write_text(_generate_user_template(), encoding="utf-8")
        try:
            os.chmod(USER_CONFIG_PATH, 0o600)
        except OSError:
            pass
        logger.info("Created user config template: %s", USER_CONFIG_PATH)
    return USER_CONFIG_PATH


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result
