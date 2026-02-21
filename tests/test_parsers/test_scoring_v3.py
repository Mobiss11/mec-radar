"""Tests for scoring model v3 (momentum-weighted)."""

from decimal import Decimal
from unittest.mock import MagicMock

from src.parsers.scoring_v3 import compute_score_v3


def _make_snapshot(**kwargs) -> MagicMock:
    """Create a mock TokenSnapshot with defaults."""
    defaults = {
        "liquidity_usd": Decimal("50000"),
        "dex_liquidity_usd": None,
        "holders_count": 200,
        "volume_5m": None,
        "volume_1h": Decimal("30000"),
        "volume_24h": None,
        "dex_volume_5m": None,
        "dex_volume_1h": None,
        "dex_volume_24h": None,
        "smart_wallets_count": 0,
        "buys_1h": 50,
        "sells_1h": 20,
        "buys_5m": None,
        "sells_5m": None,
        "buys_24h": None,
        "sells_24h": None,
        "top10_holders_pct": None,
        "price": None,
        "market_cap": None,
        "score": None,
        "dev_holds_pct": None,
        "volatility_5m": None,
    }
    defaults.update(kwargs)
    snap = MagicMock()
    for k, v in defaults.items():
        setattr(snap, k, v)
    return snap


def _make_security(**kwargs) -> MagicMock:
    """Create a mock TokenSecurity."""
    defaults = {
        "is_honeypot": False,
        "lp_burned": True,
        "lp_locked": False,
        "contract_renounced": True,
        "sell_tax": Decimal("2"),
        "top10_holders_pct": Decimal("20"),
        "is_mintable": False,
        "lp_lock_duration_days": None,
        "buy_tax": None,
    }
    defaults.update(kwargs)
    sec = MagicMock()
    for k, v in defaults.items():
        setattr(sec, k, v)
    return sec


def test_v3_returns_zero_without_liquidity():
    snap = _make_snapshot(liquidity_usd=None, dex_liquidity_usd=None)
    assert compute_score_v3(snap, None) == 0


def test_v3_honeypot_returns_zero():
    snap = _make_snapshot()
    sec = _make_security(is_honeypot=True)
    assert compute_score_v3(snap, sec) == 0


def test_v3_high_momentum_token():
    """Token with strong buy pressure + volume should score high."""
    snap = _make_snapshot(
        liquidity_usd=Decimal("60000"),
        volume_1h=Decimal("200000"),  # v/l ratio = 3.3
        buys_1h=100,
        sells_1h=20,  # ratio = 5.0
        holders_count=300,
        smart_wallets_count=2,
    )
    sec = _make_security()
    score = compute_score_v3(snap, sec)
    assert score is not None
    # High momentum: 15(liq) + 15(buy_p) + 15(vol_ratio) + 14(smart) + 10(holders) + 5(lp) + 3(renounced) + 2(top10) = 79
    assert score >= 60


def test_v3_low_momentum_token():
    """Token with low volume, few holders — should score low."""
    snap = _make_snapshot(
        liquidity_usd=Decimal("8000"),
        volume_1h=Decimal("1000"),  # v/l ratio = 0.125
        buys_1h=5,
        sells_1h=10,  # ratio = 0.5, no bonus
        holders_count=20,
        smart_wallets_count=0,
    )
    score = compute_score_v3(snap, None)
    assert score is not None
    assert score <= 20


def test_v3_smart_money_heavily_weighted():
    """Smart money should be the strongest individual signal."""
    base = _make_snapshot(
        liquidity_usd=Decimal("20000"),
        volume_1h=Decimal("5000"),
        buys_1h=10,
        sells_1h=8,
        holders_count=50,
    )

    score_no_sm = compute_score_v3(base, None)
    base_with_sm = _make_snapshot(
        liquidity_usd=Decimal("20000"),
        volume_1h=Decimal("5000"),
        buys_1h=10,
        sells_1h=8,
        holders_count=50,
        smart_wallets_count=3,
    )
    score_with_sm = compute_score_v3(base_with_sm, None)
    assert score_with_sm is not None
    assert score_no_sm is not None
    # 3 smart wallets = +20 pts
    assert score_with_sm - score_no_sm == 20


def test_v3_mintable_harsh_penalty():
    """Mintable tokens get -20 in v3 (harsher than v2's -15)."""
    snap = _make_snapshot(
        liquidity_usd=Decimal("50000"),
        volume_1h=Decimal("30000"),
    )
    sec_safe = _make_security(is_mintable=False)
    sec_mint = _make_security(is_mintable=True)
    score_safe = compute_score_v3(snap, sec_safe)
    score_mint = compute_score_v3(snap, sec_mint)
    assert score_safe is not None and score_mint is not None
    assert score_safe - score_mint == 20


def test_v3_holder_velocity_bonus():
    """Holder velocity should add up to 15 pts."""
    snap = _make_snapshot()
    score_no_vel = compute_score_v3(snap, None)
    score_high_vel = compute_score_v3(snap, None, holder_velocity=100)
    assert score_no_vel is not None and score_high_vel is not None
    assert score_high_vel - score_no_vel == 15


def test_v3_creator_risk_penalty():
    """High-risk creator should get penalized."""
    snap = _make_snapshot()
    creator = MagicMock()
    creator.risk_score = 80
    score = compute_score_v3(snap, None, creator_profile=creator)
    score_no_creator = compute_score_v3(snap, None)
    assert score is not None and score_no_creator is not None
    assert score_no_creator - score == 15


def test_v3_score_clamped_0_100():
    """Score should never go below 0 or above 100."""
    # Very bad token: low everything + mintable + high sell tax
    snap = _make_snapshot(
        liquidity_usd=Decimal("1000"),
        volume_1h=Decimal("10"),
        holders_count=5,
    )
    sec = _make_security(
        is_mintable=True,
        sell_tax=Decimal("50"),
        lp_burned=False,
        contract_renounced=False,
        top10_holders_pct=Decimal("80"),
    )
    creator = MagicMock()
    creator.risk_score = 90
    score = compute_score_v3(snap, sec, creator_profile=creator)
    assert score == 0


def test_v3_volume_acceleration():
    """5m volume acceleration should add bonus points."""
    snap_accel = _make_snapshot(
        volume_5m=Decimal("5000"),
        volume_1h=Decimal("20000"),  # 5m*12=60k > 20k*2 → accel >= 3.0
    )
    snap_flat = _make_snapshot(
        volume_5m=Decimal("1000"),
        volume_1h=Decimal("20000"),  # 1k*12=12k < 20k → accel < 1.0
    )
    score_accel = compute_score_v3(snap_accel, None)
    score_flat = compute_score_v3(snap_flat, None)
    assert score_accel is not None and score_flat is not None
    assert score_accel > score_flat
