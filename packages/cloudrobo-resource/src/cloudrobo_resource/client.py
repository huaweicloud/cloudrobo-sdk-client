# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

import logging
import os
from typing import Dict

from cloudrobo_core.sdk import BaseClient
from cloudrobo_core.sdk.exceptions import validate_safe_id

logger = logging.getLogger(__name__)


def is_debug_mode() -> bool:
    """检测是否处于 debug 模式"""
    env_val = os.environ.get("CLOUDROBO_DEBUG", "").lower()
    if env_val in ("1", "true", "yes"):
        return True
    return logger.isEnabledFor(logging.DEBUG)


class ResourceError(Exception):
    """资源管理模块自定义异常"""

    def __init__(self, message: str, suggestion: str = ""):
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)

    def get_user_message(self) -> str:
        """获取用户友好的错误消息"""
        if self.suggestion:
            return f"{self.message}\n\n建议: {self.suggestion}"
        return self.message


class ResourceClient(BaseClient):
    SERVICE = "cloudrobo-service"

    def list_quotas(self, **params) -> Dict:
        return self._client.get(self._url("/v1/resources/quotas"), params=params)

    def list_pools(self, **params) -> Dict:
        return self._client.get(self._url("/v1/resources/pools"), params=params)

    def show_pool(self, pool_id: str) -> Dict:
        validate_safe_id(pool_id, "pool_id")
        return self._client.get(self._url(f"/v1/resources/pools/{pool_id}"))
