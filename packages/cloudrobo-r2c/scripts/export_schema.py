#!/usr/bin/env python3
"""Schema 导出工具 (export_schema.py)
=====================================

作用
----
从机器人配置文件（robot_config.yaml）中提取 device_to_r2c / r2c_to_device
映射规则的 schema 描述，并导出为 JSON。基于
``r2c_sdk.common.schema.RobotConfigSchema``（见
.scratch/schema-interface-design.md）。

使用场景
--------
- Agent Skill / 文档生成器 / Dashboard 消费机器人的数据契约
- CI 校验 robot_config 与 cloud_config 的映射是否匹配

使用方法
--------
    python scripts/export_schema.py config/robot_dummy_config.yaml
    python scripts/export_schema.py config/robot_dummy_config.yaml \\
        -o schema.json
    python scripts/export_schema.py config/robot_ur5e_config.yaml \\
        --indent 4

参数
----
- config: 机器人配置文件路径（必填）
- -o/--output: 输出文件路径（省略时打印到 stdout）
- --indent: JSON 缩进空格数（默认 2）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from cloudrobo_r2c.common import RobotConfigSchema


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description=(
            "导出机器人配置的 device_to_r2c / r2c_to_device 映射 schema 为 JSON"
        ),
    )
    parser.add_argument(
        "config",
        help="机器人配置文件路径 (robot_config.yaml)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="输出文件路径；省略时打印到 stdout",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON 缩进空格数 (默认 2)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    # Resolve the config path against the config shipped inside the installed
    # package, while still honoring explicit / source-checkout relative paths.
    from cloudrobo_r2c.common.config_path import resolve_config_path

    config_path = Path(resolve_config_path(args.config))
    if not config_path.is_file():
        print(f"[Error] 配置文件不存在: {config_path}", file=sys.stderr)
        return 1

    try:
        schema = RobotConfigSchema.from_yaml(config_path)
    except Exception as exc:  # noqa: BLE001 — CLI 入口需兜底所有解析错误
        print(f"[Error] 解析失败: {exc}", file=sys.stderr)
        return 1

    payload = schema.to_json(indent=args.indent)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
        print(
            f"[Info] schema 已导出到 {args.output} "
            f"({len(schema.observation.fields)} 个观测字段 / "
            f"{len(schema.action.fields)} 个动作字段)"
        )
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
