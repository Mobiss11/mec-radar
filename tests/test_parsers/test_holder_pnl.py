"""Tests for holder PnL analysis."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.models.token import Token, TokenSnapshot, TokenTopHolder
from src.parsers.holder_pnl import analyse_holder_pnl


@pytest.mark.asyncio
async def test_most_holders_in_profit(db_session):
    """80%+ holders in profit → positive impact."""
    token = Token(
        address="holder_pnl_profit_tok_aaaaaaaaaaaa",
        chain="sol",
        first_seen_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(token)
    await db_session.flush()

    snap = TokenSnapshot(
        token_id=token.id,
        timestamp=datetime.now(UTC).replace(tzinfo=None),
        liquidity_usd=Decimal("50000"),
    )
    db_session.add(snap)
    await db_session.flush()

    # 8 out of 10 in profit
    for i in range(10):
        holder = TokenTopHolder(
            snapshot_id=snap.id,
            token_id=token.id,
            rank=i + 1,
            address=f"holder_{i:04d}aaaaaaaaaaaaaaaaaaa",
            pnl=Decimal("500") if i < 8 else Decimal("-200"),
            percentage=Decimal("5"),
        )
        db_session.add(holder)
    await db_session.flush()

    result = await analyse_holder_pnl(db_session, token.id)
    assert result is not None
    assert result.pct_in_profit >= 80
    assert result.score_impact > 0


@pytest.mark.asyncio
async def test_most_holders_at_loss(db_session):
    """<30% holders in profit → negative impact (bagholders)."""
    token = Token(
        address="holder_pnl_loss_tok_aaaaaaaaaaaaa",
        chain="sol",
        first_seen_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(token)
    await db_session.flush()

    snap = TokenSnapshot(
        token_id=token.id,
        timestamp=datetime.now(UTC).replace(tzinfo=None),
        liquidity_usd=Decimal("50000"),
    )
    db_session.add(snap)
    await db_session.flush()

    # 2 out of 10 in profit
    for i in range(10):
        holder = TokenTopHolder(
            snapshot_id=snap.id,
            token_id=token.id,
            rank=i + 1,
            address=f"loss_h_{i:04d}aaaaaaaaaaaaaaaaaa",
            pnl=Decimal("100") if i < 2 else Decimal("-300"),
            percentage=Decimal("5"),
        )
        db_session.add(holder)
    await db_session.flush()

    result = await analyse_holder_pnl(db_session, token.id)
    assert result is not None
    assert result.pct_in_profit < 30
    assert result.score_impact < 0


@pytest.mark.asyncio
async def test_no_pnl_data(db_session):
    """No holders with PnL data → None."""
    token = Token(
        address="holder_pnl_nodata_tok_aaaaaaaaaaa",
        chain="sol",
        first_seen_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(token)
    await db_session.flush()

    snap = TokenSnapshot(
        token_id=token.id,
        timestamp=datetime.now(UTC).replace(tzinfo=None),
        liquidity_usd=Decimal("50000"),
    )
    db_session.add(snap)
    await db_session.flush()

    for i in range(5):
        holder = TokenTopHolder(
            snapshot_id=snap.id,
            token_id=token.id,
            rank=i + 1,
            address=f"no_pnl_{i:04d}aaaaaaaaaaaaaaaaaa",
            pnl=None,
            percentage=Decimal("5"),
        )
        db_session.add(holder)
    await db_session.flush()

    result = await analyse_holder_pnl(db_session, token.id)
    assert result is None
