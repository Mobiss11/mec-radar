"""Pydantic models for Jupiter Price API v2 responses."""

from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel


class JupiterPriceExtraInfo(BaseModel):
    """Extra price metadata from Jupiter."""

    last_swapped_price: Decimal | None = None
    confidence_level: str = "medium"  # "high", "medium", "low"
    depth: dict | None = None  # buy/sell depth at various amounts


class JupiterPrice(BaseModel):
    """Price data for a single token from Jupiter."""

    id: str  # mint address
    mint_symbol: str = ""
    vs_token: str = ""
    vs_token_symbol: str = ""
    price: Decimal | None = None
    extra_info: JupiterPriceExtraInfo | None = None


@dataclass
class SellSimResult:
    """Result of Jupiter sell simulation (quote API)."""

    sellable: bool = False
    output_amount: Decimal | None = None  # SOL output
    price_impact_pct: float | None = None
    error: str | None = None
    api_error: bool = False  # True when API itself is unavailable (401, 5xx, timeout)
