"""Solana Tracker Risk API — sniper count, insider %, risk score 1-10.

Free API that provides pre-computed risk assessment for Solana tokens:
- Overall risk score (1-10)
- Sniper count (wallets that bought in first block)
- Insider percentage
- Holder distribution stats

Cost: $0 (free, no key needed).
Endpoint: https://data.solanatracker.io/tokens/{mint}
Latency: ~1s.
"""

from dataclasses import dataclass

import httpx
from loguru import logger


@dataclass
class SolanaTrackerRisk:
    """Risk assessment from Solana Tracker."""

    risk_score: int | None = None  # 1-10 (10 = highest risk)
    sniper_count: int | None = None
    insider_pct: float | None = None
    holder_count: int | None = None
    top10_pct: float | None = None
    is_verified: bool = False

    @property
    def score_impact(self) -> int:
        """Score impact based on risk assessment."""
        impact = 0
        if self.risk_score is not None:
            if self.risk_score >= 8:
                impact -= 12
            elif self.risk_score >= 6:
                impact -= 6
            elif self.risk_score <= 2:
                impact += 3

        if self.sniper_count is not None and self.sniper_count >= 5:
            impact -= 5

        if self.insider_pct is not None and self.insider_pct >= 30:
            impact -= 5

        return impact

    @property
    def is_high_risk(self) -> bool:
        return self.risk_score is not None and self.risk_score >= 7


_BASE_URL = "https://data.solanatracker.io"
_TIMEOUT = 10.0


async def get_token_risk(
    token_address: str,
) -> SolanaTrackerRisk | None:
    """Fetch risk assessment from Solana Tracker.

    Args:
        token_address: Token mint address.

    Returns:
        SolanaTrackerRisk or None on failure.
    """
    url = f"{_BASE_URL}/tokens/{token_address}"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                url,
                headers={"Accept": "application/json"},
            )

            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                logger.debug("[SOL_TRACKER] Rate limited")
                return None
            resp.raise_for_status()

            data = resp.json()
            return _parse_risk_data(data)

    except httpx.HTTPStatusError as e:
        logger.debug(f"[SOL_TRACKER] HTTP {e.response.status_code}")
        return None
    except Exception as e:
        logger.debug(f"[SOL_TRACKER] Error for {token_address[:12]}: {e}")
        return None


def _parse_risk_data(data: dict) -> SolanaTrackerRisk:
    """Parse Solana Tracker token response."""
    result = SolanaTrackerRisk()

    # Risk data may be nested under "risk" or top-level
    risk = data.get("risk", data)

    result.risk_score = _safe_int(risk.get("score", risk.get("riskScore")))
    result.sniper_count = _safe_int(risk.get("sniperCount", risk.get("sniper_count")))
    result.insider_pct = _safe_float(risk.get("insiderPct", risk.get("insider_pct")))

    # Holder data
    holders = data.get("holders", {})
    result.holder_count = _safe_int(holders.get("count", holders.get("total")))
    result.top10_pct = _safe_float(holders.get("top10Pct", holders.get("top10_pct")))

    # Verification status
    result.is_verified = bool(data.get("isVerified", data.get("verified", False)))

    return result


def _safe_int(val: object) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _safe_float(val: object) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
