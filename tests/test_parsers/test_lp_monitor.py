"""Tests for LP exit monitoring."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import Token, TokenSnapshot
from src.parsers.lp_monitor import check_lp_removal, get_lp_removal_pct


@pytest_asyncio.fixture
async def lp_token(db_session: AsyncSession):
    """Create a token for LP monitoring tests."""
    token = Token(address="LPMONtesttoken11111111111111111111111111111", chain="sol")
    db_session.add(token)
    await db_session.flush()
    return token


def _snap(token_id: int, minutes_ago: int, liq: str, price: str = "1.0") -> TokenSnapshot:
    return TokenSnapshot(
        token_id=token_id,
        timestamp=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=minutes_ago),
        liquidity_usd=Decimal(liq),
        price=Decimal(price),
        stage="TEST",
    )


@pytest.mark.asyncio
async def test_no_snapshots_returns_none(db_session: AsyncSession, lp_token):
    """No snapshots → no LP removal detected."""
    result = await check_lp_removal(db_session, lp_token.id)
    assert result is None


@pytest.mark.asyncio
async def test_single_snapshot_returns_none(db_session: AsyncSession, lp_token):
    """Single snapshot → not enough data."""
    db_session.add(_snap(lp_token.id, 5, "50000"))
    await db_session.flush()
    result = await check_lp_removal(db_session, lp_token.id)
    assert result is None


@pytest.mark.asyncio
async def test_small_drop_not_flagged(db_session: AsyncSession, lp_token):
    """<20% liquidity drop should not be flagged."""
    db_session.add(_snap(lp_token.id, 10, "50000", "1.0"))  # older
    db_session.add(_snap(lp_token.id, 0, "45000", "0.95"))  # current — 10% drop
    await db_session.flush()
    result = await check_lp_removal(db_session, lp_token.id)
    assert result is None


@pytest.mark.asyncio
async def test_large_lp_drop_detected(db_session: AsyncSession, lp_token):
    """50%+ liquidity drop with stable price → LP removal detected."""
    db_session.add(_snap(lp_token.id, 10, "100000", "1.0"))  # older
    db_session.add(_snap(lp_token.id, 0, "40000", "0.95"))  # current — 60% drop, price barely moved
    await db_session.flush()
    result = await check_lp_removal(db_session, lp_token.id)
    assert result is not None
    assert result.severity == "critical"
    assert result.score_impact == -25


@pytest.mark.asyncio
async def test_organic_selling_not_flagged(db_session: AsyncSession, lp_token):
    """Price dropped proportionally to liquidity → organic selling, not LP removal."""
    db_session.add(_snap(lp_token.id, 10, "100000", "1.0"))  # older
    db_session.add(_snap(lp_token.id, 0, "50000", "0.3"))  # current — both dropped ~50%
    await db_session.flush()
    result = await check_lp_removal(db_session, lp_token.id)
    assert result is None


@pytest.mark.asyncio
async def test_get_lp_removal_pct_from_peak(db_session: AsyncSession, lp_token):
    """Cumulative LP removal should compare current to peak."""
    db_session.add(_snap(lp_token.id, 30, "80000"))
    db_session.add(_snap(lp_token.id, 20, "100000"))  # peak
    db_session.add(_snap(lp_token.id, 10, "90000"))
    db_session.add(_snap(lp_token.id, 0, "60000"))  # current
    await db_session.flush()
    pct = await get_lp_removal_pct(db_session, lp_token.id)
    assert pct is not None
    assert float(pct) == pytest.approx(40.0, abs=1.0)  # (100k - 60k) / 100k = 40%


@pytest.mark.asyncio
async def test_get_lp_removal_pct_no_removal(db_session: AsyncSession, lp_token):
    """If current == peak → 0% removal."""
    db_session.add(_snap(lp_token.id, 10, "50000"))
    db_session.add(_snap(lp_token.id, 0, "60000"))  # grew, not removed
    await db_session.flush()
    pct = await get_lp_removal_pct(db_session, lp_token.id)
    assert pct is not None
    assert float(pct) == 0.0
