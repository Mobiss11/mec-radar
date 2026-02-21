"""Tests for Jupiter Price API client and price validation."""

from decimal import Decimal

import pytest

from src.parsers.jupiter.models import JupiterPrice, JupiterPriceExtraInfo
from src.parsers.price_validator import validate_price_consistency


def test_jupiter_price_model():
    """JupiterPrice parses correctly."""
    price = JupiterPrice(
        id="So11111111111111111111111111111111",
        mint_symbol="SOL",
        vs_token="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        vs_token_symbol="USDC",
        price=Decimal("120.50"),
    )
    assert price.price == Decimal("120.50")
    assert price.mint_symbol == "SOL"


def test_jupiter_price_extra_info():
    """JupiterPriceExtraInfo parses confidence level."""
    info = JupiterPriceExtraInfo(
        last_swapped_price=Decimal("120.48"),
        confidence_level="high",
        depth={"buy_price_impact_ratio": {"10": 0.01}},
    )
    assert info.confidence_level == "high"
    assert info.last_swapped_price == Decimal("120.48")


def test_price_validation_consistent():
    """Consistent prices across sources produce no manipulation flag."""
    result = validate_price_consistency(
        gmgn_price=0.001,
        dex_price=0.00102,
        jupiter_price=0.00098,
    )
    assert not result.is_manipulation_suspected
    assert result.score_impact == 0
    assert result.max_divergence_pct < 20


def test_price_validation_manipulation():
    """Divergent prices flag manipulation."""
    result = validate_price_consistency(
        gmgn_price=0.001,
        dex_price=0.0015,  # 50% divergence
        jupiter_price=0.001,
    )
    assert result.is_manipulation_suspected
    assert result.score_impact < 0


def test_price_validation_low_confidence():
    """Low Jupiter confidence applies penalty."""
    result = validate_price_consistency(
        gmgn_price=0.001,
        dex_price=0.001,
        jupiter_price=0.001,
        jupiter_confidence="low",
    )
    assert result.score_impact == -2
