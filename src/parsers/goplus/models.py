"""Data models for GoPlus Security API responses."""

from dataclasses import dataclass


@dataclass
class GoPlusReport:
    """Token security report from GoPlus API."""

    is_open_source: bool | None = None
    is_proxy: bool | None = None
    is_mintable: bool | None = None
    owner_can_change_balance: bool | None = None
    can_take_back_ownership: bool | None = None
    is_honeypot: bool | None = None
    buy_tax: float | None = None  # percentage (0-100)
    sell_tax: float | None = None  # percentage (0-100)
    holder_count: int | None = None
    lp_holder_count: int | None = None
    is_true_token: bool | None = None
    is_airdrop_scam: bool | None = None
    transfer_pausable: bool | None = None
    trading_cooldown: bool | None = None
    is_anti_whale: bool | None = None
    slippage_modifiable: bool | None = None
