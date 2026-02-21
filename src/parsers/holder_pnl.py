"""Holder PnL analysis — detect early stage vs bagholder tokens + wash trading.

Analyses top holder profit/loss data to determine if the token
is in an early accumulation phase or late dumping phase.
Phase 13: Wash trading detection via loss_ratio + price divergence.
"""

from dataclasses import dataclass
from decimal import Decimal

from loguru import logger
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import TokenSnapshot, TokenTopHolder


@dataclass
class HolderPnLResult:
    """Result of holder PnL analysis."""

    holders_with_pnl: int
    avg_pnl: float
    pct_in_profit: float  # 0.0 - 100.0
    score_impact: int
    # Phase 13: wash trading detection
    loss_ratio: float = 0.0  # 0.0-1.0
    wash_trading_suspected: bool = False
    dump_risk: bool = False

    @property
    def wash_risk_boost(self) -> int:
        """Additional risk boost from wash trading analysis."""
        if self.wash_trading_suspected:
            return 8
        if self.dump_risk:
            return 5
        return 0


async def analyse_holder_pnl(
    session: AsyncSession,
    token_id: int,
    *,
    price_change_pct: float | None = None,
) -> HolderPnLResult | None:
    """Analyse top holder PnL data for a token.

    Returns None if insufficient PnL data (< 3 holders with pnl).

    Phase 13: Added wash trading detection.
    If 80%+ holders are in loss while price is rising → wash trading.
    """
    # Get latest snapshot
    snap_stmt = (
        select(TokenSnapshot.id)
        .where(TokenSnapshot.token_id == token_id)
        .order_by(TokenSnapshot.timestamp.desc())
        .limit(1)
    )
    snap_result = await session.execute(snap_stmt)
    snap_id = snap_result.scalar_one_or_none()
    if snap_id is None:
        return None

    # Get holders with PnL data
    stmt = (
        select(TokenTopHolder.pnl)
        .where(
            and_(
                TokenTopHolder.snapshot_id == snap_id,
                TokenTopHolder.pnl.is_not(None),
            )
        )
    )
    result = await session.execute(stmt)
    pnl_values = [float(row[0]) for row in result.all()]

    if len(pnl_values) < 3:
        return None

    avg_pnl = sum(pnl_values) / len(pnl_values)
    in_profit = sum(1 for p in pnl_values if p > 0)
    in_loss = sum(1 for p in pnl_values if p < 0)
    pct_in_profit = in_profit / len(pnl_values) * 100
    loss_ratio = in_loss / len(pnl_values) if pnl_values else 0.0

    # Score impact (existing logic)
    if pct_in_profit >= 80:
        impact = 3  # early stage, most holders winning
    elif pct_in_profit >= 60:
        impact = 1
    elif pct_in_profit <= 30:
        impact = -3  # bagholders, dumps likely
    elif pct_in_profit <= 40:
        impact = -1
    else:
        impact = 0

    # Phase 13: Wash trading detection
    wash_trading = (
        loss_ratio > 0.8
        and price_change_pct is not None
        and price_change_pct > 5.0
    )
    dump_risk = loss_ratio > 0.9

    if wash_trading:
        logger.info(
            f"[PNL] Wash trading suspected for token_id={token_id}: "
            f"{in_loss}/{len(pnl_values)} holders in loss ({loss_ratio:.0%}) "
            f"while price up {price_change_pct:.1f}%"
        )
    else:
        logger.debug(
            f"[PNL] token_id={token_id}: {len(pnl_values)} holders, "
            f"avg_pnl={avg_pnl:.2f}, {pct_in_profit:.0f}% in profit, impact={impact}"
        )

    return HolderPnLResult(
        holders_with_pnl=len(pnl_values),
        avg_pnl=round(avg_pnl, 2),
        pct_in_profit=round(pct_in_profit, 1),
        score_impact=impact,
        loss_ratio=round(loss_ratio, 3),
        wash_trading_suspected=wash_trading,
        dump_risk=dump_risk,
    )
