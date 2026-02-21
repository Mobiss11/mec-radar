"""Tests for creator repeat launch detection."""

from datetime import UTC, datetime, timedelta

import pytest

from src.models.token import Token
from src.parsers.creator_repeat import check_creator_recent_launches


@pytest.mark.asyncio
async def test_serial_launcher_detected(db_session):
    """Creator with 3+ launches in 4h is serial launcher."""
    creator = "Creator111111111111111111111111111"
    now = datetime.now(UTC).replace(tzinfo=None)
    for i in range(4):
        token = Token(
            address=f"token{i}aaaaaaaaaaaaaaaaaaaaaaaaa",
            chain="sol",
            source="pumpportal",
            creator_address=creator,
            first_seen_at=now - timedelta(hours=i),
        )
        db_session.add(token)
    await db_session.flush()

    result = await check_creator_recent_launches(db_session, creator)
    assert result is not None
    assert result.is_serial_launcher
    assert result.recent_launches >= 3
    assert result.risk_boost >= 30


@pytest.mark.asyncio
async def test_normal_creator_not_flagged(db_session):
    """Creator with 1 launch is normal."""
    creator = "Creator222222222222222222222222222"
    token = Token(
        address="token_single_aaaaaaaaaaaaaaaaaa",
        chain="sol",
        source="pumpportal",
        creator_address=creator,
        first_seen_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(token)
    await db_session.flush()

    result = await check_creator_recent_launches(db_session, creator)
    assert result is not None
    assert not result.is_serial_launcher
    assert result.risk_boost == 0


@pytest.mark.asyncio
async def test_old_launches_excluded(db_session):
    """Launches older than 4h are excluded."""
    creator = "Creator333333333333333333333333333"
    now = datetime.now(UTC).replace(tzinfo=None)
    for i in range(5):
        token = Token(
            address=f"old_tok{i}aaaaaaaaaaaaaaaaaaaaa",
            chain="sol",
            source="pumpportal",
            creator_address=creator,
            first_seen_at=now - timedelta(hours=10 + i),  # all >4h ago
        )
        db_session.add(token)
    await db_session.flush()

    result = await check_creator_recent_launches(db_session, creator, hours=4)
    assert result is not None
    assert not result.is_serial_launcher
