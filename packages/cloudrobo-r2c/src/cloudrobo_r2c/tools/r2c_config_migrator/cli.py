"""r2c_config 格式迁移 CLI。

读取旧格式 r2c_config JSON 文件，转换为新 v3.4 YAML 文件。

用法:
    python -m tools.r2c_config_migrator.cli old_config.json -o new_config.yaml
    python -m tools.r2c_config_migrator.cli old_config.json --route my_route -d "desc"

依赖:
    - PyYAML（输出 YAML 所需；若未安装先 pip install PyYAML）
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Optional

from .converter import convert_r2c_config


def from_json_file(path: str) -> Dict[str, Any]:
    """从 JSON 文件读取旧格式配置。"""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def to_yaml(config: Dict[str, Any]) -> str:
    """将新配置字典序列化为 YAML 字符串。"""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "PyYAML is required to output YAML. Install with: pip install PyYAML"
        ) from exc
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True)


def to_yaml_file(config: Dict[str, Any], path: str) -> None:
    """将新配置字典写入 YAML 文件。"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(to_yaml(config))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将旧 r2c_config（model_feature_mapping）转换为新 v3.4 routes[] 格式"
    )
    parser.add_argument("input", help="旧格式 r2c_config JSON 文件路径")
    parser.add_argument(
        "-o",
        "--output",
        help="输出 YAML 文件路径（默认自动生成 <input>.v34.yaml）",
    )
    parser.add_argument(
        "--route",
        default="default",
        help="生成的 default route 名称（默认 'default'）",
    )
    parser.add_argument(
        "-d",
        "--description",
        default=None,
        help="生成的 route 描述（可选）",
    )
    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        old_config = from_json_file(args.input)
    except (FileNotFoundError, IsADirectoryError, PermissionError) as exc:
        print(
            f"[r2c-config-migrator] 无法读取输入文件 {args.input}: {exc}",
            file=sys.stderr,
        )
        return 1
    new_config = convert_r2c_config(
        old_config, route_name=args.route, description=args.description
    )

    output_path = args.output
    if not output_path:
        base = args.input
        if base.endswith(".json"):
            base = base[: -len(".json")]
        output_path = base + ".v34.yaml"

    to_yaml_file(new_config, output_path)
    num_routes = len(new_config["routes"])
    print(
        f"[r2c-config-migrator] Converted {args.input} -> {output_path} "
        f"({num_routes} route(s), schema_version={new_config['schema_version']})"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
