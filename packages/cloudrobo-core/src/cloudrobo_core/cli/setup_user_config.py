import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

USER_CLOUDROBO_DIR = Path.home() / ".cloudrobo"
USER_CONFIG_PATH = USER_CLOUDROBO_DIR / "config.yaml"

_DEFAULT_USER_CONFIG = """\
# CloudRobo 用户配置（优先级高于工程目录 config/config.yaml）
# 在此填入你的 AK/SK，无需修改工程目录下的配置

cloudrobo:
  auth:
    ak: ""
    sk: ""
"""


def ensure_user_config() -> Path:
    USER_CLOUDROBO_DIR.mkdir(parents=True, exist_ok=True)
    if not USER_CONFIG_PATH.exists():
        USER_CONFIG_PATH.write_text(_DEFAULT_USER_CONFIG, encoding="utf-8")
        logger.info("Created user config: %s", USER_CONFIG_PATH)
    return USER_CONFIG_PATH


def _post_install():
    ensure_user_config()
    secrets_dir = USER_CLOUDROBO_DIR / "conversations"
    secrets_dir.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    _post_install()
