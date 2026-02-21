"""Tests for bot formatters."""

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.formatters import (
    format_portfolio,
    format_signals,
    format_stats,
    format_token_detail,
)
from src.models.signal import Signal
from src.models.token import Token, TokenSecurity, TokenSnapshot
from src.models.trade import Position


@pytest_asyncio.fixture
async def sample_token(db_session: AsyncSession):
    """Token with snapshot and signal."""
    token = Token(
        address="BOTtesttoken111111111111111111111111111111",
        chain="sol",
        symbol="BOTTEST",
        name="Bot Test Token",
        source="pumpportal",
    )
    db_session.add(token)
    await db_session.flush()

    snap = TokenSnapshot(
        token_id=token.id,
        price=Decimal("0.001"),
        market_cap=Decimal("50000"),
        liquidity_usd=Decimal("30000"),
        holders_count=150,
        score=65,
        score_v3=58,
        stage="INITIAL",
    )
    db_session.add(snap)
    await db_session.flush()

    signal = Signal(
        token_id=token.id,
        token_address=token.address,
        score=65,
        status="strong_buy",
        token_price_at_signal=Decimal("0.001"),
        token_mcap_at_signal=Decimal("50000"),
        liquidity_at_signal=Decimal("30000"),
    )
    db_session.add(signal)
    await db_session.flush()

    return token


@pytest.mark.asyncio
async def test_format_signals_with_data(db_session: AsyncSession, sample_token):
    """Should format signals with token data."""
    result = await format_signals(db_session)
    assert "BOTTEST" in result
    assert "strong_buy" not in result or "🟢🟢" in result
    assert "Recent Signals" in result


@pytest.mark.asyncio
async def test_format_signals_empty(db_session: AsyncSession):
    """Should show empty message when no signals."""
    result = await format_signals(db_session)
    assert "No active signals" in result


@pytest.mark.asyncio
async def test_format_portfolio_no_positions(db_session: AsyncSession):
    """Should show empty portfolio."""
    result = await format_portfolio(db_session)
    assert "No open positions" in result


@pytest.mark.asyncio
async def test_format_portfolio_with_position(db_session: AsyncSession, sample_token):
    """Should show open position."""
    pos = Position(
        token_id=sample_token.id,
        token_address=sample_token.address,
        entry_price=Decimal("0.001"),
        current_price=Decimal("0.0015"),
        amount_token=Decimal("500"),
        amount_sol_invested=Decimal("0.5"),
        pnl_pct=Decimal("50"),
        status="open",
        is_paper=1,
    )
    db_session.add(pos)
    await db_session.flush()

    result = await format_portfolio(db_session)
    assert "BOTTEST" in result
    assert "Open (1)" in result


@pytest.mark.asyncio
async def test_format_token_detail(db_session: AsyncSession, sample_token):
    """Should show token details."""
    result = await format_token_detail(db_session, sample_token.address)
    assert "BOTTEST" in result
    assert "Score v2: 65" in result
    assert "Score v3: 58" in result


@pytest.mark.asyncio
async def test_format_token_not_found(db_session: AsyncSession):
    """Should show not found for unknown address."""
    result = await format_token_detail(db_session, "unknown_address")
    assert "not found" in result


@pytest.mark.asyncio
async def test_format_stats(db_session: AsyncSession, sample_token):
    """Should show pipeline stats."""
    result = await format_stats(db_session)
    assert "Pipeline Stats" in result
    assert "Tokens tracked:" in result
    assert "Signals today:" in result
