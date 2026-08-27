import sys
from typing import TYPE_CHECKING

from loguru import logger

from app.core.config import settings

if TYPE_CHECKING:
    from loguru import Logger


def setup_logging() -> "Logger":
    logger.remove()

    log_level = "DEBUG" if settings.ENVIRONMENT == "development" else "INFO"

    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=log_level,
        colorize=True,
    )

    # File sink is opt-out. Locally it is convenient; in a container it is not:
    # it writes into the container's writable layer, so the logs are invisible to
    # `docker logs`, are lost when the container is recreated, and bypass the
    # json-file rotation configured in docker-compose.prod.yml. Production sets
    # LOG_FILE_PATH="" and relies on stderr -> Docker's log driver.
    # Default is unchanged ("logs/app.log"), so local behaviour is identical.
    if settings.LOG_FILE_PATH:
        logger.add(
            settings.LOG_FILE_PATH,
            rotation="10 MB",
            retention="1 week",
            level=log_level,
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
        )

    return logger


log = setup_logging()
