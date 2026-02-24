"""Shared close-condition logic for paper and real trading.

Extracted from PaperTrader._check_close_conditions() to avoid duplication.
Both PaperTrader and RealTrader delegate to this pure function.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.models.trade import Position


def check_close_conditions(
    pos: Position,
    current_price: Decimal,
    is_rug: bool,
    now: datetime,
    *,
    take_profit_x: float = 2.0,
    stop_loss_pct: float = -50.0,
    timeout_hours: int = 8,
    liquidity_usd: float | None = None,
    liquidity_grace_period_sec: int = 90,
    is_dead_price: bool = False,
) -> str | None:
    """Check if position should be closed. Returns reason string or None.

    Close logic (aggressive profit capture for memecoins):
    0. Liquidity removed → immediate close (can't sell, total loss)
    1. Rug detected → immediate close
    2. Stop loss: -50% from entry → close
    3. Take profit: >= 2x from entry → close (capture gains before dump)
    4. Trailing stop: after hitting 1.5x, close if price drops 20% from max
    5. Early stop: if < -20% after 30 minutes → close early (not recovering)
    6. Timeout: hours max → close
    """
    # Liquidity critically low — pool drained, can't sell without massive slippage
    if liquidity_usd is not None and liquidity_usd < 5_000:
        # Phase 30: Dead-price tokens (Birdeye stale >10min) should close immediately.
        # Their price is frozen at last known value, so price_healthy check is invalid.
        # Birdeye stops updating price when token migrates or dies — if we've confirmed
        # the token is dead AND liquidity is 0, close unconditionally.
        if is_dead_price and liquidity_usd == 0.0:
            return "liquidity_removed"

        # Phase 37: Price-coherence check.
        # Birdeye multi_price often returns bonding-curve liquidity (near-zero)
        # instead of real DEX pool liquidity for migrated pump.fun tokens.
        # A real LP removal crashes price instantly (90%+ drop).
        # If price is still >= 50% of entry, the token is alive — data is wrong.
        _price_healthy = (
            pos.entry_price
            and pos.entry_price > 0
            and current_price >= pos.entry_price * Decimal("0.5")
        )

        if _price_healthy:
            # Price hasn't crashed 50%+ → token is likely alive, liq data unreliable
            pass
        elif liquidity_usd == 0.0 and pos.opened_at:
            # Exact zero on fresh position → indexing lag grace period
            age_sec = (now - pos.opened_at).total_seconds()
            if age_sec <= liquidity_grace_period_sec:
                pass  # Skip — data source likely hasn't indexed yet
            else:
                return "liquidity_removed"
        else:
            # Price crashed AND low liquidity → genuine LP drain or rug
            return "liquidity_removed"

    if is_rug:
        return "rug"

    if pos.entry_price and pos.entry_price > 0:
        multiplier = float(current_price / pos.entry_price)
        pnl_pct = float((current_price - pos.entry_price) / pos.entry_price * 100)

        # Hard take profit (capture gains before dump)
        if multiplier >= take_profit_x:
            return "take_profit"

        # Trailing stop: after 1.5x, protect gains — close if 20% drawdown from max
        if pos.max_price and pos.max_price > 0:
            max_mult = float(pos.max_price / pos.entry_price)
            if max_mult >= 1.5:
                drawdown_from_max = float(
                    (pos.max_price - current_price) / pos.max_price * 100
                )
                if drawdown_from_max >= 20:
                    # Price gap: if actual PnL is worse than stop loss,
                    # report as stop_loss for accurate reason tracking
                    if pnl_pct <= stop_loss_pct:
                        return "stop_loss"
                    return "trailing_stop"

        # Hard stop loss
        if pnl_pct <= stop_loss_pct:
            return "stop_loss"

        # Early stop: cut losses faster in first 30 minutes
        if pos.opened_at:
            age = now - pos.opened_at
            if age <= timedelta(minutes=30) and pnl_pct <= -20:
                return "early_stop"

    # Timeout
    if pos.opened_at:
        age = now - pos.opened_at
        if age >= timedelta(hours=timeout_hours):
            return "timeout"

    return None
