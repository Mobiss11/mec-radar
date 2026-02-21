"""Data models for Raydium API v3 responses."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class RaydiumPoolInfo:
    """Pool information from Raydium API."""

    pool_id: str = ""
    base_mint: str = ""
    quote_mint: str = ""
    lp_mint: str = ""
    lp_supply: Decimal = Decimal(0)
    tvl: Decimal = Decimal(0)
    burn_percent: float = 0.0  # 0-100, percentage of LP burned

    @property
    def lp_burned(self) -> bool:
        """LP is considered burned if >50% is burned."""
        return self.burn_percent > 50.0
