import logging
from typing import Optional

from .config import Config

logger = logging.getLogger(__name__)


class AuthManager:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
