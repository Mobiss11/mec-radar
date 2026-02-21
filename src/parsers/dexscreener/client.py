import asyncio

import httpx
from loguru import logger

from src.parsers.dexscreener.models import DexScreenerPair
from src.parsers.rate_limiter import RateLimiter

BASE_URL = "https://api.dexscreener.com"
MAX_RETRIES = 3
RETRY_DELAYS = [1.0, 2.0, 4.0]


class DexScreenerClient:
    """Async REST client for DexScreener public API (no auth required)."""

    def __init__(self, rate_limiter: RateLimiter | None = None, max_rps: float = 1.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=10.0,
            headers={"Accept": "application/json"},
        )
        self._rate_limiter = rate_limiter or RateLimiter(max_rps)

    async def _request_with_retry(self, path: str) -> httpx.Response:
        """Execute GET with retry on 429/timeout."""
        for attempt in range(MAX_RETRIES):
            await self._rate_limiter.acquire()
            try:
                response = await self._client.get(path)
                if response.status_code == 429:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        delay = max(float(retry_after), delay)
                    logger.debug(f"[DEXSCREENER] 429 rate limited, retrying in {delay}s")
                    await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[DEXSCREENER] {type(e).__name__}, retrying in {delay}s")
                    await asyncio.sleep(delay)
                else:
                    raise
        # Final attempt — no retry
        await self._rate_limiter.acquire()
        response = await self._client.get(path)
        response.raise_for_status()
        return response

    async def get_token_pairs(self, token_address: str) -> list[DexScreenerPair]:
        """Get all pairs for a token on Solana."""
        response = await self._request_with_retry(f"/token-pairs/v1/solana/{token_address}")
        data = response.json()
        if isinstance(data, list):
            return [DexScreenerPair.model_validate(p) for p in data]
        pairs = data.get("pairs", data.get("pair", []))
        if not isinstance(pairs, list):
            pairs = [pairs] if pairs else []
        return [DexScreenerPair.model_validate(p) for p in pairs]

    async def get_tokens_batch(self, addresses: list[str]) -> list[DexScreenerPair]:
        """Get data for multiple tokens (max 30 per request)."""
        if not addresses:
            return []
        batch = addresses[:30]
        addr_str = ",".join(batch)
        response = await self._request_with_retry(f"/tokens/v1/solana/{addr_str}")
        data = response.json()
        if isinstance(data, list):
            return [DexScreenerPair.model_validate(p) for p in data]
        return []

    async def get_token_boosts(self) -> list[dict]:
        """Get currently boosted/trending tokens."""
        response = await self._request_with_retry("/token-boosts/latest/v1")
        data = response.json()
        return data if isinstance(data, list) else []

    async def close(self) -> None:
        await self._client.aclose()
