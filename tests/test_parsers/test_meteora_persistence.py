"""Test Meteora DBC persistence functions with real PostgreSQL."""

from decimal import Decimal

import pytest

from src.parsers.meteora.models import MeteoraVirtualPool
from src.parsers.persistence import (
    get_token_by_address,
    upsert_token_from_meteora_dbc,
)


@pytest.mark.asyncio
async def test_upsert_from_dbc_creates_new_token(db_session):
    pool = MeteoraVirtualPool(
        pool_address="dbc_pool_001",
        creator="dbc_creator_001",
        base_mint="dbc_mint_001",
        quote_mint="dbc_quote_001",
        base_reserve=1_000_000_000,
        quote_reserve=500_000_000,
        is_migrated=False,
        launchpad="believe",
    )
    token = await upsert_token_from_meteora_dbc(db_session, pool)
    assert token.id is not None
    assert token.address == "dbc_mint_001"
    assert token.source == "meteora_dbc"
    assert token.creator_address == "dbc_creator_001"
    assert token.bonding_curve_key == "dbc_pool_001"
    assert token.dbc_pool_address == "dbc_pool_001"
    assert token.dbc_quote_reserve == Decimal("500000000")
    assert token.dbc_launchpad == "believe"
    assert token.dbc_is_migrated is False


@pytest.mark.asyncio
async def test_upsert_from_dbc_updates_existing(db_session):
    pool = MeteoraVirtualPool(
        pool_address="dbc_pool_002",
        creator="dbc_creator_002",
        base_mint="dbc_mint_002",
        quote_mint="dbc_quote_002",
        base_reserve=1_000_000_000,
        quote_reserve=500_000_000,
        is_migrated=False,
        launchpad="letsbonk",
    )
    token1 = await upsert_token_from_meteora_dbc(db_session, pool)
    assert token1.dbc_is_migrated is False

    db_session.expire_all()

    # Update with migration
    pool_updated = MeteoraVirtualPool(
        pool_address="dbc_pool_002",
        creator="dbc_creator_002",
        base_mint="dbc_mint_002",
        quote_mint="dbc_quote_002",
        base_reserve=0,
        quote_reserve=800_000_000,
        is_migrated=True,
        launchpad="letsbonk",
    )
    token2 = await upsert_token_from_meteora_dbc(db_session, pool_updated)
    assert token2.id == token1.id  # Same token
    assert token2.dbc_is_migrated is True
    assert token2.dbc_quote_reserve == Decimal("800000000")
    # Launchpad preserved via coalesce
    assert token2.dbc_launchpad == "letsbonk"


@pytest.mark.asyncio
async def test_upsert_from_dbc_preserves_creator(db_session):
    """Creator address should not be overwritten once set."""
    pool = MeteoraVirtualPool(
        pool_address="dbc_pool_003",
        creator="original_creator",
        base_mint="dbc_mint_003",
        quote_mint="dbc_quote_003",
        base_reserve=100,
        quote_reserve=200,
        is_migrated=False,
    )
    await upsert_token_from_meteora_dbc(db_session, pool)

    pool2 = MeteoraVirtualPool(
        pool_address="dbc_pool_003",
        creator="new_creator_attempt",
        base_mint="dbc_mint_003",
        quote_mint="dbc_quote_003",
        base_reserve=100,
        quote_reserve=200,
        is_migrated=False,
    )
    token = await upsert_token_from_meteora_dbc(db_session, pool2)
    assert token.creator_address == "original_creator"


@pytest.mark.asyncio
async def test_upsert_from_dbc_no_launchpad(db_session):
    """Token without a known launchpad."""
    pool = MeteoraVirtualPool(
        pool_address="dbc_pool_004",
        creator="dbc_creator_004",
        base_mint="dbc_mint_004",
        quote_mint="dbc_quote_004",
        base_reserve=100,
        quote_reserve=200,
        is_migrated=False,
        launchpad=None,
    )
    token = await upsert_token_from_meteora_dbc(db_session, pool)
    assert token.dbc_launchpad is None


@pytest.mark.asyncio
async def test_upsert_from_dbc_findable_by_address(db_session):
    pool = MeteoraVirtualPool(
        pool_address="dbc_pool_005",
        creator="dbc_creator_005",
        base_mint="dbc_mint_005",
        quote_mint="dbc_quote_005",
        base_reserve=100,
        quote_reserve=200,
        is_migrated=False,
    )
    await upsert_token_from_meteora_dbc(db_session, pool)
    found = await get_token_by_address(db_session, "dbc_mint_005")
    assert found is not None
    assert found.source == "meteora_dbc"
    assert found.dbc_pool_address == "dbc_pool_005"
