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
        trailing_activation_x: float = 1.3,
        trailing_drawdown_pct: float = 15.0,
        stagnation_timeout_min: float = 25.0,
        stagnation_max_pnl_pct: float = 15.0,
        alert_dispatcher: AlertDispatcher | None = None,
        micro_snipe_sol: float = 0.07,
        micro_snipe_max_positions: int = 5,
    ) -> None:
        self._sol_per_trade = Decimal(str(sol_per_trade))
        self._max_positions = max_positions
        self._take_profit_x = take_profit_x
        self._stop_loss_pct = stop_loss_pct
        self._timeout_hours = timeout_hours
        self._trailing_activation_x = trailing_activation_x
        self._trailing_drawdown_pct = trailing_drawdown_pct
        self._stagnation_timeout_min = stagnation_timeout_min
        self._stagnation_max_pnl_pct = stagnation_max_pnl_pct
        self._alerts = alert_dispatcher
        # Phase 51: micro-snipe params
        self._micro_snipe_sol = Decimal(str(micro_snipe_sol))
        self._micro_snipe_max = micro_snipe_max_positions

    async def on_signal(
        self,
        session: AsyncSession,
        signal: Signal,
        price: Decimal | None,
        symbol: str | None = None,
        liquidity_usd: float | None = None,
        sol_price_usd: float | None = None,
        lp_removed_pct: float | None = None,
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

        # Phase 30b: Block entry if LP already partially removed (scam in progress).
        # ALITA entered at MIN_5 with 49.98% LP removed → lost 100%.
        # All profitable positions had lp_removed = 0% at entry.
        if lp_removed_pct is not None and lp_removed_pct >= 30.0:
            logger.warning(
                f"[PAPER] Blocking entry for {signal.token_address[:12]}: "
                f"LP removed {lp_removed_pct:.1f}% (scam in progress)"
            )
            return None

        # Check max positions
        open_count = await self._count_open_positions(session)
        if open_count >= self._max_positions:
            logger.warning(f"[PAPER] Max positions reached ({open_count}/{self._max_positions}), skipping {signal.token_address[:12]}")
            return None

        # No duplicate position for same token (or top-up micro position)
        existing = await session.execute(
            select(Position).where(
                Position.token_id == signal.token_id,
                Position.status == "open",
                Position.is_paper == 1,
            )
        )
        existing_pos = existing.scalar_one_or_none()
        if existing_pos is not None:
            # Phase 51: if it's a micro-snipe position, top it up to full size
            if existing_pos.is_micro_entry == 1:
                return await self._topup_micro_position(
                    session, existing_pos, signal, price,
                    liquidity_usd, sol_price_usd,
                )
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

    async def on_prescan_entry(
        self,
        session: AsyncSession,
        token_id: int,
        token_address: str,
        symbol: str | None,
        price: Decimal,
        liquidity_usd: float | None = None,
        sol_price_usd: float | None = None,
    ) -> Position | None:
        """Phase 51: Open a micro-snipe position at PRE_SCAN (T+5s).

        Tiny position ($5-10) opened before full scoring — will be topped up
        to full size if INITIAL/MIN_2 confirms with buy/strong_buy signal.
        Returns Position or None if skipped.
        """
        if price <= 0:
            return None

        # Check max micro positions
        micro_count_result = await session.execute(
            select(func.count(Position.id)).where(
                Position.status == "open",
                Position.is_paper == 1,
                Position.is_micro_entry == 1,
            )
        )
        micro_count = micro_count_result.scalar_one()
        if micro_count >= self._micro_snipe_max:
            logger.debug(
                f"[MICRO] Max micro positions ({micro_count}/{self._micro_snipe_max}), "
                f"skipping {token_address[:12]}"
            )
            return None

        # Also check total max positions
        open_count = await self._count_open_positions(session)
        if open_count >= self._max_positions:
            logger.debug(f"[MICRO] Max total positions ({open_count}), skipping {token_address[:12]}")
            return None

        # No duplicate position for same token
        existing = await session.execute(
            select(Position).where(
                Position.token_id == token_id,
                Position.status == "open",
                Position.is_paper == 1,
            )
        )
        if existing.scalar_one_or_none() is not None:
            logger.debug(f"[MICRO] Already have position for {token_address[:12]}, skipping")
            return None

        invest_sol = self._micro_snipe_sol

        # Entry slippage
        effective_price = price
        if liquidity_usd and liquidity_usd > 0 and sol_price_usd and sol_price_usd > 0:
            invest_usd = float(invest_sol) * sol_price_usd
            if invest_usd > liquidity_usd * 0.02:
                slippage_pct = min(invest_usd / liquidity_usd * 100, 50)
                effective_price = price * Decimal(str(1.0 + slippage_pct / 100))

        amount_token = invest_sol / effective_price if effective_price > 0 else Decimal("0")
        _sym = symbol or token_address[:12]

        trade = Trade(
            signal_id=None,  # no signal yet — this is a prescan entry
            token_id=token_id,
            token_address=token_address,
            side="buy",
            amount_sol=invest_sol,
            amount_token=amount_token,
            price=effective_price,
            is_paper=1,
            status="filled",
        )
        session.add(trade)

        position = Position(
            signal_id=None,
            token_id=token_id,
            token_address=token_address,
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
            is_micro_entry=1,
        )
        session.add(position)

        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            logger.debug(f"[MICRO] Duplicate position for {token_address[:12]}, skipping")
            return None

        logger.info(
            f"[MICRO] Opened micro-snipe {token_address[:12]} "
            f"@ {price} ({invest_sol} SOL)"
        )

        if self._alerts:
            try:
                await self._alerts.send_paper_open(
                    symbol=_sym,
                    address=token_address,
                    price=float(price),
                    sol_amount=float(invest_sol),
                    action="micro_snipe",
                )
            except Exception as e:
                logger.warning(f"[MICRO] Alert send failed: {e}")

        return position

    async def _topup_micro_position(
        self,
        session: AsyncSession,
        position: Position,
        signal: Signal,
        price: Decimal,
        liquidity_usd: float | None = None,
        sol_price_usd: float | None = None,
    ) -> Position:
        """Phase 51: Top up a micro-snipe position to full size when signal confirms.

        Calculates weighted average entry price from micro + additional investment.
        Clears is_micro_entry flag.
        """
        # Full size = sol_per_trade * multiplier (same logic as on_signal)
        size_multiplier = Decimal("1.5") if signal.status == "strong_buy" else Decimal("1.0")
        full_size = self._sol_per_trade * size_multiplier

        already_invested = position.amount_sol_invested or Decimal("0")
        additional_sol = full_size - already_invested
        if additional_sol <= 0:
            # Already at or above full size (shouldn't happen, but safe guard)
            position.is_micro_entry = 0
            position.signal_id = signal.id
            logger.info(f"[MICRO] Top-up skipped for {signal.token_address[:12]}: already full size")
            return position

        # Entry slippage on additional amount
        effective_price = price
        if liquidity_usd and liquidity_usd > 0 and sol_price_usd and sol_price_usd > 0:
            invest_usd = float(additional_sol) * sol_price_usd
            if invest_usd > liquidity_usd * 0.02:
                slippage_pct = min(invest_usd / liquidity_usd * 100, 50)
                effective_price = price * Decimal(str(1.0 + slippage_pct / 100))

        additional_tokens = additional_sol / effective_price if effective_price > 0 else Decimal("0")

        # Weighted average entry price
        old_entry = position.entry_price or price
        old_invest = already_invested
        new_entry = (old_invest * old_entry + additional_sol * effective_price) / (old_invest + additional_sol)

        # Update position
        position.entry_price = new_entry
        position.amount_sol_invested = old_invest + additional_sol
        position.amount_token = (position.amount_token or Decimal("0")) + additional_tokens
        position.signal_id = signal.id
        position.is_micro_entry = 0  # No longer micro — fully invested

        # Recalc PnL with new entry
        if new_entry > 0:
            position.pnl_pct = (price - new_entry) / new_entry * 100

        # Create additional buy trade
        trade = Trade(
            signal_id=signal.id,
            token_id=signal.token_id,
            token_address=signal.token_address,
            side="buy",
            amount_sol=additional_sol,
            amount_token=additional_tokens,
            price=effective_price,
            is_paper=1,
            status="filled",
        )
        session.add(trade)

        logger.info(
            f"[MICRO] Top-up {signal.token_address[:12]} "
            f"{signal.status}: +{additional_sol} SOL "
            f"(total {position.amount_sol_invested} SOL, "
            f"avg entry {new_entry:.12f})"
        )

        if self._alerts:
            try:
                await self._alerts.send_paper_open(
                    symbol=position.symbol or signal.token_address[:12],
                    address=signal.token_address,
                    price=float(price),
                    sol_amount=float(additional_sol),
                    action="micro_topup",
                )
            except Exception as e:
                logger.warning(f"[MICRO] Top-up alert failed: {e}")

        return position

    async def update_positions(
        self,
        session: AsyncSession,
        token_id: int,
        current_price: Decimal | None,
        is_rug: bool = False,
        liquidity_usd: float | None = None,
        sol_price_usd: float | None = None,
        is_dead_price: bool = False,
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
            # Phase 30: Price sanity check — reject garbage prices
            # A 1000x increase from entry in minutes is almost certainly bad data
            # (e.g. DexScreener returning SOL price instead of token price).
            # Real legitimate pumps rarely exceed 100x in first hours.
            if pos.entry_price and pos.entry_price > 0:
                price_ratio = float(current_price / pos.entry_price)
                if price_ratio > 1000:
                    logger.warning(
                        f"[PAPER] Rejecting garbage price for token_id={token_id}: "
                        f"current={current_price} vs entry={pos.entry_price} "
                        f"(ratio={price_ratio:.0f}x, likely bad API data)"
                    )
                    continue
                # Also reject if price is unrealistically high (>$1 for a memecoin)
                if current_price > Decimal("1"):
                    logger.warning(
                        f"[PAPER] Rejecting suspicious high price for token_id={token_id}: "
                        f"${current_price} (memecoins rarely reach $1+)"
                    )
                    continue

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

            # Check close conditions (pass liquidity for LP removal detection)
            close_reason = self._check_close_conditions(
                pos, current_price, is_rug, now,
                liquidity_usd=liquidity_usd,
                is_dead_price=is_dead_price,
            )
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
        liquidity_usd: float | None = None,
        is_dead_price: bool = False,
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
            trailing_activation_x=self._trailing_activation_x,
            trailing_drawdown_pct=self._trailing_drawdown_pct,
            stagnation_timeout_min=self._stagnation_timeout_min,
            stagnation_max_pnl_pct=self._stagnation_max_pnl_pct,
            liquidity_usd=liquidity_usd,
            is_dead_price=is_dead_price,
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

        # Liquidity removed / critically low — estimate realistic exit with slippage
        if reason == "liquidity_removed":
            _sol_usd = Decimal(str(sol_price_usd))
            _liq = liquidity_usd or 0

            if price <= 0 or _liq == 0:
                # Truly dead: zero price or zero liquidity → total loss
                _exit_pnl = Decimal("-100")
                _exit_price = Decimal("0")
                _exit_sol = Decimal("0")
            elif _liq < 100:
                # Near-zero ($0-$100) → essentially unsellable
                _exit_pnl = Decimal("-95")
                _exit_price = Decimal("0")
                _exit_sol = Decimal("0")
            else:
                # Low liq ($100-$5K) → sellable with heavy slippage
                # Phase 36: Quadratic slippage model based on position/liquidity ratio
                raw_exit_sol = (pos.amount_token or Decimal("0")) * price
                raw_exit_usd = float(raw_exit_sol) * sol_price_usd
                impact = raw_exit_usd / max(_liq, 1.0)
                slippage = min(impact * impact * 50, 90)  # 1x impact=50%, 2x=90%
                _exit_sol = raw_exit_sol * Decimal(str(max(1.0 - slippage / 100, 0.10)))
                _exit_price = price
                invest = pos.amount_sol_invested or Decimal("1")
                _exit_pnl = (_exit_sol - invest) / invest * 100

            pos.pnl_pct = _exit_pnl
            pos.pnl_usd = (pos.amount_sol_invested or Decimal("0")) * _exit_pnl / 100 * _sol_usd
            trade = Trade(
                signal_id=pos.signal_id,
                token_id=pos.token_id,
                token_address=pos.token_address,
                side="sell",
                amount_sol=_exit_sol,
                amount_token=pos.amount_token,
                price=_exit_price,
                is_paper=1,
                status="filled",
            )
            session.add(trade)
            logger.warning(
                f"[PAPER] Closed {pos.token_address[:12]} reason=liquidity_removed "
                f"P&L={_exit_pnl:+.1f}% liq=${_liq:,.0f}"
            )
            return

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
