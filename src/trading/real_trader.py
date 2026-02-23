"""Real trading engine — executes actual Jupiter swaps on Solana mainnet.

Mirrors PaperTrader interface but with real on-chain execution:
- on_signal() → Jupiter buy swap → DB record
- update_positions() → check close conditions → Jupiter sell swap → DB record
- sweep_stale_positions() → timeout expired positions → sell

Safety layers: circuit breaker, risk manager, wallet balance checks.
All trades recorded with is_paper=0 and real tx_hash from Solana.
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
from src.trading.jupiter_swap import JupiterSwapClient, SwapResult, LAMPORTS_PER_SOL
from src.trading.risk_manager import RiskManager, TradingCircuitBreaker
from src.trading.wallet import SolanaWallet

if TYPE_CHECKING:
    from src.parsers.alerts import AlertDispatcher


class RealTrader:
    """Manages real trading positions using Jupiter swaps on Solana."""

    def __init__(
        self,
        *,
        wallet: SolanaWallet,
        swap_client: JupiterSwapClient,
        risk_manager: RiskManager,
        circuit_breaker: TradingCircuitBreaker,
        sol_per_trade: float = 0.05,
        max_positions: int = 3,
        take_profit_x: float = 2.0,
        stop_loss_pct: float = -50.0,
        timeout_hours: int = 8,
        alert_dispatcher: AlertDispatcher | None = None,
    ) -> None:
        self._wallet = wallet
        self._swap = swap_client
        self._risk = risk_manager
        self._circuit = circuit_breaker
        self._sol_per_trade = Decimal(str(sol_per_trade))
        self._max_positions = max_positions
        self._take_profit_x = take_profit_x
        self._stop_loss_pct = stop_loss_pct
        self._timeout_hours = timeout_hours
        self._alerts = alert_dispatcher
        # Track consecutive sell failures per position for auto force-close
        self._sell_fail_count: dict[int, int] = {}
        # Max sell attempts before force-closing position as total loss
        self._max_sell_attempts: int = 3
        # Escalating slippage: 5% → 15% → 25%
        self._slippage_escalation: list[int] = [500, 1500, 2500]

    async def on_signal(
        self,
        session: AsyncSession,
        signal: Signal,
        price: Decimal | None,
        symbol: str | None = None,
        liquidity_usd: float | None = None,
        sol_price_usd: float | None = None,
    ) -> Position | None:
        """Open a real position when a qualifying signal fires.

        Flow:
        1. Signal filter (strong_buy/buy only)
        2. Circuit breaker check
        3. Risk manager pre-flight (balance, exposure, liquidity)
        4. Jupiter buy swap execution
        5. On success: record Trade + Position in DB
        6. On failure: circuit breaker record, return None

        Returns the new Position or None if skipped/failed.
        """
        # Signal filter
        if signal.status not in ("strong_buy", "buy"):
            logger.info(f"[REAL] Skipping signal {signal.token_address[:12]}: status={signal.status}")
            return None
        if price is None or price <= 0:
            logger.warning(f"[REAL] Skipping signal {signal.token_address[:12]}: invalid price={price}")
            return None

        # Circuit breaker
        if self._circuit.is_tripped:
            logger.warning("[REAL] Circuit breaker tripped, skipping trade")
            return None

        # No duplicate position for same token
        existing = await session.execute(
            select(Position).where(
                Position.token_id == signal.token_id,
                Position.status == "open",
                Position.is_paper == 0,
            )
        )
        if existing.scalar_one_or_none() is not None:
            logger.info(f"[REAL] Duplicate position for {signal.token_address[:12]}, skipping")
            return None

        # Volume-weighted sizing
        size_multiplier = Decimal("1.5") if signal.status == "strong_buy" else Decimal("1.0")
        invest_sol = self._sol_per_trade * size_multiplier

        # Risk checks
        wallet_balance = await self._wallet.get_sol_balance()
        open_count = await self._count_open_positions(session)
        total_exposure = await self._total_open_exposure(session)

        allowed, reason = self._risk.pre_buy_check(
            wallet_balance_sol=wallet_balance,
            open_position_count=open_count,
            total_open_exposure_sol=float(total_exposure),
            invest_sol=float(invest_sol),
            liquidity_usd=liquidity_usd,
        )
        if not allowed:
            logger.info(f"[REAL] Risk check blocked: {reason}")
            return None

        # Execute buy swap
        sol_lamports = int(invest_sol * LAMPORTS_PER_SOL)
        result = await self._swap.buy_token(signal.token_address, sol_lamports)

        if not result.success:
            self._circuit.record_failure(result.error or "Unknown")
            logger.warning(
                f"[REAL] Buy failed for {signal.token_address[:12]}: {result.error}"
            )
            # Alert on circuit breaker trip
            if self._circuit.is_tripped and self._alerts:
                try:
                    await self._alerts.send_real_error(
                        f"Circuit breaker tripped after {self._circuit.total_failures} failures.\n"
                        f"Last error: {result.error}\n"
                        f"Cooldown: {self._circuit.seconds_until_reset:.0f}s"
                    )
                except Exception:
                    pass
            return None

        self._circuit.record_success()

        # Calculate token amount from swap result
        # output_amount from Jupiter is raw token units
        amount_token = result.output_amount or Decimal("0")

        # Effective price (what we actually paid per token)
        effective_price = price  # fallback to signal price
        if amount_token > 0 and result.input_amount:
            # input_amount is raw SOL lamports, convert to SOL
            actual_sol = Decimal(str(result.input_amount)) / Decimal(str(LAMPORTS_PER_SOL))
            # effective_price = SOL_spent / token_amount * (price per token in SOL terms)
            # But we track price in USD/token terms from signal, and amount_token in raw
            # Keep signal price as entry_price for consistency with paper trader
            pass

        # Create buy trade with real tx_hash
        _sym = symbol or getattr(signal, "symbol", None) or signal.token_address[:12]
        trade = Trade(
            signal_id=signal.id,
            token_id=signal.token_id,
            token_address=signal.token_address,
            side="buy",
            amount_sol=invest_sol,
            amount_token=amount_token,
            price=price,
            slippage_pct=Decimal(str(result.price_impact_pct or 0)),
            fee_sol=result.fee_sol,
            tx_hash=result.tx_hash,
            is_paper=0,
            status="filled",
        )
        session.add(trade)

        # Create position
        position = Position(
            signal_id=signal.id,
            token_id=signal.token_id,
            token_address=signal.token_address,
            symbol=_sym,
            entry_price=price,
            current_price=price,
            amount_token=amount_token,
            amount_sol_invested=invest_sol,
            pnl_pct=Decimal("0"),
            pnl_usd=Decimal("0"),
            max_price=price,
            status="open",
            is_paper=0,
        )
        session.add(position)

        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            logger.debug(
                f"[REAL] Duplicate position for {signal.token_address[:12]}, skipping"
            )
            return None

        logger.info(
            f"[REAL] Opened {signal.status} {_sym} @ {price} "
            f"({invest_sol} SOL) tx={result.tx_hash}"
        )

        # Telegram alert
        if self._alerts:
            try:
                await self._alerts.send_real_open(
                    symbol=_sym,
                    address=signal.token_address,
                    price=float(price),
                    sol_amount=float(invest_sol),
                    action=signal.status,
                    tx_hash=result.tx_hash or "",
                )
            except Exception as e:
                logger.warning(f"[REAL] Alert send failed: {e}")

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
        """Update all open real positions for a token.

        Recalculates P&L and checks close conditions. If a close condition is met,
        executes a sell swap via Jupiter before closing the position in DB.
        """
        if current_price is None or current_price <= 0:
            return

        result = await session.execute(
            select(Position).where(
                Position.token_id == token_id,
                Position.status == "open",
                Position.is_paper == 0,
            )
        )
        positions = list(result.scalars().all())

        if not positions:
            return

        now = datetime.now(UTC).replace(tzinfo=None)
        _sol_usd = Decimal(str(sol_price_usd)) if sol_price_usd else Decimal("83")

        for pos in positions:
            # Update price tracking
            pos.current_price = current_price
            if pos.max_price is None or current_price > pos.max_price:
                pos.max_price = current_price

            # Calculate P&L
            if pos.entry_price and pos.entry_price > 0:
                pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
                pos.pnl_pct = pnl_pct
                if pos.amount_sol_invested:
                    pos.pnl_usd = pos.amount_sol_invested * pnl_pct / 100 * _sol_usd

            # Check close conditions (shared with paper trader)
            close_reason = check_close_conditions(
                pos,
                current_price,
                is_rug,
                now,
                take_profit_x=self._take_profit_x,
                stop_loss_pct=self._stop_loss_pct,
                timeout_hours=self._timeout_hours,
            )
            if close_reason:
                await self._execute_close(
                    session,
                    pos,
                    close_reason,
                    current_price,
                    liquidity_usd=liquidity_usd,
                    sol_price_usd=float(_sol_usd),
                )

    async def _execute_close(
        self,
        session: AsyncSession,
        pos: Position,
        reason: str,
        price: Decimal,
        liquidity_usd: float | None = None,
        sol_price_usd: float = 83.0,
    ) -> bool:
        """Execute sell swap + close position in DB.

        Returns True on success. On failure, position stays open for retry on next cycle.

        Sell protection flow:
        1. First attempt: default slippage (5%)
        2. Second attempt: escalated slippage (15%)
        3. Third attempt: max slippage (25%)
        4. After max_sell_attempts failures: auto force-close as total loss
           (pool dead / rug pull — tokens likely worthless)
        """
        pos_id = pos.id
        _sym = pos.symbol or pos.token_address[:12]
        fail_count = self._sell_fail_count.get(pos_id, 0)

        # Auto force-close after max attempts (pool dead / rug pull)
        if fail_count >= self._max_sell_attempts:
            logger.warning(
                f"[REAL] Auto force-close {_sym} after {fail_count} failed sells — "
                f"pool likely dead"
            )
            pos.status = "closed"
            pos.close_reason = f"{reason}+sell_failed"
            pos.closed_at = datetime.now(UTC).replace(tzinfo=None)
            pos.current_price = price
            pos.pnl_pct = Decimal("-100")
            # Actual PnL: lost entire investment (no sell executed)
            _sol_usd = Decimal(str(sol_price_usd))
            pos.pnl_usd = -(pos.amount_sol_invested or Decimal("0")) * _sol_usd
            self._sell_fail_count.pop(pos_id, None)
            # Alert
            if self._alerts:
                try:
                    await self._alerts.send_real_error(
                        f"⚠️ Auto force-closed {_sym}\n"
                        f"Reason: {fail_count} consecutive sell failures\n"
                        f"Pool likely dead (rug pull / no liquidity)\n"
                        f"Position closed as -100% loss"
                    )
                except Exception:
                    pass
            return True

        # Circuit breaker bypass for urgent closes (rug, stop_loss, early_stop)
        # and retries (fail_count > 0)
        urgent_reasons = {"rug", "stop_loss", "early_stop", "timeout"}
        if self._circuit.is_tripped and reason not in urgent_reasons and fail_count == 0:
            logger.warning(
                f"[REAL] Circuit breaker active, deferring close of {pos.token_address[:12]}"
            )
            return False

        # Get token balance from wallet
        token_balance_raw, _decimals = await self._wallet.get_token_balance(pos.token_address)

        if token_balance_raw <= 0:
            logger.warning(
                f"[REAL] No token balance for {pos.token_address[:12]}, "
                f"closing position without sell (tokens may have been transferred)"
            )
            pos.status = "closed"
            pos.close_reason = f"{reason}+no_balance"
            pos.closed_at = datetime.now(UTC).replace(tzinfo=None)
            pos.current_price = price
            # Actual PnL: lost entire investment (no tokens to sell)
            pos.pnl_pct = Decimal("-100")
            _sol_usd = Decimal(str(sol_price_usd))
            pos.pnl_usd = -(pos.amount_sol_invested or Decimal("0")) * _sol_usd
            self._sell_fail_count.pop(pos_id, None)
            return True

        # Escalating slippage: more attempts = higher slippage tolerance
        slippage_idx = min(fail_count, len(self._slippage_escalation) - 1)
        slippage_bps = self._slippage_escalation[slippage_idx]
        if fail_count > 0:
            logger.info(
                f"[REAL] Sell retry #{fail_count + 1} for {_sym} "
                f"with escalated slippage: {slippage_bps}bps"
            )

        # Execute sell swap
        result = await self._swap.sell_token(
            pos.token_address, token_balance_raw, slippage_bps=slippage_bps,
        )

        if not result.success:
            self._sell_fail_count[pos_id] = fail_count + 1
            self._circuit.record_failure(result.error or "Sell failed")
            logger.warning(
                f"[REAL] Sell failed for {_sym}: {result.error} "
                f"(attempt {fail_count + 1}/{self._max_sell_attempts}). "
                f"{'Will auto force-close next cycle.' if fail_count + 1 >= self._max_sell_attempts else 'Will retry with higher slippage.'}"
            )
            # Alert on escalating failures
            if fail_count + 1 >= self._max_sell_attempts and self._alerts:
                try:
                    await self._alerts.send_real_error(
                        f"🚨 Sell failed {fail_count + 1}x for {_sym}\n"
                        f"Error: {result.error}\n"
                        f"Will auto force-close on next cycle (total loss)"
                    )
                except Exception:
                    pass
            return False

        self._circuit.record_success()
        self._sell_fail_count.pop(pos_id, None)

        # Close position in DB
        pos.status = "closed"
        pos.close_reason = reason
        pos.closed_at = datetime.now(UTC).replace(tzinfo=None)
        pos.current_price = price

        # Calculate exit SOL from sell result
        exit_sol = Decimal("0")
        if result.output_amount:
            exit_sol = result.output_amount / Decimal(str(LAMPORTS_PER_SOL))

        # Create sell trade with real data
        sell_fee = result.fee_sol or Decimal("0")
        trade = Trade(
            signal_id=pos.signal_id,
            token_id=pos.token_id,
            token_address=pos.token_address,
            side="sell",
            amount_sol=exit_sol,
            amount_token=Decimal(str(token_balance_raw)),
            price=price,
            slippage_pct=Decimal(str(result.price_impact_pct or 0)),
            fee_sol=sell_fee,
            tx_hash=result.tx_hash,
            is_paper=0,
            status="filled",
        )
        session.add(trade)

        # Recalculate PnL from actual blockchain data (buy SOL vs sell SOL)
        buy_sol = pos.amount_sol_invested or Decimal("0")
        actual_received = exit_sol - sell_fee
        actual_pnl_sol = actual_received - buy_sol
        _sol_usd = Decimal(str(sol_price_usd))
        if buy_sol > 0:
            pos.pnl_pct = actual_pnl_sol / buy_sol * 100
        else:
            pos.pnl_pct = Decimal("0")
        pos.pnl_usd = actual_pnl_sol * _sol_usd

        pnl = f"{pos.pnl_pct:+.1f}%" if pos.pnl_pct else "?"
        logger.info(
            f"[REAL] Closed {_sym} reason={reason} P&L={pnl} "
            f"(bought {buy_sol} SOL, received {actual_received} SOL) "
            f"tx={result.tx_hash}"
        )

        # Telegram alert
        if self._alerts:
            try:
                await self._alerts.send_real_close(
                    symbol=_sym,
                    address=pos.token_address,
                    entry_price=float(pos.entry_price or 0),
                    exit_price=float(price),
                    pnl_pct=float(pos.pnl_pct or 0),
                    reason=reason,
                    tx_hash=result.tx_hash or "",
                )
            except Exception as e:
                logger.warning(f"[REAL] Close alert failed: {e}")

        return True

    async def sweep_stale_positions(self, session: AsyncSession) -> int:
        """Close real positions that exceeded timeout_hours.

        Unlike paper trader, this executes actual sell swaps.
        Returns the number of positions closed.
        """
        now = datetime.now(UTC).replace(tzinfo=None)
        cutoff = now - timedelta(hours=self._timeout_hours)

        result = await session.execute(
            select(Position).where(
                Position.status == "open",
                Position.is_paper == 0,
                Position.opened_at < cutoff,
            )
        )
        stale = list(result.scalars().all())

        closed_count = 0
        for pos in stale:
            exit_price = pos.current_price or pos.entry_price or Decimal("0")
            success = await self._execute_close(session, pos, "timeout", exit_price)
            if success:
                closed_count += 1

        if stale:
            logger.info(
                f"[REAL] Swept {closed_count}/{len(stale)} stale positions "
                f"(>{self._timeout_hours}h)"
            )

        return closed_count

    async def _count_open_positions(self, session: AsyncSession) -> int:
        """Count currently open real positions."""
        result = await session.execute(
            select(func.count(Position.id)).where(
                Position.status == "open",
                Position.is_paper == 0,
            )
        )
        return result.scalar_one()

    async def _total_open_exposure(self, session: AsyncSession) -> Decimal:
        """Sum of amount_sol_invested for all open real positions."""
        result = await session.execute(
            select(func.coalesce(func.sum(Position.amount_sol_invested), 0)).where(
                Position.status == "open",
                Position.is_paper == 0,
            )
        )
        return Decimal(str(result.scalar_one()))

    async def get_portfolio_summary(self, session: AsyncSession) -> dict:
        """Get aggregate real trading stats for display."""
        # Open positions
        open_result = await session.execute(
            select(Position).where(
                Position.status == "open", Position.is_paper == 0
            )
        )
        open_positions = list(open_result.scalars().all())

        # Closed positions
        closed_result = await session.execute(
            select(Position).where(
                Position.status == "closed", Position.is_paper == 0
            )
        )
        closed_positions = list(closed_result.scalars().all())

        total_invested = sum(
            float(p.amount_sol_invested or 0) for p in open_positions + closed_positions
        )
        total_pnl = sum(float(p.pnl_usd or 0) for p in open_positions + closed_positions)
        wins = sum(1 for p in closed_positions if p.pnl_pct and p.pnl_pct > 0)
        losses = sum(1 for p in closed_positions if p.pnl_pct and p.pnl_pct <= 0)

        return {
            "open_count": len(open_positions),
            "closed_count": len(closed_positions),
            "total_invested_sol": total_invested,
            "total_pnl_usd": total_pnl,
            "win_rate": round(wins / max(wins + losses, 1) * 100, 1),
            "wins": wins,
            "losses": losses,
            "wallet_balance": await self._wallet.get_sol_balance(),
            "circuit_breaker_tripped": self._circuit.is_tripped,
            "total_failures": self._circuit.total_failures,
        }
