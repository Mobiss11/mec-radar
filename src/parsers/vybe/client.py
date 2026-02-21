"""Vybe Network API client — holder data and wallet PnL.

Developer plan: 500K credits/month, 500 RPM.
Docs: https://docs.vybenetwork.com/
"""

import asyncio

import httpx
from loguru import logger

from src.parsers.rate_limiter import RateLimiter
from src.parsers.vybe.models import VybeHolder, VybeTokenHoldersPnL, VybeWalletPnL

MAX_RETRIES = 2
RETRY_DELAYS = [1.0, 3.0]


class VybeError(Exception):
    """Vybe API error."""


class VybeClient:
    """HTTP client for Vybe Network API."""

    BASE_URL = "https://api.vybenetwork.xyz"

    def __init__(
        self,
        api_key: str,
        max_rps: float = 8.0,  # Developer plan: 500 RPM
    ) -> None:
        self._rate_limiter = RateLimiter(max_rps)
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=30.0,
            headers={
                "X-API-Key": api_key,
                "Accept": "application/json",
            },
        )

    async def _request(self, method: str, path: str, **kwargs: object) -> dict:
        """Make rate-limited API request with retry on transient errors."""
        for attempt in range(MAX_RETRIES + 1):
            await self._rate_limiter.acquire()
            try:
                response = await self._client.request(method, path, **kwargs)
                if response.status_code == 429:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAYS[attempt])
                        continue
                    raise VybeError("Rate limited (429)")
                if response.status_code == 403:
                    raise VybeError("Forbidden — check API key")
                if response.status_code >= 500 and attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                    continue
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                raise VybeError(f"HTTP {e.response.status_code}: {e.response.text[:200]}") from e
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                    continue
                raise VybeError(f"Request failed after {MAX_RETRIES + 1} attempts: {e}") from e
            except httpx.RequestError as e:
                raise VybeError(f"Request failed: {e}") from e
        raise VybeError("Max retries exceeded")

    async def get_top_holders(
        self,
        mint_address: str,
        limit: int = 10,
    ) -> list[VybeHolder]:
        """Get top token holders by balance.

        Endpoint: GET /v4/tokens/{mintAddress}/top-holders
        """
        data = await self._request(
            "GET",
            f"/v4/tokens/{mint_address}/top-holders",
            params={"limit": limit},
        )
        holders_raw = data.get("data", []) if isinstance(data, dict) else data
        if isinstance(holders_raw, list):
            return [VybeHolder.model_validate(h) for h in holders_raw]
        return []

    async def get_wallet_pnl(self, owner_address: str) -> VybeWalletPnL | None:
        """Get wallet trading PnL.

        Endpoint: GET /v4/wallets/{accountAddress}/pnl
        """
        try:
            data = await self._request(
                "GET",
                f"/v4/wallets/{owner_address}/pnl",
                params={"resolution": "7d"},
            )
            summary = data.get("summary") if isinstance(data, dict) else None
            if isinstance(summary, dict):
                summary["ownerAddress"] = owner_address
                return VybeWalletPnL.model_validate(summary)
            return None
        except VybeError:
            return None

    async def analyze_holders_pnl(
        self,
        mint_address: str,
        max_holders: int = 10,
    ) -> VybeTokenHoldersPnL:
        """Get top holders and check their PnL — composite operation.

        Uses 1 + N credits (1 for holders, N for PnL per holder).
        For free tier (25K/mo), limit to high-scoring tokens only.
        """
        result = VybeTokenHoldersPnL()

        try:
            holders = await self.get_top_holders(mint_address, limit=max_holders)
        except VybeError as e:
            logger.debug(f"[VYBE] Failed to get holders for {mint_address[:12]}: {e}")
            return result

        if not holders:
            return result

        # Calculate top holder concentration
        total_pct = sum(h.percentage for h in holders)
        result.top_holder_pct = total_pct

        # Check PnL for each holder
        in_profit = 0
        in_loss = 0
        total_pnl = 0.0
        checked = 0

        for holder in holders:
            pnl = await self.get_wallet_pnl(holder.ownerAddress)
            if pnl is None or pnl.tradesCount == 0:
                continue

            checked += 1
            pnl_value = pnl.total_pnl_usd
            total_pnl += pnl_value

            if pnl_value > 0:
                in_profit += 1
            elif pnl_value < 0:
                in_loss += 1

        result.total_holders_checked = checked
        result.holders_in_profit = in_profit
        result.holders_in_loss = in_loss
        result.holders_in_profit_pct = (
            (in_profit / checked * 100) if checked > 0 else 0.0
        )
        result.avg_pnl_usd = total_pnl / checked if checked > 0 else 0.0

        return result

    async def close(self) -> None:
        await self._client.aclose()
