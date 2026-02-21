"""Tests for multi-timeframe price momentum analysis."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.models.token import Token, TokenSnapshot
from src.parsers.price_momentum import compute_price_momentum


@pytest.mark.asyncio
async def test_accelerating_up_trend(db_session):
    """Rising prices with increasing momentum → accelerating_up."""
    token = Token(
        address="price_mom_accel_tok_aaaaaaaaaaaaaa",
        chain="sol",
        first_seen_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(token)
    await db_session.flush()

    now = datetime.now(UTC).replace(tzinfo=None)
    # Create snapshots with accelerating price: 1.0 → 1.05 → 1.15 → 1.30
    prices = [1.30, 1.15, 1.05, 1.0, 0.95, 0.90]
    for i, price in enumerate(prices):
        snap = TokenSnapshot(
            token_id=token.id,
            timestamp=now - timedelta(minutes=5 * i),
            price=Decimal(str(price)),
            liquidity_usd=Decimal("50000"),
        )
        db_session.add(snap)
    await db_session.flush()

    result = await compute_price_momentum(db_session, token.id)
    assert result is not None
    assert result.change_5m is not None
    assert result.change_5m > 0
    assert result.score_impact > 0


@pytest.mark.asyncio
async def test_falling_trend(db_session):
    """Declining prices → falling trend with negative impact."""
    token = Token(
        address="price_mom_fall_tok_aaaaaaaaaaaaaaa",
        chain="sol",
        first_seen_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(token)
    await db_session.flush()

    now = datetime.now(UTC).replace(tzinfo=None)
    prices = [0.50, 0.65, 0.80, 1.00, 1.10, 1.20]
    for i, price in enumerate(prices):
        snap = TokenSnapshot(
            token_id=token.id,
            timestamp=now - timedelta(minutes=5 * i),
            price=Decimal(str(price)),
            liquidity_usd=Decimal("50000"),
        )
        db_session.add(snap)
    await db_session.flush()

    result = await compute_price_momentum(db_session, token.id)
    assert result is not None
    assert result.change_5m is not None
    assert result.change_5m < 0
    assert result.score_impact < 0


@pytest.mark.asyncio
async def test_insufficient_data(db_session):
    """Returns None with fewer than 3 snapshots."""
    token = Token(
        address="price_mom_insuf_tok_aaaaaaaaaaaaaa",
        chain="sol",
        first_seen_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(token)
    await db_session.flush()

    snap = TokenSnapshot(
        token_id=token.id,
        timestamp=datetime.now(UTC).replace(tzinfo=None),
        price=Decimal("1.0"),
        liquidity_usd=Decimal("50000"),
    )
    db_session.add(snap)
    await db_session.flush()

    result = await compute_price_momentum(db_session, token.id)
    assert result is None


@pytest.mark.asyncio
async def test_peak_drawdown_computed(db_session):
    """Peak drawdown is computed correctly."""
    token = Token(
        address="price_mom_drawdn_tok_aaaaaaaaaaaaa",
        chain="sol",
        first_seen_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(token)
    await db_session.flush()

    now = datetime.now(UTC).replace(tzinfo=None)
    # Current price 0.6, peak was 1.0 → 40% drawdown
    prices = [0.60, 0.70, 0.90, 1.00, 0.95]
    for i, price in enumerate(prices):
        snap = TokenSnapshot(
            token_id=token.id,
            timestamp=now - timedelta(minutes=5 * i),
            price=Decimal(str(price)),
            liquidity_usd=Decimal("50000"),
        )
        db_session.add(snap)
    await db_session.flush()

    result = await compute_price_momentum(db_session, token.id)
    assert result is not None
    assert result.peak_drawdown is not None
    assert result.peak_drawdown < -30  # -40% drawdown
