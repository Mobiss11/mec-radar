"""Jupiter API client — sell simulation (Quote API) and pricing fallback.

Uses Jupiter Customer Portal API key (free tier: 1 RPS).
Sell simulation via /swap/v1/quote, pricing via /price/v2.
"""

import asyncio
from decimal import Decimal

import httpx
from loguru import logger

from src.parsers.jupiter.models import JupiterPrice, JupiterPriceExtraInfo, SellSimResult
from src.parsers.rate_limiter import RateLimiter

# New authenticated API gateway (requires x-api-key header)
BASE_URL = "https://api.jup.ag/price/v2"
QUOTE_URL = "https://api.jup.ag/swap/v1/quote"
# Wrapped SOL mint address
WSOL_MINT = "So11111111111111111111111111111111111111112"
MAX_RETRIES = 2
RETRY_DELAYS = [1.0, 3.0]


class JupiterClient:
    """Async HTTP client for Jupiter APIs (free tier: 1 RPS, requires API key)."""

    def __init__(self, api_key: str = "", max_rps: float = 1.0) -> None:
        self._api_key = api_key
        self._rate_limiter = RateLimiter(max_rps)
        headers: dict[str, str] = {}
        if api_key:
            headers["x-api-key"] = api_key
        self._client = httpx.AsyncClient(timeout=10.0, headers=headers)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_price(self, mint: str, show_extra: bool = True) -> JupiterPrice | None:
        """Fetch price for a single token.

        If show_extra=True, includes confidence level and depth info.
        """
        params: dict = {"ids": mint}
        if show_extra:
            params["showExtraInfo"] = "true"

        for attempt in range(MAX_RETRIES + 1):
            try:
                await self._rate_limiter.acquire()
                resp = await self._client.get(BASE_URL, params=params)

                if resp.status_code == 429:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[JUPITER] Rate limited, waiting {delay}s")
                    await asyncio.sleep(delay)
                    continue
                if resp.status_code != 200:
                    logger.debug(f"[JUPITER] HTTP {resp.status_code} for {mint}")
                    return None

                data = resp.json()
                return _parse_price(data, mint)

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[JUPITER] {type(e).__name__}, retry in {delay}s")
                    await asyncio.sleep(delay)
                else:
                    logger.warning(f"[JUPITER] Failed after {MAX_RETRIES + 1} attempts: {e}")
                    return None

        return None

    async def simulate_sell(
        self, mint: str, amount_tokens: int = 1000, decimals: int = 6
    ) -> SellSimResult:
        """Simulate selling tokens via Jupiter Quote API.

        Checks if a token is sellable by requesting a swap quote
        from token → SOL. If no route exists, the token may be a honeypot.
        """
        # Amount in smallest units (lamports equivalent)
        raw_amount = amount_tokens * (10 ** decimals)
        params = {
            "inputMint": mint,
            "outputMint": WSOL_MINT,
            "amount": str(raw_amount),
            "slippageBps": "5000",  # 50% slippage tolerance for simulation
        }

        for attempt in range(MAX_RETRIES + 1):
            try:
                await self._rate_limiter.acquire()
                resp = await self._client.get(QUOTE_URL, params=params)

                if resp.status_code == 429:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[JUPITER] Quote rate limited, waiting {delay}s")
                    await asyncio.sleep(delay)
                    continue

                if resp.status_code == 400:
                    data = resp.json()
                    error_msg = data.get("error", data.get("message", "Unknown error"))
                    return SellSimResult(sellable=False, error=str(error_msg))

                if resp.status_code in (401, 403):
                    return SellSimResult(
                        sellable=False, error=f"HTTP {resp.status_code}", api_error=True
                    )

                if resp.status_code >= 500:
                    if attempt < MAX_RETRIES:
                        delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                        logger.debug(f"[JUPITER] Sell sim {resp.status_code}, retry in {delay}s")
                        await asyncio.sleep(delay)
                        continue
                    return SellSimResult(
                        sellable=False, error=f"HTTP {resp.status_code}", api_error=True
                    )

                if resp.status_code != 200:
                    return SellSimResult(
                        sellable=False, error=f"HTTP {resp.status_code}", api_error=True
                    )

                data = resp.json()
                out_amount_raw = int(data.get("outAmount", 0))
                price_impact = data.get("priceImpactPct")

                return SellSimResult(
                    sellable=out_amount_raw > 0,
                    output_amount=Decimal(str(out_amount_raw)) / Decimal("1000000000"),  # lamports → SOL
                    price_impact_pct=float(price_impact) if price_impact else None,
                )

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[JUPITER] Sell sim {type(e).__name__}, retry in {delay}s")
                    await asyncio.sleep(delay)
                else:
                    logger.warning(f"[JUPITER] Sell sim failed after retries: {e}")
                    return SellSimResult(sellable=False, error=str(e), api_error=True)

        return SellSimResult(sellable=False, error="Max retries exceeded", api_error=True)

    async def get_prices_batch(self, mints: list[str]) -> dict[str, JupiterPrice]:
        """Fetch prices for multiple tokens (max 100 per call)."""
        if not mints:
            return {}

        params = {"ids": ",".join(mints[:100]), "showExtraInfo": "true"}

        try:
            await self._rate_limiter.acquire()
            resp = await self._client.get(BASE_URL, params=params)
            if resp.status_code != 200:
                return {}

            data = resp.json()
            results: dict[str, JupiterPrice] = {}
            for mint in mints:
                price = _parse_price(data, mint)
                if price:
                    results[mint] = price
            return results

        except (httpx.TimeoutException, httpx.ConnectError):
            return {}


def _parse_price(data: dict, mint: str) -> JupiterPrice | None:
    """Parse Jupiter API response for a single mint."""
    token_data = data.get("data", {}).get(mint)
    if not token_data:
        return None

    price_str = token_data.get("price")
    if price_str is None:
        return None

    extra_info = None
    extra_raw = token_data.get("extraInfo")
    if extra_raw:
        last_swapped = extra_raw.get("lastSwappedPrice", {}).get("lastJupiterSellPrice")
        extra_info = JupiterPriceExtraInfo(
            last_swapped_price=Decimal(str(last_swapped)) if last_swapped else None,
            confidence_level=extra_raw.get("confidenceLevel", "medium"),
            depth=extra_raw.get("depth"),
        )

    return JupiterPrice(
        id=mint,
        mint_symbol=token_data.get("mintSymbol", ""),
        vs_token=token_data.get("vsToken", ""),
        vs_token_symbol=token_data.get("vsTokenSymbol", ""),
        price=Decimal(str(price_str)),
        extra_info=extra_info,
    )
