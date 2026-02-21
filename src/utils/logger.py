import os
import sys

from loguru import logger


def setup_logger(*, json_logs: bool = False, level: str = "INFO") -> None:
    """Configure loguru for the application.

    Console level controlled by LOG_LEVEL env (default: INFO).
    File always captures DEBUG for post-mortem analysis.
    """
    console_level = os.getenv("LOG_LEVEL", level).upper()
    logger.remove()

    if json_logs:
        logger.add(sys.stdout, serialize=True, level=console_level)
    else:
        logger.add(
            sys.stdout,
            format=(
                "<green>{time:HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan> - "
                "<level>{message}</level>"
            ),
            level=console_level,
            colorize=True,
        )

    logger.add(
        "logs/trader_{time:YYYY-MM-DD}.log",
        rotation="50 MB",
        retention="3 days",
        compression="gz",
        level="DEBUG",
        serialize=json_logs,
    )
