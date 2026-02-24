"""RugGuard — real-time rug pull detection via gRPC pool monitoring.

Monitors Raydium AMM transactions in the same Chainstack gRPC stream.
When a removeLiquidity event is detected for an open position, triggers
an immediate emergency close (paper + real).

Architecture:
- gRPC client adds Raydium AMM tx filter to existing subscription
- gRPC dispatches LP removal events to RugGuard via callback
- RugGuard matches mint against open positions and triggers close
- Close is done directly via DB update (paper) or Jupiter sell (real)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from loguru import logger
from sqlalchemy import select

from src.db.database import async_session_factory
from src.models.trade import Position
from src.parsers.sol_price import get_sol_price_safe

if TYPE_CHECKING:
    from src.parsers.alerts import AlertDispatcher
    from src.parsers.paper_trader import PaperTrader
    from src.trading.real_trader import RealTrader


class RugGuard:
    """Real-time rug pull detector using gRPC LP removal events.

    Maintains a set of token mint addresses with open positions.
    When gRPC detects a removeLiquidity transaction on Raydium AMM
    involving one of these mints, triggers immediate position close.
    """

    def __init__(
        self,
        paper_trader: PaperTrader | None = None,
        real_trader: RealTrader | None = None,
        alert_dispatcher: AlertDispatcher | None = None,
    ) -> None:
        self._paper_trader = paper_trader
        self._real_trader = real_trader
        self._alerts = alert_dispatcher

        # mint → set of position IDs being tracked
        self._watched_mints: dict[str, set[int]] = {}

        # Stats
        self._lp_events_received = 0
        self._lp_events_matched = 0
        self._positions_closed = 0
        self._false_positives_avoided = 0

        # Debounce: don't close same position twice
        self._recently_closed: set[int] = set()

    @property
    def watched_count(self) -> int:
        """Number of unique mints being watched."""
        return len(self._watched_mints)

    @property
    def stats(self) -> dict:
        return {
            "watched_mints": len(self._watched_mints),
            "lp_events_received": self._lp_events_received,
            "lp_events_matched": self._lp_events_matched,
            "positions_closed": self._positions_closed,
            "false_positives_avoided": self._false_positives_avoided,
        }

    async def refresh_watched_positions(self) -> None:
        """Scan DB for all open positions and update watched mint set.

        Called periodically (every 30s) to pick up newly opened positions
        and remove closed ones.
        """
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(Position.id, Position.token_address).where(
                        Position.status == "open",
                    )
                )
                rows = result.all()

            new_watched: dict[str, set[int]] = {}
            for pos_id, mint in rows:
                if mint not in new_watched:
                    new_watched[mint] = set()
                new_watched[mint].add(pos_id)

            # Log changes
            old_mints = set(self._watched_mints.keys())
            new_mints = set(new_watched.keys())
            added = new_mints - old_mints
            removed = old_mints - new_mints

            if added:
                logger.info(f"[RUGGUARD] Watching {len(added)} new mints: {[m[:12] for m in list(added)[:5]]}")
            if removed:
                logger.debug(f"[RUGGUARD] Stopped watching {len(removed)} mints (positions closed)")
                # Clean up recently_closed for removed positions
                for mint in removed:
                    for pid in self._watched_mints.get(mint, set()):
                        self._recently_closed.discard(pid)

            self._watched_mints = new_watched

        except Exception as e:
            logger.error(f"[RUGGUARD] Failed to refresh watched positions: {e}")

    async def on_lp_removal(
        self,
        mint: str,
        signature: str,
        sol_amount: int,
        token_amount: int,
    ) -> None:
        """Handle LP removal event from gRPC.

        Called when a removeLiquidity transaction is detected on Raydium AMM
        involving the given token mint.

        Args:
            mint: Token mint address
            signature: Transaction signature
            sol_amount: SOL amount removed (lamports)
            token_amount: Token amount removed (raw)
        """
        self._lp_events_received += 1

        # Check if this mint has open positions
        position_ids = self._watched_mints.get(mint)
        if not position_ids:
            return

        self._lp_events_matched += 1
        sol_removed = sol_amount / 1e9

        logger.warning(
            f"[RUGGUARD] 🚨 LP REMOVAL detected! mint={mint[:16]}... "
            f"sig={signature[:16]}... sol_removed={sol_removed:.2f} "
            f"positions={len(position_ids)}"
        )

        # Emergency close all positions for this mint
        for pos_id in position_ids:
            if pos_id in self._recently_closed:
                continue
            self._recently_closed.add(pos_id)
            # Run close in background to not block gRPC stream
            asyncio.create_task(
                self._emergency_close(pos_id, mint, signature, sol_removed),
                name=f"rugguard_close_{pos_id}",
            )

    async def _emergency_close(
        self,
        pos_id: int,
        mint: str,
        signature: str,
        sol_removed: float,
    ) -> None:
        """Close a single position due to LP removal.

        Paper positions: direct DB update.
        Real positions: attempt Jupiter sell, then force-close.
        """
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(Position).where(
                        Position.id == pos_id,
                        Position.status == "open",
                    )
                )
                pos = result.scalar_one_or_none()
                if not pos:
                    self._recently_closed.discard(pos_id)
                    return

                _sym = pos.symbol or mint[:12]
                is_paper = pos.is_paper == 1

                if is_paper:
                    # Paper: direct DB close
                    await self._close_paper_position(session, pos, _sym)
                else:
                    # Real: try Jupiter sell first
                    await self._close_real_position(session, pos, _sym)

                await session.commit()
                self._positions_closed += 1

                logger.warning(
                    f"[RUGGUARD] {'PAPER' if is_paper else 'REAL'} position closed: "
                    f"{_sym} (pos_id={pos_id}, reason=rug_lp_removed)"
                )

                # Send alert via Telegram
                if self._alerts:
                    try:
                        mode = "PAPER" if is_paper else "🚨 REAL"
                        await self._alerts.send_real_error(
                            f"🛡️ RugGuard [{mode}]\n"
                            f"LP removal detected for {_sym}\n"
                            f"SOL removed: {sol_removed:.2f}\n"
                            f"Position closed as -100% loss\n"
                            f"TX: {signature[:32]}..."
                        )
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"[RUGGUARD] Emergency close failed for pos_id={pos_id}: {e}")
            self._recently_closed.discard(pos_id)

    async def _close_paper_position(
        self,
        session: "AsyncSession",  # noqa: F821
        pos: Position,
        symbol: str,
    ) -> None:
        """Close paper position as total loss."""
        sol_usd = get_sol_price_safe()
        pos.status = "closed"
        pos.close_reason = "rug_lp_removed"
        pos.closed_at = datetime.now(UTC).replace(tzinfo=None)
        pos.pnl_pct = Decimal("-100")
        pos.pnl_usd = -(pos.amount_sol_invested or Decimal("0")) * Decimal(str(sol_usd))
        pos.current_price = Decimal("0")

        logger.info(f"[RUGGUARD] Paper position {symbol} closed: -100% (rug_lp_removed)")

    async def _close_real_position(
        self,
        session: "AsyncSession",  # noqa: F821
        pos: Position,
        symbol: str,
    ) -> None:
        """Close real position — attempt sell, then force-close if needed.

        Delegates to RealTrader._execute_close() if available, otherwise
        does a direct DB close (tokens are worthless anyway).
        """
        if self._real_trader:
            try:
                # RealTrader handles Jupiter sell with escalating slippage
                closed = await asyncio.wait_for(
                    self._real_trader._execute_close(
                        session,
                        pos,
                        reason="rug_lp_removed",
                        price=Decimal("0"),
                        liquidity_usd=0.0,
                    ),
                    timeout=15.0,
                )
                if closed:
                    return
            except asyncio.TimeoutError:
                logger.warning(f"[RUGGUARD] Real close timeout for {symbol}, force-closing")
            except Exception as e:
                logger.warning(f"[RUGGUARD] Real close error for {symbol}: {e}, force-closing")

        # Force close — tokens are worthless, can't sell
        sol_usd = get_sol_price_safe()
        pos.status = "closed"
        pos.close_reason = "rug_lp_removed"
        pos.closed_at = datetime.now(UTC).replace(tzinfo=None)
        pos.pnl_pct = Decimal("-100")
        pos.pnl_usd = -(pos.amount_sol_invested or Decimal("0")) * Decimal(str(sol_usd))
        pos.current_price = Decimal("0")

        logger.warning(f"[RUGGUARD] Real position {symbol} force-closed: -100% (rug_lp_removed)")

    async def run_refresh_loop(self) -> None:
        """Periodically refresh watched positions (every 30s)."""
        while True:
            try:
                await self.refresh_watched_positions()
            except Exception as e:
                logger.error(f"[RUGGUARD] Refresh loop error: {e}")
            await asyncio.sleep(30)
