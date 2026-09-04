"""TSD (Tsd) 机器人硬件适配器 for R2C SDK.

基于 xapi-python 包暴露的 TSD 控制器控制能力，
接入 R2C SDK 的 ``IRobotHardwareAdapter`` 统一接口。

常用启动命令：
``python -m cloudrobo_r2c.cloudroboclient --project-id test --device-id tsd --client-config config/client_config.yaml --robot-config config/robot_tsd_config.yaml``
"""

from __future__ import annotations

from cloudrobo_r2c.robots.tsd.tsd_hardware_adapter import TSDHardwareAdapter
from cloudrobo_r2c.robots.tsd.tsd_config_validator import validate_tsd_config

__all__ = ["TSDHardwareAdapter", "validate_tsd_config"]
