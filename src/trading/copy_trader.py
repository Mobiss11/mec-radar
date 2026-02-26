"""Copy trading engine — mirror trades from tracked wallets in real-time.

Phase 57: gRPC detects wallet transactions (sub-second), Helius parses
swap details, CopyTrader opens paper/real positions.

Flow:
  gRPC (account_include: 14 wallets)
    → fee_payer check (signer only)
    → Helius get_parsed_transactions (type=SWAP)
    → _handle_buy / _handle_sell
    → Paper position (is_paper=1) and/or Real Jupiter swap (is_paper=0)
    → Telegram alert
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import Token
from src.models.trade import Position, Trade
from src.trading.close_conditions import check_close_conditions

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from src.parsers.alerts import AlertDispatcher
    from src.parsers.helius.client import HeliusClient

SOL_MINT = "So11111111111111111111111111111111111111112"


@dataclass
class CopySwap:
    """Parsed swap event from a tracked wallet."""

    wallet_address: str
    wallet_label: str
    signature: str
    side: str  # "buy" | "sell"
    token_mint: str
    token_symbol: str | None
    sol_amount: Decimal  # SOL spent (buy) or received (sell)
    token_amount: Decimal
    source_dex: str  # "JUPITER" | "RAYDIUM" | "PUMP_FUN" etc.
    timestamp: int


class CopyTrader:
    """Monitors tracked wallets via gRPC + Helius and opens copy-trade positions.

    Composition pattern: delegates to existing PaperTrader/RealTrader for real
    execution, but creates positions directly for paper mode.
    """

    def __init__(
        self,
        *,
        helius: HeliusClient,
        alert_dispatcher: AlertDispatcher | None = None,
        redis: Redis | None = None,
        # Close condition params
        take_profit_x: float = 1.5,
        stop_loss_pct: float = -50.0,
        timeout_hours: int = 8,
        trailing_activation_x: float = 1.3,
        trailing_drawdown_pct: float = 15.0,
        stagnation_timeout_min: float = 25.0,
        stagnation_max_pnl_pct: float = 15.0,
        # Copy-specific params
        max_positions: int = 20,
        default_sol_per_trade: float = 0.05,
        min_sol_amount: float = 0.01,
        sell_mirror: bool = True,
        dedup_ttl_sec: int = 300,
    ) -> None:
        self._helius = helius
        self._alerts = alert_dispatcher
        self._redis = redis

        # Close conditions
        self._take_profit_x = take_profit_x
        self._stop_loss_pct = stop_loss_pct
        self._timeout_hours = timeout_hours
        self._trailing_activation_x = trailing_activation_x
        self._trailing_drawdown_pct = trailing_drawdown_pct
        self._stagnation_timeout_min = stagnation_timeout_min
        self._stagnation_max_pnl_pct = stagnation_max_pnl_pct

        # Copy params
        self._max_positions = max_positions
        self._default_sol = Decimal(str(default_sol_per_trade))
        self._min_sol = Decimal(str(min_sol_amount))
        self._sell_mirror = sell_mirror
        self._dedup_ttl = dedup_ttl_sec

        # Stats
        self._events_received = 0
        self._swaps_parsed = 0
        self._buys_opened = 0
        self._sells_mirrored = 0
        self._skipped_dedup = 0
        self._skipped_non_swap = 0
        self._errors = 0

    # ── Wallet config access ──────────────────────────────────────────

    @staticmethod
    def _get_tracked_wallets() -> dict[str, dict[str, Any]]:
        """Import tracked wallets from copy_trading router (avoids circular import)."""
        from src.api.routers.copy_trading import get_tracked_wallets
        return get_tracked_wallets()

    @staticmethod
    def _get_copy_settings() -> dict[str, Any]:
        """Import copy settings from copy_trading router."""
        from src.api.routers.copy_trading import get_copy_settings
        return get_copy_settings()

    # ── gRPC callback (main entry point) ──────────────────────────────

    async def on_copy_swap_detected(
        self,
        wallet_address: str,
        signature: str,
        session: AsyncSession,
    ) -> None:
        """Handle a transaction from a tracked wallet detected by gRPC.

        1. Redis dedup
        2. Helius parse → CopySwap
        3. Route to _handle_buy or _handle_sell
        """
        self._events_received += 1
        sig_short = signature[:16]
        wallet_short = wallet_address[:12]

        logger.info(f"[COPY] Event #{self._events_received} from {wallet_short} sig={sig_short}")

        # 1. Redis dedup
        if self._redis:
            try:
                was_new = await self._redis.set(
                    f"copy:seen:{signature}", "1", nx=True, ex=self._dedup_ttl,
                )
                if not was_new:
                    self._skipped_dedup += 1
                    logger.debug(f"[COPY] Dedup skip: {sig_short}")
                    return
            except Exception as e:
                logger.debug(f"[COPY] Redis dedup check failed: {e}")

        # 2. Wallet enabled?
        tracked = self._get_tracked_wallets()
        config = tracked.get(wallet_address)
        if not config or not config.get("enabled", True):
            logger.info(f"[COPY] Wallet disabled: {wallet_short}")
            return

        # 3. Helius parse with retry
        # gRPC detects at PROCESSED commitment (~400ms), but Helius Enhanced API
        # needs CONFIRMED (~5-30s). Retry up to 3 times with increasing delays.
        tx = None
        helius_delays = [2.0, 5.0, 10.0]  # seconds before each attempt
        for attempt, delay in enumerate(helius_delays):
            await asyncio.sleep(delay)
            try:
                txs = await asyncio.wait_for(
                    self._helius.get_parsed_transactions([signature]),
                    timeout=10.0,
                )
            except TimeoutError:
                logger.warning(f"[COPY] Helius timeout for {sig_short} (attempt {attempt + 1})")
                continue
            except Exception as e:
                logger.warning(f"[COPY] Helius error: {e} (attempt {attempt + 1})")
                continue

            if txs:
                tx = txs[0]
                if attempt > 0:
                    logger.info(f"[COPY] Helius resolved on attempt {attempt + 1} for {sig_short}")
                break
            logger.debug(f"[COPY] Helius empty attempt {attempt + 1} for {sig_short}")

        if tx is None:
            self._errors += 1
            logger.warning(f"[COPY] Helius returned empty after 3 attempts for {sig_short}")
            return

        # 4. Validate: must be SWAP, no error, fee_payer matches wallet
        if tx.type != "SWAP":
            self._skipped_non_swap += 1
            logger.info(
                f"[COPY] Not SWAP: type={tx.type} source={getattr(tx, 'source', '?')} "
                f"sig={sig_short}"
            )
            return
        if tx.transaction_error:
            logger.info(f"[COPY] TX error: {sig_short}")
            return
        if tx.fee_payer != wallet_address:
            logger.info(
                f"[COPY] Fee payer mismatch: expected={wallet_short} "
                f"got={tx.fee_payer[:12] if tx.fee_payer else 'None'}"
            )
            return

        # 5. Parse swap details
        swap = self._parse_swap(wallet_address, config, tx)
        if not swap:
            logger.info(f"[COPY] Parse failed (no SOL flow or min_sol): {sig_short}")
            return

        self._swaps_parsed += 1

        # 6. Route
        settings = self._get_copy_settings()
        if swap.side == "buy":
            if settings.get("paper_mode"):
                await self._handle_buy(session, swap, config, is_paper=True)
            if settings.get("real_mode"):
                await self._handle_buy(session, swap, config, is_paper=False)
        elif swap.side == "sell" and self._sell_mirror:
            await self._handle_sell(session, swap)

    # ── Swap parsing from Helius ──────────────────────────────────────

    def _parse_swap(
        self,
        wallet_address: str,
        config: dict[str, Any],
        tx: Any,
    ) -> CopySwap | None:
        """Parse a Helius SWAP transaction into a CopySwap event.

        BUY: wallet sends SOL → receives token
        SELL: wallet sends token → receives SOL
        """
        # Collect SOL outflows (wallet spending SOL = buy)
        sol_out = sum(
            t.amount
            for t in tx.native_transfers
            if t.from_user_account == wallet_address
        )
        # Collect SOL inflows (wallet receiving SOL = sell)
        sol_in = sum(
            t.amount
            for t in tx.native_transfers
            if t.to_user_account == wallet_address
        )

        # Non-SOL tokens received by wallet (= bought)
        tokens_received = [
            t for t in tx.token_transfers
            if t.to_user_account == wallet_address and t.mint != SOL_MINT and t.mint
        ]
        # Non-SOL tokens sent by wallet (= sold)
        tokens_sent = [
            t for t in tx.token_transfers
            if t.from_user_account == wallet_address and t.mint != SOL_MINT and t.mint
        ]

        label = config.get("label", wallet_address[:12])

        # BUY: SOL out > fee AND received token
        if sol_out > tx.fee and tokens_received:
            token = tokens_received[0]
            sol_amount = Decimal(sol_out - tx.fee) / Decimal(10**9)
            if sol_amount < self._min_sol:
                return None
            return CopySwap(
                wallet_address=wallet_address,
                wallet_label=label,
                signature=tx.signature,
                side="buy",
                token_mint=token.mint,
                token_symbol=None,
                sol_amount=sol_amount,
                token_amount=token.token_amount,
                source_dex=tx.source or "UNKNOWN",
                timestamp=tx.timestamp,
            )

        # SELL: tokens sent AND SOL received (beyond fee refund)
        net_sol_in = sol_in - max(sol_out, 0)
        if tokens_sent and net_sol_in > 0:
            token = tokens_sent[0]
            sol_amount = Decimal(net_sol_in) / Decimal(10**9)
            return CopySwap(
                wallet_address=wallet_address,
                wallet_label=label,
                signature=tx.signature,
                side="sell",
                token_mint=token.mint,
                token_symbol=None,
                sol_amount=sol_amount,
                token_amount=token.token_amount,
                source_dex=tx.source or "UNKNOWN",
                timestamp=tx.timestamp,
            )

        return None

    # ── Buy handler ───────────────────────────────────────────────────

    async def _handle_buy(
        self,
        session: AsyncSession,
        swap: CopySwap,
        config: dict[str, Any],
        is_paper: bool,
    ) -> Position | None:
        """Open a copy-trade position when tracked wallet buys."""
        is_paper_int = 1 if is_paper else 0
        mode = "PAPER" if is_paper else "REAL"

        # Max positions check
        open_count = await self._count_open_positions(session, is_paper_int)
        if open_count >= self._max_positions:
            logger.debug(
                f"[COPY] Max positions reached ({open_count}/{self._max_positions}), "
                f"skipping {swap.token_mint[:12]}"
            )
            return None

        # Get/create token record
        token = await self._get_or_create_token(session, swap.token_mint)
        if not token:
            return None

        # Duplicate check (same token, same paper/real, source=copy_trade)
        existing = await session.execute(
            select(Position).where(
                Position.token_id == token.id,
                Position.status == "open",
                Position.is_paper == is_paper_int,
                Position.source == "copy_trade",
            )
        )
        if existing.scalar_one_or_none() is not None:
            logger.debug(f"[COPY] Duplicate {mode} position for {swap.token_mint[:12]}, skipping")
            return None

        # Calculate invest amount
        multiplier = Decimal(str(config.get("multiplier", 1.0)))
        max_sol = Decimal(str(config.get("max_sol_per_trade", float(self._default_sol))))
        invest_sol = min(swap.sol_amount * multiplier, max_sol)
        invest_sol = max(invest_sol, self._min_sol)

        # Price from swap data
        price = Decimal("0")
        if swap.token_amount and swap.token_amount > 0:
            price = swap.sol_amount / swap.token_amount
        if price <= 0:
            logger.warning(f"[COPY] Zero price for {swap.token_mint[:12]}, skipping")
            return None

        # Calculate token amount for our position
        amount_token = invest_sol / price if price > 0 else Decimal("0")

        # TODO: For real mode, execute Jupiter buy swap here
        # For Phase 57 MVP, real mode creates paper-like records with is_paper=0
        # Real Jupiter execution will be wired in Phase 58
        tx_hash = swap.signature if not is_paper else None

        # Create trade
        trade = Trade(
            signal_id=None,
            token_id=token.id,
            token_address=swap.token_mint,
            side="buy",
            amount_sol=invest_sol,
            amount_token=amount_token,
            price=price,
            is_paper=is_paper_int,
            source="copy_trade",
            copied_from_wallet=swap.wallet_address,
            tx_hash=tx_hash,
            status="filled",
        )
        session.add(trade)

        # Create position
        symbol = swap.token_symbol or token.symbol or swap.token_mint[:12]
        position = Position(
            signal_id=None,
            token_id=token.id,
            token_address=swap.token_mint,
            symbol=symbol,
            entry_price=price,
            current_price=price,
            amount_token=amount_token,
            amount_sol_invested=invest_sol,
            pnl_pct=Decimal("0"),
            pnl_usd=Decimal("0"),
            max_price=price,
            status="open",
            is_paper=is_paper_int,
            source="copy_trade",
            copied_from_wallet=swap.wallet_address,
        )
        session.add(position)

        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            logger.debug(f"[COPY] Duplicate {mode} position for {swap.token_mint[:12]}")
            return None

        self._buys_opened += 1
        logger.info(
            f"[COPY] {mode} BUY {symbol} from {swap.wallet_label}: "
            f"{invest_sol:.4f} SOL @ {price:.10g} ({swap.source_dex})"
        )

        # Alert
        if self._alerts:
            try:
                await self._alerts.send_copy_open(
                    symbol=symbol,
                    address=swap.token_mint,
                    price=float(price),
                    sol_amount=float(invest_sol),
                    wallet_label=swap.wallet_label,
                    wallet_address=swap.wallet_address,
                    source_dex=swap.source_dex,
                    is_paper=is_paper,
                    tx_hash=tx_hash,
                )
            except Exception as e:
                logger.warning(f"[COPY] Alert failed: {e}")

        return position

    # ── Sell handler (mirror close) ───────────────────────────────────

    async def _handle_sell(
        self, session: AsyncSession, swap: CopySwap,
    ) -> None:
        """Close copy-trade positions when tracked wallet sells the token."""
        result = await session.execute(
            select(Position).where(
                Position.token_address == swap.token_mint,
                Position.source == "copy_trade",
                Position.copied_from_wallet == swap.wallet_address,
                Position.status == "open",
            )
        )
        positions = list(result.scalars().all())

        for pos in positions:
            exit_price = pos.current_price or pos.entry_price or Decimal("0")
            await self._close_position(session, pos, "mirror_sell", exit_price)
            self._sells_mirrored += 1

    # ── Position updates (price loop) ─────────────────────────────────

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
        """Update all open copy-trade positions for a token."""
        if current_price is None or current_price <= 0:
            return

        result = await session.execute(
            select(Position).where(
                Position.token_id == token_id,
                Position.status == "open",
                Position.source == "copy_trade",
            )
        )
        positions = list(result.scalars().all())
        if not positions:
            return

        now = datetime.now(UTC).replace(tzinfo=None)
        _sol_usd = Decimal(str(sol_price_usd)) if sol_price_usd else Decimal("150")

        for pos in positions:
            # Price sanity check (same as PaperTrader)
            if pos.entry_price and pos.entry_price > 0:
                price_ratio = float(current_price / pos.entry_price)
                if price_ratio > 1000:
                    continue
                if current_price > Decimal("1"):
                    continue

            pos.current_price = current_price
            if pos.max_price is None or current_price > pos.max_price:
                pos.max_price = current_price

            # P&L calculation
            if pos.entry_price and pos.entry_price > 0:
                pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
                pos.pnl_pct = pnl_pct
                if pos.amount_sol_invested:
                    pos.pnl_usd = pos.amount_sol_invested * pnl_pct / 100 * _sol_usd

            # Close conditions
            close_reason = check_close_conditions(
                pos, current_price, is_rug, now,
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
            if close_reason:
                await self._close_position(
                    session, pos, close_reason, current_price,
                    liquidity_usd=liquidity_usd,
                    sol_price_usd=float(_sol_usd),
                )

    # ── Sweep stale positions ─────────────────────────────────────────

    async def sweep_stale_positions(self, session: AsyncSession) -> int:
        """Close copy-trade positions that exceeded timeout."""
        now = datetime.now(UTC).replace(tzinfo=None)
        cutoff = now - timedelta(hours=self._timeout_hours)

        result = await session.execute(
            select(Position).where(
                Position.status == "open",
                Position.source == "copy_trade",
                Position.opened_at < cutoff,
            )
        )
        stale = list(result.scalars().all())

        for pos in stale:
            exit_price = pos.current_price or pos.entry_price or Decimal("0")
            await self._close_position(session, pos, "timeout", exit_price)

        if stale:
            logger.info(f"[COPY] Swept {len(stale)} stale positions (>{self._timeout_hours}h)")

        return len(stale)

    # ── Close position ────────────────────────────────────────────────

    async def _close_position(
        self,
        session: AsyncSession,
        pos: Position,
        reason: str,
        price: Decimal,
        liquidity_usd: float | None = None,
        sol_price_usd: float = 150.0,
    ) -> None:
        """Close a copy-trade position and create sell trade."""
        pos.status = "closed"
        pos.close_reason = reason
        pos.closed_at = datetime.now(UTC).replace(tzinfo=None)
        pos.current_price = price

        # Liquidity removed = special handling (same as PaperTrader)
        if reason == "liquidity_removed":
            _sol_usd = Decimal(str(sol_price_usd))
            _liq = liquidity_usd or 0
            if price <= 0 or _liq == 0:
                pos.pnl_pct = Decimal("-100")
                pos.pnl_usd = (pos.amount_sol_invested or Decimal("0")) * Decimal("-1") * _sol_usd
            elif _liq < 100:
                pos.pnl_pct = Decimal("-95")
                _inv = pos.amount_sol_invested or Decimal("0")
                pos.pnl_usd = _inv * Decimal("-0.95") * _sol_usd
            else:
                raw_exit_sol = (pos.amount_token or Decimal("0")) * price
                raw_exit_usd = float(raw_exit_sol) * sol_price_usd
                impact = raw_exit_usd / max(_liq, 1.0)
                slippage = min(impact * impact * 50, 90)
                exit_sol = raw_exit_sol * Decimal(str(max(1.0 - slippage / 100, 0.10)))
                invest = pos.amount_sol_invested or Decimal("1")
                pos.pnl_pct = (exit_sol - invest) / invest * 100
                _inv2 = pos.amount_sol_invested or Decimal("0")
                pos.pnl_usd = _inv2 * pos.pnl_pct / 100 * _sol_usd
        else:
            # Standard close
            exit_sol = pos.amount_sol_invested or Decimal("0")
            if pos.entry_price and pos.entry_price > 0 and price > 0:
                exit_sol = (pos.amount_token or Decimal("0")) * price

            # Slippage estimate
            if liquidity_usd and liquidity_usd > 0:
                exit_usd = float(exit_sol) * sol_price_usd
                if exit_usd > liquidity_usd * 0.02:
                    slippage_pct = min(exit_usd / liquidity_usd * 100, 50)
                    exit_sol = exit_sol * Decimal(str(max(1.0 - slippage_pct / 100, 0.5)))
                    pos.close_reason = f"{reason}+slippage"

        # Create sell trade
        trade = Trade(
            signal_id=None,
            token_id=pos.token_id,
            token_address=pos.token_address,
            side="sell",
            amount_sol=pos.amount_sol_invested,
            amount_token=pos.amount_token,
            price=price,
            is_paper=pos.is_paper,
            source="copy_trade",
            copied_from_wallet=pos.copied_from_wallet,
            status="filled",
        )
        session.add(trade)

        mode = "PAPER" if pos.is_paper else "REAL"
        pnl = f"{pos.pnl_pct:+.1f}%" if pos.pnl_pct else "?"
        logger.info(f"[COPY] {mode} CLOSE {pos.symbol} reason={reason} P&L={pnl}")

        # Alert
        if self._alerts:
            try:
                wallet_label = ""
                tracked = self._get_tracked_wallets()
                w_config = tracked.get(pos.copied_from_wallet or "")
                if w_config:
                    wallet_label = w_config.get("label", "")
                await self._alerts.send_copy_close(
                    symbol=pos.symbol or pos.token_address[:12],
                    address=pos.token_address,
                    entry_price=float(pos.entry_price or 0),
                    exit_price=float(price),
                    pnl_pct=float(pos.pnl_pct or 0),
                    reason=reason,
                    wallet_label=wallet_label or (pos.copied_from_wallet or "")[:12],
                    is_paper=bool(pos.is_paper),
                )
            except Exception as e:
                logger.warning(f"[COPY] Close alert failed: {e}")

    # ── Helpers ───────────────────────────────────────────────────────

    async def _count_open_positions(
        self, session: AsyncSession, is_paper: int,
    ) -> int:
        """Count currently open copy-trade positions."""
        result = await session.execute(
            select(func.count(Position.id)).where(
                Position.status == "open",
                Position.source == "copy_trade",
                Position.is_paper == is_paper,
            )
        )
        return result.scalar_one()

    async def _get_or_create_token(
        self, session: AsyncSession, mint: str,
    ) -> Token | None:
        """Get existing token or create a minimal record for FK constraint."""
        result = await session.execute(
            select(Token).where(Token.address == mint)
        )
        token = result.scalar_one_or_none()
        if token:
            return token

        # Create minimal token record
        token = Token(
            address=mint,
            chain="sol",
            source="copy_trade",
        )
        session.add(token)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            # Race condition: another worker created it
            result = await session.execute(
                select(Token).where(Token.address == mint)
            )
            token = result.scalar_one_or_none()
        return token

    # ── Stats ─────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, int]:
        """Return copy trading stats for logging/metrics."""
        return {
            "events_received": self._events_received,
            "swaps_parsed": self._swaps_parsed,
            "buys_opened": self._buys_opened,
            "sells_mirrored": self._sells_mirrored,
            "skipped_dedup": self._skipped_dedup,
            "skipped_non_swap": self._skipped_non_swap,
            "errors": self._errors,
        }
