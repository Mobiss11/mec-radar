"""Tests for creator funding trace and risk assessment."""

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import CreatorProfile
from src.parsers.creator_trace import (
    DEFAULT_FIRST_LAUNCH_RISK,
    assess_creator_risk,
    check_funding_source_risk,
    update_creator_funding,
)


@pytest_asyncio.fixture
async def serial_rugger(db_session: AsyncSession):
    """Create a known serial rugger profile."""
    profile = CreatorProfile(
        address="RUGGERaddr1111111111111111111111111111111111",
        total_launches=10,
        rugged_count=8,
        risk_score=75,
    )
    db_session.add(profile)
    await db_session.flush()
    return profile


@pytest_asyncio.fixture
async def clean_creator(db_session: AsyncSession):
    """Create a clean creator with good history."""
    profile = CreatorProfile(
        address="CLEANcreator1111111111111111111111111111111",
        total_launches=5,
        rugged_count=0,
        risk_score=10,
    )
    db_session.add(profile)
    await db_session.flush()
    return profile


@pytest.mark.asyncio
async def test_unknown_creator_gets_default_risk(db_session: AsyncSession):
    """Brand new creator → DEFAULT_FIRST_LAUNCH_RISK, not 0."""
    risk, is_first = await assess_creator_risk(
        db_session, "UNKNOWNcreator111111111111111111111111111"
    )
    assert risk == DEFAULT_FIRST_LAUNCH_RISK
    assert is_first is True


@pytest.mark.asyncio
async def test_default_risk_is_25():
    """Verify the constant is 25."""
    assert DEFAULT_FIRST_LAUNCH_RISK == 25


@pytest.mark.asyncio
async def test_serial_rugger_high_risk(db_session: AsyncSession, serial_rugger):
    """Known serial rugger should return high risk."""
    risk, is_first = await assess_creator_risk(db_session, serial_rugger.address)
    assert risk >= 60
    assert is_first is False


@pytest.mark.asyncio
async def test_clean_creator_low_risk(db_session: AsyncSession, clean_creator):
    """Clean creator with good history → low risk."""
    risk, is_first = await assess_creator_risk(db_session, clean_creator.address)
    assert risk <= 20
    assert is_first is False


@pytest.mark.asyncio
async def test_funding_source_serial_rugger(db_session: AsyncSession, serial_rugger):
    """Checking if a funder is a known rugger."""
    risk = await check_funding_source_risk(db_session, serial_rugger.address)
    assert risk >= 60  # 80% rug rate → 80 risk


@pytest.mark.asyncio
async def test_funding_source_unknown(db_session: AsyncSession):
    """Unknown funder → 0 risk."""
    risk = await check_funding_source_risk(
        db_session, "UNKNOWNfunder111111111111111111111111111"
    )
    assert risk == 0


@pytest.mark.asyncio
async def test_update_creator_funding(db_session: AsyncSession, clean_creator):
    """Updating creator with funding info should boost risk if needed."""
    await update_creator_funding(
        db_session, clean_creator.address,
        funded_by="some_rugger_wallet",
        funding_risk=60,
    )
    await db_session.flush()

    # Re-assess — should have higher risk now
    risk, _ = await assess_creator_risk(db_session, clean_creator.address)
    assert risk >= 60  # funding risk should override low base risk
