"""Data models for Bubblemaps Data API responses."""

from dataclasses import dataclass, field


@dataclass
class BubblemapsHolder:
    """Top holder from Bubblemaps map data."""

    address: str = ""
    share: float = 0.0  # 0.0-1.0
    amount: float = 0.0
    rank: int = 0
    is_contract: bool = False
    is_cex: bool = False
    is_dex: bool = False
    label: str = ""


@dataclass
class BubblemapsCluster:
    """A cluster of connected holders."""

    share: float = 0.0  # 0.0-1.0
    amount: float = 0.0
    holder_count: int = 0
    holders: list[str] = field(default_factory=list)


@dataclass
class BubblemapsRelationship:
    """Transfer relationship between two holders."""

    from_address: str = ""
    to_address: str = ""
    total_value: float = 0.0
    total_transfers: int = 0


@dataclass
class BubblemapsReport:
    """Token holder analysis report from Bubblemaps Data API."""

    decentralization_score: float | None = None  # 0.0-1.0
    clusters: list[BubblemapsCluster] = field(default_factory=list)
    top_holders: list[BubblemapsHolder] = field(default_factory=list)
    relationships: list[BubblemapsRelationship] = field(default_factory=list)

    @property
    def largest_cluster_share(self) -> float:
        """Share of the largest cluster."""
        if not self.clusters:
            return 0.0
        return max(c.share for c in self.clusters)

    @property
    def risk_boost(self) -> int:
        """Risk boost based on decentralization and cluster analysis."""
        boost = 0
        if self.decentralization_score is not None and self.decentralization_score < 0.3:
            boost += 15
        if self.largest_cluster_share > 0.4:
            boost += 20
        # Large transfers between top holders
        suspicious_transfers = sum(
            1 for r in self.relationships
            if r.total_value > 10_000  # >$10K transfers
        )
        if suspicious_transfers >= 3:
            boost += 10
        return boost
