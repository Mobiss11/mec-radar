"""Format database queries into Telegram HTML messages."""

import html
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.signal import Signal
from src.models.token import Token, TokenSecurity, TokenSnapshot
from src.models.trade import Position


async def format_signals(session: AsyncSession, limit: int = 10) -> str:
    """Format recent strong_buy/buy signals as HTML."""
    stmt = (
        select(Signal, Token)
        .join(Token, Signal.token_id == Token.id)
        .where(Signal.status.in_(["strong_buy", "buy"]))
        .order_by(Signal.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = result.all()

    if not rows:
        return "<b>No active signals</b>\nWaiting for high-score tokens..."

    lines = ["<b>Recent Signals</b>\n"]
    for signal, token in rows:
        emoji = "🟢🟢" if signal.status == "strong_buy" else "🟢"
        name = html.escape(token.symbol or signal.token_address[:12])
        mcap = f"${int(signal.token_mcap_at_signal):,}" if signal.token_mcap_at_signal else "?"
        age = _relative_time(signal.created_at)
        lines.append(
            f"{emoji} <b>{name}</b> score={signal.score} mcap={mcap} ({age})\n"
            f"<code>{signal.token_address}</code>"
        )

    return "\n\n".join(lines)


async def format_portfolio(session: AsyncSession) -> str:
    """Format paper trading portfolio summary."""
    # Open positions
    open_stmt = select(Position, Token).join(
        Token, Position.token_id == Token.id
    ).where(Position.status == "open", Position.is_paper == 1)
    open_result = await session.execute(open_stmt)
    open_rows = list(open_result.all())

    # Closed positions stats
    closed_stmt = select(
        func.count(Position.id),
        func.sum(Position.pnl_usd),
    ).where(Position.status == "closed", Position.is_paper == 1)
    closed_result = await session.execute(closed_stmt)
    closed_count, total_pnl = closed_result.one()

    lines = ["<b>Paper Trading Portfolio</b>\n"]

    if open_rows:
        lines.append(f"<b>Open ({len(open_rows)}):</b>")
        for pos, token in open_rows:
            name = html.escape(token.symbol or pos.token_address[:12])
            pnl = f"{pos.pnl_pct:+.1f}%" if pos.pnl_pct else "0%"
            emoji = "📈" if pos.pnl_pct and pos.pnl_pct > 0 else "📉"
            lines.append(f"  {emoji} {name}: {pnl} ({pos.amount_sol_invested} SOL)")
    else:
        lines.append("No open positions")

    pnl_str = f"${float(total_pnl or 0):,.2f}"
    lines.append(f"\n<b>Closed:</b> {closed_count or 0} trades, P&L: {pnl_str}")

    return "\n".join(lines)


async def format_token_detail(session: AsyncSession, address: str) -> str:
    """Format detailed token info."""
    stmt = select(Token).where(Token.address == address)
    result = await session.execute(stmt)
    token = result.scalar_one_or_none()
    if not token:
        return f"Token <code>{address[:16]}</code> not found"

    # Latest snapshot
    snap_stmt = (
        select(TokenSnapshot)
        .where(TokenSnapshot.token_id == token.id)
        .order_by(TokenSnapshot.timestamp.desc())
        .limit(1)
    )
    snap = (await session.execute(snap_stmt)).scalar_one_or_none()

    # Security
    sec_stmt = select(TokenSecurity).where(TokenSecurity.token_id == token.id)
    sec = (await session.execute(sec_stmt)).scalar_one_or_none()

    lines = [f"<b>{html.escape(token.symbol or token.address[:12])}</b>"]
    if token.name:
        lines.append(f"Name: {html.escape(token.name)}")
    lines.append(f"Address: <code>{token.address}</code>")
    lines.append(f"Source: {token.source or '?'}")

    if snap:
        price = f"${float(snap.price):,.8f}" if snap.price else "?"
        mcap = f"${int(snap.market_cap):,}" if snap.market_cap else "?"
        liq = f"${int(snap.liquidity_usd):,}" if snap.liquidity_usd else "?"
        lines.append(f"\nPrice: {price}")
        lines.append(f"MCap: {mcap}")
        lines.append(f"Liquidity: {liq}")
        lines.append(f"Holders: {snap.holders_count or '?'}")
        if snap.score is not None:
            lines.append(f"Score v2: {snap.score}")
        if snap.score_v3 is not None:
            lines.append(f"Score v3: {snap.score_v3}")

    if sec:
        flags = []
        if sec.is_honeypot:
            flags.append("⛔ HONEYPOT")
        if sec.is_mintable:
            flags.append("⚠️ Mintable")
        if sec.lp_burned:
            flags.append("✅ LP burned")
        if sec.contract_renounced:
            flags.append("✅ Renounced")
        if flags:
            lines.append(f"\nSecurity: {', '.join(flags)}")

    return "\n".join(lines)


async def format_stats(session: AsyncSession) -> str:
    """Format pipeline statistics."""
    # Token count
    token_count = (await session.execute(
        select(func.count(Token.id))
    )).scalar_one()

    # Signals today
    today_start = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    signals_today = (await session.execute(
        select(func.count(Signal.id)).where(Signal.created_at >= today_start)
    )).scalar_one()

    strong_buy_today = (await session.execute(
        select(func.count(Signal.id)).where(
            Signal.created_at >= today_start,
            Signal.status == "strong_buy",
        )
    )).scalar_one()

    # Open positions
    open_positions = (await session.execute(
        select(func.count(Position.id)).where(
            Position.status == "open", Position.is_paper == 1
        )
    )).scalar_one()

    return (
        "<b>Pipeline Stats</b>\n\n"
        f"Tokens tracked: {token_count:,}\n"
        f"Signals today: {signals_today} (🟢🟢 {strong_buy_today})\n"
        f"Open positions: {open_positions}\n"
    )


def _relative_time(dt: datetime | None) -> str:
    """Format datetime as relative time string."""
    if dt is None:
        return "?"
    now = datetime.now(UTC).replace(tzinfo=None)
    diff = now - dt
    minutes = int(diff.total_seconds() / 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"
