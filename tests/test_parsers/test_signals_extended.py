"""Tests for Phase 11 signal rules R15-R22."""

from decimal import Decimal

from src.models.token import CreatorProfile, TokenSecurity, TokenSnapshot
from src.parsers.signals import evaluate_signals


def _snap(**kwargs) -> TokenSnapshot:
    defaults = {
        "token_id": 1,
        "liquidity_usd": Decimal("50000"),
        "holders_count": 100,
        "volume_1h": Decimal("30000"),
        "volume_5m": Decimal("5000"),
        "buys_1h": 50,
        "sells_1h": 20,
        "price": Decimal("0.001"),
        "market_cap": Decimal("100000"),
        "score": 60,
    }
    defaults.update(kwargs)
    return TokenSnapshot(**defaults)


def _sec(**kwargs) -> TokenSecurity:
    defaults = {
        "token_id": 1,
        "is_honeypot": False,
        "is_mintable": False,
        "lp_burned": True,
        "contract_renounced": True,
        "sell_tax": Decimal("0"),
        "top10_holders_pct": Decimal("20"),
    }
    defaults.update(kwargs)
    return TokenSecurity(**defaults)


def test_r15_rugcheck_danger():
    """R15: rugcheck_score >= 50 fires rugcheck_danger."""
    result = evaluate_signals(_snap(), _sec(), rugcheck_score=60)
    fired_names = [r.name for r in result.rules_fired]
    assert "rugcheck_danger" in fired_names


def test_r15_rugcheck_safe_no_fire():
    """R15: rugcheck_score < 50 does not fire."""
    result = evaluate_signals(_snap(), _sec(), rugcheck_score=20)
    fired_names = [r.name for r in result.rules_fired]
    assert "rugcheck_danger" not in fired_names


def test_r16_high_dev_holds():
    """R16: dev_holds_pct >= 50 fires high_dev_holds."""
    result = evaluate_signals(_snap(), _sec(), dev_holds_pct=65.0)
    fired_names = [r.name for r in result.rules_fired]
    assert "high_dev_holds" in fired_names


def test_r17_price_manipulation():
    """R17: price divergence > 20% fires price_manipulation."""
    snap = _snap(price=Decimal("0.001"))
    result = evaluate_signals(snap, _sec(), jupiter_price=0.0015)  # 50% divergence
    fired_names = [r.name for r in result.rules_fired]
    assert "price_manipulation" in fired_names


def test_r18_volume_dried_up():
    """R18: vol_1h/vol_5m > 12 fires volume_dried_up (5m volume is negligible)."""
    snap = _snap(volume_5m=Decimal("500"), volume_1h=Decimal("100000"))
    result = evaluate_signals(snap, _sec())
    fired_names = [r.name for r in result.rules_fired]
    assert "volume_dried_up" in fired_names


def test_r20_lp_removal_active():
    """R20: lp_removed_pct >= 20 fires lp_removal_active."""
    result = evaluate_signals(_snap(), _sec(), lp_removed_pct=35.0)
    fired_names = [r.name for r in result.rules_fired]
    assert "lp_removal_active" in fired_names


def test_r21_cross_token_coordination():
    """R21: cross_whale_detected fires cross_token_coordination."""
    result = evaluate_signals(_snap(), _sec(), cross_whale_detected=True)
    fired_names = [r.name for r in result.rules_fired]
    assert "cross_token_coordination" in fired_names


def test_r22_strong_momentum():
    """R22: healthy growth pattern fires strong_momentum."""
    prev = _snap(price=Decimal("0.0008"))
    snap = _snap(price=Decimal("0.001"), buys_1h=50, sells_1h=20)
    result = evaluate_signals(snap, _sec(), prev_snapshot=prev, volatility_5m=5.0)
    fired_names = [r.name for r in result.rules_fired]
    assert "strong_momentum" in fired_names
