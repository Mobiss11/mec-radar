"""Tests for whale & holder dynamics detection."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from src.models.token import Token, TokenSnapshot, TokenTopHolder
from src.parsers.persistence import upsert_token, save_token_snapshot
from src.parsers.gmgn.models import GmgnTokenInfo, GmgnTopHolder
from src.parsers.whale_dynamics import (
    HolderChange,
    HolderDiff,
    WhalePattern,
    diff_holders,
    detect_patterns,
    analyse_whale_dynamics,
)


async def _create_token_with_snapshots(db_session, holders_old, holders_new):
    """Helper: create token, 2 snapshots, and holders for each."""
    token = await upsert_token(
        db_session, address="whale_test_001", source="test"
    )

    info1 = GmgnTokenInfo(
        address="whale_test_001", price="0.001", market_cap="10000", liquidity="5000"
    )
    snap1 = await save_token_snapshot(db_session, token.id, info1, stage="INITIAL")

    # Add holders for snap1
    for rank, (addr, pct) in enumerate(holders_old.items(), 1):
        h = TokenTopHolder(
            snapshot_id=snap1.id,
            token_id=token.id,
            rank=rank,
            address=addr,
            percentage=Decimal(str(pct)),
        )
        db_session.add(h)

    info2 = GmgnTokenInfo(
        address="whale_test_001", price="0.002", market_cap="20000", liquidity="8000"
    )
    snap2 = await save_token_snapshot(db_session, token.id, info2, stage="MIN_5")

    # Add holders for snap2
    for rank, (addr, pct) in enumerate(holders_new.items(), 1):
        h = TokenTopHolder(
            snapshot_id=snap2.id,
            token_id=token.id,
            rank=rank,
            address=addr,
            percentage=Decimal(str(pct)),
        )
        db_session.add(h)

    await db_session.flush()
    return token


@pytest.mark.asyncio
async def test_diff_holders_returns_none_with_single_snapshot(db_session):
    token = await upsert_token(
        db_session, address="whale_single_001", source="test"
    )
    info = GmgnTokenInfo(
        address="whale_single_001", price="0.001", market_cap="10000", liquidity="5000"
    )
    await save_token_snapshot(db_session, token.id, info, stage="INITIAL")

    result = await diff_holders(db_session, token.id)
    assert result is None


@pytest.mark.asyncio
async def test_diff_holders_detects_new_whale(db_session):
    token = await _create_token_with_snapshots(
        db_session,
        holders_old={"addr_a": "10", "addr_b": "5"},
        holders_new={"addr_a": "10", "addr_b": "5", "addr_c": "8"},
    )

    diff = await diff_holders(db_session, token.id)
    assert diff is not None
    assert len(diff.new_whales) == 1
    assert diff.new_whales[0].address == "addr_c"
    assert diff.new_whales[0].is_new is True


@pytest.mark.asyncio
async def test_diff_holders_detects_exited_whale(db_session):
    token = await _create_token_with_snapshots(
        db_session,
        holders_old={"addr_a": "10", "addr_b": "5"},
        holders_new={"addr_a": "12"},
    )

    diff = await diff_holders(db_session, token.id)
    assert diff is not None
    assert len(diff.exited_whales) == 1
    assert diff.exited_whales[0].address == "addr_b"


@pytest.mark.asyncio
async def test_diff_holders_concentration_delta(db_session):
    token = await _create_token_with_snapshots(
        db_session,
        holders_old={"addr_a": "10", "addr_b": "5"},  # total = 15%
        holders_new={"addr_a": "20", "addr_b": "10"},  # total = 30%
    )

    diff = await diff_holders(db_session, token.id)
    assert diff is not None
    assert diff.top10_pct_old == Decimal("15")
    assert diff.top10_pct_new == Decimal("30")
    assert diff.concentration_delta == Decimal("15")


def test_detect_patterns_accumulation():
    diff = HolderDiff(
        token_id=1,
        old_snapshot_id=1,
        new_snapshot_id=2,
        top10_pct_old=Decimal("20"),
        top10_pct_new=Decimal("28"),
    )
    diff.new_whales = [
        HolderChange(address="whale1", old_pct=None, new_pct=Decimal("5"), delta_pct=Decimal("5"), is_new=True),
        HolderChange(address="whale2", old_pct=None, new_pct=Decimal("3"), delta_pct=Decimal("3"), is_new=True),
    ]
    diff.changes = diff.new_whales

    patterns = detect_patterns(diff)
    accum = [p for p in patterns if p.pattern == "accumulation"]
    assert len(accum) == 1
    assert accum[0].severity == "high"  # total 8% >= 5%
    assert accum[0].score_impact > 0


def test_detect_patterns_distribution():
    diff = HolderDiff(
        token_id=1,
        old_snapshot_id=1,
        new_snapshot_id=2,
        top10_pct_old=Decimal("30"),
        top10_pct_new=Decimal("15"),
    )
    diff.changes = [
        HolderChange(address="seller1", old_pct=Decimal("15"), new_pct=Decimal("5"), delta_pct=Decimal("-10")),
        HolderChange(address="seller2", old_pct=Decimal("10"), new_pct=Decimal("5"), delta_pct=Decimal("-5")),
    ]

    patterns = detect_patterns(diff)
    dist = [p for p in patterns if p.pattern == "distribution"]
    assert len(dist) == 1
    assert dist[0].severity == "high"  # total sold 15% >= 10%
    assert dist[0].score_impact < 0

    # Concentration decreased by 15% → should also detect dilution
    dilution = [p for p in patterns if p.pattern == "dilution"]
    assert len(dilution) == 1


def test_detect_patterns_concentration():
    diff = HolderDiff(
        token_id=1,
        old_snapshot_id=1,
        new_snapshot_id=2,
        top10_pct_old=Decimal("20"),
        top10_pct_new=Decimal("35"),
    )
    diff.changes = [
        HolderChange(address="accum1", old_pct=Decimal("10"), new_pct=Decimal("25"), delta_pct=Decimal("15")),
    ]

    patterns = detect_patterns(diff)
    conc = [p for p in patterns if p.pattern == "concentration"]
    assert len(conc) == 1
    assert conc[0].severity == "high"  # delta 15% >= 10%
    assert conc[0].score_impact < 0


@pytest.mark.asyncio
async def test_analyse_whale_dynamics_full(db_session):
    """Full integration: create snapshots, run analysis."""
    token = await _create_token_with_snapshots(
        db_session,
        holders_old={"addr_a": "10", "addr_b": "5", "addr_c": "3"},
        holders_new={"addr_a": "15", "addr_d": "8"},  # a grew, b+c exited, d new
    )

    diff, patterns = await analyse_whale_dynamics(db_session, token.id)
    assert diff is not None
    assert len(diff.exited_whales) == 2  # b and c exited (both >= 1%)
    assert len(diff.new_whales) == 1  # d is new with 8%
    assert any(p.pattern == "accumulation" for p in patterns)
