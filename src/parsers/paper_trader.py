"""Paper trading engine — auto-open/close positions based on signals.

Opens a position when a strong_buy/buy signal fires. Updates P&L on
each enrichment cycle. Auto-closes on take_profit, stop_loss, timeout,
or rug detection. Sends alerts to Telegram on open/close.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from loguru import logger
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.signal import Signal
from src.models.trade import Position, Trade
from src.trading.close_conditions import check_close_conditions

if TYPE_CHECKING:
    from src.parsers.alerts import AlertDispatcher


class PaperTrader:
    """Manages paper trading positions."""

    def __init__(
        self,
        *,
        sol_per_trade: float = 0.5,
        max_positions: int = 10,
        take_profit_x: float = 2.0,
        stop_loss_pct: float = -50.0,
        timeout_hours: int = 4,
        alert_dispatcher: AlertDispatcher | None = None,
    ) -> None:
        self._sol_per_trade = Decimal(str(sol_per_trade))
        self._max_positions = max_positions
        self._take_profit_x = take_profit_x
        self._stop_loss_pct = stop_loss_pct
        self._timeout_hours = timeout_hours
        self._alerts = alert_dispatcher

    async def on_signal(
        self,
        session: AsyncSession,
        signal: Signal,
        price: Decimal | None,
        symbol: str | None = None,
        liquidity_usd: float | None = None,
        sol_price_usd: float | None = None,
    ) -> Position | None:
        """Open a paper position when a qualifying signal fires.

        Returns the new Position or None if skipped.
        """
        if signal.status not in ("strong_buy", "buy"):
            logger.info(f"[PAPER] Skipping signal {signal.token_address[:12]}: status={signal.status}")
            return None

        if price is None or price <= 0:
            logger.warning(f"[PAPER] Skipping signal {signal.token_address[:12]}: invalid price={price}")
            return None

        # Check max positions
        open_count = await self._count_open_positions(session)
        if open_count >= self._max_positions:
            logger.warning(f"[PAPER] Max positions reached ({open_count}/{self._max_positions}), skipping {signal.token_address[:12]}")
            return None

        # No duplicate position for same token
        existing = await session.execute(
            select(Position).where(
                Position.token_id == signal.token_id,
                Position.status == "open",
                Position.is_paper == 1,
            )
        )
        if existing.scalar_one_or_none() is not None:
            logger.info(f"[PAPER] Duplicate position for {signal.token_address[:12]}, skipping")
            return None

        # Volume-weighted entry: strong_buy = 1.5x, buy = 1.0x base size
        size_multiplier = Decimal("1.5") if signal.status == "strong_buy" else Decimal("1.0")
        invest_sol = self._sol_per_trade * size_multiplier

        # Entry slippage: if invest > 2% of liquidity, apply price penalty
        effective_price = price
        if liquidity_usd and liquidity_usd > 0 and sol_price_usd and sol_price_usd > 0:
            invest_usd = float(invest_sol) * sol_price_usd
            if invest_usd > liquidity_usd * 0.02:
                slippage_pct = min(invest_usd / liquidity_usd * 100, 50)
                effective_price = price * Decimal(str(1.0 + slippage_pct / 100))
                logger.debug(
                    f"[PAPER] Entry slippage {slippage_pct:.1f}% "
                    f"for {signal.token_address[:12]}"
                )

        amount_token = invest_sol / effective_price if effective_price > 0 else Decimal("0")

        # Create buy trade (records effective price after slippage)
        trade = Trade(
            signal_id=signal.id,
            token_id=signal.token_id,
            token_address=signal.token_address,
            side="buy",
            amount_sol=invest_sol,
            amount_token=amount_token,
            price=effective_price,
            is_paper=1,
            status="filled",
        )
        session.add(trade)

        # Create position (entry_price includes slippage impact)
        _sym = symbol or getattr(signal, "symbol", None) or signal.token_address[:12]
        position = Position(
            signal_id=signal.id,
            token_id=signal.token_id,
            token_address=signal.token_address,
            symbol=_sym,
            entry_price=effective_price,
            current_price=price,
            amount_token=amount_token,
            amount_sol_invested=invest_sol,
            pnl_pct=Decimal("0"),
            pnl_usd=Decimal("0"),
            max_price=price,
            status="open",
            is_paper=1,
        )
        session.add(position)

        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            logger.debug(f"[PAPER] Duplicate position for {signal.token_address[:12]}, skipping")
            return None

        logger.info(
            f"[PAPER] Opened {signal.status} {signal.token_address[:12]} "
            f"@ {price} ({invest_sol} SOL)"
        )

        if self._alerts:
            try:
                await self._alerts.send_paper_open(
                    symbol=_sym,
                    address=signal.token_address,
                    price=float(price),
                    sol_amount=float(self._sol_per_trade),
                    action=signal.status,
                )
            except Exception as e:
                logger.warning(f"[PAPER] Alert send failed: {e}")

        return position

    async def update_positions(
        self,
        session: AsyncSession,
        token_id: int,
        current_price: Decimal | None,
        is_rug: bool = False,
        liquidity_usd: float | None = None,
        sol_price_usd: float | None = None,
    ) -> None:
        """Update all open paper positions for a token."""
        if current_price is None or current_price <= 0:
            return

        result = await session.execute(
            select(Position).where(
                Position.token_id == token_id,
                Position.status == "open",
                Position.is_paper == 1,
            )
        )
        positions = list(result.scalars().all())

        now = datetime.now(UTC).replace(tzinfo=None)
        _sol_usd = Decimal(str(sol_price_usd)) if sol_price_usd else Decimal("150")

        for pos in positions:
            pos.current_price = current_price
            if pos.max_price is None or current_price > pos.max_price:
                pos.max_price = current_price

            # Calculate P&L
            if pos.entry_price and pos.entry_price > 0:
                pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
                pos.pnl_pct = pnl_pct
                if pos.amount_sol_invested:
                    # Convert SOL P&L to USD (SOL * pnl% * sol_price)
                    pos.pnl_usd = pos.amount_sol_invested * pnl_pct / 100 * _sol_usd

            # Check close conditions
            close_reason = self._check_close_conditions(pos, current_price, is_rug, now)
            if close_reason:
                await self._close_position(
                    session, pos, close_reason, current_price,
                    liquidity_usd=liquidity_usd,
                    sol_price_usd=float(_sol_usd),
                )

    def _check_close_conditions(
        self,
        pos: Position,
        current_price: Decimal,
        is_rug: bool,
        now: datetime,
    ) -> str | None:
        """Check if position should be closed. Returns reason or None.

        Delegates to shared close_conditions module (used by both paper and real trader).
        """
        return check_close_conditions(
            pos,
            current_price,
            is_rug,
            now,
            take_profit_x=self._take_profit_x,
            stop_loss_pct=self._stop_loss_pct,
            timeout_hours=self._timeout_hours,
        )

    async def _close_position(
        self,
        session: AsyncSession,
        pos: Position,
        reason: str,
        price: Decimal,
        liquidity_usd: float | None = None,
        sol_price_usd: float = 150.0,
    ) -> None:
        """Close a position and create a sell trade.

        If liquidity_usd is provided, estimates slippage impact on exit value.
        """
        pos.status = "closed"
        pos.close_reason = reason
        pos.closed_at = datetime.now(UTC).replace(tzinfo=None)
        pos.current_price = price

        # Create sell trade (amount_sol = exit value, not entry)
        exit_sol = pos.amount_sol_invested or Decimal("0")
        if pos.entry_price and pos.entry_price > 0 and price > 0:
            exit_sol = (pos.amount_token or Decimal("0")) * price

        # Slippage estimate: if exit value > 2% of liquidity, apply penalty
        if liquidity_usd and liquidity_usd > 0:
            exit_usd = float(exit_sol) * sol_price_usd
            if exit_usd > liquidity_usd * 0.02:
                # High slippage: mark reason and apply 10% haircut
                slippage_pct = min(exit_usd / liquidity_usd * 100, 50)
                exit_sol = exit_sol * Decimal(str(max(1.0 - slippage_pct / 100, 0.5)))
                pos.close_reason = f"{reason}+slippage"
        trade = Trade(
            signal_id=pos.signal_id,
            token_id=pos.token_id,
            token_address=pos.token_address,
            side="sell",
            amount_sol=exit_sol,
            amount_token=pos.amount_token,
            price=price,
            is_paper=1,
            status="filled",
        )
        session.add(trade)

        pnl = f"{pos.pnl_pct:+.1f}%" if pos.pnl_pct else "?"
        logger.info(
            f"[PAPER] Closed {pos.token_address[:12]} reason={reason} P&L={pnl}"
        )

        if self._alerts:
            try:
                await self._alerts.send_paper_close(
                    symbol=pos.symbol or pos.token_address[:12],
                    address=pos.token_address,
                    entry_price=float(pos.entry_price or 0),
                    exit_price=float(price),
                    pnl_pct=float(pos.pnl_pct or 0),
                    reason=reason,
                )
            except Exception as e:
                logger.warning(f"[PAPER] Close alert failed: {e}")

    async def sweep_stale_positions(self, session: AsyncSession) -> int:
        """Close positions that exceeded timeout_hours regardless of price updates.

        Should be called periodically (e.g. every 5 minutes) from the main loop.
        Returns the number of positions closed.
        """
        now = datetime.now(UTC).replace(tzinfo=None)
        cutoff = now - timedelta(hours=self._timeout_hours)

        result = await session.execute(
            select(Position).where(
                Position.status == "open",
                Position.is_paper == 1,
                Position.opened_at < cutoff,
            )
        )
        stale = list(result.scalars().all())

        for pos in stale:
            exit_price = pos.current_price or pos.entry_price or Decimal("0")
            await self._close_position(session, pos, "timeout", exit_price)

        if stale:
            logger.info(f"[PAPER] Swept {len(stale)} stale positions (>{self._timeout_hours}h)")

        return len(stale)

    async def _count_open_positions(self, session: AsyncSession) -> int:
        """Count currently open paper positions."""
        result = await session.execute(
            select(func.count(Position.id)).where(
                Position.status == "open",
                Position.is_paper == 1,
            )
        )
        return result.scalar_one()

    async def get_portfolio_summary(self, session: AsyncSession) -> dict:
        """Get aggregate portfolio stats for display."""
        # Open positions
        open_result = await session.execute(
            select(Position).where(
                Position.status == "open", Position.is_paper == 1
            )
        )
        open_positions = list(open_result.scalars().all())

        # Closed positions
        closed_result = await session.execute(
            select(Position).where(
                Position.status == "closed", Position.is_paper == 1
            )
        )
        closed_positions = list(closed_result.scalars().all())

        total_invested = sum(
            float(p.amount_sol_invested or 0) for p in open_positions + closed_positions
        )
        total_pnl = sum(float(p.pnl_usd or 0) for p in open_positions + closed_positions)
        wins = sum(1 for p in closed_positions if p.pnl_pct and p.pnl_pct > 0)
        losses = sum(1 for p in closed_positions if p.pnl_pct and p.pnl_pct <= 0)

        # Details for open positions
        open_details = [
            {
                "address": p.token_address,
                "symbol": p.symbol or p.token_address[:8],
                "pnl_pct": float(p.pnl_pct or 0),
                "entry_price": float(p.entry_price or 0),
                "current_price": float(p.current_price or 0),
                "sol_invested": float(p.amount_sol_invested or 0),
            }
            for p in open_positions
        ]

        # Best / worst closed trades
        best_trade = max(closed_positions, key=lambda p: float(p.pnl_pct or 0), default=None)
        worst_trade = min(closed_positions, key=lambda p: float(p.pnl_pct or 0), default=None)

        return {
            "open_count": len(open_positions),
            "closed_count": len(closed_positions),
            "total_invested_sol": total_invested,
            "total_pnl_usd": total_pnl,
            "win_rate": round(wins / max(wins + losses, 1) * 100, 1),
            "wins": wins,
            "losses": losses,
            "open_positions": open_details,
            "best_pnl_pct": float(best_trade.pnl_pct or 0) if best_trade else 0,
            "worst_pnl_pct": float(worst_trade.pnl_pct or 0) if worst_trade else 0,
        }
