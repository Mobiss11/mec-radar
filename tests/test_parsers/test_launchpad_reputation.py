"""Tests for dynamic launchpad reputation scoring."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.models.token import Token, TokenOutcome
from src.parsers.launchpad_reputation import (
    _cache,
    compute_launchpad_reputation,
    get_launchpad_score_impact,
)


@pytest.fixture(autouse=True)
def _clear_launchpad_cache():
    """Clear in-memory cache between tests."""
    _cache.clear()
    yield
    _cache.clear()


@pytest.mark.asyncio
async def test_good_launchpad_reputation(db_session):
    """Launchpad with low rug rate and high multiplier → high reputation."""
    tokens = []
    for i in range(10):
        token = Token(
            address=f"good_lp_tok_{i:04d}aaaaaaaaaaaaaaaa",
            chain="sol",
            source="meteora_dbc",
            dbc_launchpad="trustpad",
            first_seen_at=datetime.now(UTC).replace(tzinfo=None),
        )
        db_session.add(token)
        tokens.append(token)
    await db_session.flush()

    # Add outcomes: 8 successes, 2 rugs
    for i, t in enumerate(tokens):
        outcome = TokenOutcome(
            token_id=t.id,
            peak_multiplier=Decimal("5.0") if i < 8 else Decimal("0.1"),
            is_rug=i >= 8,
        )
        db_session.add(outcome)
    await db_session.flush()

    rep = await compute_launchpad_reputation(db_session, "trustpad")
    assert rep.total_launches == 10
    assert rep.rug_rate <= 0.3
    assert rep.reputation_score >= 60

    impact = get_launchpad_score_impact(rep)
    assert impact > 0


@pytest.mark.asyncio
async def test_bad_launchpad_reputation(db_session):
    """Launchpad with high rug rate → low reputation."""
    tokens = []
    for i in range(10):
        token = Token(
            address=f"bad_lp_tok_{i:04d}aaaaaaaaaaaaaaaaaa",
            chain="sol",
            source="meteora_dbc",
            dbc_launchpad="scampad",
            first_seen_at=datetime.now(UTC).replace(tzinfo=None),
        )
        db_session.add(token)
        tokens.append(token)
    await db_session.flush()

    for i, t in enumerate(tokens):
        outcome = TokenOutcome(
            token_id=t.id,
            peak_multiplier=Decimal("0.05"),
            is_rug=True,  # all rugs
        )
        db_session.add(outcome)
    await db_session.flush()

    rep = await compute_launchpad_reputation(db_session, "scampad")
    assert rep.rug_rate >= 0.5
    assert rep.reputation_score < 40

    impact = get_launchpad_score_impact(rep)
    assert impact < 0


@pytest.mark.asyncio
async def test_unknown_launchpad(db_session):
    """Unknown launchpad with <5 launches → mild penalty."""
    token = Token(
        address="unknown_lp_tok_aaaaaaaaaaaaaaaa",
        chain="sol",
        source="meteora_dbc",
        dbc_launchpad="newpad",
        first_seen_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(token)
    await db_session.flush()

    rep = await compute_launchpad_reputation(db_session, "newpad")
    assert rep.total_launches < 5

    impact = get_launchpad_score_impact(rep)
    assert impact == -1
