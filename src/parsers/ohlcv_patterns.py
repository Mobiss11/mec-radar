"""OHLCV pattern detection — pump/dump, volume spikes, consolidation.

Analyses stored candle data for a token to identify trading patterns.
"""

from dataclasses import dataclass
from decimal import Decimal

from loguru import logger
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import TokenOHLCV


@dataclass
class OHLCVPattern:
    """Detected OHLCV pattern."""

    pattern: str  # "pump", "dump", "volume_spike", "consolidation", "steady_rise"
    severity: str  # "low", "medium", "high"
    description: str
    score_impact: int


async def detect_ohlcv_patterns(
    session: AsyncSession, token_id: int, interval: str = "5m", limit: int = 12
) -> list[OHLCVPattern]:
    """Analyse recent candles for a token. Returns detected patterns.

    Default: last 12 x 5m candles (= 1 hour of data).
    """
    stmt = (
        select(TokenOHLCV)
        .where(
            and_(
                TokenOHLCV.token_id == token_id,
                TokenOHLCV.interval == interval,
            )
        )
        .order_by(TokenOHLCV.timestamp.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    candles = list(reversed(result.scalars().all()))

    if len(candles) < 3:
        return []

    patterns: list[OHLCVPattern] = []

    # Extract price/volume series
    closes = [float(c.close) for c in candles if c.close]
    volumes = [float(c.volume) for c in candles if c.volume]
    highs = [float(c.high) for c in candles if c.high]
    lows = [float(c.low) for c in candles if c.low]

    if len(closes) < 3:
        return []

    # 1. Pump detection: rapid price increase (>50% in the window)
    if closes[0] > 0:
        total_change = (closes[-1] - closes[0]) / closes[0] * 100
        if total_change >= 100:
            patterns.append(OHLCVPattern(
                pattern="pump",
                severity="high",
                description=f"Price +{total_change:.0f}% over {len(candles)} candles",
                score_impact=5,
            ))
        elif total_change >= 50:
            patterns.append(OHLCVPattern(
                pattern="pump",
                severity="medium",
                description=f"Price +{total_change:.0f}% over {len(candles)} candles",
                score_impact=3,
            ))

    # 2. Dump detection: rapid price decrease (>30%)
    if closes[0] > 0:
        total_change = (closes[-1] - closes[0]) / closes[0] * 100
        if total_change <= -50:
            patterns.append(OHLCVPattern(
                pattern="dump",
                severity="high",
                description=f"Price {total_change:.0f}% over {len(candles)} candles",
                score_impact=-8,
            ))
        elif total_change <= -30:
            patterns.append(OHLCVPattern(
                pattern="dump",
                severity="medium",
                description=f"Price {total_change:.0f}% over {len(candles)} candles",
                score_impact=-4,
            ))

    # 3. Volume spike: recent volume >> average
    if len(volumes) >= 4:
        avg_vol = sum(volumes[:-2]) / max(len(volumes) - 2, 1)
        recent_vol = sum(volumes[-2:]) / 2
        if avg_vol > 0 and recent_vol / avg_vol >= 5:
            patterns.append(OHLCVPattern(
                pattern="volume_spike",
                severity="high",
                description=f"Recent volume {recent_vol/avg_vol:.1f}x average",
                score_impact=4,
            ))
        elif avg_vol > 0 and recent_vol / avg_vol >= 2.5:
            patterns.append(OHLCVPattern(
                pattern="volume_spike",
                severity="medium",
                description=f"Recent volume {recent_vol/avg_vol:.1f}x average",
                score_impact=2,
            ))

    # 4. Consolidation: low volatility (range < 5% of avg price)
    if highs and lows and closes:
        price_range = max(highs) - min(lows)
        avg_price = sum(closes) / len(closes)
        if avg_price > 0:
            volatility_pct = price_range / avg_price * 100
            if volatility_pct < 5 and len(candles) >= 6:
                patterns.append(OHLCVPattern(
                    pattern="consolidation",
                    severity="low",
                    description=f"Price range only {volatility_pct:.1f}% (consolidating)",
                    score_impact=2,  # consolidation before breakout is positive
                ))

    # 5. Steady rise: consistent higher closes (>70% of candles green)
    if len(closes) >= 4:
        green_count = sum(
            1 for i in range(1, len(closes)) if closes[i] > closes[i - 1]
        )
        green_pct = green_count / (len(closes) - 1) * 100
        if green_pct >= 75 and closes[0] > 0:
            rise_pct = (closes[-1] - closes[0]) / closes[0] * 100
            if rise_pct >= 10:
                patterns.append(OHLCVPattern(
                    pattern="steady_rise",
                    severity="medium",
                    description=f"{green_pct:.0f}% green candles, +{rise_pct:.0f}% total",
                    score_impact=3,
                ))

    if patterns:
        logger.debug(
            f"[OHLCV] token_id={token_id}: "
            + ", ".join(f"{p.pattern}({p.severity})" for p in patterns)
        )

    return patterns


def compute_volatility(candles: list[TokenOHLCV]) -> float | None:
    """Compute price volatility as standard deviation of returns.

    Returns volatility as a percentage (e.g. 5.0 = 5% std dev).
    Requires at least 3 candles.
    """
    closes = [float(c.close) for c in candles if c.close and c.close > 0]
    if len(closes) < 3:
        return None

    # Compute log returns
    returns = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            returns.append((closes[i] - closes[i - 1]) / closes[i - 1])

    if len(returns) < 2:
        return None

    mean_ret = sum(returns) / len(returns)
    variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
    std_dev = variance ** 0.5

    return round(std_dev * 100, 2)  # as percentage


async def get_volatility_metrics(
    session: AsyncSession, token_id: int
) -> tuple[float | None, float | None]:
    """Compute volatility from stored candles.

    Returns (volatility_5m, volatility_1h) as percentages.
    """
    # 5m candles for short-term volatility (last 12 = 1 hour)
    stmt_5m = (
        select(TokenOHLCV)
        .where(
            and_(
                TokenOHLCV.token_id == token_id,
                TokenOHLCV.interval == "5m",
            )
        )
        .order_by(TokenOHLCV.timestamp.desc())
        .limit(12)
    )
    result_5m = await session.execute(stmt_5m)
    candles_5m = list(reversed(result_5m.scalars().all()))
    vol_5m = compute_volatility(candles_5m)

    # 1h candles for longer-term volatility (last 24 = 24 hours)
    stmt_1h = (
        select(TokenOHLCV)
        .where(
            and_(
                TokenOHLCV.token_id == token_id,
                TokenOHLCV.interval == "1H",
            )
        )
        .order_by(TokenOHLCV.timestamp.desc())
        .limit(24)
    )
    result_1h = await session.execute(stmt_1h)
    candles_1h = list(reversed(result_1h.scalars().all()))
    vol_1h = compute_volatility(candles_1h)

    return vol_5m, vol_1h
