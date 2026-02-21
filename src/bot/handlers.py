"""Telegram bot command handlers."""

import html

from aiogram import Router
from aiogram.filters import BaseFilter, Command
from aiogram.types import Message

from src.bot.formatters import (
    format_portfolio,
    format_signals,
    format_stats,
    format_token_detail,
)
from src.db.database import async_session_factory

router = Router()

MAX_ADDRESS_LEN = 64


class AdminFilter(BaseFilter):
    """Only allow messages from the configured admin user."""

    async def __call__(self, message: Message) -> bool:
        from config.settings import settings

        admin_id = settings.telegram_admin_id
        if not admin_id:
            return True  # no admin configured = allow all (dev mode)
        return message.from_user is not None and message.from_user.id == admin_id


# Apply admin filter to all handlers in this router
router.message.filter(AdminFilter())


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Welcome message with current stats."""
    async with async_session_factory() as session:
        stats = await format_stats(session)

    await message.answer(
        f"<b>Memcoin Scanner Bot</b>\n\n"
        f"Commands:\n"
        f"/signals — Recent strong_buy/buy signals\n"
        f"/portfolio — Paper trading P&L\n"
        f"/token &lt;address&gt; — Token details\n"
        f"/stats — Pipeline statistics\n\n"
        f"{stats}",
        parse_mode="HTML",
    )


@router.message(Command("signals"))
async def cmd_signals(message: Message) -> None:
    """Show recent signals."""
    async with async_session_factory() as session:
        text = await format_signals(session)
    await message.answer(text, parse_mode="HTML")


@router.message(Command("portfolio"))
async def cmd_portfolio(message: Message) -> None:
    """Show paper trading portfolio."""
    async with async_session_factory() as session:
        text = await format_portfolio(session)
    await message.answer(text, parse_mode="HTML")


@router.message(Command("token"))
async def cmd_token(message: Message) -> None:
    """Show token details by address."""
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.answer("Usage: /token &lt;address&gt;", parse_mode="HTML")
        return

    address = html.escape(args[1].strip()[:MAX_ADDRESS_LEN])
    async with async_session_factory() as session:
        text = await format_token_detail(session, address)
    await message.answer(text, parse_mode="HTML")


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Show pipeline stats."""
    async with async_session_factory() as session:
        text = await format_stats(session)
    await message.answer(text, parse_mode="HTML")
