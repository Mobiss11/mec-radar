from decimal import Decimal

from pydantic import BaseModel


class PumpPortalNewToken(BaseModel):
    """Event from subscribeNewToken."""

    signature: str | None = None
    mint: str
    name: str | None = None
    symbol: str | None = None
    uri: str | None = None
    traderPublicKey: str | None = None
    txType: str | None = None
    initialBuy: Decimal | None = None
    marketCapSol: Decimal | None = None
    bondingCurveKey: str | None = None
    vTokensInBondingCurve: Decimal | None = None
    vSolInBondingCurve: Decimal | None = None

    model_config = {"extra": "ignore"}


class PumpPortalTrade(BaseModel):
    """Event from subscribeAccountTrade / subscribeTokenTrade."""

    signature: str | None = None
    mint: str
    txType: str  # "buy" or "sell"
    traderPublicKey: str
    tokenAmount: Decimal | None = None
    solAmount: Decimal | None = None
    newTokenBalance: Decimal | None = None
    marketCapSol: Decimal | None = None
    bondingCurveKey: str | None = None

    model_config = {"extra": "ignore"}


class PumpPortalMigration(BaseModel):
    """Event from subscribeMigration."""

    mint: str
    signature: str | None = None

    model_config = {"extra": "allow"}
