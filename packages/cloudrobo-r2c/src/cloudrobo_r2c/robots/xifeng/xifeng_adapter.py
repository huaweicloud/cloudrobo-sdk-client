"""R2C SDK 注入点：XifengHardwareAdapter（隙锋人形机器人适配器）。

继承自 ``cloudrobo_r2c.robots.vendor_sdk_hardware_adapter.VendorSDKHardwareAdapter``，
内置一个根据配置选择 mock / real 实现的 ``robot_factory``。

本子包是**自包含**的：只依赖 cloudrobo_r2c 内置模块、
numpy 与（real 模式下延迟导入的）pypilot，不依赖任何独立 py 文件。

通过 entry_point 注册（``hardware.type: xifeng``），无需 class_path。

启动方式（推荐）：

    python -m cloudrobo_r2c.cloudroboclient \\
        --bundle secrets/cert_xxx.zip \\
        --robot-config config/robot_xifeng_config.yaml

config schema（在 robot_xifeng_config.yaml 的 hardware.config 下）::

    hardware:
      type: xifeng
      config:
        source: mock          # mock | real
        dry_run: true         # 调试期只打印 action 不下发真机；测通后改 false
        real:
          robot_host: "192.168.1.100"
          arm_local_ip: "192.168.1.10"
          arm_ip: "192.168.1.200"
          arm_port: 8080
        mock:
          max_joint_speed_rad_s: 1.5
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from cloudrobo_r2c.common.utils import summarize_observation_for_log
from cloudrobo_r2c.core.interfaces import IRobotHardwareAdapter
from cloudrobo_r2c.robots.vendor_sdk_hardware_adapter import VendorSDKHardwareAdapter

from .robot_interface import XifengRobotProtocol
from .robot_mock import MockXifengRobot

logger = logging.getLogger(__name__)


def _build_xifeng_robot(config: Mapping[str, Any]) -> XifengRobotProtocol:
    """根据 config 创建 mock 或 real Robot。

    config 是 cloudroboclient 通过 ``hardware.config`` 透传过来的 dict。
    """
    source = str(config.get("source", "mock")).strip().lower()
    if source == "real":
        # 延迟导入：避免没有 pypilot 时 mock 路径也报错
        from .robot_real import RealXifengRobot

        real_cfg = config.get("real") or {}
        return RealXifengRobot(**real_cfg)

    if source == "mock":
        mock_cfg = config.get("mock") or {}
        return MockXifengRobot(
            max_joint_speed_rad_s=float(mock_cfg.get("max_joint_speed_rad_s", 1.5)),
        )

    raise ValueError(
        f"Unknown xifeng source: {source!r}. Expected 'mock' or 'real'."
    )


def create_xifeng_adapter(
    config: Mapping[str, Any], **extra_kwargs: Any
) -> IRobotHardwareAdapter:
    """Entry_point factory for XifengHardwareAdapter."""
    return XifengHardwareAdapter(config=dict(config))


class XifengHardwareAdapter(VendorSDKHardwareAdapter):
    """R2C SDK 边缘端硬件适配器入口（隙锋人形机器人）。

    通过 entry_point 注册（``hardware.type: xifeng``），由 R2C SDK 的
    ``RobotFactory`` 通过 ``hardware.config`` 注入实例。

    实现策略：
    - 继承 R2C 内置 ``VendorSDKHardwareAdapter``，复用其 connect/disconnect/
      get_observation/send_action 转发
    - 注入自定义 ``robot_factory`` 让其根据 ``source=mock|real`` 切换实现
    - 叠加 ``dry_run`` 调试开关：开启时只打印 observation/action，不向真机下发动作
    """

    def __init__(self, config: Mapping[str, Any] | None = None, **kwargs: Any) -> None:
        # R2C RobotFactory(entry_point) 用 ``factory(config=<hardware.config 整体>)``
        # 方式实例化（见 robot_factory.py _try_entry_point_adapter）；
        # 同时兼容直接 splat 调用 ``XifengHardwareAdapter(source="mock", dry_run=True)`` 便于单测。
        merged: dict[str, Any] = dict(config) if config else {}
        merged.update(kwargs)
        super().__init__(
            config=merged,
            robot_factory=_build_xifeng_robot,
        )
        # dry_run：调试期只打印 action 不下发真机。默认 False；交付 config 置 true，
        # 测通后改 false（见 r2c-custom-robot.md §7 测试流程）。
        self._dry_run = bool(merged.get("dry_run", False))
        # 缓存最近一次 observation，供 dry_run 日志比对关节名/数值用
        self._last_observation: Mapping[str, Any] = {}
        logger.info(
            "XifengHardwareAdapter initialized (source=%s, dry_run=%s)",
            merged.get("source", "mock"),
            self._dry_run,
        )

    def get_observation(self) -> Mapping[str, Any]:
        """转发父类的 observation 读取，并缓存供 dry_run 比对。"""
        obs = super().get_observation()
        self._last_observation = obs
        return obs

    def send_action(self, command: Mapping[str, Any]) -> None:
        """转发 action 给底层 robot；dry_run 模式下只打印不执行。

        dry_run 日志同时输出 observation（排除图像 ndarray）与 action，
        便于比对通信链路与数据格式是否正确（见 r2c-custom-robot.md §9）。
        """
        if self._dry_run:
            logger.info(
                "[DRY_RUN] observation: %s",
                summarize_observation_for_log(self._last_observation),
            )
            logger.info("[DRY_RUN] action: %s", command)
            return
        super().send_action(command)
