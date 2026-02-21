"""Pydantic v2 models for Meteora DBC events and data."""

from decimal import Decimal

from pydantic import BaseModel


class MeteoraVirtualPool(BaseModel):
    """Decoded on-chain VirtualPool account data."""

    pool_address: str
    creator: str
    base_mint: str
    quote_mint: str
    base_reserve: int
    quote_reserve: int
    is_migrated: bool
    launchpad: str | None = None
    bonding_curve_progress_pct: Decimal | None = None

    model_config = {"extra": "ignore"}


class MeteoraNewPool(BaseModel):
    """Event: new DBC pool detected via logsSubscribe."""

    signature: str
    pool_address: str | None = None
    base_mint: str | None = None
    creator: str | None = None

    model_config = {"extra": "ignore"}


class MeteoraMigration(BaseModel):
    """Event: DBC pool migrated to DAMM/DAMM v2."""

    signature: str
    pool_address: str | None = None
    base_mint: str | None = None
    migration_type: str  # "damm" or "damm_v2"

    model_config = {"extra": "ignore"}


class MeteoraDAMMPool(BaseModel):
    """Post-graduation pool data from DAMM v2 REST API."""

    pool_address: str
    pool_tvl: Decimal | None = None
    trading_volume: Decimal | None = None

    model_config = {"extra": "ignore"}
