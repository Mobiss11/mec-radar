"""Tests for trade flow analysis."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import Token, TokenTrade
from src.parsers.trade_flow import analyse_trade_flow


@pytest_asyncio.fixture
async def token_for_trades(db_session: AsyncSession):
    """Create a token for trade flow tests."""
    token = Token(address="TRADEtesttoken11111111111111111111111111111", chain="sol")
    db_session.add(token)
    await db_session.flush()
    return token


def _trade(token_id: int, side: str, usd: float, wallet: str, minutes_ago: int = 5) -> TokenTrade:
    return TokenTrade(
        token_id=token_id,
        source="test",
        side=side,
        amount_usd=Decimal(str(usd)),
        wallet_address=wallet,
        timestamp=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=minutes_ago),
    )


@pytest.mark.asyncio
async def test_no_trades_returns_none(db_session: AsyncSession, token_for_trades):
    """No trades → None."""
    result = await analyse_trade_flow(db_session, token_for_trades.id)
    assert result is None


@pytest.mark.asyncio
async def test_buy_dominated_flow(db_session: AsyncSession, token_for_trades):
    """Many buys, few sells → positive net flow."""
    token = token_for_trades
    for i in range(5):
        db_session.add(_trade(token.id, "buy", 2000, f"buyer_{i}"))
    db_session.add(_trade(token.id, "sell", 500, "seller_1"))
    await db_session.flush()

    result = await analyse_trade_flow(db_session, token.id)
    assert result is not None
    assert result.buy_count == 5
    assert result.sell_count == 1
    assert result.net_flow_usd > 0
    assert result.total_buy_volume_usd == Decimal("10000")
    assert result.total_sell_volume_usd == Decimal("500")


@pytest.mark.asyncio
async def test_whale_detection(db_session: AsyncSession, token_for_trades):
    """Trades >= $1000 should count as whale trades."""
    token = token_for_trades
    db_session.add(_trade(token.id, "buy", 5000, "whale_1"))
    db_session.add(_trade(token.id, "buy", 2000, "whale_2"))
    db_session.add(_trade(token.id, "buy", 100, "retail_1"))
    db_session.add(_trade(token.id, "sell", 1500, "whale_seller"))
    await db_session.flush()

    result = await analyse_trade_flow(db_session, token.id)
    assert result is not None
    assert result.whale_buy_count == 2  # 5000, 2000
    assert result.whale_sell_count == 1  # 1500


@pytest.mark.asyncio
async def test_unique_wallets_counted(db_session: AsyncSession, token_for_trades):
    """Unique buyers/sellers should be counted correctly."""
    token = token_for_trades
    # Same buyer 3 times
    db_session.add(_trade(token.id, "buy", 100, "same_buyer"))
    db_session.add(_trade(token.id, "buy", 200, "same_buyer"))
    db_session.add(_trade(token.id, "buy", 300, "another_buyer"))
    db_session.add(_trade(token.id, "sell", 100, "seller"))
    await db_session.flush()

    result = await analyse_trade_flow(db_session, token.id)
    assert result is not None
    assert result.unique_buyers == 2  # same_buyer + another_buyer
    assert result.unique_sellers == 1


@pytest.mark.asyncio
async def test_largest_trade_tracked(db_session: AsyncSession, token_for_trades):
    """Largest buy and sell should be tracked."""
    token = token_for_trades
    db_session.add(_trade(token.id, "buy", 100, "w1"))
    db_session.add(_trade(token.id, "buy", 5000, "w2"))
    db_session.add(_trade(token.id, "buy", 300, "w3"))
    db_session.add(_trade(token.id, "sell", 200, "s1"))
    db_session.add(_trade(token.id, "sell", 3000, "s2"))
    await db_session.flush()

    result = await analyse_trade_flow(db_session, token.id)
    assert result is not None
    assert result.largest_buy_usd == Decimal("5000")
    assert result.largest_sell_usd == Decimal("3000")


@pytest.mark.asyncio
async def test_score_impact_whale_accumulation(db_session: AsyncSession, token_for_trades):
    """Many whale buys + few whale sells → positive score impact."""
    token = token_for_trades
    for i in range(4):
        db_session.add(_trade(token.id, "buy", 2000, f"whale_{i}"))
    db_session.add(_trade(token.id, "sell", 100, "retail_sell"))
    await db_session.flush()

    result = await analyse_trade_flow(db_session, token.id)
    assert result is not None
    assert result.score_impact > 0


@pytest.mark.asyncio
async def test_score_impact_whale_distribution(db_session: AsyncSession, token_for_trades):
    """Many whale sells, no whale buys → negative score impact."""
    token = token_for_trades
    for i in range(4):
        db_session.add(_trade(token.id, "sell", 3000, f"whale_seller_{i}"))
    db_session.add(_trade(token.id, "buy", 50, "retail"))
    await db_session.flush()

    result = await analyse_trade_flow(db_session, token.id)
    assert result is not None
    assert result.score_impact < 0


@pytest.mark.asyncio
async def test_buy_sell_volume_ratio(db_session: AsyncSession, token_for_trades):
    """buy_sell_volume_ratio property should work correctly."""
    token = token_for_trades
    db_session.add(_trade(token.id, "buy", 6000, "b1"))
    db_session.add(_trade(token.id, "sell", 2000, "s1"))
    await db_session.flush()

    result = await analyse_trade_flow(db_session, token.id)
    assert result is not None
    assert result.buy_sell_volume_ratio == 3.0


@pytest.mark.asyncio
async def test_old_trades_excluded(db_session: AsyncSession, token_for_trades):
    """Trades older than hours_back should be excluded."""
    token = token_for_trades
    # Old trade (2 hours ago)
    db_session.add(_trade(token.id, "buy", 5000, "old_buyer", minutes_ago=120))
    # Recent trade (5 min ago)
    db_session.add(_trade(token.id, "sell", 100, "recent_seller", minutes_ago=5))
    await db_session.flush()

    result = await analyse_trade_flow(db_session, token.id, hours_back=1)
    assert result is not None
    # Only the recent sell should be counted
    assert result.buy_count == 0
    assert result.sell_count == 1
