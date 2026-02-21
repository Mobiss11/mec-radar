"""Tests for OHLCV pattern detection."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import Token, TokenOHLCV
from src.parsers.ohlcv_patterns import (
    compute_volatility,
    detect_ohlcv_patterns,
    get_volatility_metrics,
)


@pytest_asyncio.fixture
async def token_with_candles(db_session: AsyncSession):
    """Create a token for candle tests."""
    token = Token(address="OHLCVtesttoken111111111111111111111111111111", chain="sol")
    db_session.add(token)
    await db_session.flush()
    return token


def _candle(token_id: int, minutes_ago: int, **kw) -> TokenOHLCV:
    defaults = {
        "token_id": token_id,
        "timestamp": datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=minutes_ago),
        "interval": "5m",
        "open": Decimal("1.0"),
        "high": Decimal("1.1"),
        "low": Decimal("0.9"),
        "close": Decimal("1.0"),
        "volume": Decimal("10000"),
    }
    defaults.update(kw)
    return TokenOHLCV(**defaults)


@pytest.mark.asyncio
async def test_no_candles_returns_empty(db_session: AsyncSession, token_with_candles):
    """No candle data → empty list."""
    patterns = await detect_ohlcv_patterns(db_session, token_with_candles.id)
    assert patterns == []


@pytest.mark.asyncio
async def test_too_few_candles_returns_empty(db_session: AsyncSession, token_with_candles):
    """Less than 3 candles → empty list."""
    token = token_with_candles
    db_session.add(_candle(token.id, 10, close=Decimal("1.0")))
    db_session.add(_candle(token.id, 5, close=Decimal("1.5")))
    await db_session.flush()

    patterns = await detect_ohlcv_patterns(db_session, token.id)
    assert patterns == []


@pytest.mark.asyncio
async def test_pump_detected(db_session: AsyncSession, token_with_candles):
    """Price +100% over candles → pump detected."""
    token = token_with_candles
    # Create 6 candles with rising prices (100% increase)
    for i, price in enumerate([1.0, 1.1, 1.3, 1.5, 1.8, 2.0]):
        db_session.add(_candle(
            token.id, (6 - i) * 5,
            close=Decimal(str(price)),
            open=Decimal(str(price * 0.95)),
            high=Decimal(str(price * 1.05)),
            low=Decimal(str(price * 0.9)),
        ))
    await db_session.flush()

    patterns = await detect_ohlcv_patterns(db_session, token.id)
    pattern_names = [p.pattern for p in patterns]
    assert "pump" in pattern_names


@pytest.mark.asyncio
async def test_dump_detected(db_session: AsyncSession, token_with_candles):
    """Price -50% → dump detected."""
    token = token_with_candles
    for i, price in enumerate([2.0, 1.8, 1.5, 1.2, 1.0, 0.9]):
        db_session.add(_candle(
            token.id, (6 - i) * 5,
            close=Decimal(str(price)),
            open=Decimal(str(price * 1.05)),
            high=Decimal(str(price * 1.1)),
            low=Decimal(str(price * 0.95)),
        ))
    await db_session.flush()

    patterns = await detect_ohlcv_patterns(db_session, token.id)
    pattern_names = [p.pattern for p in patterns]
    assert "dump" in pattern_names


@pytest.mark.asyncio
async def test_volume_spike_detected(db_session: AsyncSession, token_with_candles):
    """Recent volume >> average → volume_spike."""
    token = token_with_candles
    # 6 candles: first 4 low volume, last 2 huge volume
    for i in range(6):
        vol = Decimal("1000") if i < 4 else Decimal("20000")
        db_session.add(_candle(
            token.id, (6 - i) * 5,
            volume=vol,
        ))
    await db_session.flush()

    patterns = await detect_ohlcv_patterns(db_session, token.id)
    pattern_names = [p.pattern for p in patterns]
    assert "volume_spike" in pattern_names


@pytest.mark.asyncio
async def test_consolidation_detected(db_session: AsyncSession, token_with_candles):
    """Low volatility → consolidation."""
    token = token_with_candles
    # 6 candles, all very close in price
    for i in range(6):
        db_session.add(_candle(
            token.id, (6 - i) * 5,
            open=Decimal("1.00"),
            high=Decimal("1.02"),
            low=Decimal("0.99"),
            close=Decimal("1.01"),
        ))
    await db_session.flush()

    patterns = await detect_ohlcv_patterns(db_session, token.id)
    pattern_names = [p.pattern for p in patterns]
    assert "consolidation" in pattern_names


@pytest.mark.asyncio
async def test_steady_rise_detected(db_session: AsyncSession, token_with_candles):
    """Consistent higher closes → steady_rise."""
    token = token_with_candles
    # 6 candles, each close higher than prev (but not pump-level)
    for i, price in enumerate([1.0, 1.03, 1.06, 1.09, 1.12, 1.15]):
        db_session.add(_candle(
            token.id, (6 - i) * 5,
            open=Decimal(str(price - 0.01)),
            close=Decimal(str(price)),
            high=Decimal(str(price + 0.01)),
            low=Decimal(str(price - 0.02)),
        ))
    await db_session.flush()

    patterns = await detect_ohlcv_patterns(db_session, token.id)
    pattern_names = [p.pattern for p in patterns]
    assert "steady_rise" in pattern_names


@pytest.mark.asyncio
async def test_score_impact_positive_for_bullish(db_session: AsyncSession, token_with_candles):
    """Bullish patterns should have positive score_impact."""
    token = token_with_candles
    for i, price in enumerate([1.0, 1.2, 1.4, 1.6, 1.8, 2.1]):
        db_session.add(_candle(
            token.id, (6 - i) * 5,
            close=Decimal(str(price)),
            open=Decimal(str(price * 0.95)),
            high=Decimal(str(price * 1.05)),
            low=Decimal(str(price * 0.9)),
        ))
    await db_session.flush()

    patterns = await detect_ohlcv_patterns(db_session, token.id)
    total_impact = sum(p.score_impact for p in patterns)
    assert total_impact > 0


@pytest.mark.asyncio
async def test_score_impact_negative_for_dump(db_session: AsyncSession, token_with_candles):
    """Dump pattern should have negative score_impact."""
    token = token_with_candles
    for i, price in enumerate([2.0, 1.5, 1.2, 0.9, 0.7, 0.5]):
        db_session.add(_candle(
            token.id, (6 - i) * 5,
            close=Decimal(str(price)),
            open=Decimal(str(price * 1.1)),
            high=Decimal(str(price * 1.2)),
            low=Decimal(str(price * 0.9)),
        ))
    await db_session.flush()

    patterns = await detect_ohlcv_patterns(db_session, token.id)
    dump_patterns = [p for p in patterns if p.pattern == "dump"]
    assert len(dump_patterns) > 0
    assert dump_patterns[0].score_impact < 0


# --- Volatility tests ---


def test_compute_volatility_too_few_candles():
    """Less than 3 candles → None."""
    candles = [
        TokenOHLCV(token_id=1, timestamp=datetime.now(), interval="5m",
                   open=Decimal("1"), high=Decimal("1.1"), low=Decimal("0.9"),
                   close=Decimal("1.0"), volume=Decimal("100")),
        TokenOHLCV(token_id=1, timestamp=datetime.now(), interval="5m",
                   open=Decimal("1"), high=Decimal("1.1"), low=Decimal("0.9"),
                   close=Decimal("1.1"), volume=Decimal("100")),
    ]
    assert compute_volatility(candles) is None


def test_compute_volatility_stable_prices():
    """Very stable prices → low volatility."""
    candles = [
        TokenOHLCV(token_id=1, timestamp=datetime.now(), interval="5m",
                   open=Decimal("1"), high=Decimal("1.01"), low=Decimal("0.99"),
                   close=Decimal(str(1.0 + i * 0.001)), volume=Decimal("100"))
        for i in range(6)
    ]
    vol = compute_volatility(candles)
    assert vol is not None
    assert vol < 1.0  # very low volatility


def test_compute_volatility_high_swings():
    """Big price swings → high volatility."""
    prices = [1.0, 1.5, 0.8, 1.3, 0.9, 1.4]
    candles = [
        TokenOHLCV(token_id=1, timestamp=datetime.now(), interval="5m",
                   open=Decimal("1"), high=Decimal("1.6"), low=Decimal("0.7"),
                   close=Decimal(str(p)), volume=Decimal("100"))
        for p in prices
    ]
    vol = compute_volatility(candles)
    assert vol is not None
    assert vol > 10.0  # high volatility


@pytest.mark.asyncio
async def test_get_volatility_metrics_no_data(db_session: AsyncSession, token_with_candles):
    """No candles → both None."""
    vol_5m, vol_1h = await get_volatility_metrics(db_session, token_with_candles.id)
    assert vol_5m is None
    assert vol_1h is None


@pytest.mark.asyncio
async def test_get_volatility_metrics_with_5m_candles(db_session: AsyncSession, token_with_candles):
    """With 5m candles → volatility_5m computed."""
    token = token_with_candles
    for i, price in enumerate([1.0, 1.05, 0.95, 1.1, 0.9, 1.0]):
        db_session.add(_candle(token.id, (6 - i) * 5, close=Decimal(str(price))))
    await db_session.flush()

    vol_5m, vol_1h = await get_volatility_metrics(db_session, token.id)
    assert vol_5m is not None
    assert vol_5m > 0
    assert vol_1h is None  # no 1H candles
