"""
Startup script for Ship container.
This script runs when the container starts to restore user accounts.
"""

import asyncio
import logging
from components.user_manager import UserManager

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def startup():
    """启动时初始化任务"""
    logger.info("Starting Ship container initialization...")

    # 恢复所有用户账户
    restored_count = await UserManager.restore_all_users()
    logger.info(f"User restoration completed: {restored_count} users restored")

    logger.info("Ship container initialization completed")


if __name__ == "__main__":
    asyncio.run(startup())
