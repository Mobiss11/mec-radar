"""Tests for paper trading engine."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.signal import Signal
from src.models.token import Token
from src.models.trade import Position, Trade
from src.parsers.paper_trader import PaperTrader


@pytest_asyncio.fixture
async def token(db_session: AsyncSession):
    t = Token(address="PAPERtesttoken1111111111111111111111111111", chain="sol")
    db_session.add(t)
    await db_session.flush()
    return t


@pytest_asyncio.fixture
async def signal(db_session: AsyncSession, token):
    sig = Signal(
        token_id=token.id,
        token_address=token.address,
        score=70,
        status="strong_buy",
    )
    db_session.add(sig)
    await db_session.flush()
    return sig


@pytest_asyncio.fixture
def trader():
    return PaperTrader(
        sol_per_trade=0.5,
        max_positions=3,
        take_profit_x=2.0,
        stop_loss_pct=-50.0,
        timeout_hours=4,
    )


@pytest.mark.asyncio
async def test_opens_position_on_signal(db_session, token, signal, trader):
    """Should open position on strong_buy signal."""
    pos = await trader.on_signal(db_session, signal, Decimal("0.001"))
    assert pos is not None
    assert pos.status == "open"
    assert pos.entry_price == Decimal("0.001")
    # strong_buy = 1.5x base (0.5 * 1.5 = 0.75)
    assert pos.amount_sol_invested == Decimal("0.75")


@pytest.mark.asyncio
async def test_skips_watch_signal(db_session, token, trader):
    """watch signals should not open positions."""
    sig = Signal(
        token_id=token.id, token_address=token.address,
        score=40, status="watch",
    )
    db_session.add(sig)
    await db_session.flush()
    pos = await trader.on_signal(db_session, sig, Decimal("0.001"))
    assert pos is None


@pytest.mark.asyncio
async def test_no_duplicate_position(db_session, token, signal, trader):
    """Should not open two positions for same token."""
    pos1 = await trader.on_signal(db_session, signal, Decimal("0.001"))
    assert pos1 is not None
    await db_session.flush()

    sig2 = Signal(
        token_id=token.id, token_address=token.address,
        score=75, status="buy",
    )
    db_session.add(sig2)
    await db_session.flush()
    pos2 = await trader.on_signal(db_session, sig2, Decimal("0.002"))
    assert pos2 is None


@pytest.mark.asyncio
async def test_max_positions_enforced(db_session, trader):
    """Should not exceed max_positions."""
    for i in range(4):
        t = Token(address=f"MAXtest{i}token111111111111111111111111111", chain="sol")
        db_session.add(t)
        await db_session.flush()
        sig = Signal(
            token_id=t.id, token_address=t.address,
            score=70, status="strong_buy",
        )
        db_session.add(sig)
        await db_session.flush()
        pos = await trader.on_signal(db_session, sig, Decimal("0.001"))
        if i < 3:
            assert pos is not None
            await db_session.flush()
        else:
            assert pos is None  # max_positions=3


@pytest.mark.asyncio
async def test_take_profit_closes_position(db_session, token, signal, trader):
    """Position should close when price hits 2x."""
    pos = await trader.on_signal(db_session, signal, Decimal("0.001"))
    await db_session.flush()

    # Price goes to 2.5x entry
    await trader.update_positions(db_session, token.id, Decimal("0.0025"))
    await db_session.flush()

    result = await db_session.execute(
        select(Position).where(Position.token_id == token.id)
    )
    pos_updated = result.scalar_one()
    assert pos_updated.status == "closed"
    assert pos_updated.close_reason == "take_profit"


@pytest.mark.asyncio
async def test_stop_loss_closes_position(db_session, token, signal, trader):
    """Position should close when P&L drops below -50%."""
    pos = await trader.on_signal(db_session, signal, Decimal("0.001"))
    await db_session.flush()

    await trader.update_positions(db_session, token.id, Decimal("0.0004"))
    await db_session.flush()

    result = await db_session.execute(
        select(Position).where(Position.token_id == token.id)
    )
    pos_updated = result.scalar_one()
    assert pos_updated.status == "closed"
    assert pos_updated.close_reason == "stop_loss"


@pytest.mark.asyncio
async def test_rug_closes_position(db_session, token, signal, trader):
    """Rug detection should close position immediately."""
    pos = await trader.on_signal(db_session, signal, Decimal("0.001"))
    await db_session.flush()

    await trader.update_positions(db_session, token.id, Decimal("0.0008"), is_rug=True)
    await db_session.flush()

    result = await db_session.execute(
        select(Position).where(Position.token_id == token.id)
    )
    pos_updated = result.scalar_one()
    assert pos_updated.status == "closed"
    assert pos_updated.close_reason == "rug"


@pytest.mark.asyncio
async def test_timeout_closes_position(db_session, token, signal, trader):
    """Position should close after timeout_hours."""
    pos = await trader.on_signal(db_session, signal, Decimal("0.001"))
    await db_session.flush()

    # Manually set opened_at to 5 hours ago
    result = await db_session.execute(
        select(Position).where(Position.token_id == token.id)
    )
    pos_db = result.scalar_one()
    pos_db.opened_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=5)
    await db_session.flush()

    await trader.update_positions(db_session, token.id, Decimal("0.0011"))
    await db_session.flush()

    result = await db_session.execute(
        select(Position).where(Position.token_id == token.id)
    )
    pos_updated = result.scalar_one()
    assert pos_updated.status == "closed"
    assert pos_updated.close_reason == "timeout"


@pytest.mark.asyncio
async def test_portfolio_summary(db_session, token, signal, trader):
    """Portfolio summary should reflect open/closed positions."""
    pos = await trader.on_signal(db_session, signal, Decimal("0.001"))
    await db_session.flush()

    summary = await trader.get_portfolio_summary(db_session)
    assert summary["open_count"] == 1
    assert summary["closed_count"] == 0
    assert summary["total_invested_sol"] == 0.75  # strong_buy = 1.5x base
