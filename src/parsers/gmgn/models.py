from decimal import Decimal

from pydantic import BaseModel, Field


class GmgnTokenPrice(BaseModel):
    """Nested price data from mutil_window_token_info."""

    price: Decimal | None = None
    volume_5m: Decimal | None = None
    volume_1h: Decimal | None = None
    volume_24h: Decimal | None = None

    model_config = {"extra": "allow"}


class GmgnTokenInfo(BaseModel):
    """Response from /api/v1/token_info or /api/v1/mutil_window_token_info.

    The batch endpoint nests price/volume data in a `price` sub-object.
    The single endpoint returns a flat structure with liquidity + holder_count.
    """

    address: str = ""
    name: str | None = None
    symbol: str | None = None
    liquidity: Decimal | None = None
    holder_count: int | None = None
    created_timestamp: int | None = None

    # Flat fields (from /api/v1/token_info) — fallback
    price: Decimal | GmgnTokenPrice | None = None
    market_cap: Decimal | None = None
    volume_5m: Decimal | None = Field(None, alias="volume_5min")
    volume_1h: Decimal | None = None
    volume_24h: Decimal | None = None

    model_config = {"populate_by_name": True, "extra": "allow"}

    @property
    def effective_price(self) -> Decimal | None:
        if isinstance(self.price, GmgnTokenPrice):
            return self.price.price
        return self.price

    @property
    def effective_volume_5m(self) -> Decimal | None:
        if isinstance(self.price, GmgnTokenPrice):
            return self.price.volume_5m
        return self.volume_5m

    @property
    def effective_volume_1h(self) -> Decimal | None:
        if isinstance(self.price, GmgnTokenPrice):
            return self.price.volume_1h
        return self.volume_1h

    @property
    def effective_volume_24h(self) -> Decimal | None:
        if isinstance(self.price, GmgnTokenPrice):
            return self.price.volume_24h
        return self.volume_24h


class GmgnSecurityInfo(BaseModel):
    """Response from /tokens/security/{chain}/{address}.

    Security data is nested under 'goplus' key.
    """

    is_open_source: bool | None = None
    is_proxy: bool | None = None
    is_mintable: bool | None = None
    lp_burned: bool | None = None
    lp_locked: bool | None = None
    contract_renounced: bool | None = None
    top10_holder_rate: Decimal | None = None
    dev_token_burn_amount: Decimal | None = None
    is_honeypot: bool | None = None
    buy_tax: Decimal | None = None
    sell_tax: Decimal | None = None
    lp_holders: list[dict] | None = None  # GoPlus LP holder info with lock details

    model_config = {"extra": "allow"}

    @property
    def lp_lock_duration_days(self) -> int | None:
        """Extract max LP lock duration from lp_holders data."""
        if not self.lp_holders:
            return None
        max_days = 0
        for holder in self.lp_holders:
            if not isinstance(holder, dict):
                continue
            locked = holder.get("is_locked") or holder.get("locked")
            if not locked:
                continue
            # lock_detail may contain end_time (unix timestamp)
            lock_detail = holder.get("lock_detail")
            if lock_detail and isinstance(lock_detail, dict):
                end_time = lock_detail.get("end_time")
                if end_time:
                    try:
                        import time
                        remaining_sec = int(end_time) - int(time.time())
                        if remaining_sec > 0:
                            days = remaining_sec // 86400
                            max_days = max(max_days, days)
                    except (ValueError, TypeError):
                        pass
            # Some responses have 'locked_detail' instead
            locked_detail = holder.get("locked_detail")
            if locked_detail and isinstance(locked_detail, list):
                for detail in locked_detail:
                    if isinstance(detail, dict):
                        end_time = detail.get("end_time")
                        if end_time:
                            try:
                                import time
                                remaining_sec = int(end_time) - int(time.time())
                                if remaining_sec > 0:
                                    days = remaining_sec // 86400
                                    max_days = max(max_days, days)
                            except (ValueError, TypeError):
                                pass
        return max_days if max_days > 0 else None


class GmgnTopHolder(BaseModel):
    address: str = ""
    balance: Decimal | None = None
    percentage: Decimal | None = None
    pnl: Decimal | None = None

    model_config = {"extra": "allow"}


class GmgnSmartWallet(BaseModel):
    address: str = ""
    category: str | None = None
    win_rate: Decimal | None = None
    total_profit: Decimal | None = None
    total_trades: int | None = None
    avg_profit_pct: Decimal | None = None

    model_config = {"extra": "allow"}


class GmgnNewPair(BaseModel):
    """A pair from the new_pairs endpoint.

    The token address is in `base_address`, not `address`.
    `address` is the pool/pair address.
    `base_token_info` contains the token metadata.
    """

    address: str = ""
    base_address: str | None = None
    name: str | None = None
    symbol: str | None = None
    price: Decimal | None = None
    market_cap: Decimal | None = None
    liquidity: Decimal | None = Field(None, alias="initial_liquidity")
    holder_count: int | None = None
    created_timestamp: int | None = None
    base_token_info: dict | None = None

    model_config = {"populate_by_name": True, "extra": "allow"}

    @property
    def token_address(self) -> str:
        """Return the actual token address (base_address) or fall back to address."""
        return self.base_address or self.address

    @property
    def token_name(self) -> str | None:
        if self.base_token_info:
            return self.base_token_info.get("name", self.name)
        return self.name

    @property
    def token_symbol(self) -> str | None:
        if self.base_token_info:
            return self.base_token_info.get("symbol", self.symbol)
        return self.symbol


class GmgnPumpToken(BaseModel):
    """A token from the pump trending endpoint."""

    address: str = ""
    name: str | None = None
    symbol: str | None = None
    progress: Decimal | None = None
    market_cap: Decimal | None = None
    holder_count: int | None = None
    created_timestamp: int | None = None

    model_config = {"extra": "allow"}
