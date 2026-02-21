"""Tests for cross-token whale correlation detection."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import Token, TokenSnapshot, TokenTopHolder
from src.parsers.cross_token_whales import detect_cross_token_coordination


@pytest_asyncio.fixture
async def recent_tokens(db_session: AsyncSession):
    """Create multiple recent tokens with snapshots for holder attachment."""
    now = datetime.now(UTC).replace(tzinfo=None)

    token_a = Token(
        address="CROSStokenA11111111111111111111111111111111",
        chain="sol",
        first_seen_at=now - timedelta(minutes=30),
    )
    token_b = Token(
        address="CROSStokenB11111111111111111111111111111111",
        chain="sol",
        first_seen_at=now - timedelta(minutes=20),
    )
    token_c = Token(
        address="CROSStokenC11111111111111111111111111111111",
        chain="sol",
        first_seen_at=now - timedelta(minutes=10),
    )
    db_session.add_all([token_a, token_b, token_c])
    await db_session.flush()

    # Create snapshots for each token (required by TokenTopHolder FK)
    snap_a = TokenSnapshot(token_id=token_a.id, stage="TEST")
    snap_b = TokenSnapshot(token_id=token_b.id, stage="TEST")
    snap_c = TokenSnapshot(token_id=token_c.id, stage="TEST")
    db_session.add_all([snap_a, snap_b, snap_c])
    await db_session.flush()

    return token_a, token_b, token_c, snap_a, snap_b, snap_c


@pytest.mark.asyncio
async def test_no_holders_returns_none(db_session: AsyncSession, recent_tokens):
    """No holders → no coordination detected."""
    token_a, _, _, _, _, _ = recent_tokens
    result = await detect_cross_token_coordination(db_session, token_a.id)
    assert result is None


@pytest.mark.asyncio
async def test_no_overlap_returns_none(db_session: AsyncSession, recent_tokens):
    """Unique holders per token → no coordination."""
    token_a, token_b, _, snap_a, snap_b, _ = recent_tokens

    # Token A holders
    for i in range(5):
        db_session.add(TokenTopHolder(
            snapshot_id=snap_a.id,
            token_id=token_a.id,
            address=f"walletA{i}111111111111111111111111111111111111",
            rank=i + 1,
            percentage=Decimal("2.0"),
        ))
    # Token B holders — all different
    for i in range(5):
        db_session.add(TokenTopHolder(
            snapshot_id=snap_b.id,
            token_id=token_b.id,
            address=f"walletB{i}111111111111111111111111111111111111",
            rank=i + 1,
            percentage=Decimal("2.0"),
        ))
    await db_session.flush()

    result = await detect_cross_token_coordination(db_session, token_a.id)
    assert result is None


@pytest.mark.asyncio
async def test_coordinated_pump_detected(db_session: AsyncSession, recent_tokens):
    """3+ shared wallets across 2+ tokens → coordination flagged."""
    token_a, token_b, token_c, snap_a, snap_b, snap_c = recent_tokens

    # Shared wallets that appear in all three tokens
    shared_wallets = [
        f"shared{i}111111111111111111111111111111111111111"
        for i in range(4)
    ]

    for rank, addr in enumerate(shared_wallets):
        db_session.add(TokenTopHolder(
            snapshot_id=snap_a.id,
            token_id=token_a.id,
            address=addr,
            rank=rank + 1,
            percentage=Decimal("3.0"),
        ))
        db_session.add(TokenTopHolder(
            snapshot_id=snap_b.id,
            token_id=token_b.id,
            address=addr,
            rank=rank + 1,
            percentage=Decimal("3.0"),
        ))
        db_session.add(TokenTopHolder(
            snapshot_id=snap_c.id,
            token_id=token_c.id,
            address=addr,
            rank=rank + 1,
            percentage=Decimal("3.0"),
        ))

    await db_session.flush()

    result = await detect_cross_token_coordination(db_session, token_a.id)
    assert result is not None
    assert result.wallet_count >= 3
    assert result.token_count >= 2
    assert result.score_impact < 0  # negative impact
