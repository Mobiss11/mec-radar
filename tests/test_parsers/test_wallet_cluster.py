"""Tests for wallet clustering detection."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import Token, TokenTopHolder, TokenTrade, TokenSnapshot
from src.models.wallet import WalletCluster
from src.parsers.wallet_cluster import (
    check_clustered_holders,
    detect_coordinated_traders,
    detect_holder_overlap,
    save_clusters,
)


@pytest_asyncio.fixture
async def token(db_session: AsyncSession):
    t = Token(address="CLUSTERtesttoken111111111111111111111111111", chain="sol")
    db_session.add(t)
    await db_session.flush()
    return t


@pytest.mark.asyncio
async def test_detect_coordinated_traders(db_session: AsyncSession, token):
    """Wallets buying within 5min window should be grouped."""
    base_time = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=30)
    wallets = ["walletA", "walletB", "walletC"]
    for i, w in enumerate(wallets):
        trade = TokenTrade(
            token_id=token.id,
            source="pumpportal",
            side="buy",
            wallet_address=w,
            timestamp=base_time + timedelta(minutes=i),
            amount_usd=Decimal("100"),
        )
        db_session.add(trade)
    await db_session.flush()

    groups = await detect_coordinated_traders(db_session, token.id)
    assert len(groups) >= 1
    assert len(groups[0]) == 3


@pytest.mark.asyncio
async def test_detect_holder_overlap(db_session: AsyncSession):
    """Wallets appearing in 3+ token top holders should be detected."""
    tokens = []
    for i in range(3):
        t = Token(address=f"OVERLAPtoken{i}111111111111111111111111111", chain="sol")
        db_session.add(t)
        await db_session.flush()
        snap = TokenSnapshot(token_id=t.id, stage="INITIAL")
        db_session.add(snap)
        await db_session.flush()
        tokens.append((t, snap))

    # Two wallets appear in all 3 tokens
    for t, snap in tokens:
        for rank, addr in enumerate(["overlap_A", "overlap_B", "other_C"]):
            db_session.add(TokenTopHolder(
                snapshot_id=snap.id, token_id=t.id,
                rank=rank + 1, address=addr, percentage=Decimal("5"),
            ))
    await db_session.flush()

    pairs = await detect_holder_overlap(
        db_session, ["overlap_A", "overlap_B", "other_C"], min_overlap=3
    )
    # overlap_A and overlap_B should have overlap=3
    assert len(pairs) >= 1
    pair_addrs = {(p[0], p[1]) for p in pairs}
    assert ("overlap_A", "overlap_B") in pair_addrs or ("overlap_B", "overlap_A") in pair_addrs


@pytest.mark.asyncio
async def test_check_clustered_holders(db_session: AsyncSession):
    """Known clusters should be detectable."""
    cluster_id = await save_clusters(
        db_session, ["sybil_1", "sybil_2", "sybil_3"],
        method="coordinated_trade",
    )
    await db_session.flush()

    found = await check_clustered_holders(db_session, ["sybil_1", "unknown"])
    assert cluster_id in found

    found_empty = await check_clustered_holders(db_session, ["clean_wallet"])
    assert len(found_empty) == 0
