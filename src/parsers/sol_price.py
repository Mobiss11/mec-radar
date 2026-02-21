"""Real-time SOL/USD price cache via Birdeye API (primary) with Jupiter fallback.

Fetches SOL price every 60s and caches it. Used by paper trader
for accurate PnL USD conversion instead of hardcoded $150.
"""

import asyncio

from loguru import logger

# Cached SOL price — updated by background loop
_sol_price_usd: float = 150.0  # conservative default
_last_update: float = 0.0

# Wrapped SOL mint
WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def get_sol_price() -> float:
    """Get cached SOL/USD price. Thread-safe read."""
    return _sol_price_usd


def get_sol_price_safe(max_stale_seconds: float = 600.0) -> float | None:
    """Get cached SOL/USD price, returning None if too stale.

    Paper trader should use this to avoid PnL calculations with
    a price that hasn't been updated in > max_stale_seconds.
    """
    if _last_update <= 0:
        return _sol_price_usd  # Never updated, use default
    import asyncio
    try:
        now = asyncio.get_event_loop().time()
    except RuntimeError:
        return _sol_price_usd
    if now - _last_update > max_stale_seconds:
        return None
    return _sol_price_usd


async def sol_price_loop(
    birdeye_client: object | None = None,
    jupiter_client: object | None = None,
) -> None:
    """Background loop: fetch SOL/USD every 60s.

    Uses Birdeye as primary source (paid, reliable).
    Falls back to Jupiter if Birdeye unavailable.

    Args:
        birdeye_client: BirdeyeClient instance with get_price() method.
        jupiter_client: JupiterClient instance (fallback).
    """
    global _sol_price_usd, _last_update

    await asyncio.sleep(5)  # let other systems start first

    while True:
        updated = False

        # Primary: Birdeye
        if birdeye_client and not updated:
            try:
                price = await birdeye_client.get_price(WSOL_MINT)
                if price and price.value and price.value > 0:
                    _sol_price_usd = float(price.value)
                    _last_update = asyncio.get_event_loop().time()
                    logger.debug(f"[SOL_PRICE] Updated via Birdeye: ${_sol_price_usd:.2f}")
                    updated = True
            except Exception as e:
                logger.debug(f"[SOL_PRICE] Birdeye failed: {e}")

        # Fallback: Jupiter
        if jupiter_client and not updated:
            try:
                price = await jupiter_client.get_price(WSOL_MINT, show_extra=False)
                if price and price.price and price.price > 0:
                    _sol_price_usd = float(price.price)
                    _last_update = asyncio.get_event_loop().time()
                    logger.debug(f"[SOL_PRICE] Updated via Jupiter: ${_sol_price_usd:.2f}")
                    updated = True
            except Exception as e:
                logger.debug(f"[SOL_PRICE] Jupiter failed: {e}")

        if not updated:
            loop_time = asyncio.get_event_loop().time()
            stale_seconds = loop_time - _last_update if _last_update > 0 else 0
            if stale_seconds > 300:  # 5 min without update
                logger.warning(
                    f"[SOL_PRICE] Stale for {stale_seconds:.0f}s, "
                    f"using cached: ${_sol_price_usd:.2f}"
                )
            else:
                logger.debug(f"[SOL_PRICE] No update, using cached: ${_sol_price_usd:.2f}")

        await asyncio.sleep(60)
