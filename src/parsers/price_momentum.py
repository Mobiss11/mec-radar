"""Multi-timeframe price momentum analysis — detect early entry patterns."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import TokenSnapshot


@dataclass
class PriceMomentum:
    """Multi-timeframe momentum analysis."""

    change_5m: float | None  # % change over ~5 min
    change_15m: float | None  # % change over ~15 min
    change_1h: float | None  # % change over ~1 hour
    acceleration: float | None  # 5m_change / 15m_change ratio
    trend: str  # "accelerating_up", "decelerating", "consolidating", "falling"
    peak_drawdown: float | None  # % drop from peak price
    score_impact: int


async def compute_price_momentum(
    session: AsyncSession,
    token_id: int,
) -> PriceMomentum | None:
    """Compute multi-timeframe price momentum from snapshots.

    Requires at least 3 snapshots for meaningful analysis.
    Returns None if insufficient data.
    """
    stmt = (
        select(TokenSnapshot.price, TokenSnapshot.timestamp)
        .where(
            TokenSnapshot.token_id == token_id,
            TokenSnapshot.price.is_not(None),
            TokenSnapshot.price > 0,
        )
        .order_by(TokenSnapshot.timestamp.desc())
        .limit(20)
    )
    result = await session.execute(stmt)
    rows = result.all()

    if len(rows) < 3:
        return None

    prices = [(float(r[0]), r[1]) for r in rows]
    current_price = prices[0][0]
    current_ts = prices[0][1]

    # Find prices at various lookback windows
    def _find_price_at(minutes: int) -> float | None:
        from datetime import timedelta
        target = current_ts - timedelta(minutes=minutes)
        closest = None
        closest_diff = float("inf")
        for price, ts in prices:
            diff = abs((ts - target).total_seconds())
            if diff < closest_diff:
                closest_diff = diff
                closest = price
        # Only use if within 50% tolerance of target window
        if closest_diff < minutes * 60 * 0.5:
            return closest
        return None

    p_5m = _find_price_at(5)
    p_15m = _find_price_at(15)
    p_1h = _find_price_at(60)

    change_5m = ((current_price - p_5m) / p_5m * 100) if p_5m and p_5m > 0 else None
    change_15m = ((current_price - p_15m) / p_15m * 100) if p_15m and p_15m > 0 else None
    change_1h = ((current_price - p_1h) / p_1h * 100) if p_1h and p_1h > 0 else None

    # Acceleration: ratio of short-term to medium-term momentum
    acceleration = None
    if change_5m is not None and change_15m is not None and change_15m != 0:
        acceleration = change_5m / (change_15m / 3)  # normalize to per-5-min

    # Peak drawdown
    peak = max(p for p, _ in prices)
    peak_drawdown = (current_price - peak) / peak * 100 if peak > 0 else None

    # Determine trend
    trend = _classify_trend(change_5m, change_15m, change_1h, acceleration)

    # Score impact
    impact = _compute_impact(trend, change_1h, peak_drawdown)

    if impact != 0:
        parts = [f"[MOMENTUM] token_id={token_id}:"]
        if change_5m is not None:
            parts.append(f"5m={change_5m:+.1f}%")
        if change_15m is not None:
            parts.append(f"15m={change_15m:+.1f}%")
        if change_1h is not None:
            parts.append(f"1h={change_1h:+.1f}%")
        parts.append(f"trend={trend}, impact={impact}")
        logger.debug(" ".join(parts))

    return PriceMomentum(
        change_5m=round(change_5m, 1) if change_5m else None,
        change_15m=round(change_15m, 1) if change_15m else None,
        change_1h=round(change_1h, 1) if change_1h else None,
        acceleration=round(acceleration, 2) if acceleration else None,
        trend=trend,
        peak_drawdown=round(peak_drawdown, 1) if peak_drawdown else None,
        score_impact=impact,
    )


def _classify_trend(
    c5: float | None,
    c15: float | None,
    c1h: float | None,
    accel: float | None,
) -> str:
    """Classify price trend from momentum data."""
    if c5 is not None and c15 is not None:
        if c5 > 5 and c15 > 10 and (accel is None or accel > 1.2):
            return "accelerating_up"
        if c5 > 0 and c15 > 0:
            return "rising"
        if c5 < -5 and c15 < -10:
            return "falling"

    if c1h is not None:
        if abs(c1h) < 5:
            return "consolidating"
        if c1h > 0 and c5 is not None and c5 < 0:
            return "decelerating"
        if c1h < -30:
            return "falling"
        if c1h > 0:
            return "rising"

    return "unknown"


def _compute_impact(trend: str, change_1h: float | None, drawdown: float | None) -> int:
    """Compute score impact from trend."""
    if trend == "accelerating_up":
        return 3
    if trend == "rising":
        return 1
    if trend == "decelerating":
        return -2
    if trend == "falling":
        if drawdown is not None and drawdown < -30:
            return -4
        return -2
    return 0
