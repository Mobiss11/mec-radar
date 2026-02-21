"""Pydantic models for Vybe Network API responses."""

from decimal import Decimal

from pydantic import BaseModel


class VybeHolder(BaseModel):
    """Token holder from GET /v4/tokens/{mint}/top-holders."""

    ownerAddress: str
    balance: str = "0"  # API returns string
    percentageOfSupplyHeld: float = 0.0
    rank: int = 0
    valueUsd: str = "0"  # API returns string

    model_config = {"extra": "ignore"}

    @property
    def balance_decimal(self) -> Decimal:
        return Decimal(self.balance) if self.balance else Decimal(0)

    @property
    def percentage(self) -> float:
        return self.percentageOfSupplyHeld


class VybeWalletPnL(BaseModel):
    """Wallet PnL summary from GET /v4/wallets/{addr}/pnl → summary."""

    ownerAddress: str = ""
    realizedPnlUsd: float | None = None
    unrealizedPnlUsd: float | None = None
    winRate: float = 0.0
    tradesCount: int = 0
    winningTradesCount: int = 0
    losingTradesCount: int = 0

    model_config = {"extra": "ignore"}

    @property
    def total_pnl_usd(self) -> float:
        r = self.realizedPnlUsd or 0.0
        u = self.unrealizedPnlUsd or 0.0
        return r + u


class VybeTokenHoldersPnL(BaseModel):
    """Aggregated holder PnL analysis result."""

    total_holders_checked: int = 0
    holders_in_profit: int = 0
    holders_in_loss: int = 0
    holders_in_profit_pct: float = 0.0
    avg_pnl_usd: float = 0.0
    top_holder_pct: float = 0.0
