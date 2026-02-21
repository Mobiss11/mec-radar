"""Cross-source price validation — detect price manipulation.

Compares prices from GMGN, DexScreener, and Jupiter to flag
tokens with suspicious price discrepancies.
"""

from dataclasses import dataclass
from decimal import Decimal

from loguru import logger


@dataclass
class PriceValidation:
    """Result of cross-source price validation."""

    gmgn_price: float | None
    dex_price: float | None
    jupiter_price: float | None
    max_divergence_pct: float  # largest % difference between any two sources
    is_manipulation_suspected: bool  # divergence > 20%
    jupiter_confidence: str  # "high", "medium", "low"
    score_impact: int


def validate_price_consistency(
    gmgn_price: Decimal | None,
    dex_price: Decimal | None,
    jupiter_price: Decimal | None,
    jupiter_confidence: str = "medium",
    *,
    manipulation_threshold_pct: float = 20.0,
) -> PriceValidation:
    """Compare prices from multiple sources and flag manipulation.

    Args:
        gmgn_price: Price from GMGN.
        dex_price: Price from DexScreener.
        jupiter_price: Price from Jupiter.
        jupiter_confidence: Jupiter's confidence level.
        manipulation_threshold_pct: % divergence to flag as suspicious.

    Returns PriceValidation with divergence analysis.
    """
    prices: list[float] = []
    g = float(gmgn_price) if gmgn_price and gmgn_price > 0 else None
    d = float(dex_price) if dex_price and dex_price > 0 else None
    j = float(jupiter_price) if jupiter_price and jupiter_price > 0 else None

    if g:
        prices.append(g)
    if d:
        prices.append(d)
    if j:
        prices.append(j)

    if len(prices) < 2:
        return PriceValidation(
            gmgn_price=g,
            dex_price=d,
            jupiter_price=j,
            max_divergence_pct=0.0,
            is_manipulation_suspected=False,
            jupiter_confidence=jupiter_confidence,
            score_impact=0,
        )

    # Compute max divergence between any pair
    max_div = 0.0
    for i in range(len(prices)):
        for k in range(i + 1, len(prices)):
            avg = (prices[i] + prices[k]) / 2
            if avg > 0:
                div = abs(prices[i] - prices[k]) / avg * 100
                max_div = max(max_div, div)

    is_suspicious = max_div > manipulation_threshold_pct

    # Score impact
    impact = 0
    if is_suspicious:
        impact = -5
    if jupiter_confidence == "low":
        impact -= 2

    if is_suspicious:
        logger.info(
            f"[PRICE] Divergence {max_div:.1f}%: "
            f"gmgn={g}, dex={d}, jup={j}"
        )

    return PriceValidation(
        gmgn_price=g,
        dex_price=d,
        jupiter_price=j,
        max_divergence_pct=round(max_div, 1),
        is_manipulation_suspected=is_suspicious,
        jupiter_confidence=jupiter_confidence,
        score_impact=impact,
    )
