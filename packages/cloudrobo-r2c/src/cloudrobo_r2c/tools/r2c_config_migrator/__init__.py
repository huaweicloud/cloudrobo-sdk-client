"""r2c_config 格式迁移工具（旧 model_feature_mapping → 新 v3.4 routes[]）。

自包含的纯数据格式转换，不依赖 skill-engine，仅依赖标准库（JSON/YAML 解析）
与可选 cloudrobo_r2c 校验。

对外接口：
    - convert_r2c_config(old_config: dict) -> dict   纯函数，old → 新 v3.4 dict
    - from_json_file / to_yaml_file                  文件读写辅助
    - cli 入口见 cli.py
"""

from .converter import (
    convert_r2c_config,
    convert_legacy_to_routes,
    build_default_route,
    convert_input_features,
    convert_output_features,
    parse_mapping_path,
)

__all__ = [
    "convert_r2c_config",
    "convert_legacy_to_routes",
    "build_default_route",
    "convert_input_features",
    "convert_output_features",
    "parse_mapping_path",
]

__version__ = "1.0.0"
