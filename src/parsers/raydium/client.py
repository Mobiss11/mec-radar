"""Raydium API v3 client — LP verification for Solana tokens."""

import asyncio
from decimal import Decimal

import httpx
from loguru import logger

from src.parsers.raydium.models import RaydiumPoolInfo
from src.parsers.rate_limiter import RateLimiter

BASE_URL = "https://api-v3.raydium.io"
# Well-known burn address
BURN_ADDRESS = "1111111111111111111111111111111111111111111"
MAX_RETRIES = 2
RETRY_DELAYS = [1.0, 3.0]


class RaydiumClient:
    """Async HTTP client for Raydium API v3 (free, no key)."""

    def __init__(self, max_rps: float = 5.0) -> None:
        self._rate_limiter = RateLimiter(max_rps)
        self._client = httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_pool_info(self, mint: str) -> RaydiumPoolInfo | None:
        """Fetch pool info for a token from Raydium."""
        url = f"{BASE_URL}/pools/info/mint"
        params = {
            "mint1": mint,
            "poolType": "standard",
            "poolSortField": "liquidity",
            "sortType": "desc",
            "pageSize": "1",
        }

        for attempt in range(MAX_RETRIES + 1):
            try:
                await self._rate_limiter.acquire()
                resp = await self._client.get(url, params=params)

                if resp.status_code == 429:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[RAYDIUM] Rate limited, waiting {delay}s")
                    await asyncio.sleep(delay)
                    continue

                if resp.status_code != 200:
                    logger.debug(f"[RAYDIUM] HTTP {resp.status_code} for {mint[:12]}")
                    return None

                data = resp.json()
                return _parse_pool(data)

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[RAYDIUM] {type(e).__name__}, retry in {delay}s")
                    await asyncio.sleep(delay)
                else:
                    logger.warning(f"[RAYDIUM] Failed for {mint[:12]}: {e}")
                    return None

        return None


def _parse_pool(data: dict) -> RaydiumPoolInfo | None:
    """Parse Raydium API pool response."""
    pools = data.get("data", {}).get("data", [])
    if not pools:
        return None

    pool = pools[0]  # Highest liquidity pool

    lp_mint = pool.get("lpMint", {})
    lp_mint_address = lp_mint.get("address", "") if isinstance(lp_mint, dict) else str(lp_mint)

    # Parse burn percentage from lpAmount data
    burn_pct = 0.0
    lp_amount = pool.get("burnPercent")
    if lp_amount is not None:
        try:
            burn_pct = float(lp_amount)
        except (ValueError, TypeError):
            pass

    # Fallback: check if lpAmount field has burn info
    if burn_pct == 0 and pool.get("lpAmount"):
        # Some Raydium responses include burn percentage in different format
        raw_burn = pool.get("burnPercent", pool.get("burn_percent", 0))
        try:
            burn_pct = float(raw_burn) if raw_burn else 0.0
        except (ValueError, TypeError):
            pass

    mint_a = pool.get("mintA", {})
    mint_b = pool.get("mintB", {})

    return RaydiumPoolInfo(
        pool_id=pool.get("id", ""),
        base_mint=mint_a.get("address", "") if isinstance(mint_a, dict) else "",
        quote_mint=mint_b.get("address", "") if isinstance(mint_b, dict) else "",
        lp_mint=lp_mint_address,
        lp_supply=Decimal(str(pool.get("lpAmount", 0) or 0)),
        tvl=Decimal(str(pool.get("tvl", 0) or 0)),
        burn_percent=burn_pct,
    )
