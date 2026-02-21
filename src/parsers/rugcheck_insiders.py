"""RugCheck Insider Networks API — graph of connections between top holders.

Uses the free RugCheck API endpoint `/tokens/{id}/insiders/graph` to detect
insider networks — groups of wallets that are linked through common funding
sources, shared transactions, or other on-chain relationships.

Cost: $0 (free API, no key needed).
Latency: ~1s.
"""

from dataclasses import dataclass, field

import httpx
from loguru import logger


@dataclass
class InsiderNode:
    """A wallet in the insider network."""

    address: str
    label: str | None = None
    is_insider: bool = False
    balance_pct: float | None = None


@dataclass
class InsiderEdge:
    """A connection between two wallets."""

    source: str
    target: str
    relationship: str | None = None  # e.g. "funded_by", "same_fee_payer"


@dataclass
class InsiderNetworkResult:
    """Result of RugCheck insider network analysis."""

    insider_count: int = 0
    total_nodes: int = 0
    insider_pct: float = 0.0  # % of top holders that are insiders
    edges: list[InsiderEdge] = field(default_factory=list)
    insiders: list[InsiderNode] = field(default_factory=list)

    @property
    def score_impact(self) -> int:
        """Negative score impact based on insider concentration."""
        if self.insider_pct >= 50:
            return -15
        if self.insider_pct >= 30:
            return -10
        if self.insider_pct >= 15:
            return -5
        return 0

    @property
    def is_high_risk(self) -> bool:
        return self.insider_pct >= 30


_BASE_URL = "https://api.rugcheck.xyz/v1"
_TIMEOUT = 10.0


async def get_insider_network(
    token_address: str,
    *,
    max_rps: float = 1.0,
) -> InsiderNetworkResult | None:
    """Fetch insider network graph from RugCheck API.

    Args:
        token_address: Token mint address.
        max_rps: Rate limit (requests per second).

    Returns:
        InsiderNetworkResult or None on failure.
    """
    url = f"{_BASE_URL}/tokens/{token_address}/insiders/graph"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url)

            if resp.status_code == 404:
                # Token not indexed yet
                return None
            if resp.status_code == 429:
                logger.debug("[RUGCHECK_INSIDERS] Rate limited")
                return None
            resp.raise_for_status()

            data = resp.json()
            return _parse_insider_graph(data)

    except httpx.HTTPStatusError as e:
        logger.debug(f"[RUGCHECK_INSIDERS] HTTP error: {e.response.status_code}")
        return None
    except Exception as e:
        logger.debug(f"[RUGCHECK_INSIDERS] Error for {token_address[:12]}: {e}")
        return None


def _parse_insider_graph(data: dict) -> InsiderNetworkResult:
    """Parse RugCheck insider graph response."""
    result = InsiderNetworkResult()

    nodes = data.get("nodes", [])
    edges_raw = data.get("edges", data.get("links", []))

    result.total_nodes = len(nodes)

    for node in nodes:
        addr = node.get("id", node.get("address", ""))
        is_insider = node.get("isInsider", node.get("insider", False))
        label = node.get("label", node.get("type"))
        balance_pct = node.get("balancePct", node.get("percentage"))

        insider_node = InsiderNode(
            address=addr,
            label=label,
            is_insider=bool(is_insider),
            balance_pct=float(balance_pct) if balance_pct is not None else None,
        )

        if insider_node.is_insider:
            result.insiders.append(insider_node)

    result.insider_count = len(result.insiders)

    for edge in edges_raw:
        source = edge.get("source", edge.get("from", ""))
        target = edge.get("target", edge.get("to", ""))
        rel = edge.get("type", edge.get("relationship"))
        result.edges.append(InsiderEdge(source=source, target=target, relationship=rel))

    # Compute insider percentage
    if result.total_nodes > 0:
        result.insider_pct = (result.insider_count / result.total_nodes) * 100

    return result
