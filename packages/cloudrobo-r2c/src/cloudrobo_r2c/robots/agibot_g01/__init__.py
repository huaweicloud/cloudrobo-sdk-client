"""AgiBot G01（智元精灵 G01）R2C 硬件适配器。

基于智元 GDK Python SDK（``a2d_sdk``）实现 ``IRobotHardwareAdapter``：
- :mod:`agibot_g01_client` — ``RobotDds`` / ``CosineCamera`` 的轻量封装
- :mod:`agibot_g01_hardware_adapter` — R2C 硬件适配器与入口点工厂
- :mod:`agibot_g01_mock_client` / :mod:`agibot_g01_mock_adapter` — 无真机/无
  GDK 环境下的全功能 mock 适配器
"""

from __future__ import annotations

from .agibot_g01_client import AgiBotG01Client
from .agibot_g01_hardware_adapter import (
    AgiBotG01HardwareAdapter,
    AgiBotG01ResetCommand,
    create_agibot_g01_adapter,
)
from .agibot_g01_mock_adapter import (
    AgiBotG01MockAdapter,
    create_agibot_g01_mock_adapter,
)
from .agibot_g01_mock_client import AgiBotG01MockClient

__all__ = [
    "AgiBotG01Client",
    "AgiBotG01HardwareAdapter",
    "AgiBotG01MockAdapter",
    "AgiBotG01MockClient",
    "AgiBotG01ResetCommand",
    "create_agibot_g01_adapter",
    "create_agibot_g01_mock_adapter",
]
