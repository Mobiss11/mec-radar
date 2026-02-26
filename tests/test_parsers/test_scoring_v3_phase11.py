"""Tests for scoring_v3 Phase 11 additions."""

from decimal import Decimal

from src.models.token import TokenSecurity, TokenSnapshot
from src.parsers.scoring_v3 import compute_score_v3


def _snap(**kwargs) -> TokenSnapshot:
    defaults = {
        "token_id": 1,
        "liquidity_usd": Decimal("50000"),
        "holders_count": 100,
        "volume_1h": Decimal("30000"),
        "buys_1h": 50,
        "sells_1h": 20,
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


def test_dev_holds_high_penalty():
    """dev_holds >= 80% gives -20 penalty in v3."""
    base = compute_score_v3(_snap(), _sec())
    with_dev = compute_score_v3(_snap(), _sec(), dev_holds_pct=85.0)
    assert base - with_dev == 20


def test_dev_holds_low_bonus():
    """dev_holds < 5% gives +3 bonus in v3."""
    base = compute_score_v3(_snap(), _sec())
    with_low_dev = compute_score_v3(_snap(), _sec(), dev_holds_pct=3.0)
    assert with_low_dev - base == 3


def test_volatility_high_penalty():
    """volatility_5m >= 50 gives -8 penalty in v3."""
    base = compute_score_v3(_snap(), _sec())
    with_vol = compute_score_v3(_snap(), _sec(), volatility_5m=55.0)
    assert base - with_vol == 8


def test_rugcheck_dangerous_penalty():
    """rugcheck_score >= 50 gives -20 penalty in v3."""
    base = compute_score_v3(_snap(), _sec())
    with_rug = compute_score_v3(_snap(), _sec(), rugcheck_score=60)
    assert base - with_rug == 20


def test_rugcheck_low_rc_neutral():
    """Phase 54: rugcheck_score < 10 gives NO bonus in v3 (neutral 0).

    rc=1 means 'unanalyzed' (Token-2022), NOT 'clean'.
    """
    base = compute_score_v3(_snap(), _sec())
    with_low_rc = compute_score_v3(_snap(), _sec(), rugcheck_score=5)
    assert with_low_rc - base == 0  # neutral, no bonus


def test_smart_money_weighted():
    """smart_money_weighted is used instead of raw count."""
    snap = _snap(smart_wallets_count=3)  # raw count = 3 → +20
    score_raw = compute_score_v3(snap, _sec())

    # weighted=0.5 → +8 (lower than raw)
    score_weighted = compute_score_v3(snap, _sec(), smart_money_weighted=0.5)
    assert score_weighted < score_raw


def test_dbc_launchpad_dynamic_score():
    """Dynamic launchpad score overrides hardcoded logic."""
    # Hardcoded: "believe" → +3
    score_hardcoded = compute_score_v3(_snap(), _sec(), dbc_launchpad="believe")
    # Dynamic: score = +1 (lower)
    score_dynamic = compute_score_v3(
        _snap(), _sec(), dbc_launchpad="believe", dbc_launchpad_score=1
    )
    assert score_hardcoded - score_dynamic == 2  # +3 vs +1


def test_24h_sustained_buying():
    """24h buy ratio >= 2 + 1h ratio >= 2 gives +3."""
    snap = _snap(buys_1h=50, sells_1h=20, buys_24h=200, sells_24h=80)
    base = compute_score_v3(_snap(buys_1h=50, sells_1h=20), _sec())
    with_24h = compute_score_v3(snap, _sec())
    assert with_24h - base == 3
