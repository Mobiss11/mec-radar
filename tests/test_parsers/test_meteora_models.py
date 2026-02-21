"""Test Pydantic models for Meteora DBC parser."""

from decimal import Decimal

from src.parsers.meteora.models import (
    MeteoraDAMMPool,
    MeteoraMigration,
    MeteoraNewPool,
    MeteoraVirtualPool,
)


def test_virtual_pool_minimal():
    data = {
        "pool_address": "pool123",
        "creator": "creator_addr",
        "base_mint": "base_mint_addr",
        "quote_mint": "quote_mint_addr",
        "base_reserve": 1000000,
        "quote_reserve": 500000,
        "is_migrated": False,
    }
    pool = MeteoraVirtualPool.model_validate(data)
    assert pool.pool_address == "pool123"
    assert pool.creator == "creator_addr"
    assert pool.is_migrated is False
    assert pool.launchpad is None


def test_virtual_pool_full():
    data = {
        "pool_address": "pool_full",
        "creator": "creator_full",
        "base_mint": "base_full",
        "quote_mint": "quote_full",
        "base_reserve": 999999999,
        "quote_reserve": 888888888,
        "is_migrated": True,
        "launchpad": "believe",
    }
    pool = MeteoraVirtualPool.model_validate(data)
    assert pool.is_migrated is True
    assert pool.launchpad == "believe"


def test_virtual_pool_extra_fields_ignored():
    data = {
        "pool_address": "pool_extra",
        "creator": "creator_extra",
        "base_mint": "base_extra",
        "quote_mint": "quote_extra",
        "base_reserve": 100,
        "quote_reserve": 200,
        "is_migrated": False,
        "unknown_field": "should_be_ignored",
        "another_field": 42,
    }
    pool = MeteoraVirtualPool.model_validate(data)
    assert pool.pool_address == "pool_extra"


def test_new_pool_event():
    data = {
        "signature": "sig_new_pool_abc",
        "pool_address": "pool_new",
        "base_mint": "mint_new",
        "creator": "creator_new",
    }
    event = MeteoraNewPool.model_validate(data)
    assert event.signature == "sig_new_pool_abc"
    assert event.pool_address == "pool_new"
    assert event.base_mint == "mint_new"


def test_new_pool_event_minimal():
    data = {"signature": "sig_only"}
    event = MeteoraNewPool.model_validate(data)
    assert event.signature == "sig_only"
    assert event.pool_address is None
    assert event.base_mint is None
    assert event.creator is None


def test_migration_event():
    data = {
        "signature": "sig_migration_abc",
        "pool_address": "pool_migrated",
        "base_mint": "mint_migrated",
        "migration_type": "damm_v2",
    }
    event = MeteoraMigration.model_validate(data)
    assert event.migration_type == "damm_v2"
    assert event.pool_address == "pool_migrated"


def test_migration_event_damm():
    data = {
        "signature": "sig_damm",
        "migration_type": "damm",
    }
    event = MeteoraMigration.model_validate(data)
    assert event.migration_type == "damm"
    assert event.pool_address is None


def test_damm_pool():
    data = {
        "pool_address": "damm_pool_addr",
        "pool_tvl": "150000.50",
        "trading_volume": "2500000.75",
    }
    pool = MeteoraDAMMPool.model_validate(data)
    assert pool.pool_tvl == Decimal("150000.50")
    assert pool.trading_volume == Decimal("2500000.75")


def test_damm_pool_minimal():
    data = {"pool_address": "damm_minimal"}
    pool = MeteoraDAMMPool.model_validate(data)
    assert pool.pool_tvl is None
    assert pool.trading_volume is None
