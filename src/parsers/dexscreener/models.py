from decimal import Decimal

from pydantic import BaseModel


class DexScreenerToken(BaseModel):
    address: str
    name: str | None = None
    symbol: str | None = None

    model_config = {"extra": "ignore"}


class DexScreenerVolume(BaseModel):
    m5: Decimal | None = None
    h1: Decimal | None = None
    h6: Decimal | None = None
    h24: Decimal | None = None

    model_config = {"extra": "ignore"}


class DexScreenerLiquidity(BaseModel):
    usd: Decimal | None = None
    base: Decimal | None = None
    quote: Decimal | None = None

    model_config = {"extra": "ignore"}


class DexScreenerTxns(BaseModel):
    buys: int | None = None
    sells: int | None = None

    model_config = {"extra": "ignore"}


class DexScreenerTxnsByPeriod(BaseModel):
    m5: DexScreenerTxns | None = None
    h1: DexScreenerTxns | None = None
    h6: DexScreenerTxns | None = None
    h24: DexScreenerTxns | None = None

    model_config = {"extra": "ignore"}


class DexScreenerPair(BaseModel):
    chainId: str = ""
    dexId: str = ""
    pairAddress: str = ""
    baseToken: DexScreenerToken | None = None
    quoteToken: DexScreenerToken | None = None
    priceUsd: str | None = None
    volume: DexScreenerVolume | None = None
    liquidity: DexScreenerLiquidity | None = None
    fdv: Decimal | None = None
    pairCreatedAt: int | None = None
    txns: DexScreenerTxnsByPeriod | None = None

    model_config = {"extra": "ignore"}
