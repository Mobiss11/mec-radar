"""Test data persistence layer with real PostgreSQL."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from src.models.signal import Signal
from src.models.token import Token, TokenOutcome, TokenSecurity, TokenSnapshot, TokenTopHolder
from src.models.wallet import SmartWallet
from src.parsers.dexscreener.models import (
    DexScreenerLiquidity,
    DexScreenerPair,
    DexScreenerVolume,
)
from src.parsers.gmgn.models import (
    GmgnSecurityInfo,
    GmgnSmartWallet,
    GmgnTokenInfo,
    GmgnTopHolder,
)
from src.parsers.persistence import (
    get_token_by_address,
    save_token_security,
    save_token_snapshot,
    save_top_holders,
    upsert_smart_wallet,
    upsert_token,
    upsert_token_from_pumpportal,
    upsert_token_outcome,
)
from src.parsers.pumpportal.models import PumpPortalNewToken


@pytest.mark.asyncio
async def test_upsert_token_creates_new(db_session):
    token = await upsert_token(
        db_session,
        address="test_addr_001",
        name="TestToken",
        symbol="TT",
        source="test",
    )
    assert token.id is not None
    assert token.address == "test_addr_001"
    assert token.symbol == "TT"


@pytest.mark.asyncio
async def test_upsert_token_updates_existing(db_session):
    await upsert_token(
        db_session,
        address="test_addr_002",
        name="OriginalName",
        symbol="ON",
        source="test",
    )
    updated = await upsert_token(
        db_session,
        address="test_addr_002",
        name="UpdatedName",
        symbol="UN",
        source="test",
    )
    assert updated.name == "UpdatedName"
    assert updated.symbol == "UN"


@pytest.mark.asyncio
async def test_get_token_by_address(db_session):
    await upsert_token(
        db_session,
        address="test_addr_003",
        name="FindMe",
        symbol="FM",
        source="test",
    )
    found = await get_token_by_address(db_session, "test_addr_003")
    assert found is not None
    assert found.name == "FindMe"

    not_found = await get_token_by_address(db_session, "nonexistent")
    assert not_found is None


@pytest.mark.asyncio
async def test_save_token_snapshot(db_session):
    token = await upsert_token(
        db_session,
        address="test_addr_004",
        source="test",
    )
    info = GmgnTokenInfo(
        address="test_addr_004",
        price="0.001",
        market_cap="50000",
        liquidity="25000",
        holder_count=100,
    )
    snapshot = await save_token_snapshot(db_session, token.id, info)
    assert snapshot.id is not None
    assert snapshot.token_id == token.id


@pytest.mark.asyncio
async def test_save_token_security(db_session):
    token = await upsert_token(
        db_session,
        address="test_addr_005",
        source="test",
    )
    security_data = GmgnSecurityInfo(
        is_honeypot=False,
        lp_burned=True,
        buy_tax="0",
        sell_tax="2",
    )
    sec = await save_token_security(db_session, token.id, security_data)
    assert sec.is_honeypot is False
    assert sec.lp_burned is True


@pytest.mark.asyncio
async def test_upsert_smart_wallet(db_session):
    wallet_data = GmgnSmartWallet(
        address="wallet_test_001",
        category="smart_degen",
        win_rate="0.62",
        total_trades=150,
    )
    wallet = await upsert_smart_wallet(db_session, wallet_data)
    assert wallet.id is not None
    assert wallet.category == "smart_degen"


@pytest.mark.asyncio
async def test_upsert_token_from_pumpportal_saves_launch_data(db_session):
    event = PumpPortalNewToken(
        mint="pp_launch_001",
        name="LaunchToken",
        symbol="LT",
        traderPublicKey="creator_wallet_abc",
        initialBuy=Decimal("0.5"),
        marketCapSol=Decimal("30.0"),
        bondingCurveKey="bc_key_xyz",
        vSolInBondingCurve=Decimal("85.0"),
        vTokensInBondingCurve=Decimal("1000000"),
    )
    token = await upsert_token_from_pumpportal(db_session, event)
    assert token.creator_address == "creator_wallet_abc"
    assert token.initial_buy_sol == Decimal("0.5")
    assert token.initial_mcap_sol == Decimal("30.0")
    assert token.bonding_curve_key == "bc_key_xyz"
    assert token.v_sol_in_bonding_curve == Decimal("85.0")


@pytest.mark.asyncio
async def test_save_token_snapshot_with_stage_and_dex(db_session):
    token = await upsert_token(
        db_session, address="snap_dex_001", source="test"
    )
    info = GmgnTokenInfo(
        address="snap_dex_001",
        price="0.001",
        market_cap="50000",
        liquidity="25000",
    )
    dex = DexScreenerPair(
        priceUsd="0.0011",
        volume=DexScreenerVolume(m5=Decimal("500"), h1=Decimal("3000")),
        liquidity=DexScreenerLiquidity(usd=Decimal("24000")),
        fdv=Decimal("100000"),
    )
    snapshot = await save_token_snapshot(
        db_session,
        token.id,
        info,
        stage="INITIAL",
        dex_data=dex,
    )
    assert snapshot.stage == "INITIAL"
    assert snapshot.dex_price == Decimal("0.0011")
    assert snapshot.dex_liquidity_usd == Decimal("24000")
    assert snapshot.dex_volume_5m == Decimal("500")
    assert snapshot.dex_fdv == Decimal("100000")


@pytest.mark.asyncio
async def test_save_token_snapshot_dex_only(db_session):
    """DexScreener-only stage (gmgn info=None)."""
    token = await upsert_token(
        db_session, address="snap_dexonly_001", source="test"
    )
    dex = DexScreenerPair(
        priceUsd="0.002",
        liquidity=DexScreenerLiquidity(usd=Decimal("10000")),
    )
    snapshot = await save_token_snapshot(
        db_session,
        token.id,
        None,
        stage="MIN_2",
        dex_data=dex,
    )
    assert snapshot.price is None  # no gmgn data
    assert snapshot.dex_price == Decimal("0.002")
    assert snapshot.dex_liquidity_usd == Decimal("10000")


@pytest.mark.asyncio
async def test_save_top_holders(db_session):
    token = await upsert_token(
        db_session, address="th_001", source="test"
    )
    info = GmgnTokenInfo(
        address="th_001", price="0.001", market_cap="50000", liquidity="25000"
    )
    snapshot = await save_token_snapshot(db_session, token.id, info, stage="INITIAL")
    holders = [
        GmgnTopHolder(
            address=f"holder_{i}",
            percentage=Decimal(str(10 - i)),
            balance=Decimal(str(1000 * (10 - i))),
        )
        for i in range(10)
    ]
    saved = await save_top_holders(db_session, snapshot.id, token.id, holders)
    assert len(saved) == 10
    assert saved[0].rank == 1
    assert saved[0].address == "holder_0"
    assert saved[9].rank == 10


@pytest.mark.asyncio
async def test_upsert_token_outcome_creates_and_updates(db_session):
    token = await upsert_token(
        db_session, address="outcome_001", source="test"
    )
    info = GmgnTokenInfo(
        address="outcome_001", price="0.001", market_cap="10000", liquidity="5000"
    )
    snap1 = await save_token_snapshot(db_session, token.id, info, stage="INITIAL")

    # Initial outcome
    outcome = await upsert_token_outcome(
        db_session, token.id, snap1, is_initial=True
    )
    assert outcome.initial_mcap == Decimal("10000")
    assert outcome.peak_mcap == Decimal("10000")

    # Higher mcap → peak updates
    info2 = GmgnTokenInfo(
        address="outcome_001", price="0.005", market_cap="50000", liquidity="20000"
    )
    snap2 = await save_token_snapshot(db_session, token.id, info2, stage="MIN_15")
    outcome = await upsert_token_outcome(db_session, token.id, snap2)
    assert outcome.peak_mcap == Decimal("50000")
    assert outcome.peak_multiplier == Decimal("5")

    # Final with rug detection (90%+ drawdown)
    info3 = GmgnTokenInfo(
        address="outcome_001", price="0.0001", market_cap="1000", liquidity="500"
    )
    snap3 = await save_token_snapshot(db_session, token.id, info3, stage="HOUR_24")
    outcome = await upsert_token_outcome(
        db_session, token.id, snap3, is_final=True
    )
    assert outcome.final_mcap == Decimal("1000")
    assert outcome.is_rug is True


@pytest.mark.asyncio
async def test_signal_outcome_updated_on_upsert_token_outcome(db_session):
    """Signal outcome columns should be populated when TokenOutcome is updated."""
    token = await upsert_token(
        db_session, address="sig_outcome_001", source="test"
    )

    # Create initial snapshot + signal
    info1 = GmgnTokenInfo(
        address="sig_outcome_001", price="0.001", market_cap="10000", liquidity="5000"
    )
    snap1 = await save_token_snapshot(db_session, token.id, info1, stage="INITIAL")

    signal = Signal(
        token_id=token.id,
        token_address="sig_outcome_001",
        score=60,
        token_price_at_signal=Decimal("0.001"),
        token_mcap_at_signal=Decimal("10000"),
        liquidity_at_signal=Decimal("5000"),
        status="strong_buy",
    )
    db_session.add(signal)
    await db_session.flush()

    # Initial outcome
    await upsert_token_outcome(db_session, token.id, snap1, is_initial=True)

    # Peak mcap = 50k → 5x multiplier
    info2 = GmgnTokenInfo(
        address="sig_outcome_001", price="0.005", market_cap="50000", liquidity="20000"
    )
    snap2 = await save_token_snapshot(db_session, token.id, info2, stage="MIN_15")
    await upsert_token_outcome(db_session, token.id, snap2)

    await db_session.refresh(signal)
    assert signal.peak_multiplier_after == Decimal("5")
    assert signal.peak_roi_pct == Decimal("400")  # (50k-10k)/10k * 100
    assert signal.outcome_updated_at is not None

    # Rug: 90%+ drawdown
    info3 = GmgnTokenInfo(
        address="sig_outcome_001", price="0.0001", market_cap="1000", liquidity="500"
    )
    snap3 = await save_token_snapshot(db_session, token.id, info3, stage="HOUR_24")
    await upsert_token_outcome(db_session, token.id, snap3, is_final=True)

    await db_session.refresh(signal)
    assert signal.is_rug_after is True
