"""Holder concentration rate of change — detect rapid accumulation/distribution."""

from dataclasses import dataclass
from decimal import Decimal

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import TokenSnapshot


@dataclass
class ConcentrationRate:
    """Rate of change in top holder concentration."""

    current_pct: float
    delta_pct_per_hour: float  # positive = concentrating, negative = distributing
    trend: str  # "increasing", "stable", "decreasing"
    score_impact: int


async def compute_concentration_rate(
    session: AsyncSession,
    token_id: int,
) -> ConcentrationRate | None:
    """Compute rate of change in top10 holder percentage.

    Requires at least 2 snapshots with top10_holders_pct.
    Returns None if insufficient data.
    """
    stmt = (
        select(TokenSnapshot.top10_holders_pct, TokenSnapshot.timestamp)
        .where(
            TokenSnapshot.token_id == token_id,
            TokenSnapshot.top10_holders_pct.is_not(None),
        )
        .order_by(TokenSnapshot.timestamp.desc())
        .limit(3)
    )
    result = await session.execute(stmt)
    rows = result.all()

    if len(rows) < 2:
        return None

    newest_pct = float(rows[0][0])
    oldest_pct = float(rows[-1][0])
    newest_ts = rows[0][1]
    oldest_ts = rows[-1][1]

    time_diff_hours = (newest_ts - oldest_ts).total_seconds() / 3600
    if time_diff_hours < 0.01:  # avoid division by zero
        return None

    delta_per_hour = (newest_pct - oldest_pct) / time_diff_hours

    if delta_per_hour > 5:
        trend = "increasing"
        impact = -5  # rapid concentration = someone accumulating to dump
    elif delta_per_hour > 2:
        trend = "increasing"
        impact = -2
    elif delta_per_hour < -5:
        trend = "decreasing"
        impact = 3  # rapid distribution = healthy
    elif delta_per_hour < -2:
        trend = "decreasing"
        impact = 1
    else:
        trend = "stable"
        impact = 0

    if impact != 0:
        logger.debug(
            f"[CONCENTRATION] token_id={token_id}: "
            f"{oldest_pct:.1f}%→{newest_pct:.1f}%, "
            f"rate={delta_per_hour:+.1f}%/h, trend={trend}"
        )

    return ConcentrationRate(
        current_pct=round(newest_pct, 1),
        delta_pct_per_hour=round(delta_per_hour, 2),
        trend=trend,
        score_impact=impact,
    )
