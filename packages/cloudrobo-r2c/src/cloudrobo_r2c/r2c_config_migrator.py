"""r2c_config 格式迁移工具 —— `python -m cloudrobo_r2c.r2c_config_migrator` 入口模块。

作为 r2c_config 迁移 CLI 的统一入口，支持以模块方式启动：

    python -m cloudrobo_r2c.r2c_config_migrator old_config.json -o new_config.yaml

实际转换逻辑与命令行参数解析复用 ``cloudrobo_r2c.tools.r2c_config_migrator.cli.main``，
以保持行为与帮助信息完全兼容。

对外接口：
    - main(argv: Optional[list] = None) -> int   入口函数，等价于 cli.main
"""

from __future__ import annotations

import sys
from typing import List, Optional

from cloudrobo_r2c.tools.r2c_config_migrator.cli import main as _cli_main


def main(argv: Optional[List[str]] = None) -> int:
    """r2c_config 迁移 CLI 入口。

    等价于 ``cloudrobo_r2c.tools.r2c_config_migrator.cli.main``，仅为顶层模块暴露统一入口。
    """
    return _cli_main(argv)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
