"""Data models for SolSniffer API responses."""

from dataclasses import dataclass, field


@dataclass
class SolSnifferHolder:
    """Top holder from SolSniffer analysis."""

    address: str = ""
    percentage: float = 0.0
    is_contract: bool = False


@dataclass
class SolSnifferReport:
    """Token security report from SolSniffer API.

    snifscore: 0 = most dangerous, 100 = safest.
    """

    snifscore: int = 0
    # Security features
    is_mintable: bool | None = None
    is_freezable: bool | None = None
    is_mutable_metadata: bool | None = None
    lp_burned: bool | None = None
    top10_pct: float | None = None
    # Liquidity
    liquidity_usd: float | None = None
    # Top holders
    top_holders: list[SolSnifferHolder] = field(default_factory=list)
    # Raw response for future use
    raw_data: dict = field(default_factory=dict)
