"""Tests for Phase 51: Micro-snipe entry at PRE_SCAN."""

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
    t = Token(address="MICROtesttoken111111111111111111111111111", chain="sol")
    db_session.add(t)
    await db_session.flush()
    return t


@pytest_asyncio.fixture
def trader():
    return PaperTrader(
        sol_per_trade=0.5,
        max_positions=10,
        take_profit_x=2.0,
        stop_loss_pct=-50.0,
        timeout_hours=4,
        micro_snipe_sol=0.07,
        micro_snipe_max_positions=3,
    )


# --- on_prescan_entry tests ---


@pytest.mark.asyncio
async def test_prescan_entry_creates_micro_position(db_session, token, trader):
    """on_prescan_entry should create a micro position with is_micro_entry=1."""
    pos = await trader.on_prescan_entry(
        db_session,
        token_id=token.id,
        token_address=token.address,
        symbol="TEST",
        price=Decimal("0.001"),
        liquidity_usd=5000.0,
        sol_price_usd=83.0,
    )
    assert pos is not None
    assert pos.is_micro_entry == 1
    assert pos.is_paper == 1
    assert pos.status == "open"
    assert pos.amount_sol_invested == Decimal("0.07")
    assert pos.signal_id is None  # no signal at prescan


@pytest.mark.asyncio
async def test_prescan_entry_no_duplicate(db_session, token, trader):
    """Should not create micro position if token already has open position."""
    pos1 = await trader.on_prescan_entry(
        db_session,
        token_id=token.id,
        token_address=token.address,
        symbol="TEST",
        price=Decimal("0.001"),
    )
    assert pos1 is not None
    await db_session.flush()

    pos2 = await trader.on_prescan_entry(
        db_session,
        token_id=token.id,
        token_address=token.address,
        symbol="TEST",
        price=Decimal("0.002"),
    )
    assert pos2 is None


@pytest.mark.asyncio
async def test_prescan_entry_max_micro_positions(db_session, trader):
    """Should not exceed micro_snipe_max_positions."""
    for i in range(4):
        t = Token(address=f"MICROmax{i}token11111111111111111111111111", chain="sol")
        db_session.add(t)
        await db_session.flush()
        pos = await trader.on_prescan_entry(
            db_session,
            token_id=t.id,
            token_address=t.address,
            symbol=f"TEST{i}",
            price=Decimal("0.001"),
        )
        if i < 3:  # micro_snipe_max_positions=3
            assert pos is not None, f"Position {i} should have been created"
            await db_session.flush()
        else:
            assert pos is None, "Should reject 4th micro position"


@pytest.mark.asyncio
async def test_prescan_entry_rejects_zero_price(db_session, token, trader):
    """Should reject zero or negative price."""
    pos = await trader.on_prescan_entry(
        db_session,
        token_id=token.id,
        token_address=token.address,
        symbol="TEST",
        price=Decimal("0"),
    )
    assert pos is None


@pytest.mark.asyncio
async def test_prescan_entry_creates_trade(db_session, token, trader):
    """on_prescan_entry should also create a buy Trade record."""
    pos = await trader.on_prescan_entry(
        db_session,
        token_id=token.id,
        token_address=token.address,
        symbol="TEST",
        price=Decimal("0.001"),
    )
    assert pos is not None
    await db_session.flush()

    result = await db_session.execute(
        select(Trade).where(Trade.token_id == token.id, Trade.side == "buy")
    )
    trade = result.scalar_one()
    assert trade.amount_sol == Decimal("0.07")
    assert trade.signal_id is None
    assert trade.is_paper == 1


# --- _topup_micro_position tests (via on_signal) ---


@pytest.mark.asyncio
async def test_topup_micro_on_signal(db_session, token, trader):
    """When signal fires on a micro position, it should top up to full size."""
    # Create micro position
    pos = await trader.on_prescan_entry(
        db_session,
        token_id=token.id,
        token_address=token.address,
        symbol="TEST",
        price=Decimal("0.001"),
        liquidity_usd=10000.0,
        sol_price_usd=83.0,
    )
    assert pos is not None
    await db_session.flush()

    # Now signal fires
    sig = Signal(
        token_id=token.id,
        token_address=token.address,
        score=60,
        status="buy",
    )
    db_session.add(sig)
    await db_session.flush()

    topped = await trader.on_signal(
        db_session, sig, Decimal("0.002"),
        liquidity_usd=10000.0, sol_price_usd=83.0,
    )
    assert topped is not None
    assert topped.is_micro_entry == 0  # no longer micro
    assert topped.signal_id == sig.id
    # Full size for buy = 0.5 SOL (1.0x)
    assert topped.amount_sol_invested == Decimal("0.5")


@pytest.mark.asyncio
async def test_topup_micro_strong_buy(db_session, token, trader):
    """strong_buy should top up micro to 1.5x base size."""
    pos = await trader.on_prescan_entry(
        db_session,
        token_id=token.id,
        token_address=token.address,
        symbol="TEST",
        price=Decimal("0.001"),
    )
    assert pos is not None
    await db_session.flush()

    sig = Signal(
        token_id=token.id,
        token_address=token.address,
        score=80,
        status="strong_buy",
    )
    db_session.add(sig)
    await db_session.flush()

    topped = await trader.on_signal(db_session, sig, Decimal("0.002"))
    assert topped is not None
    assert topped.is_micro_entry == 0
    # Full size for strong_buy = 0.5 * 1.5 = 0.75 SOL
    assert topped.amount_sol_invested == Decimal("0.75")


@pytest.mark.asyncio
async def test_topup_creates_additional_trade(db_session, token, trader):
    """Top-up should create a second buy Trade with the additional SOL amount."""
    pos = await trader.on_prescan_entry(
        db_session,
        token_id=token.id,
        token_address=token.address,
        symbol="TEST",
        price=Decimal("0.001"),
    )
    await db_session.flush()

    sig = Signal(
        token_id=token.id,
        token_address=token.address,
        score=60,
        status="buy",
    )
    db_session.add(sig)
    await db_session.flush()

    await trader.on_signal(db_session, sig, Decimal("0.002"))
    await db_session.flush()

    result = await db_session.execute(
        select(Trade).where(Trade.token_id == token.id, Trade.side == "buy")
    )
    trades = list(result.scalars().all())
    assert len(trades) == 2  # micro entry + top-up
    # First trade = 0.07 SOL (micro), second = 0.43 SOL (top-up to 0.5)
    amounts = sorted(float(t.amount_sol) for t in trades)
    assert abs(amounts[0] - 0.07) < 0.001
    assert abs(amounts[1] - 0.43) < 0.001


@pytest.mark.asyncio
async def test_topup_weighted_average_entry(db_session, token, trader):
    """Top-up should calculate weighted average entry price."""
    # Micro entry at price 0.001
    pos = await trader.on_prescan_entry(
        db_session,
        token_id=token.id,
        token_address=token.address,
        symbol="TEST",
        price=Decimal("0.001"),
    )
    await db_session.flush()

    # Top-up at price 0.002 (price doubled)
    sig = Signal(
        token_id=token.id,
        token_address=token.address,
        score=60,
        status="buy",
    )
    db_session.add(sig)
    await db_session.flush()

    topped = await trader.on_signal(db_session, sig, Decimal("0.002"))
    assert topped is not None
    # Weighted avg: (0.07 * 0.001 + 0.43 * 0.002) / 0.5 = (0.00007 + 0.00086) / 0.5 = 0.00186
    expected_avg = (Decimal("0.07") * Decimal("0.001") + Decimal("0.43") * Decimal("0.002")) / Decimal("0.5")
    assert abs(float(topped.entry_price) - float(expected_avg)) < 0.0001


@pytest.mark.asyncio
async def test_normal_signal_skips_non_micro_duplicate(db_session, token, trader):
    """Normal (non-micro) position should still block duplicate signals."""
    # Create normal position via signal
    sig1 = Signal(
        token_id=token.id,
        token_address=token.address,
        score=70,
        status="strong_buy",
    )
    db_session.add(sig1)
    await db_session.flush()

    pos1 = await trader.on_signal(db_session, sig1, Decimal("0.001"))
    assert pos1 is not None
    assert pos1.is_micro_entry == 0
    await db_session.flush()

    # Second signal should be blocked (not a micro position)
    sig2 = Signal(
        token_id=token.id,
        token_address=token.address,
        score=65,
        status="buy",
    )
    db_session.add(sig2)
    await db_session.flush()

    pos2 = await trader.on_signal(db_session, sig2, Decimal("0.002"))
    assert pos2 is None


# --- Enrichment queue serialization tests ---


def test_enrichment_task_prescan_birdeye_overview_default():
    """prescan_birdeye_overview should default to None."""
    from src.parsers.enrichment_types import EnrichmentTask, EnrichmentPriority

    task = EnrichmentTask(
        priority=EnrichmentPriority.NORMAL,
        scheduled_at=0.0,
        address="test",
    )
    assert task.prescan_birdeye_overview is None


def test_enrichment_queue_roundtrip_birdeye_overview():
    """BirdeyeTokenOverview should survive serialization roundtrip through queue."""
    from src.parsers.enrichment_types import EnrichmentTask, EnrichmentPriority, EnrichmentStage
    from src.parsers.enrichment_queue import _task_to_dict, _dict_to_task
    from src.parsers.birdeye.models import BirdeyeTokenOverview

    overview = BirdeyeTokenOverview(
        address="test_addr",
        price=Decimal("0.001"),
        marketCap=Decimal("5000"),
        liquidity=Decimal("3000"),
        buy5m=42,
        sell5m=10,
        uniqueWallet5m=25,
    )

    task = EnrichmentTask(
        priority=EnrichmentPriority.NORMAL,
        scheduled_at=100.0,
        address="test_addr",
        stage=EnrichmentStage.PRE_SCAN,
        prescan_birdeye_overview=overview,
    )

    d = _task_to_dict(task)
    assert d["prescan_birdeye_overview"] is not None
    assert d["prescan_birdeye_overview"]["buy5m"] == 42

    restored = _dict_to_task(d)
    assert restored.prescan_birdeye_overview is not None
    assert isinstance(restored.prescan_birdeye_overview, BirdeyeTokenOverview)
    assert restored.prescan_birdeye_overview.buy5m == 42
    assert restored.prescan_birdeye_overview.sell5m == 10
    assert float(restored.prescan_birdeye_overview.price) == pytest.approx(0.001)
    assert float(restored.prescan_birdeye_overview.marketCap) == pytest.approx(5000.0)


def test_enrichment_queue_roundtrip_without_overview():
    """Task without prescan_birdeye_overview should deserialize cleanly."""
    from src.parsers.enrichment_types import EnrichmentTask, EnrichmentPriority
    from src.parsers.enrichment_queue import _task_to_dict, _dict_to_task

    task = EnrichmentTask(
        priority=EnrichmentPriority.NORMAL,
        scheduled_at=100.0,
        address="test_addr",
    )

    d = _task_to_dict(task)
    assert "prescan_birdeye_overview" not in d

    restored = _dict_to_task(d)
    assert restored.prescan_birdeye_overview is None
