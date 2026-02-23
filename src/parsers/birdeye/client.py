"""Birdeye Data Services API client.

Official API — stable, 15 RPS on Lite plan, no TLS fingerprint hacks.
Primary data source for price, market_cap, liquidity, volume, security.
Retry with exponential backoff for transient errors (timeout, 429, 5xx).
"""

import asyncio
from typing import Any

import httpx
from loguru import logger

from src.parsers.birdeye.models import (
    BirdeyeOHLCVItem,
    BirdeyePrice,
    BirdeyeTokenMetadata,
    BirdeyeTokenOverview,
    BirdeyeTokenSecurity,
    BirdeyeTradeItem,
)
from src.parsers.rate_limiter import RateLimiter

BASE_URL = "https://public-api.birdeye.so"
MAX_RETRIES = 2
RETRY_DELAYS = [1.0, 3.0]


class BirdeyeApiError(Exception):
    pass


class BirdeyeClient:
    """Async client for Birdeye Data Services API (Lite plan: 15 RPS, 1.5M CU/mo)."""

    def __init__(
        self,
        api_key: str,
        rate_limiter: RateLimiter | None = None,
        max_rps: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._rate_limiter = rate_limiter or RateLimiter(max_rps)
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=15.0,
            headers={
                "X-API-KEY": api_key,
                "Accept": "application/json",
                "x-chain": "solana",
            },
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Execute a rate-limited request with retry for transient errors."""
        last_exc: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            await self._rate_limiter.acquire()
            try:
                resp = await self._client.request(method, path, **kwargs)

                if resp.status_code == 429:
                    if attempt < MAX_RETRIES:
                        delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                        logger.debug(f"[BIRDEYE] 429 rate limited, retry {attempt + 1} in {delay}s: {path}")
                        await asyncio.sleep(delay)
                        continue
                    raise BirdeyeApiError("Rate limited (429)")

                if resp.status_code == 401:
                    raise BirdeyeApiError("Invalid API key (401)")

                if resp.status_code >= 500:
                    if attempt < MAX_RETRIES:
                        delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                        logger.debug(f"[BIRDEYE] {resp.status_code} server error, retry {attempt + 1} in {delay}s: {path}")
                        await asyncio.sleep(delay)
                        continue

                resp.raise_for_status()
                data = resp.json()
                if not data.get("success", True):
                    raise BirdeyeApiError(f"API error: {data.get('message', 'unknown')}")
                return data.get("data", data)

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_exc = e
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[BIRDEYE] {type(e).__name__}, retry {attempt + 1} in {delay}s: {path}")
                    await asyncio.sleep(delay)
                    continue
                raise BirdeyeApiError(f"Request failed after {MAX_RETRIES + 1} attempts: {path}: {e}") from e
            except httpx.HTTPStatusError as e:
                raise BirdeyeApiError(f"HTTP {e.response.status_code}: {path}") from e
            except httpx.RequestError as e:
                last_exc = e
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    await asyncio.sleep(delay)
                    continue
                raise BirdeyeApiError(f"Request failed: {path}: {e}") from e

        raise BirdeyeApiError(f"Request failed after retries: {path}") from last_exc

    async def get_token_overview(self, address: str) -> BirdeyeTokenOverview:
        """Fetch token overview — price, mcap, liquidity, volume, holders. 30 CU."""
        data = await self._request("GET", "/defi/token_overview", params={"address": address})
        return BirdeyeTokenOverview.model_validate(data)

    async def get_price(self, address: str) -> BirdeyePrice:
        """Fetch current token price. 10 CU."""
        data = await self._request("GET", "/defi/price", params={"address": address})
        return BirdeyePrice.model_validate(data)

    async def get_price_multi(
        self, addresses: list[str], *, include_liquidity: bool = False,
    ) -> dict[str, BirdeyePrice]:
        """Fetch prices for multiple tokens at once. Available on Lite. ~10 CU.

        With include_liquidity=True, response includes liquidity field per token.
        """
        addr_str = ",".join(addresses[:100])
        params: dict[str, str] = {"list_address": addr_str}
        if include_liquidity:
            params["include_liquidity"] = "true"
        data = await self._request("GET", "/defi/multi_price", params=params)
        result: dict[str, BirdeyePrice] = {}
        if isinstance(data, dict):
            for addr, price_data in data.items():
                try:
                    result[addr] = BirdeyePrice.model_validate(price_data)
                except Exception:
                    pass
        return result

    async def get_token_security(self, address: str) -> BirdeyeTokenSecurity:
        """Fetch token security info. 50 CU."""
        data = await self._request(
            "GET", "/defi/token_security", params={"address": address}
        )
        return BirdeyeTokenSecurity.model_validate(data)

    async def get_ohlcv(
        self,
        address: str,
        interval: str = "5m",
        time_from: int | None = None,
        time_to: int | None = None,
    ) -> list[BirdeyeOHLCVItem]:
        """Fetch OHLCV candles. 40 CU.

        interval: "1m", "3m", "5m", "15m", "30m", "1H", "2H", "4H", "6H", "8H", "12H", "1D"
        """
        params: dict = {"address": address, "type": interval}
        if time_from is not None:
            params["time_from"] = time_from
        if time_to is not None:
            params["time_to"] = time_to
        data = await self._request("GET", "/defi/ohlcv", params=params)
        items = data.get("items", []) if isinstance(data, dict) else []
        return [BirdeyeOHLCVItem.model_validate(item) for item in items]

    async def get_trades(
        self,
        address: str,
        tx_type: str = "all",
        limit: int = 50,
        offset: int = 0,
    ) -> list[BirdeyeTradeItem]:
        """Fetch recent trades for a token. 15 CU.

        tx_type: "all", "buy", "sell"
        """
        data = await self._request(
            "GET",
            "/defi/v3/token/trade-data/single",
            params={
                "address": address,
                "tx_type": tx_type,
                "limit": limit,
                "offset": offset,
            },
        )
        items = data.get("items", []) if isinstance(data, dict) else []
        return [BirdeyeTradeItem.model_validate(item) for item in items]

    async def get_token_metadata(self, address: str) -> BirdeyeTokenMetadata:
        """Fetch token metadata (name, image, social links). 5 CU."""
        data = await self._request(
            "GET",
            "/defi/v3/token/meta-data/single",
            params={"address": address},
        )
        return BirdeyeTokenMetadata.model_validate(data)

    async def close(self) -> None:
        await self._client.aclose()
