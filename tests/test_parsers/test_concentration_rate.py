"""Tests for holder concentration rate of change analysis."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.models.token import Token, TokenSnapshot
from src.parsers.concentration_rate import compute_concentration_rate


@pytest.mark.asyncio
async def test_concentration_increasing(db_session):
    """Rapidly increasing concentration triggers negative impact."""
    token = Token(
        address="conc_rate_incr_tok_aaaaaaaaaaaaaaa",
        chain="sol",
        first_seen_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(token)
    await db_session.flush()

    now = datetime.now(UTC).replace(tzinfo=None)
    for i, pct in enumerate([Decimal("20"), Decimal("30"), Decimal("45")]):
        snap = TokenSnapshot(
            token_id=token.id,
            timestamp=now - timedelta(minutes=30 * (2 - i)),
            top10_holders_pct=pct,
            liquidity_usd=Decimal("50000"),
        )
        db_session.add(snap)
    await db_session.flush()

    result = await compute_concentration_rate(db_session, token.id)
    assert result is not None
    assert result.trend == "increasing"
    assert result.score_impact < 0


@pytest.mark.asyncio
async def test_concentration_decreasing(db_session):
    """Decreasing concentration (distribution) is healthy."""
    token = Token(
        address="conc_rate_decr_tok_aaaaaaaaaaaaaaa",
        chain="sol",
        first_seen_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(token)
    await db_session.flush()

    now = datetime.now(UTC).replace(tzinfo=None)
    for i, pct in enumerate([Decimal("60"), Decimal("45"), Decimal("30")]):
        snap = TokenSnapshot(
            token_id=token.id,
            timestamp=now - timedelta(minutes=30 * (2 - i)),
            top10_holders_pct=pct,
            liquidity_usd=Decimal("50000"),
        )
        db_session.add(snap)
    await db_session.flush()

    result = await compute_concentration_rate(db_session, token.id)
    assert result is not None
    assert result.trend == "decreasing"
    assert result.score_impact > 0


@pytest.mark.asyncio
async def test_concentration_insufficient_data(db_session):
    """Returns None with fewer than 3 snapshots."""
    token = Token(
        address="conc_rate_insuf_tok_aaaaaaaaaaaaaa",
        chain="sol",
        first_seen_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(token)
    await db_session.flush()

    snap = TokenSnapshot(
        token_id=token.id,
        timestamp=datetime.now(UTC).replace(tzinfo=None),
        top10_holders_pct=Decimal("30"),
        liquidity_usd=Decimal("50000"),
    )
    db_session.add(snap)
    await db_session.flush()

    result = await compute_concentration_rate(db_session, token.id)
    assert result is None
