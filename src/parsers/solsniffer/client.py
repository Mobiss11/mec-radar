"""SolSniffer API client — paid plan (5000 calls/month).

Only used for cross-validation of tokens in the "gray zone" (score 30-60).
Conserves API calls by selective invocation.
"""

import asyncio
from datetime import datetime, UTC

import httpx
from loguru import logger
from redis.asyncio import Redis

from src.parsers.rate_limiter import RateLimiter
from src.parsers.solsniffer.models import SolSnifferHolder, SolSnifferReport

BASE_URL = "https://solsniffer.com/api/v2"
MAX_RETRIES = 2
RETRY_DELAYS = [1.0, 3.0]
REDIS_KEY = "solsniffer:monthly_calls"


class SolSnifferClient:
    """Async HTTP client for SolSniffer API with Redis-backed monthly counter."""

    def __init__(
        self,
        api_key: str,
        max_rps: float = 0.1,
        redis: Redis | None = None,
    ) -> None:
        self._api_key = api_key
        self._rate_limiter = RateLimiter(max_rps)
        self._client = httpx.AsyncClient(timeout=15.0)
        self._redis = redis
        self._monthly_calls = 0
        self._last_month: str = ""

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def monthly_calls(self) -> int:
        return self._monthly_calls

    async def _get_monthly_calls(self) -> int:
        """Get current month's call count from Redis or memory."""
        current_month = datetime.now(UTC).strftime("%Y-%m")
        if current_month != self._last_month:
            self._monthly_calls = 0
            self._last_month = current_month

        if self._redis:
            try:
                key = f"{REDIS_KEY}:{current_month}"
                val = await self._redis.get(key)
                if val is not None:
                    self._monthly_calls = int(val)
            except Exception:
                pass  # Fall back to in-memory
        return self._monthly_calls

    async def _try_reserve_call(self, monthly_cap: int) -> bool:
        """Atomically reserve a call slot. Returns True if under cap.

        Uses Redis INCR for atomic increment across multiple workers,
        preventing TOCTOU race conditions on the monthly cap.
        """
        current_month = datetime.now(UTC).strftime("%Y-%m")
        if current_month != self._last_month:
            self._monthly_calls = 0
            self._last_month = current_month

        if self._redis:
            try:
                key = f"{REDIS_KEY}:{current_month}"
                new_count = await self._redis.incr(key)
                await self._redis.expire(key, 32 * 86400)
                self._monthly_calls = new_count
                if new_count > monthly_cap:
                    # Over cap — decrement back to undo reservation
                    await self._redis.decr(key)
                    self._monthly_calls = new_count - 1
                    return False
                return True
            except Exception:
                pass  # Fall back to in-memory

        # In-memory fallback (single process only)
        self._monthly_calls += 1
        if self._monthly_calls > monthly_cap:
            self._monthly_calls -= 1
            return False
        return True

    async def get_token_audit(
        self, mint: str, *, monthly_cap: int = 5000
    ) -> SolSnifferReport | None:
        """Fetch security audit for a Solana token.

        Args:
            mint: Token mint address.
            monthly_cap: Maximum API calls per month.
        """
        # Atomic cap reservation — prevents TOCTOU race across workers
        reserved = await self._try_reserve_call(monthly_cap)
        if not reserved:
            logger.debug(
                f"[SOLSNIFFER] Monthly cap reached ({self._monthly_calls}/{monthly_cap})"
            )
            return None

        url = f"{BASE_URL}/token/{mint}"
        headers = {"X-API-KEY": self._api_key}

        for attempt in range(MAX_RETRIES + 1):
            try:
                await self._rate_limiter.acquire()
                resp = await self._client.get(url, headers=headers)

                if resp.status_code == 429:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[SOLSNIFFER] Rate limited, waiting {delay}s")
                    await asyncio.sleep(delay)
                    continue

                if resp.status_code == 401:
                    logger.warning("[SOLSNIFFER] Invalid API key")
                    return None

                if resp.status_code != 200:
                    logger.debug(f"[SOLSNIFFER] HTTP {resp.status_code} for {mint[:12]}")
                    return None

                data = resp.json()
                return _parse_report(data)

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    await asyncio.sleep(delay)
                else:
                    logger.warning(f"[SOLSNIFFER] Failed for {mint[:12]}: {e}")
                    return None

        return None


def _parse_report(data: dict) -> SolSnifferReport:
    """Parse SolSniffer API response."""
    token_data = data.get("tokenData", data)

    # Parse top holders if available
    holders_raw = token_data.get("topHolders", [])
    holders = [
        SolSnifferHolder(
            address=h.get("address", ""),
            percentage=float(h.get("percentage", 0)),
            is_contract=h.get("isContract", False),
        )
        for h in holders_raw[:20]
    ]

    return SolSnifferReport(
        snifscore=int(token_data.get("snifScore", token_data.get("score", 0))),
        is_mintable=token_data.get("isMintable"),
        is_freezable=token_data.get("isFreezable"),
        is_mutable_metadata=token_data.get("isMutableMetadata"),
        lp_burned=token_data.get("lpBurned"),
        top10_pct=token_data.get("top10Percentage"),
        liquidity_usd=token_data.get("liquidityUsd"),
        top_holders=holders,
        raw_data=data,
    )
