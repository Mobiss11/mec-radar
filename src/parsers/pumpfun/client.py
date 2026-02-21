"""Pump.fun frontend API client — creator history for serial scammer detection."""

import asyncio

import httpx
from loguru import logger

from src.parsers.pumpfun.models import PumpfunCreatorHistory, PumpfunToken
from src.parsers.rate_limiter import RateLimiter

BASE_URL = "https://frontend-api-v3.pump.fun"
MAX_RETRIES = 2
RETRY_DELAYS = [1.0, 3.0]


class PumpfunClient:
    """Async HTTP client for Pump.fun frontend API (free, no key)."""

    def __init__(self, max_rps: float = 2.0) -> None:
        self._rate_limiter = RateLimiter(max_rps)
        self._client = httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_creator_history(self, wallet: str) -> PumpfunCreatorHistory | None:
        """Fetch all tokens created by a wallet on Pump.fun."""
        url = f"{BASE_URL}/coins/user-created-coins/{wallet}"
        params = {"limit": 50, "offset": 0, "includeNsfw": "true"}

        for attempt in range(MAX_RETRIES + 1):
            try:
                await self._rate_limiter.acquire()
                resp = await self._client.get(url, params=params)

                if resp.status_code == 429:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[PUMPFUN] Rate limited, waiting {delay}s")
                    await asyncio.sleep(delay)
                    continue

                if resp.status_code == 404:
                    return PumpfunCreatorHistory()  # No tokens found

                if resp.status_code != 200:
                    logger.debug(f"[PUMPFUN] HTTP {resp.status_code} for {wallet[:12]}")
                    return None

                data = resp.json()
                return _parse_creator_history(data)

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[PUMPFUN] {type(e).__name__}, retry in {delay}s")
                    await asyncio.sleep(delay)
                else:
                    logger.warning(f"[PUMPFUN] Failed for {wallet[:12]}: {e}")
                    return None

        return None


def _parse_creator_history(data: list | dict) -> PumpfunCreatorHistory:
    """Parse Pump.fun API response for creator tokens."""
    # API returns a list of token objects
    tokens_list = data if isinstance(data, list) else []

    tokens: list[PumpfunToken] = []
    for item in tokens_list:
        token = PumpfunToken(
            mint=item.get("mint", ""),
            name=item.get("name", ""),
            symbol=item.get("symbol", ""),
            created_timestamp=item.get("created_timestamp", 0),
            market_cap=float(item.get("market_cap", 0) or 0),
            usd_market_cap=float(item.get("usd_market_cap", 0) or 0),
        )
        tokens.append(token)

    dead_count = sum(1 for t in tokens if t.is_dead)

    return PumpfunCreatorHistory(
        total_tokens=len(tokens),
        dead_token_count=dead_count,
        tokens=tokens,
    )
