"""Tests for volume profile and wash trading detection."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.models.token import Token, TokenTrade
from src.parsers.volume_profile import analyse_volume_profile


@pytest.mark.asyncio
async def test_wash_trading_detected(db_session):
    """Uniform trade sizes flag wash trading."""
    token = Token(
        address="vol_prof_wash_tok_aaaaaaaaaaaaaaaa",
        chain="sol",
        first_seen_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(token)
    await db_session.flush()

    now = datetime.now(UTC).replace(tzinfo=None)
    for i in range(30):
        trade = TokenTrade(
            token_id=token.id,
            source="birdeye",
            side="buy",
            amount_usd=Decimal("100.00"),  # all same size
            wallet_address=f"wallet{i % 3}aaaaaaaaaaaaaaaaaaaaa",  # only 3 wallets
            timestamp=now - timedelta(minutes=i),
        )
        db_session.add(trade)
    await db_session.flush()

    result = await analyse_volume_profile(db_session, token.id)
    assert result is not None
    assert result.wash_trading_score > 50
    assert result.score_impact < 0


@pytest.mark.asyncio
async def test_diverse_volume_is_healthy(db_session):
    """Diverse trade sizes and many wallets = healthy."""
    token = Token(
        address="vol_prof_diverse_tok_aaaaaaaaaaaaaa",
        chain="sol",
        first_seen_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(token)
    await db_session.flush()

    now = datetime.now(UTC).replace(tzinfo=None)
    import random

    random.seed(42)
    for i in range(30):
        trade = TokenTrade(
            token_id=token.id,
            source="birdeye",
            side="buy" if i % 2 == 0 else "sell",
            amount_usd=Decimal(str(random.uniform(10, 5000))),
            wallet_address=f"div_wallet_{i:04d}aaaaaaaaaaaaaaa",  # all unique
            timestamp=now - timedelta(minutes=i),
        )
        db_session.add(trade)
    await db_session.flush()

    result = await analyse_volume_profile(db_session, token.id)
    assert result is not None
    assert result.wash_trading_score < 50
    assert result.score_impact >= 0


@pytest.mark.asyncio
async def test_insufficient_trades(db_session):
    """Too few trades returns None."""
    token = Token(
        address="vol_prof_insuf_tok_aaaaaaaaaaaaaaa",
        chain="sol",
        first_seen_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(token)
    await db_session.flush()

    trade = TokenTrade(
        token_id=token.id,
        source="birdeye",
        side="buy",
        amount_usd=Decimal("100"),
        wallet_address="single_wallet_aaaaaaaaaaaaaaaa",
        timestamp=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(trade)
    await db_session.flush()

    result = await analyse_volume_profile(db_session, token.id)
    assert result is None


@pytest.mark.asyncio
async def test_micro_trades_detection(db_session):
    """Many micro trades ($<10) contribute to wash score."""
    token = Token(
        address="vol_prof_micro_tok_aaaaaaaaaaaaaaa",
        chain="sol",
        first_seen_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(token)
    await db_session.flush()

    now = datetime.now(UTC).replace(tzinfo=None)
    for i in range(25):
        trade = TokenTrade(
            token_id=token.id,
            source="birdeye",
            side="buy",
            amount_usd=Decimal("2.50"),  # all micro
            wallet_address=f"micro_w_{i:04d}aaaaaaaaaaaaaaaa",
            timestamp=now - timedelta(minutes=i),
        )
        db_session.add(trade)
    await db_session.flush()

    result = await analyse_volume_profile(db_session, token.id)
    assert result is not None
    assert result.pct_micro_trades > 50
