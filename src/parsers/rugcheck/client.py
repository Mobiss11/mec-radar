"""Rugcheck.xyz API client — free contract security analysis for Solana tokens."""

import asyncio

import httpx
from loguru import logger

from src.parsers.rate_limiter import RateLimiter
from src.parsers.rugcheck.models import RugcheckReport, RugcheckRisk

BASE_URL = "https://api.rugcheck.xyz/v1"
MAX_RETRIES = 2
RETRY_DELAYS = [2.0, 5.0]


class RugcheckClient:
    """Async HTTP client for Rugcheck.xyz (free, no API key)."""

    def __init__(self, max_rps: float = 2.0) -> None:
        self._rate_limiter = RateLimiter(max_rps)
        self._client = httpx.AsyncClient(timeout=15.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_token_report(self, mint: str) -> RugcheckReport | None:
        """Fetch token security report from Rugcheck.

        Returns None if token not found or API error.
        """
        url = f"{BASE_URL}/tokens/{mint}/report/summary"

        for attempt in range(MAX_RETRIES + 1):
            try:
                await self._rate_limiter.acquire()
                resp = await self._client.get(url)

                if resp.status_code == 404:
                    return None
                if resp.status_code == 429:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[RUGCHECK] Rate limited, waiting {delay}s")
                    await asyncio.sleep(delay)
                    continue
                if resp.status_code != 200:
                    logger.debug(f"[RUGCHECK] HTTP {resp.status_code} for {mint}")
                    return None

                data = resp.json()
                return _parse_report(data, mint)

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[RUGCHECK] {type(e).__name__}, retry in {delay}s")
                    await asyncio.sleep(delay)
                else:
                    logger.warning(f"[RUGCHECK] Failed after {MAX_RETRIES + 1} attempts: {e}")
                    return None

        return None


def _parse_report(data: dict, mint: str) -> RugcheckReport:
    """Parse raw JSON into RugcheckReport."""
    risks = []
    for risk_data in data.get("risks", []):
        risks.append(RugcheckRisk(
            name=risk_data.get("name", "unknown"),
            description=risk_data.get("description", ""),
            level=risk_data.get("level", "info"),
            score=risk_data.get("score", 0),
        ))

    token_meta = data.get("tokenMeta", {})
    return RugcheckReport(
        score=data.get("score", 0),
        risks=risks,
        mint=mint,
        token_name=token_meta.get("name", ""),
        token_symbol=token_meta.get("symbol", ""),
    )
