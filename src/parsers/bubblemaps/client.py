"""Bubblemaps Data API client [Beta] — holder clustering analysis.

Provides unique data: top-80 holders with cluster analysis,
inter-holder transfer relationships, and decentralization score.
"""

import asyncio

import httpx
from loguru import logger

from src.parsers.bubblemaps.models import (
    BubblemapsCluster,
    BubblemapsHolder,
    BubblemapsRelationship,
    BubblemapsReport,
)
from src.parsers.rate_limiter import RateLimiter

BASE_URL = "https://api.bubblemaps.io/maps"
MAX_RETRIES = 2
RETRY_DELAYS = [2.0, 5.0]  # Queries can take up to 15s


class BubblemapsClient:
    """Async HTTP client for Bubblemaps Data API [Beta]."""

    def __init__(self, api_key: str, max_rps: float = 1.0) -> None:
        self._api_key = api_key
        self._rate_limiter = RateLimiter(max_rps)
        self._client = httpx.AsyncClient(timeout=30.0)  # Long timeout: queries up to 15s

    async def close(self) -> None:
        await self._client.aclose()

    async def get_map_data(self, mint: str) -> BubblemapsReport | None:
        """Fetch holder map data for a Solana token.

        Returns clusters, decentralization score, top holders, and relationships.
        """
        url = f"{BASE_URL}/solana/{mint}"
        params = {
            "use_magic_nodes": "true",
            "return_clusters": "true",
            "return_decentralization_score": "true",
            "return_relationships": "true",
            "return_nodes": "true",
        }
        headers = {"X-ApiKey": self._api_key}

        for attempt in range(MAX_RETRIES + 1):
            try:
                await self._rate_limiter.acquire()
                resp = await self._client.get(url, params=params, headers=headers)

                if resp.status_code == 429:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[BUBBLEMAPS] Rate limited, waiting {delay}s")
                    await asyncio.sleep(delay)
                    continue

                if resp.status_code == 401:
                    logger.warning("[BUBBLEMAPS] Invalid API key")
                    return None

                if resp.status_code == 404:
                    logger.debug(f"[BUBBLEMAPS] No data for {mint[:12]}")
                    return None

                if resp.status_code != 200:
                    logger.debug(f"[BUBBLEMAPS] HTTP {resp.status_code} for {mint[:12]}")
                    return None

                data = resp.json()
                return _parse_report(data)

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    await asyncio.sleep(delay)
                else:
                    logger.warning(f"[BUBBLEMAPS] Failed for {mint[:12]}: {e}")
                    return None

        return None


def _parse_report(data: dict) -> BubblemapsReport:
    """Parse Bubblemaps Data API response."""
    # Parse clusters
    clusters = [
        BubblemapsCluster(
            share=c.get("share", 0),
            amount=c.get("amount", 0),
            holder_count=c.get("holder_count", 0),
            holders=c.get("holders", []),
        )
        for c in (data.get("clusters") or [])
    ]

    # Parse top holders from nodes
    nodes_data = data.get("nodes", {})
    top_holders_raw = nodes_data.get("top_holders", [])
    top_holders = [
        BubblemapsHolder(
            address=h.get("address", ""),
            share=h.get("holder_data", {}).get("share", 0),
            amount=h.get("holder_data", {}).get("amount", 0),
            rank=h.get("holder_data", {}).get("rank", 0),
            is_contract=h.get("address_details", {}).get("is_contract", False),
            is_cex=h.get("address_details", {}).get("is_cex", False),
            is_dex=h.get("address_details", {}).get("is_dex", False),
            label=h.get("address_details", {}).get("label", ""),
        )
        for h in top_holders_raw
    ]

    # Parse relationships
    relationships = [
        BubblemapsRelationship(
            from_address=r.get("from_address", ""),
            to_address=r.get("to_address", ""),
            total_value=r.get("data", {}).get("total_value", 0),
            total_transfers=r.get("data", {}).get("total_transfers", 0),
        )
        for r in (data.get("relationships") or [])
    ]

    score = data.get("decentralization_score")

    report = BubblemapsReport(
        decentralization_score=score,
        clusters=clusters,
        top_holders=top_holders,
        relationships=relationships,
    )

    if score is not None and score < 0.3:
        logger.info(
            f"[BUBBLEMAPS] Low decentralization: {score:.2f}, "
            f"largest_cluster={report.largest_cluster_share:.1%}"
        )

    return report
