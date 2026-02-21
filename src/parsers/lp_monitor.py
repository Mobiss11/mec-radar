"""LP exit monitoring — detect liquidity removal events.

Uses Helius parsed transaction history to detect LP removals from Raydium AMM.
Large removals (>30% of pool) are strong rug indicators.
"""

from dataclasses import dataclass
from decimal import Decimal

from loguru import logger
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import TokenSnapshot


@dataclass
class LPRemovalEvent:
    """Detected LP removal event."""

    token_address: str
    removed_pct: float  # estimated % of LP removed
    severity: str  # "warning", "critical"
    score_impact: int


async def check_lp_removal(
    session: AsyncSession,
    token_id: int,
) -> LPRemovalEvent | None:
    """Compare liquidity between two most recent snapshots to detect LP removal.

    If liquidity dropped >20% between snapshots without corresponding price drop,
    it's likely an LP removal event (not just organic selling).
    """
    stmt = (
        select(TokenSnapshot)
        .where(TokenSnapshot.token_id == token_id)
        .order_by(TokenSnapshot.timestamp.desc())
        .limit(2)
    )
    result = await session.execute(stmt)
    snapshots = result.scalars().all()

    if len(snapshots) < 2:
        return None

    current = snapshots[0]
    previous = snapshots[1]

    cur_liq = float(current.liquidity_usd or current.dex_liquidity_usd or 0)
    prev_liq = float(previous.liquidity_usd or previous.dex_liquidity_usd or 0)

    if prev_liq <= 0 or cur_liq <= 0:
        return None

    liq_change_pct = (cur_liq - prev_liq) / prev_liq * 100

    # Only flag if liquidity dropped significantly
    if liq_change_pct >= -20:
        return None

    # Check if price also dropped proportionally — if yes, it's organic selling
    cur_price = float(current.price or current.dex_price or 0)
    prev_price = float(previous.price or previous.dex_price or 0)

    if prev_price > 0 and cur_price > 0:
        price_change_pct = (cur_price - prev_price) / prev_price * 100
        # If price dropped roughly as much as liquidity, it's selling not LP removal
        if price_change_pct < liq_change_pct * 0.5:
            return None

    removed_pct = abs(liq_change_pct)
    token_address = ""

    # Determine severity
    if removed_pct >= 50:
        severity = "critical"
        score_impact = -25  # effectively kills the score
    elif removed_pct >= 30:
        severity = "critical"
        score_impact = -15
    else:
        severity = "warning"
        score_impact = -8

    logger.warning(
        f"[LP] Detected removal ~{removed_pct:.0f}% for token_id={token_id} "
        f"(${prev_liq:,.0f} → ${cur_liq:,.0f})"
    )

    return LPRemovalEvent(
        token_address=token_address,
        removed_pct=removed_pct,
        severity=severity,
        score_impact=score_impact,
    )


async def get_lp_removal_pct(
    session: AsyncSession,
    token_id: int,
) -> Decimal | None:
    """Get cumulative LP removal % from initial liquidity.

    Compares current liquidity to the peak observed liquidity.
    Returns None if insufficient data.
    """
    stmt = (
        select(
            func.max(TokenSnapshot.liquidity_usd).label("peak_liq"),
        )
        .where(TokenSnapshot.token_id == token_id)
    )
    result = await session.execute(stmt)
    row = result.one_or_none()

    if row is None or row.peak_liq is None:
        return None

    # Get current liquidity
    cur_stmt = (
        select(TokenSnapshot.liquidity_usd)
        .where(TokenSnapshot.token_id == token_id)
        .order_by(TokenSnapshot.timestamp.desc())
        .limit(1)
    )
    cur_result = await session.execute(cur_stmt)
    cur_liq = cur_result.scalar_one_or_none()

    if cur_liq is None or row.peak_liq <= 0:
        return None

    removed = (row.peak_liq - cur_liq) / row.peak_liq * 100
    return Decimal(str(max(float(removed), 0)))
