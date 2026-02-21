"""Pydantic models for Birdeye Data Services API responses."""

from decimal import Decimal

from pydantic import BaseModel


class BirdeyeTokenOverview(BaseModel):
    """Response from /defi/token_overview.

    Primary data source — price, mcap, liquidity, volume, holders, trade counts.
    30 CU per call.
    """

    address: str = ""
    name: str | None = None
    symbol: str | None = None
    decimals: int | None = None

    price: Decimal | None = None
    marketCap: Decimal | None = None
    fdv: Decimal | None = None
    liquidity: Decimal | None = None
    holder: int | None = None

    # Volume in USD by time window
    v5mUSD: Decimal | None = None
    v1hUSD: Decimal | None = None
    v24hUSD: Decimal | None = None

    # Trade counts by time window
    trade5m: int | None = None
    buy5m: int | None = None
    sell5m: int | None = None
    trade1h: int | None = None
    buy1h: int | None = None
    sell1h: int | None = None
    trade24h: int | None = None
    buy24h: int | None = None
    sell24h: int | None = None

    # Buy/sell volume in USD
    vBuy5mUSD: Decimal | None = None
    vSell5mUSD: Decimal | None = None
    vBuy1hUSD: Decimal | None = None
    vSell1hUSD: Decimal | None = None

    # Unique wallets
    uniqueWallet5m: int | None = None
    uniqueWallet1h: int | None = None
    uniqueWallet24h: int | None = None

    # Price changes
    priceChange5mPercent: Decimal | None = None
    priceChange1hPercent: Decimal | None = None
    priceChange24hPercent: Decimal | None = None

    model_config = {"extra": "ignore"}


class BirdeyePrice(BaseModel):
    """Response from /defi/price. 10 CU."""

    value: Decimal | None = None
    updateUnixTime: int | None = None
    liquidity: Decimal | None = None

    model_config = {"extra": "ignore"}


class BirdeyeOHLCVItem(BaseModel):
    """Single OHLCV candle from /defi/ohlcv."""

    o: Decimal  # open
    h: Decimal  # high
    l: Decimal  # low
    c: Decimal  # close
    v: Decimal  # volume
    unixTime: int  # unix timestamp
    type: str | None = None  # interval type

    model_config = {"extra": "ignore"}


class BirdeyeTradeItem(BaseModel):
    """Single trade from /defi/v3/token/trade-data/single."""

    txHash: str | None = None
    blockUnixTime: int | None = None
    side: str | None = None  # "buy" or "sell"
    price: Decimal | None = None
    from_: dict | None = None  # token sold (alias for 'from')
    to: dict | None = None  # token bought
    owner: str | None = None
    source: str | None = None

    model_config = {"extra": "ignore", "populate_by_name": True}


class BirdeyeTokenMetadata(BaseModel):
    """Response from /defi/v3/token/meta-data/single. 5 CU."""

    address: str = ""
    name: str | None = None
    symbol: str | None = None
    decimals: int | None = None
    logoURI: str | None = None
    description: str | None = None
    website: str | None = None
    twitter: str | None = None
    telegram: str | None = None
    discord: str | None = None
    coingeckoId: str | None = None

    model_config = {"extra": "ignore"}


class BirdeyeTokenSecurity(BaseModel):
    """Response from /defi/token_security. 50 CU."""

    ownerAddress: str | None = None
    ownerPercentage: Decimal | None = None
    creatorAddress: str | None = None
    creationTx: str | None = None
    creationTime: int | None = None
    top10HolderPercent: Decimal | None = None
    top10HolderBalance: Decimal | None = None
    top10UserPercent: Decimal | None = None
    isTrueToken: bool | None = None
    totalSupply: Decimal | None = None
    isToken2022: bool | None = None
    freezeAuthority: str | None = None
    mintAuthority: str | None = None
    transferFeeEnable: bool | None = None
    nonTransferable: bool | None = None
    lockInfo: dict | None = None
    mutableMetadata: bool | None = None
    jupStrictList: bool | None = None

    model_config = {"extra": "ignore"}

    @property
    def is_mintable(self) -> bool:
        return self.mintAuthority is not None

    @property
    def is_freezable(self) -> bool:
        return self.freezeAuthority is not None

    @property
    def has_transfer_fee(self) -> bool:
        return self.transferFeeEnable is True
