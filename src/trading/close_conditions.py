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
) -> str | None:
    """Check if position should be closed. Returns reason string or None.

    Close logic (aggressive profit capture for memecoins):
    1. Rug detected → immediate close
    2. Stop loss: -50% from entry → close
    3. Take profit: >= 2x from entry → close (capture gains before dump)
    4. Trailing stop: after hitting 1.5x, close if price drops 20% from max
    5. Early stop: if < -20% after 30 minutes → close early (not recovering)
    6. Timeout: hours max → close
    """
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
