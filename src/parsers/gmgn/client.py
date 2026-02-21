import asyncio
import random
from typing import Any

import tls_client
from fake_useragent import UserAgent
from loguru import logger

from src.parsers.gmgn import endpoints
from src.parsers.gmgn.exceptions import (
    CloudflareBlockedError,
    GmgnApiError,
    GmgnRateLimitError,
)
from src.parsers.gmgn.models import (
    GmgnNewPair,
    GmgnPumpToken,
    GmgnSecurityInfo,
    GmgnSmartWallet,
    GmgnTokenInfo,
    GmgnTopHolder,
)
from src.parsers.rate_limiter import RateLimiter

TLS_IDENTIFIERS = [
    "chrome_120",
    "chrome_119",
    "safari_ios_17_0",
    "firefox_120",
]

MAX_RETRIES = 3
RETRY_DELAYS = [2.0, 4.0, 8.0]
CIRCUIT_BREAKER_THRESHOLD = 5
CIRCUIT_BREAKER_BASE_COOLDOWN = 60.0  # Base cooldown in seconds
CIRCUIT_BREAKER_MAX_COOLDOWN = 600.0  # Max 10 minutes
SESSION_ROTATE_INTERVAL = 50


class GmgnClient:
    """Async HTTP client for gmgn.ai with TLS fingerprint bypass and proxy support."""

    def __init__(
        self,
        rate_limiter: RateLimiter | None = None,
        max_rps: float = 1.5,
        proxy_url: str = "",
    ) -> None:
        self._rate_limiter = rate_limiter or RateLimiter(max_rps)
        self._ua = UserAgent()
        self._proxy_url = proxy_url
        self._session = self._create_session()
        self._request_count = 0
        self._consecutive_403s = 0
        self._circuit_open_until = 0.0
        self._circuit_trip_count = 0  # Exponential backoff multiplier

    def _create_session(self) -> tls_client.Session:
        session = tls_client.Session(
            client_identifier=random.choice(TLS_IDENTIFIERS),
            random_tls_extension_order=True,
        )
        session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://gmgn.ai/?chain=sol",
                "Origin": "https://gmgn.ai",
                "User-Agent": self._ua.random,
            }
        )
        # Apply proxy if configured
        if self._proxy_url:
            session.proxies = {
                "http": self._proxy_url,
                "https": self._proxy_url,
            }
        return session

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """Execute a rate-limited request with retry and circuit breaker."""
        # Circuit breaker check — fail fast so caller can fallback to other sources
        now = asyncio.get_event_loop().time()
        if now < self._circuit_open_until:
            remaining = self._circuit_open_until - now
            raise CloudflareBlockedError(
                f"Circuit breaker open ({remaining:.0f}s remaining), skipping GMGN"
            )

        for attempt in range(MAX_RETRIES):
            await self._rate_limiter.acquire()

            # Rotate session periodically
            self._request_count += 1
            if self._request_count % SESSION_ROTATE_INTERVAL == 0:
                self._session = self._create_session()
                logger.debug("Rotated TLS session")

            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        getattr(self._session, method), url, **kwargs
                    ),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                logger.warning(f"Request timed out (attempt {attempt + 1}): {url}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                    continue
                raise GmgnApiError(f"Request timed out after {MAX_RETRIES} attempts: {url}")
            except Exception as e:
                logger.warning(f"Request failed (attempt {attempt + 1}): {e}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                    continue
                raise GmgnApiError(f"All retries exhausted for {url}") from e

            if response.status_code == 403:
                self._consecutive_403s += 1
                logger.warning(
                    f"Cloudflare 403 (#{self._consecutive_403s}) for {url}"
                )
                if self._consecutive_403s >= CIRCUIT_BREAKER_THRESHOLD:
                    # Exponential cooldown: 60s, 120s, 240s, ... up to 600s
                    self._circuit_trip_count += 1
                    cooldown = min(
                        CIRCUIT_BREAKER_BASE_COOLDOWN * (2 ** (self._circuit_trip_count - 1)),
                        CIRCUIT_BREAKER_MAX_COOLDOWN,
                    )
                    self._circuit_open_until = asyncio.get_event_loop().time() + cooldown
                    logger.error(
                        f"Circuit breaker OPEN for {cooldown:.0f}s "
                        f"(trip #{self._circuit_trip_count})"
                    )
                    self._consecutive_403s = 0
                    raise CloudflareBlockedError(f"Circuit breaker triggered for {url}")
                self._session = self._create_session()
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                    continue
                raise CloudflareBlockedError(f"403 after {MAX_RETRIES} retries: {url}")

            if response.status_code == 429:
                raise GmgnRateLimitError(f"Rate limited: {url}")

            if response.status_code != 200:
                raise GmgnApiError(f"HTTP {response.status_code}: {url}")

            # Reset circuit breaker on success — gradual recovery
            self._consecutive_403s = 0
            # Decrement trip count by 1 instead of full reset: if we were at
            # trip_count=4 (240s cooldown), next trip starts at trip_count=3 (120s)
            # instead of jumping back to trip_count=1 (60s) after a single success.
            if self._circuit_trip_count > 0:
                self._circuit_trip_count -= 1

            data = response.json()
            if data.get("code") != 0:
                raise GmgnApiError(f"API error code {data.get('code')}: {data.get('msg', '')}")

            return data.get("data", data)

        raise GmgnApiError(f"Exhausted retries for {url}")

    # === Public endpoints ===

    async def get_token_info(self, address: str, chain: str = "sol") -> GmgnTokenInfo:
        """Fetch token info via batch endpoint (most reliable, returns price data)."""
        url = endpoints.BASE_URL + endpoints.TOKEN_INFO_BATCH
        data = await self._request(
            "post", url, json={"chain": chain, "addresses": [address]}
        )
        # Response is a list of tokens
        items = data if isinstance(data, list) else [data]
        if not items:
            raise GmgnApiError(f"No data returned for {address}")
        return GmgnTokenInfo.model_validate(items[0])

    async def get_top_holders(
        self, address: str, chain: str = "sol", limit: int = 100
    ) -> list[GmgnTopHolder]:
        url = endpoints.BASE_URL + endpoints.TOP_HOLDERS.format(chain=chain, address=address)
        data = await self._request("get", url, params={"limit": limit})
        if isinstance(data, list):
            holders = data
        else:
            raw = data.get("holders", [])
            if isinstance(raw, dict):
                # API may return holders as dict (address→info) — keep only dict values
                holders = [v for v in raw.values() if isinstance(v, dict)]
            else:
                holders = raw if isinstance(raw, list) else []
        return [GmgnTopHolder.model_validate(h) for h in holders]

    async def get_token_security(self, address: str, chain: str = "sol") -> GmgnSecurityInfo:
        url = endpoints.BASE_URL + endpoints.TOKEN_SECURITY.format(chain=chain, address=address)
        data = await self._request("get", url)
        # Security data is nested under 'goplus' key
        goplus = data.get("goplus", data) if isinstance(data, dict) else data
        return GmgnSecurityInfo.model_validate(goplus)

    async def get_new_pairs(self, chain: str = "sol", limit: int = 50) -> list[GmgnNewPair]:
        url = endpoints.BASE_URL + endpoints.NEW_PAIRS.format(chain=chain)
        data = await self._request(
            "get", url, params={"limit": limit, "orderby": "open_timestamp", "direction": "desc"}
        )
        pairs = data if isinstance(data, list) else data.get("pairs", [])
        return [GmgnNewPair.model_validate(p) for p in pairs]

    async def get_pump_trending(
        self, chain: str = "sol", limit: int = 50, orderby: str = "progress"
    ) -> list[GmgnPumpToken]:
        url = endpoints.BASE_URL + endpoints.PUMP_TRENDING.format(chain=chain)
        data = await self._request("get", url, params={"limit": limit, "orderby": orderby})
        tokens = data if isinstance(data, list) else data.get("tokens", data.get("rank", []))
        return [GmgnPumpToken.model_validate(t) for t in tokens]

    async def get_smart_wallets(
        self, chain: str = "sol", category: str = "7d", limit: int = 50
    ) -> list[GmgnSmartWallet]:
        """Fetch top-performing wallets from GMGN rank API.

        category is now the time period: '1d', '7d', '30d'.
        """
        url = endpoints.BASE_URL + endpoints.SMART_WALLETS.format(
            chain=chain, period=category
        )
        data = await self._request(
            "get", url,
            params={"orderby": f"pnl_{category}", "direction": "desc", "limit": limit},
        )
        wallets = data if isinstance(data, list) else data.get("rank", [])
        return [GmgnSmartWallet.model_validate(w) for w in wallets]

    async def get_wallet_info(self, wallet_address: str, chain: str = "sol") -> dict[str, Any]:
        url = endpoints.BASE_URL + endpoints.WALLET_INFO.format(
            chain=chain, address=wallet_address
        )
        return await self._request("get", url)

    async def get_wallet_trades(
        self, wallet_address: str, chain: str = "sol", limit: int = 100
    ) -> list[dict[str, Any]]:
        url = endpoints.BASE_URL + endpoints.WALLET_TRADES.format(
            chain=chain, address=wallet_address
        )
        data = await self._request("get", url, params={"limit": limit})
        return data if isinstance(data, list) else data.get("trades", [])

    async def close(self) -> None:
        """Clean up TLS session."""
        try:
            self._session.close()
        except Exception:
            pass
