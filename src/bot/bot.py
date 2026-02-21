"""Telegram bot lifecycle — aiogram 3.x polling mode.

Starts the bot in polling mode as an asyncio task alongside the parser.
Only runs if telegram_bot_token is configured.
"""

import os

from loguru import logger

_bot_instance = None
_dp_instance = None


def get_bot():
    """Get or create the aiogram Bot singleton."""
    global _bot_instance
    if _bot_instance is None:
        from aiogram import Bot

        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            from config.settings import settings
            token = settings.telegram_bot_token
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN not configured")
        _bot_instance = Bot(token=token)
    return _bot_instance


def get_dispatcher():
    """Get or create the Dispatcher singleton with handlers registered."""
    global _dp_instance
    if _dp_instance is None:
        from aiogram import Dispatcher
        from src.bot.handlers import router

        _dp_instance = Dispatcher()
        _dp_instance.include_router(router)
    return _dp_instance


async def run_bot() -> None:
    """Start the Telegram bot in polling mode.

    Runs indefinitely as an asyncio task. Designed to be launched via
    asyncio.create_task() in the main parser worker.
    """
    try:
        bot = get_bot()
        dp = get_dispatcher()
        logger.info("[BOT] Starting Telegram bot (polling mode)")
        await dp.start_polling(bot, close_bot_session=False)
    except RuntimeError as e:
        logger.warning(f"[BOT] Cannot start: {e}")
    except Exception as e:
        logger.error(f"[BOT] Fatal error: {e}")


async def stop_bot() -> None:
    """Gracefully stop the bot."""
    global _bot_instance
    if _bot_instance:
        await _bot_instance.session.close()
        _bot_instance = None
