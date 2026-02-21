"""Trade flow analysis — whale buys, volume distribution, buy/sell pressure from trades.

Uses stored token_trades data to identify large-wallet activity patterns.
"""

from dataclasses import dataclass
from decimal import Decimal
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import select, and_, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import TokenTrade


@dataclass
class TradeFlowAnalysis:
    """Summary of recent trade flow for a token."""

    total_buy_volume_usd: Decimal
    total_sell_volume_usd: Decimal
    buy_count: int
    sell_count: int
    whale_buy_count: int  # single trades > $1000
    whale_sell_count: int
    unique_buyers: int
    unique_sellers: int
    largest_buy_usd: Decimal
    largest_sell_usd: Decimal
    net_flow_usd: Decimal  # positive = net buying
    score_impact: int  # bonus/penalty for scoring

    @property
    def buy_sell_volume_ratio(self) -> float:
        if self.total_sell_volume_usd and self.total_sell_volume_usd > 0:
            return float(self.total_buy_volume_usd / self.total_sell_volume_usd)
        return float("inf") if self.total_buy_volume_usd > 0 else 0.0


async def analyse_trade_flow(
    session: AsyncSession,
    token_id: int,
    hours_back: int = 1,
    whale_threshold_usd: float = 1000.0,
) -> TradeFlowAnalysis | None:
    """Analyse recent trades for a token. Returns None if no trade data."""
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=hours_back)

    stmt = (
        select(TokenTrade)
        .where(
            and_(
                TokenTrade.token_id == token_id,
                TokenTrade.timestamp >= cutoff,
            )
        )
        .order_by(TokenTrade.timestamp.desc())
    )
    result = await session.execute(stmt)
    trades = result.scalars().all()

    if not trades:
        return None

    total_buy = Decimal("0")
    total_sell = Decimal("0")
    buy_count = 0
    sell_count = 0
    whale_buys = 0
    whale_sells = 0
    buyers: set[str] = set()
    sellers: set[str] = set()
    largest_buy = Decimal("0")
    largest_sell = Decimal("0")

    for t in trades:
        usd = t.amount_usd or Decimal("0")
        wallet = t.wallet_address or ""

        if t.side == "buy":
            total_buy += usd
            buy_count += 1
            if wallet:
                buyers.add(wallet)
            if usd > largest_buy:
                largest_buy = usd
            if float(usd) >= whale_threshold_usd:
                whale_buys += 1
        elif t.side == "sell":
            total_sell += usd
            sell_count += 1
            if wallet:
                sellers.add(wallet)
            if usd > largest_sell:
                largest_sell = usd
            if float(usd) >= whale_threshold_usd:
                whale_sells += 1

    net_flow = total_buy - total_sell

    # Compute score impact
    score_impact = 0

    # Strong net buying pressure
    if total_sell > 0:
        ratio = float(total_buy / total_sell)
        if ratio >= 5.0:
            score_impact += 5
        elif ratio >= 3.0:
            score_impact += 3
        elif ratio >= 2.0:
            score_impact += 1

    # Whale accumulation (many large buys, few large sells)
    if whale_buys >= 3 and whale_sells <= 1:
        score_impact += 3
    elif whale_buys == 0 and whale_sells >= 3:
        score_impact -= 4  # whale distribution

    # Diverse buyer base (many unique wallets buying)
    if len(buyers) >= 10 and len(sellers) <= 5:
        score_impact += 2
    elif len(sellers) >= 10 and len(buyers) <= 3:
        score_impact -= 3  # mass selling, few buying

    analysis = TradeFlowAnalysis(
        total_buy_volume_usd=total_buy,
        total_sell_volume_usd=total_sell,
        buy_count=buy_count,
        sell_count=sell_count,
        whale_buy_count=whale_buys,
        whale_sell_count=whale_sells,
        unique_buyers=len(buyers),
        unique_sellers=len(sellers),
        largest_buy_usd=largest_buy,
        largest_sell_usd=largest_sell,
        net_flow_usd=net_flow,
        score_impact=score_impact,
    )

    if score_impact != 0:
        logger.debug(
            f"[TRADE_FLOW] token_id={token_id}: "
            f"net_flow=${float(net_flow):,.0f} "
            f"buy_ratio={analysis.buy_sell_volume_ratio:.1f}x "
            f"whale_buys={whale_buys} whale_sells={whale_sells} "
            f"impact={score_impact:+d}"
        )

    return analysis
