"""Entry point for the memcoin-trader parser."""

import asyncio
import signal

from loguru import logger

from src.bot.bot import stop_bot
from src.db.redis import close_redis
from src.parsers.worker import run_parser
from src.utils.logger import setup_logger


async def main() -> None:
    setup_logger(level="INFO")
    logger.info("Starting memcoin-trader parser...")

    # Graceful shutdown on SIGINT/SIGTERM
    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    parser_task = asyncio.create_task(run_parser())

    # Wait for either parser to finish or shutdown signal
    done, pending = await asyncio.wait(
        [parser_task, asyncio.create_task(shutdown_event.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Cancel remaining tasks
    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await stop_bot()
    await close_redis()
    logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
