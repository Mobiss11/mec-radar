"""Test persistence layer with Birdeye data integration."""

from decimal import Decimal

import pytest

from src.models.token import TokenSecurity
from src.parsers.birdeye.models import BirdeyeTokenOverview, BirdeyeTokenSecurity
from src.parsers.dexscreener.models import (
    DexScreenerLiquidity,
    DexScreenerPair,
    DexScreenerTxns,
    DexScreenerTxnsByPeriod,
    DexScreenerVolume,
)
from src.parsers.persistence import (
    save_token_security_from_birdeye,
    save_token_snapshot,
    upsert_token,
    upsert_token_outcome,
)


@pytest.mark.asyncio
async def test_snapshot_birdeye_primary(db_session):
    """Birdeye data should be primary when provided."""
    token = await upsert_token(db_session, address="be_snap_001", source="test")
    birdeye = BirdeyeTokenOverview(
        address="be_snap_001",
        price=Decimal("0.005"),
        marketCap=Decimal("100000"),
        liquidity=Decimal("50000"),
        v1hUSD=Decimal("30000"),
        holder=200,
        buy5m=50,
        sell5m=30,
        buy1h=200,
        sell1h=150,
    )
    snapshot = await save_token_snapshot(
        db_session, token.id, None, stage="INITIAL", birdeye_data=birdeye
    )
    assert snapshot.price == Decimal("0.005")
    assert snapshot.market_cap == Decimal("100000")
    assert snapshot.liquidity_usd == Decimal("50000")
    assert snapshot.volume_1h == Decimal("30000")
    assert snapshot.holders_count == 200
    assert snapshot.buys_5m == 50
    assert snapshot.sells_5m == 30
    assert snapshot.buys_1h == 200
    assert snapshot.sells_1h == 150


@pytest.mark.asyncio
async def test_snapshot_birdeye_over_dex(db_session):
    """Birdeye data takes priority over DexScreener."""
    token = await upsert_token(db_session, address="be_snap_002", source="test")
    birdeye = BirdeyeTokenOverview(
        price=Decimal("0.005"),
        marketCap=Decimal("100000"),
        liquidity=Decimal("50000"),
        buy1h=200,
        sell1h=150,
    )
    dex = DexScreenerPair(
        priceUsd="0.004",
        liquidity=DexScreenerLiquidity(usd=Decimal("40000")),
        txns=DexScreenerTxnsByPeriod(
            h1=DexScreenerTxns(buys=100, sells=80),
        ),
    )
    snapshot = await save_token_snapshot(
        db_session, token.id, None, stage="MIN_2", birdeye_data=birdeye, dex_data=dex
    )
    # Primary from Birdeye
    assert snapshot.price == Decimal("0.005")
    assert snapshot.liquidity_usd == Decimal("50000")
    assert snapshot.buys_1h == 200  # Birdeye, not DexScreener 100
    # DexScreener still saved for cross-validation
    assert snapshot.dex_price == Decimal("0.004")
    assert snapshot.dex_liquidity_usd == Decimal("40000")


@pytest.mark.asyncio
async def test_snapshot_dex_txns_fallback(db_session):
    """DexScreener txns used when no Birdeye trade counts."""
    token = await upsert_token(db_session, address="be_snap_003", source="test")
    dex = DexScreenerPair(
        priceUsd="0.003",
        liquidity=DexScreenerLiquidity(usd=Decimal("20000")),
        volume=DexScreenerVolume(h1=Decimal("5000")),
        txns=DexScreenerTxnsByPeriod(
            m5=DexScreenerTxns(buys=10, sells=5),
            h1=DexScreenerTxns(buys=80, sells=60),
            h24=DexScreenerTxns(buys=500, sells=400),
        ),
    )
    snapshot = await save_token_snapshot(
        db_session, token.id, None, stage="MIN_5", dex_data=dex
    )
    assert snapshot.buys_5m == 10
    assert snapshot.sells_5m == 5
    assert snapshot.buys_1h == 80
    assert snapshot.sells_1h == 60
    assert snapshot.buys_24h == 500
    assert snapshot.sells_24h == 400


@pytest.mark.asyncio
async def test_security_from_birdeye_mintable(db_session):
    """Birdeye security with mintAuthority → is_mintable=True."""
    token = await upsert_token(db_session, address="be_sec_001", source="test")
    sec_data = BirdeyeTokenSecurity(
        mintAuthority="SomeAuthority",
        freezeAuthority=None,
        top10HolderPercent=Decimal("25.0"),
        lockInfo={"lockTag": "known"},
        nonTransferable=False,
    )
    sec = await save_token_security_from_birdeye(db_session, token.id, sec_data)
    assert sec.is_mintable is True
    assert sec.lp_locked is True
    assert sec.top10_holders_pct == Decimal("25.0")
    assert sec.is_honeypot is False


@pytest.mark.asyncio
async def test_security_from_birdeye_not_mintable(db_session):
    """Birdeye security without mintAuthority → is_mintable=False."""
    token = await upsert_token(db_session, address="be_sec_002", source="test")
    sec_data = BirdeyeTokenSecurity(
        mintAuthority=None,
        freezeAuthority=None,
        lockInfo=None,
    )
    sec = await save_token_security_from_birdeye(db_session, token.id, sec_data)
    assert sec.is_mintable is False
    assert sec.lp_locked is False


@pytest.mark.asyncio
async def test_outcome_backfill_initial_mcap(db_session):
    """Outcome with NULL initial_mcap gets backfilled on later snapshot."""
    token = await upsert_token(db_session, address="be_out_001", source="test")

    # First snapshot — no market_cap (simulates old GMGN-only behavior)
    snap1 = await save_token_snapshot(
        db_session, token.id, None, stage="INITIAL"
    )
    outcome = await upsert_token_outcome(db_session, token.id, snap1, is_initial=True)
    assert outcome.initial_mcap is None

    # Second snapshot — Birdeye provides market_cap
    birdeye = BirdeyeTokenOverview(
        price=Decimal("0.01"),
        marketCap=Decimal("50000"),
        liquidity=Decimal("20000"),
    )
    snap2 = await save_token_snapshot(
        db_session, token.id, None, stage="MIN_2", birdeye_data=birdeye
    )
    outcome = await upsert_token_outcome(db_session, token.id, snap2)
    # initial_mcap should be backfilled
    assert outcome.initial_mcap == Decimal("50000")
    assert outcome.peak_mcap == Decimal("50000")
