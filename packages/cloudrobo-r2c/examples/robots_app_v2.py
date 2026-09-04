#!/usr/bin/env python3
"""机器人仿真环境启动脚本。

通过读取固定路径的配置文件启动机器人仿真环境。
支持多种机器人型号，通过ROBOT_TYPE环境变量指定。
"""

import logging
import os
import sys
import threading
from pathlib import Path

# 添加项目根目录到Python路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from cloudrobo_r2c.client import R2CClient
from cloudrobo_r2c.sync_client import SyncRobotClient

# 机器人型号配置（环境变量默认值为so101）
ROBOT_TYPE = os.getenv("ROBOT_TYPE", "so101")

# 机器人配置文件路径映射
ROBOT_CONFIGS = {
    "so101": str(PROJECT_ROOT / "config" / "robot_so101_simulation_config.yaml"),
    "qinglong": str(PROJECT_ROOT / "config" / "robot_qinglong_simulation_config.yaml"),
    "moz1": str(PROJECT_ROOT / "config" / "robot_moz1_simulation_config.yaml"),
    "jaka": str(PROJECT_ROOT / "config" / "robot_jaka_simulation_config.yaml"),
}

# 获取当前机器人配置文件路径
ROBOT_CONFIG_PATH = ROBOT_CONFIGS.get(ROBOT_TYPE)
if ROBOT_CONFIG_PATH is None:
    print(f"Error: Unsupported robot type '{ROBOT_TYPE}'")
    print(f"Supported robot types: {', '.join(ROBOT_CONFIGS.keys())}")
    sys.exit(1)

LOG_LEVEL = "info"

# 配置日志
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(lineno)d - %(message)s"
)
logger = logging.getLogger(__file__)

logger.info(f"Working directory: {Path.cwd()}")
logger.info(f"Project root: {PROJECT_ROOT}")
logger.info(f"Robot type: {ROBOT_TYPE}")


def main():
    """主函数。"""
    # 检查配置文件是否存在
    robot_config_path = Path(ROBOT_CONFIG_PATH)
    if not robot_config_path.exists():
        logger.error(f"Robot configuration file not found: {ROBOT_CONFIG_PATH}")
        sys.exit(1)

    # 加载配置文件
    import yaml
    with open(ROBOT_CONFIG_PATH, 'r', encoding='utf-8') as f:
        robot_config = yaml.safe_load(f)

    logger.info("=" * 50)
    logger.info(f"Robot Environment Starting - {ROBOT_TYPE}")
    logger.info("=" * 50)
    logger.info(f"Robot configuration: {ROBOT_CONFIG_PATH}")
    logger.info(f"Log level: {LOG_LEVEL}")
    logger.info("=" * 50)
    try:
        # 创建R2C客户端（仿真环境不需要证书包）
        logger.info("Creating R2C client for simulation environment")
        from cloudrobo_r2c.common.config import ClientConfig

        # 根据机器人型号动态生成客户端配置
        client_config = ClientConfig(
            project_id="test-tenant",
            device_id=f"{ROBOT_TYPE}-simulation-01",
            client_id=f"{ROBOT_TYPE}-simulation-client",
            endpoints=["tcp/0.0.0.0:7447"],
            mode="peer",
            protocol="zenoh"
        )
        session = R2CClient.connect(client_config)

        # 创建同步机器人客户端
        logger.info("Creating sync robot client")
        robot_client = SyncRobotClient.from_config(
            session=session,
            robot_config=robot_config,
        )

        # 启动客户端
        logger.info("Starting robot client")
        robot_client.hardware_adapter.connect()
        robot_client.start()

        # 主循环
        logger.info("Robot client started, running main loop...")
        stop_event = threading.Event()
        try:
            while not stop_event.wait(timeout=1.0):
                pass
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
        finally:
            logger.info("Stopping robot client")
            robot_client.stop()
            try:
                robot_client.hardware_adapter.disconnect()
            finally:
                session.close()

    except KeyboardInterrupt:
        logger.info("Program interrupted by user")
    except Exception as e:
        logger.error(f"Startup failed: {e}")
        raise


if __name__ == "__main__":
    main()
