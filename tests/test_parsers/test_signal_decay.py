"""Tests for signal decay TTL logic."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.signal import Signal
from src.models.token import Token
from src.parsers.signal_decay import decay_stale_signals


@pytest_asyncio.fixture
async def token_for_signals(db_session: AsyncSession):
    """Create a token for signal tests."""
    token = Token(address="DECAYtesttoken11111111111111111111111111111", chain="sol")
    db_session.add(token)
    await db_session.flush()
    return token


@pytest_asyncio.fixture
async def token2_for_signals(db_session: AsyncSession):
    """Create a second token for multi-token tests."""
    token = Token(address="DECAY2testtoken1111111111111111111111111111", chain="sol")
    db_session.add(token)
    await db_session.flush()
    return token


def _signal(token_id: int, status: str, hours_ago: float, address: str = "DECAYtesttoken11111111111111111111111111111") -> Signal:
    ts = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=hours_ago)
    return Signal(
        token_id=token_id,
        token_address=address,
        score=60,
        status=status,
        created_at=ts,
        updated_at=ts,
    )


@pytest.mark.asyncio
async def test_strong_buy_decays_to_buy(db_session: AsyncSession, token_for_signals):
    """strong_buy older than TTL should become buy."""
    token = token_for_signals
    db_session.add(_signal(token.id, "strong_buy", hours_ago=5))
    await db_session.flush()

    total = await decay_stale_signals(db_session, strong_buy_ttl_hours=4)
    assert total == 1


@pytest.mark.asyncio
async def test_strong_buy_fresh_not_decayed(db_session: AsyncSession, token2_for_signals):
    """strong_buy within TTL should NOT be decayed."""
    token = token2_for_signals
    db_session.add(_signal(token.id, "strong_buy", hours_ago=1, address=token.address))
    await db_session.flush()

    total = await decay_stale_signals(db_session, strong_buy_ttl_hours=4)
    assert total == 0


@pytest.mark.asyncio
async def test_buy_decays_to_watch(db_session: AsyncSession, token_for_signals):
    """buy older than TTL should become watch."""
    token = token_for_signals
    db_session.add(_signal(token.id, "buy", hours_ago=7))
    await db_session.flush()

    total = await decay_stale_signals(db_session, buy_ttl_hours=6)
    assert total == 1


@pytest.mark.asyncio
async def test_watch_decays_to_expired(db_session: AsyncSession, token_for_signals):
    """watch older than TTL should become expired."""
    token = token_for_signals
    db_session.add(_signal(token.id, "watch", hours_ago=13))
    await db_session.flush()

    total = await decay_stale_signals(db_session, watch_ttl_hours=12)
    assert total == 1


@pytest.mark.asyncio
async def test_no_decay_when_fresh(db_session: AsyncSession, token_for_signals, token2_for_signals):
    """Fresh signals should not be decayed."""
    t1, t2 = token_for_signals, token2_for_signals
    db_session.add(_signal(t1.id, "strong_buy", hours_ago=1))
    db_session.add(_signal(t1.id, "buy", hours_ago=2))
    db_session.add(_signal(t2.id, "watch", hours_ago=3, address=t2.address))
    await db_session.flush()

    total = await decay_stale_signals(db_session)
    assert total == 0


@pytest.mark.asyncio
async def test_expired_not_touched(db_session: AsyncSession, token_for_signals):
    """Already expired signals should not be affected."""
    token = token_for_signals
    db_session.add(_signal(token.id, "expired", hours_ago=100))
    await db_session.flush()

    total = await decay_stale_signals(db_session)
    assert total == 0


@pytest.mark.asyncio
async def test_cascade_decay_separate_tokens(db_session: AsyncSession, token_for_signals, token2_for_signals):
    """Multiple signals at different stages on different tokens decay correctly."""
    t1, t2 = token_for_signals, token2_for_signals
    db_session.add(_signal(t1.id, "strong_buy", hours_ago=5))
    db_session.add(_signal(t1.id, "watch", hours_ago=13))
    db_session.add(_signal(t2.id, "buy", hours_ago=7, address=t2.address))
    await db_session.flush()

    total = await decay_stale_signals(db_session)
    # strong_buy→buy(1) + buy→watch(1) + watch→expired(1) = 3
    assert total == 3


@pytest.mark.asyncio
async def test_strong_buy_decay_expires_existing_buy(db_session: AsyncSession, token_for_signals):
    """When strong_buy decays to buy, an existing buy for the same token is expired first.

    This prevents violation of uq_signals_token_status_active partial unique index.
    """
    token = token_for_signals
    token_id = token.id  # capture before expire_all
    # Old buy signal (7h ago) + stale strong_buy (5h ago)
    db_session.add(_signal(token_id, "buy", hours_ago=7))
    db_session.add(_signal(token_id, "strong_buy", hours_ago=5))
    await db_session.flush()

    total = await decay_stale_signals(db_session, strong_buy_ttl_hours=4, buy_ttl_hours=6)
    # Step 1: expire old buy, then strong_buy→buy = 1
    # Step 2: buy→watch: new buy is fresh (updated_at=now) → 0
    assert total == 1

    # Verify: one buy (from decayed strong_buy), one expired (the old buy)
    db_session.expire_all()
    rows = (await db_session.execute(
        select(Signal.status).where(Signal.token_id == token_id).order_by(Signal.status)
    )).scalars().all()
    assert sorted(rows) == ["buy", "expired"]


@pytest.mark.asyncio
async def test_buy_decay_expires_existing_watch(db_session: AsyncSession, token_for_signals):
    """When buy decays to watch, an existing watch for the same token is expired first."""
    token = token_for_signals
    token_id = token.id
    db_session.add(_signal(token_id, "watch", hours_ago=13))
    db_session.add(_signal(token_id, "buy", hours_ago=7))
    await db_session.flush()

    total = await decay_stale_signals(db_session, buy_ttl_hours=6, watch_ttl_hours=12)
    # Step 2 pre-clear: decaying_buy_ids = buy(7h) → expire existing watch? Only if buy(7h)
    #   is stale (>6h TTL). Yes, buy(7h) > 6h. So decaying_buy_ids has token_id.
    #   Pre-clear: expire watch(13h) for that token_id.
    # Step 2: buy→watch = 1
    # Step 3: watch→expired: old watch was already expired by pre-clear → 0
    # Total = 1
    assert total == 1

    db_session.expire_all()
    rows = (await db_session.execute(
        select(Signal.status).where(Signal.token_id == token_id).order_by(Signal.status)
    )).scalars().all()
    assert sorted(rows) == ["expired", "watch"]


@pytest.mark.asyncio
async def test_full_cascade_same_token(db_session: AsyncSession, token_for_signals):
    """All three statuses for one token: strong_buy(5h) + buy(7h) + watch(13h).

    Decay should handle all without unique violations.
    """
    token = token_for_signals
    token_id = token.id
    db_session.add(_signal(token_id, "strong_buy", hours_ago=5))
    db_session.add(_signal(token_id, "buy", hours_ago=7))
    db_session.add(_signal(token_id, "watch", hours_ago=13))
    await db_session.flush()

    total = await decay_stale_signals(db_session)
    # Step 1: pre-clear expires old buy(7h), then strong_buy→buy = 1
    # Step 2: decaying_buy_ids = buy signals with updated_at < 6h cutoff.
    #   The new buy (from step 1) has updated_at=now → NOT stale.
    #   Old buy was expired → not status='buy'. So decaying_buy_ids is EMPTY.
    #   No pre-clear of watch. buy→watch = 0.
    # Step 3: watch(13h) is stale (>12h) → watch→expired = 1
    # Total = 2
    assert total == 2

    db_session.expire_all()
    rows = (await db_session.execute(
        select(Signal.status).where(Signal.token_id == token_id).order_by(Signal.id)
    )).scalars().all()
    # strong_buy→buy, old buy→expired, old watch→expired
    assert sorted(rows) == ["buy", "expired", "expired"]
