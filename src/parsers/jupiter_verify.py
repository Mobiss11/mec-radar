"""Jupiter VERIFY status check — verified/strict/community badge.

The Jupiter token list provides verification status for Solana tokens:
- "strict": Fully verified, highest trust (approved by Jupiter team)
- "all": In the full token list (community-submitted, not fully verified)
- Not found: Unknown/unverified token

Being in the strict list is a strong positive signal. Not being in any list
is neutral (most new tokens won't be listed yet). Being explicitly banned
is a strong negative.

Cost: $0 (free, no key needed).
Endpoint: https://tokens.jup.ag/token/{mint}
Latency: <200ms.
"""

from dataclasses import dataclass

import httpx
from loguru import logger


@dataclass
class JupiterVerifyResult:
    """Jupiter verification status for a token."""

    found: bool = False
    verification_status: str | None = None  # "strict", "community", None
    is_strict: bool = False
    is_community: bool = False
    is_banned: bool = False
    daily_volume: float | None = None
    name: str | None = None
    symbol: str | None = None

    @property
    def score_impact(self) -> int:
        """Score impact based on verification status."""
        if self.is_banned:
            return -20
        if self.is_strict:
            return 5
        if self.is_community:
            return 2
        return 0  # Not found is neutral for new tokens


_BASE_URL = "https://tokens.jup.ag"
_TIMEOUT = 5.0


async def check_jupiter_verify(
    token_address: str,
) -> JupiterVerifyResult:
    """Check token verification status on Jupiter.

    Args:
        token_address: Token mint address.

    Returns:
        JupiterVerifyResult with verification details.
    """
    result = JupiterVerifyResult()

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{_BASE_URL}/token/{token_address}",
                headers={"Accept": "application/json"},
            )

            if resp.status_code == 404:
                # Token not in Jupiter list — neutral for new tokens
                return result

            if resp.status_code == 429:
                logger.debug("[JUP_VERIFY] Rate limited")
                return result

            resp.raise_for_status()
            data = resp.json()
            return _parse_verify_data(data, result)

    except httpx.HTTPStatusError as e:
        logger.debug(f"[JUP_VERIFY] HTTP {e.response.status_code}")
        return result
    except Exception as e:
        logger.debug(f"[JUP_VERIFY] Error for {token_address[:12]}: {e}")
        return result


def _parse_verify_data(data: dict, result: JupiterVerifyResult) -> JupiterVerifyResult:
    """Parse Jupiter token API response."""
    result.found = True
    result.name = data.get("name")
    result.symbol = data.get("symbol")
    result.daily_volume = data.get("daily_volume")

    # Tags indicate verification level
    tags = data.get("tags", [])

    if "banned" in tags:
        result.is_banned = True
        result.verification_status = "banned"
    elif "verified" in tags or "strict" in tags:
        result.is_strict = True
        result.verification_status = "strict"
    elif "community" in tags:
        result.is_community = True
        result.verification_status = "community"
    elif "token-2022" in tags:
        result.verification_status = "token2022"
    else:
        # Found but no specific verification tag
        result.verification_status = "listed"

    return result
