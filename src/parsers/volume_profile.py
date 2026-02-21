"""Volume profile analysis — detect wash trading and organic volume."""

from dataclasses import dataclass
from decimal import Decimal
import math

from loguru import logger
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import TokenTrade


@dataclass
class VolumeProfile:
    """Volume distribution analysis."""

    total_trades: int
    avg_trade_usd: float
    median_trade_usd: float
    pct_whale_trades: float  # trades > $1000
    pct_micro_trades: float  # trades < $10
    trade_size_std_dev: float
    wash_trading_score: int  # 0-100, higher = more suspicious
    score_impact: int


async def analyse_volume_profile(
    session: AsyncSession,
    token_id: int,
    *,
    trade_limit: int = 100,
) -> VolumeProfile | None:
    """Analyse trade size distribution to detect wash trading.

    Wash trading indicators:
    - Very uniform trade sizes (low std dev relative to mean)
    - High % of micro trades (bots)
    - Very few unique wallet addresses
    """
    stmt = (
        select(TokenTrade.amount_usd, TokenTrade.wallet_address)
        .where(
            and_(
                TokenTrade.token_id == token_id,
                TokenTrade.amount_usd.is_not(None),
                TokenTrade.amount_usd > 0,
            )
        )
        .order_by(TokenTrade.timestamp.desc())
        .limit(trade_limit)
    )
    result = await session.execute(stmt)
    rows = result.all()

    if len(rows) < 10:
        return None

    amounts = sorted([float(r[0]) for r in rows])
    wallets = set(r[1] for r in rows if r[1])
    total = len(amounts)

    avg_usd = sum(amounts) / total
    median_usd = amounts[total // 2]

    whale_count = sum(1 for a in amounts if a > 1000)
    micro_count = sum(1 for a in amounts if a < 10)

    # Standard deviation
    variance = sum((a - avg_usd) ** 2 for a in amounts) / total
    std_dev = math.sqrt(variance)

    # Coefficient of variation (std_dev / mean)
    cv = std_dev / avg_usd if avg_usd > 0 else 0

    # Wash trading score
    wash_score = 0

    # Uniform trade sizes (low CV) = suspicious
    if cv < 0.1:
        wash_score += 40
    elif cv < 0.2:
        wash_score += 20

    # Too many micro trades = bot activity
    micro_pct = micro_count / total * 100
    if micro_pct > 70:
        wash_score += 30
    elif micro_pct > 50:
        wash_score += 15

    # Very few unique wallets relative to trades
    wallet_ratio = len(wallets) / total if total > 0 else 0
    if wallet_ratio < 0.1:
        wash_score += 30
    elif wallet_ratio < 0.3:
        wash_score += 15

    wash_score = min(wash_score, 100)

    # Score impact
    if wash_score > 50:
        impact = -5
    elif wash_score > 30:
        impact = -2
    elif cv > 0.8 and len(wallets) > total * 0.5:
        impact = 2  # diverse organic trading
    else:
        impact = 0

    if wash_score > 30:
        logger.info(
            f"[VOLUME] token_id={token_id}: wash_score={wash_score}, "
            f"cv={cv:.2f}, micro={micro_pct:.0f}%, "
            f"wallets={len(wallets)}/{total}"
        )

    return VolumeProfile(
        total_trades=total,
        avg_trade_usd=round(avg_usd, 2),
        median_trade_usd=round(median_usd, 2),
        pct_whale_trades=round(whale_count / total * 100, 1),
        pct_micro_trades=round(micro_pct, 1),
        trade_size_std_dev=round(std_dev, 2),
        wash_trading_score=wash_score,
        score_impact=impact,
    )
