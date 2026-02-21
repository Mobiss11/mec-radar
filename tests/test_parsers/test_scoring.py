"""Test token scoring function."""

from decimal import Decimal

from src.models.token import CreatorProfile, TokenSecurity, TokenSnapshot
from src.parsers.scoring import compute_score


def _make_snapshot(**kwargs) -> TokenSnapshot:
    defaults = {
        "token_id": 1,
        "liquidity_usd": Decimal("50000"),
        "holders_count": 100,
        "volume_1h": Decimal("30000"),
    }
    defaults.update(kwargs)
    return TokenSnapshot(**defaults)


def _make_security(**kwargs) -> TokenSecurity:
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


def test_score_good_token():
    """Token meeting all criteria should score high."""
    snapshot = _make_snapshot()
    security = _make_security()
    score = compute_score(snapshot, security)
    assert score is not None
    assert score >= 50


def test_score_honeypot_is_zero():
    snapshot = _make_snapshot()
    security = _make_security(is_honeypot=True)
    score = compute_score(snapshot, security)
    assert score == 0


def test_score_mintable_soft_penalty():
    """Mintable is now a soft penalty (-15), not a hard disqualifier."""
    snapshot = _make_snapshot()
    security_no_mint = _make_security(is_mintable=False)
    security_mint = _make_security(is_mintable=True)
    score_normal = compute_score(snapshot, security_no_mint)
    score_mint = compute_score(snapshot, security_mint)
    assert score_mint is not None
    assert score_normal is not None
    assert score_mint == score_normal - 15


def test_score_no_liquidity_returns_zero():
    snapshot = _make_snapshot(liquidity_usd=None)
    score = compute_score(snapshot, None)
    assert score == 0


def test_score_low_liquidity_low_score():
    """Below $10K liq, few holders = low score."""
    snapshot = _make_snapshot(
        liquidity_usd=Decimal("5000"),
        holders_count=10,
        volume_1h=Decimal("1000"),
    )
    score = compute_score(snapshot, None)
    assert score is not None
    assert score < 20


def test_score_no_security_still_scores():
    """Should score based on liquidity/holders/volume alone."""
    snapshot = _make_snapshot()
    score = compute_score(snapshot, None)
    assert score is not None
    assert score > 0


def test_score_high_volume_ratio():
    """Very high vol/liq ratio scores max volume points."""
    snapshot = _make_snapshot(
        liquidity_usd=Decimal("10000"),
        volume_1h=Decimal("60000"),
        holders_count=50,
    )
    score = compute_score(snapshot, None)
    assert score is not None
    assert score >= 40  # 15 (liq) + 8 (holders) + 25 (ratio 6x)


def test_score_smart_money_bonus():
    """Smart wallets in top holders should boost score."""
    snap_0 = _make_snapshot(smart_wallets_count=0)
    snap_1 = _make_snapshot(smart_wallets_count=1)
    snap_2 = _make_snapshot(smart_wallets_count=2)
    snap_3 = _make_snapshot(smart_wallets_count=3)
    s0 = compute_score(snap_0, None)
    s1 = compute_score(snap_1, None)
    s2 = compute_score(snap_2, None)
    s3 = compute_score(snap_3, None)
    assert s1 == s0 + 5
    assert s2 == s0 + 10
    assert s3 == s0 + 15


def test_score_holder_velocity_bonus():
    """High holder velocity should boost score."""
    snapshot = _make_snapshot()
    s_base = compute_score(snapshot, None)
    s_low = compute_score(snapshot, None, holder_velocity=10.0)
    s_med = compute_score(snapshot, None, holder_velocity=50.0)
    s_high = compute_score(snapshot, None, holder_velocity=100.0)
    assert s_low == s_base  # <20 → no bonus
    assert s_med == s_base + 7
    assert s_high == s_base + 10


def test_score_creator_risk_penalty():
    """High-risk creator should reduce score."""
    snapshot = _make_snapshot()
    safe = CreatorProfile(address="safe", risk_score=10)
    risky = CreatorProfile(address="risky", risk_score=60)
    scammer = CreatorProfile(address="scam", risk_score=80)
    s_base = compute_score(snapshot, None)
    s_safe = compute_score(snapshot, None, creator_profile=safe)
    s_risky = compute_score(snapshot, None, creator_profile=risky)
    s_scam = compute_score(snapshot, None, creator_profile=scammer)
    assert s_safe == s_base  # risk <40 → no penalty
    assert s_risky == s_base - 12
    assert s_scam == s_base - 20


def test_score_capped_at_100():
    """Score should never exceed 100."""
    snapshot = _make_snapshot(
        liquidity_usd=Decimal("200000"),
        holders_count=1000,
        volume_1h=Decimal("2000000"),
        smart_wallets_count=5,
    )
    security = _make_security()
    score = compute_score(snapshot, security, holder_velocity=200.0)
    assert score is not None
    assert score <= 100


def test_score_whale_dynamics_impact():
    """Whale score impact should be added to score."""
    snapshot = _make_snapshot()
    s_base = compute_score(snapshot, None)
    s_positive = compute_score(snapshot, None, whale_score_impact=5)
    s_negative = compute_score(snapshot, None, whale_score_impact=-5)
    assert s_positive == s_base + 5
    assert s_negative == s_base - 5


def test_score_lp_lock_duration_bonus():
    """LP lock duration should add bonus points."""
    snapshot = _make_snapshot()
    sec_no_lock = _make_security(lp_lock_duration_days=None)
    sec_short = _make_security(lp_lock_duration_days=30)
    sec_medium = _make_security(lp_lock_duration_days=90)
    sec_long = _make_security(lp_lock_duration_days=365)

    s_none = compute_score(snapshot, sec_no_lock)
    s_short = compute_score(snapshot, sec_short)
    s_medium = compute_score(snapshot, sec_medium)
    s_long = compute_score(snapshot, sec_long)

    assert s_short == s_none + 1
    assert s_medium == s_none + 3
    assert s_long == s_none + 5


def test_score_buy_tax_penalty():
    """High buy tax should reduce score."""
    snapshot = _make_snapshot()
    sec_no_tax = _make_security(buy_tax=None)
    sec_low = _make_security(buy_tax=Decimal("3"))
    sec_medium = _make_security(buy_tax=Decimal("7"))
    sec_high = _make_security(buy_tax=Decimal("15"))

    s_none = compute_score(snapshot, sec_no_tax)
    s_low = compute_score(snapshot, sec_low)
    s_med = compute_score(snapshot, sec_medium)
    s_high = compute_score(snapshot, sec_high)

    assert s_low == s_none  # <=5% → no penalty
    assert s_med == s_none - 2  # >5% → -2
    assert s_high == s_none - 5  # >10% → -5


def test_score_bonding_curve_bonus():
    """Bonding curve fill % should add bonus points."""
    snapshot = _make_snapshot()
    s_base = compute_score(snapshot, None)
    s_low = compute_score(snapshot, None, bonding_curve_pct=10.0)
    s_mid = compute_score(snapshot, None, bonding_curve_pct=50.0)
    s_high = compute_score(snapshot, None, bonding_curve_pct=80.0)

    assert s_low == s_base  # <25% → no bonus
    assert s_mid == s_base + 3  # >=50% → +3
    assert s_high == s_base + 5  # >=80% → +5


def test_score_dbc_launchpad_trusted():
    """Trusted DBC launchpad should add bonus."""
    snapshot = _make_snapshot()
    s_base = compute_score(snapshot, None)
    s_believe = compute_score(snapshot, None, dbc_launchpad="believe")
    s_boop = compute_score(snapshot, None, dbc_launchpad="boop")
    s_unknown = compute_score(snapshot, None, dbc_launchpad="random_pad")

    assert s_believe == s_base + 3
    assert s_boop == s_base + 3
    assert s_unknown == s_base - 2


def test_score_combined_bonding_and_launchpad():
    """Bonding curve + trusted launchpad should stack."""
    snapshot = _make_snapshot()
    s_base = compute_score(snapshot, None)
    s_combo = compute_score(
        snapshot, None,
        bonding_curve_pct=90.0,
        dbc_launchpad="letsbonk",
    )
    assert s_combo == s_base + 5 + 3  # 5 (bc) + 3 (launchpad)


def test_score_lp_removal_penalty():
    """LP removal should penalise score harshly."""
    snapshot = _make_snapshot()
    s_base = compute_score(snapshot, None)
    s_minor = compute_score(snapshot, None, lp_removed_pct=20.0)
    s_major = compute_score(snapshot, None, lp_removed_pct=30.0)
    s_rug = compute_score(snapshot, None, lp_removed_pct=50.0)

    assert s_minor == s_base - 8
    assert s_major == s_base - 15
    assert s_rug == s_base - 25


def test_score_data_completeness_cap():
    """Too few data points → score capped at 40."""
    # Snapshot with only liquidity, no holders, no volume, no security, no smart money
    snapshot = _make_snapshot(
        holders_count=None,
        volume_1h=None,
    )
    # Only 1/6 data points available (liquidity)
    score = compute_score(snapshot, None)
    assert score <= 40
