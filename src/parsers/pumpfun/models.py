"""Data models for Pump.fun frontend API responses."""

from dataclasses import dataclass, field


@dataclass
class PumpfunToken:
    """A token created by a wallet on Pump.fun."""

    mint: str = ""
    name: str = ""
    symbol: str = ""
    created_timestamp: int = 0
    market_cap: float = 0.0
    usd_market_cap: float = 0.0

    @property
    def is_dead(self) -> bool:
        """Token is dead if mcap < $100 or effectively zero."""
        return self.usd_market_cap < 100


@dataclass
class PumpfunCreatorHistory:
    """Creator history summary from Pump.fun."""

    total_tokens: int = 0
    dead_token_count: int = 0
    tokens: list[PumpfunToken] = field(default_factory=list)

    @property
    def is_serial_scammer(self) -> bool:
        return self.dead_token_count >= 10

    @property
    def risk_boost(self) -> int:
        """Risk score boost based on dead token count."""
        if self.dead_token_count >= 10:
            return 40
        if self.dead_token_count >= 5:
            return 20
        if self.dead_token_count >= 3:
            return 10
        return 0
