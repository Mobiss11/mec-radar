"""GoPlus Security API client — free token security analysis for Solana."""

import asyncio

import httpx
from loguru import logger

from src.parsers.goplus.models import GoPlusReport
from src.parsers.rate_limiter import RateLimiter

BASE_URL = "https://api.gopluslabs.io/api/v1/solana/token_security"
MAX_RETRIES = 2
RETRY_DELAYS = [1.0, 3.0]


class GoPlusClient:
    """Async HTTP client for GoPlus Security API (free, no key)."""

    def __init__(self, max_rps: float = 0.5) -> None:
        self._rate_limiter = RateLimiter(max_rps)
        self._client = httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_token_security(self, mint: str) -> GoPlusReport | None:
        """Fetch security report for a Solana token."""
        url = f"{BASE_URL}/{mint}"

        for attempt in range(MAX_RETRIES + 1):
            try:
                await self._rate_limiter.acquire()
                resp = await self._client.get(url)

                if resp.status_code == 429:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[GOPLUS] Rate limited, waiting {delay}s")
                    await asyncio.sleep(delay)
                    continue

                if resp.status_code != 200:
                    logger.debug(f"[GOPLUS] HTTP {resp.status_code} for {mint[:12]}")
                    return None

                data = resp.json()
                return _parse_report(data, mint)

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[GOPLUS] {type(e).__name__}, retry in {delay}s")
                    await asyncio.sleep(delay)
                else:
                    logger.warning(f"[GOPLUS] Failed after retries for {mint[:12]}: {e}")
                    return None

        return None


def _parse_bool(val: str | None) -> bool | None:
    """Parse GoPlus '0'/'1' string to bool."""
    if val is None or val == "":
        return None
    return val == "1"


def _parse_tax(val: str | None) -> float | None:
    """Parse GoPlus tax string to float percentage."""
    if val is None or val == "":
        return None
    try:
        pct = float(val) * 100  # GoPlus returns 0.0-1.0, convert to 0-100
        return pct
    except (ValueError, TypeError):
        return None


def _parse_report(data: dict, mint: str) -> GoPlusReport | None:
    """Parse GoPlus API response."""
    result = data.get("result", {})
    if not result:
        return None

    # GoPlus returns {mint: {...fields...}} inside result
    token_data = result.get(mint)
    if not token_data:
        # Try lowercase
        token_data = result.get(mint.lower())
    if not token_data:
        return None

    return GoPlusReport(
        is_open_source=_parse_bool(token_data.get("is_open_source")),
        is_proxy=_parse_bool(token_data.get("is_proxy")),
        is_mintable=_parse_bool(token_data.get("is_mintable")),
        owner_can_change_balance=_parse_bool(token_data.get("owner_change_balance")),
        can_take_back_ownership=_parse_bool(token_data.get("can_take_back_ownership")),
        is_honeypot=_parse_bool(token_data.get("is_honeypot")),
        buy_tax=_parse_tax(token_data.get("buy_tax")),
        sell_tax=_parse_tax(token_data.get("sell_tax")),
        holder_count=int(token_data["holder_count"]) if token_data.get("holder_count") else None,
        lp_holder_count=int(token_data["lp_holder_count"]) if token_data.get("lp_holder_count") else None,
        is_true_token=_parse_bool(token_data.get("is_true_token")),
        is_airdrop_scam=_parse_bool(token_data.get("is_airdrop_scam")),
        transfer_pausable=_parse_bool(token_data.get("transfer_pausable")),
        trading_cooldown=_parse_bool(token_data.get("trading_cooldown")),
        is_anti_whale=_parse_bool(token_data.get("is_anti_whale")),
        slippage_modifiable=_parse_bool(token_data.get("slippage_modifiable")),
    )
