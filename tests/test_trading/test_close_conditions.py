"""Tests for close_conditions.check_close_conditions() pure function.

Covers: rug detection, take profit, trailing stop, stop loss, early stop,
timeout, liquidity removal with Phase 37 price-coherence guard, and no-close scenarios.
"""

from datetime import datetime, timedelta, UTC
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.trading.close_conditions import check_close_conditions


_SENTINEL = object()


def _make_position(
    *,
    entry_price: Decimal = Decimal("0.001"),
    current_price: Decimal | None = None,
    max_price: Decimal | None = None,
    opened_at: datetime | None | object = _SENTINEL,
    status: str = "open",
) -> SimpleNamespace:
    """Build a lightweight Position-like object for testing.

    Pass opened_at=None explicitly to get a position with opened_at=None.
    Omit opened_at to get datetime.now(UTC) as default.
    """
    return SimpleNamespace(
        entry_price=entry_price,
        current_price=current_price or entry_price,
        max_price=max_price,
        opened_at=datetime.now(UTC) if opened_at is _SENTINEL else opened_at,
        status=status,
    )


# ── Rug detection ──────────────────────────────────────────────────────


def test_close_rug_detected_returns_immediately():
    """Rug flag should close the position unconditionally."""
    pos = _make_position()
    result = check_close_conditions(
        pos, pos.entry_price, is_rug=True, now=datetime.now(UTC)
    )
    assert result == "rug"


def test_close_rug_ignores_profitable_position():
    """Even a profitable position is closed on rug detection."""
    pos = _make_position(
        entry_price=Decimal("0.001"),
        max_price=Decimal("0.010"),
    )
    result = check_close_conditions(
        pos, Decimal("0.005"), is_rug=True, now=datetime.now(UTC)
    )
    assert result == "rug"


# ── Take profit ────────────────────────────────────────────────────────


def test_close_take_profit_at_2x():
    """Position should close at 2x entry price (default take_profit_x)."""
    pos = _make_position(entry_price=Decimal("0.001"), max_price=Decimal("0.002"))
    result = check_close_conditions(
        pos, Decimal("0.002"), is_rug=False, now=datetime.now(UTC)
    )
    assert result == "take_profit"


def test_close_take_profit_above_2x():
    """Price exceeding 2x still triggers take_profit."""
    pos = _make_position(entry_price=Decimal("0.001"), max_price=Decimal("0.005"))
    result = check_close_conditions(
        pos, Decimal("0.005"), is_rug=False, now=datetime.now(UTC)
    )
    assert result == "take_profit"


def test_close_take_profit_custom_multiplier():
    """Custom take_profit_x=3.0 should require 3x for close."""
    pos = _make_position(entry_price=Decimal("0.001"), max_price=Decimal("0.002"))
    # At 2x with 3x threshold — should NOT close
    result = check_close_conditions(
        pos, Decimal("0.002"), is_rug=False, now=datetime.now(UTC), take_profit_x=3.0
    )
    assert result is None

    # At 3x — should close
    pos2 = _make_position(entry_price=Decimal("0.001"), max_price=Decimal("0.003"))
    result2 = check_close_conditions(
        pos2, Decimal("0.003"), is_rug=False, now=datetime.now(UTC), take_profit_x=3.0
    )
    assert result2 == "take_profit"


def test_close_take_profit_just_below_2x_no_close():
    """1.99x should NOT trigger take_profit."""
    pos = _make_position(entry_price=Decimal("1.00"), max_price=Decimal("1.99"))
    result = check_close_conditions(
        pos, Decimal("1.99"), is_rug=False, now=datetime.now(UTC)
    )
    assert result is None


# ── Trailing stop ──────────────────────────────────────────────────────


def test_close_trailing_stop_after_1_3x_with_20pct_drawdown():
    """After max hits 1.3x activation, a 20% drawdown from max triggers trailing_stop."""
    # Entry: 1.00, Max: 1.60 (1.6x > 1.3x activation), Current: 1.28 (20% > 15% drawdown)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.60"),
    )
    current = Decimal("1.28")  # (1.60 - 1.28) / 1.60 = 0.20 = 20% drawdown
    result = check_close_conditions(
        pos, current, is_rug=False, now=datetime.now(UTC)
    )
    assert result == "trailing_stop"


def test_close_trailing_stop_above_20pct_drawdown():
    """25% drawdown from max (still above stop_loss) triggers trailing_stop."""
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("2.00"),  # 2x max
    )
    current = Decimal("1.50")  # (2.00 - 1.50) / 2.00 = 25% drawdown, pnl_pct=+50%
    result = check_close_conditions(
        pos, current, is_rug=False, now=datetime.now(UTC)
    )
    assert result == "trailing_stop"


def test_close_trailing_stop_extreme_loss_becomes_stop_loss():
    """If trailing-stop drawdown but PnL <= stop_loss, reason is stop_loss."""
    # Entry: 1.00, Max: 1.60 (>1.5x), Current: 0.40 (pnl=-60%, below -50% SL)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.60"),
    )
    current = Decimal("0.40")  # drawdown=75%, pnl=-60%
    result = check_close_conditions(
        pos, current, is_rug=False, now=datetime.now(UTC)
    )
    assert result == "stop_loss"


def test_close_trailing_stop_not_triggered_below_activation_max():
    """If max never reached trailing_activation_x (1.3x default), trailing stop should not apply."""
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.25"),  # only 1.25x, below 1.3x activation
    )
    current = Decimal("1.00")  # 20% drawdown from max, but max < 1.3x
    result = check_close_conditions(
        pos, current, is_rug=False, now=datetime.now(UTC)
    )
    # Should not be trailing_stop (1.25x < 1.3x threshold)
    assert result is None


def test_close_trailing_stop_not_triggered_small_drawdown():
    """14% drawdown from max after 1.3x should NOT trigger trailing_stop (threshold is 15%)."""
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("2.00"),  # 2x
    )
    current = Decimal("1.72")  # (2.00 - 1.72) / 2.00 = 14% drawdown, below 15%
    result = check_close_conditions(
        pos, current, is_rug=False, now=datetime.now(UTC)
    )
    assert result is None


# ── Stop loss ──────────────────────────────────────────────────────────


def test_close_stop_loss_at_minus_50pct():
    """Position should close at -50% from entry."""
    pos = _make_position(entry_price=Decimal("1.00"), max_price=Decimal("1.00"))
    result = check_close_conditions(
        pos, Decimal("0.50"), is_rug=False, now=datetime.now(UTC)
    )
    assert result == "stop_loss"


def test_close_stop_loss_below_minus_50pct():
    """Deeper than -50% still triggers stop_loss."""
    pos = _make_position(entry_price=Decimal("1.00"), max_price=Decimal("1.00"))
    result = check_close_conditions(
        pos, Decimal("0.30"), is_rug=False, now=datetime.now(UTC)
    )
    assert result == "stop_loss"


def test_close_stop_loss_custom_threshold():
    """Custom stop_loss_pct=-30% changes the threshold."""
    pos = _make_position(entry_price=Decimal("1.00"), max_price=Decimal("1.00"))
    # -35% should trigger with -30% threshold
    result = check_close_conditions(
        pos,
        Decimal("0.65"),
        is_rug=False,
        now=datetime.now(UTC),
        stop_loss_pct=-30.0,
    )
    assert result == "stop_loss"


def test_close_stop_loss_not_triggered_at_minus_49pct():
    """-49% should NOT trigger stop_loss (threshold is -50%).
    But Phase 31B stagnation fires: age=1h > 25min, PnL=-49% < 15%.
    """
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(hours=1),  # past early-stop window
    )
    result = check_close_conditions(
        pos, Decimal("0.51"), is_rug=False, now=now
    )
    # Phase 31B: 1h > 25min AND -49% < 15% → stagnation
    assert result == "stagnation"


# ── Early stop ─────────────────────────────────────────────────────────


def test_close_early_stop_minus_20pct_within_30min():
    """If -20% within 30 minutes, close early."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(minutes=15),
    )
    result = check_close_conditions(
        pos, Decimal("0.80"), is_rug=False, now=now
    )
    assert result == "early_stop"


def test_close_early_stop_minus_25pct_within_30min():
    """Deeper loss (-25%) within 30 min also triggers early_stop."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(minutes=5),
    )
    result = check_close_conditions(
        pos, Decimal("0.75"), is_rug=False, now=now
    )
    assert result == "early_stop"


def test_close_early_stop_not_triggered_after_30min():
    """At -20% but 31 minutes old — early_stop should NOT apply.
    Note: stagnation (Phase 31B) fires instead since age>25min and PnL<15%.
    """
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(minutes=31),
    )
    result = check_close_conditions(
        pos, Decimal("0.80"), is_rug=False, now=now
    )
    # Phase 31B: 31min > 25min stagnation timeout AND -20% < 15% max PnL → stagnation
    assert result == "stagnation"


def test_close_early_stop_not_triggered_small_loss():
    """-10% loss within 30 min should NOT trigger early_stop."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(minutes=10),
    )
    result = check_close_conditions(
        pos, Decimal("0.90"), is_rug=False, now=now
    )
    assert result is None


# ── Stagnation exit (Phase 31B) ────────────────────────────────────────


def test_close_stagnation_after_25min_low_pnl():
    """Position open >25min with PnL <15% → stagnation close."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.10"),
        opened_at=now - timedelta(minutes=26),
    )
    result = check_close_conditions(
        pos, Decimal("1.10"), is_rug=False, now=now
    )
    assert result == "stagnation"


def test_close_stagnation_negative_pnl():
    """Position open >25min with negative PnL → stagnation close."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.05"),
        opened_at=now - timedelta(minutes=30),
    )
    result = check_close_conditions(
        pos, Decimal("0.95"), is_rug=False, now=now
    )
    assert result == "stagnation"


def test_no_stagnation_before_25min():
    """Position at 24min with low PnL → NOT stagnation (too young)."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.05"),
        opened_at=now - timedelta(minutes=24),
    )
    result = check_close_conditions(
        pos, Decimal("1.05"), is_rug=False, now=now
    )
    assert result is None


def test_no_stagnation_high_pnl():
    """Position open >25min but PnL ≥15% → NOT stagnation (doing well)."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.20"),
        opened_at=now - timedelta(minutes=30),
    )
    # PnL = +20% → above 15% threshold → no stagnation
    result = check_close_conditions(
        pos, Decimal("1.20"), is_rug=False, now=now
    )
    assert result is None


def test_close_stagnation_exactly_25min():
    """Position at exactly 25min with low PnL → stagnation."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.10"),
        opened_at=now - timedelta(minutes=25),
    )
    result = check_close_conditions(
        pos, Decimal("1.10"), is_rug=False, now=now
    )
    assert result == "stagnation"


def test_close_stagnation_custom_params():
    """Custom stagnation_timeout_min=10 and stagnation_max_pnl_pct=5."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.04"),
        opened_at=now - timedelta(minutes=11),
    )
    result = check_close_conditions(
        pos, Decimal("1.04"), is_rug=False, now=now,
        stagnation_timeout_min=10.0,
        stagnation_max_pnl_pct=5.0,
    )
    assert result == "stagnation"


def test_no_stagnation_when_disabled():
    """Stagnation disabled (timeout=0) → no stagnation close."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.05"),
        opened_at=now - timedelta(hours=2),
    )
    result = check_close_conditions(
        pos, Decimal("1.05"), is_rug=False, now=now,
        stagnation_timeout_min=0,
    )
    # Should not close for stagnation (disabled); 2h < 8h timeout
    assert result is None


def test_stagnation_does_not_override_stop_loss():
    """Stop loss (-50%) fires before stagnation check."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(minutes=30),
    )
    result = check_close_conditions(
        pos, Decimal("0.50"), is_rug=False, now=now
    )
    assert result == "stop_loss"


def test_stagnation_profitable_position_borderline():
    """Position at exactly 15% PnL and >25min → NOT stagnation (< threshold, not ≤)."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.15"),
        opened_at=now - timedelta(minutes=30),
    )
    # PnL = exactly +15% → not < 15% → no stagnation
    result = check_close_conditions(
        pos, Decimal("1.15"), is_rug=False, now=now
    )
    assert result is None


# ── Trailing stop with custom params (Phase 31C) ─────────────────────


def test_trailing_stop_custom_activation_1_5x():
    """Custom trailing_activation_x=1.5 → max at 1.4x should NOT trigger."""
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.40"),
    )
    current = Decimal("1.10")  # 21% drawdown from max, but max < 1.5x
    result = check_close_conditions(
        pos, current, is_rug=False, now=datetime.now(UTC),
        trailing_activation_x=1.5,
        trailing_drawdown_pct=20.0,
    )
    assert result is None


def test_trailing_stop_custom_activation_1_5x_triggers():
    """Custom trailing_activation_x=1.5 → max at 1.6x + 20% drawdown triggers."""
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.60"),
    )
    current = Decimal("1.28")  # 20% drawdown from 1.60
    result = check_close_conditions(
        pos, current, is_rug=False, now=datetime.now(UTC),
        trailing_activation_x=1.5,
        trailing_drawdown_pct=20.0,
    )
    assert result == "trailing_stop"


def test_trailing_stop_at_1_3x_activation_15pct_drawdown():
    """Default Phase 31C params: 1.3x activation, 15% drawdown."""
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.40"),  # 1.4x > 1.3x activation
    )
    current = Decimal("1.19")  # (1.40 - 1.19) / 1.40 = 15% drawdown
    result = check_close_conditions(
        pos, current, is_rug=False, now=datetime.now(UTC)
    )
    assert result == "trailing_stop"


def test_trailing_stop_at_1_3x_activation_14pct_no_trigger():
    """14% drawdown after 1.3x activation → NOT trailing_stop."""
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.40"),  # 1.4x > 1.3x activation
    )
    current = Decimal("1.21")  # (1.40 - 1.21) / 1.40 = 13.6% drawdown
    result = check_close_conditions(
        pos, current, is_rug=False, now=datetime.now(UTC)
    )
    assert result is None


# ── Timeout ────────────────────────────────────────────────────────────


def test_close_timeout_after_8_hours():
    """Position should close after 8 hours (default timeout).
    With stagnation disabled, timeout fires for old positions with 0% PnL.
    """
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(hours=8, minutes=1),
    )
    # Disable stagnation to test pure timeout
    result = check_close_conditions(
        pos, Decimal("1.00"), is_rug=False, now=now,
        stagnation_timeout_min=0,
    )
    assert result == "timeout"


def test_close_timeout_with_stagnation_fires_stagnation_first():
    """8h position with 0% PnL and stagnation enabled → stagnation (before timeout)."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(hours=8, minutes=1),
    )
    result = check_close_conditions(
        pos, Decimal("1.00"), is_rug=False, now=now
    )
    # Stagnation fires first: 8h > 25min AND 0% < 15%
    assert result == "stagnation"


def test_close_timeout_high_pnl_position():
    """8h position with PnL ≥15% → timeout (stagnation doesn't apply)."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.20"),
        opened_at=now - timedelta(hours=8, minutes=1),
    )
    result = check_close_conditions(
        pos, Decimal("1.20"), is_rug=False, now=now
    )
    # PnL=+20% >= 15% → no stagnation → timeout
    assert result == "timeout"


def test_close_timeout_custom_hours():
    """Custom timeout_hours=4 shortens the window."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(hours=4, minutes=1),
    )
    # Disable stagnation to test pure timeout
    result = check_close_conditions(
        pos, Decimal("1.00"), is_rug=False, now=now,
        timeout_hours=4, stagnation_timeout_min=0,
    )
    assert result == "timeout"


def test_close_timeout_not_reached():
    """Position within timeout window and above stagnation PnL → no close."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.20"),
        opened_at=now - timedelta(hours=7),
    )
    # PnL=+20% >= 15% → no stagnation; 7h < 8h → no timeout
    result = check_close_conditions(
        pos, Decimal("1.20"), is_rug=False, now=now
    )
    assert result is None


# ── No close ───────────────────────────────────────────────────────────


def test_no_close_normal_price():
    """Normal price movement within first 20 min should return None."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.20"),
        opened_at=now - timedelta(minutes=20),
    )
    result = check_close_conditions(
        pos, Decimal("1.10"), is_rug=False, now=now
    )
    assert result is None


def test_no_close_young_position_small_loss():
    """Young position with -10% loss should NOT close."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.05"),
        opened_at=now - timedelta(minutes=10),
    )
    result = check_close_conditions(
        pos, Decimal("0.90"), is_rug=False, now=now
    )
    assert result is None


def test_no_close_zero_entry_price():
    """Zero entry_price should skip all price-based checks, only timeout applies."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("0"),
        opened_at=now - timedelta(hours=1),
    )
    result = check_close_conditions(
        pos, Decimal("0.50"), is_rug=False, now=now
    )
    assert result is None


def test_no_close_none_max_price_skips_trailing():
    """None max_price should skip trailing stop logic."""
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=None,
        opened_at=datetime.now(UTC) - timedelta(hours=1),
    )
    # Without trailing stop, +50% is not enough for take_profit (needs 2x)
    result = check_close_conditions(
        pos, Decimal("1.50"), is_rug=False, now=datetime.now(UTC)
    )
    assert result is None


# ── Priority order ─────────────────────────────────────────────────────


def test_close_rug_takes_priority_over_take_profit():
    """Rug should override take_profit even at 5x."""
    pos = _make_position(entry_price=Decimal("1.00"), max_price=Decimal("5.00"))
    result = check_close_conditions(
        pos, Decimal("5.00"), is_rug=True, now=datetime.now(UTC)
    )
    assert result == "rug"


def test_close_take_profit_before_trailing_stop():
    """At exactly 2x, take_profit fires before trailing stop could apply."""
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("2.50"),  # max above 1.5x
    )
    # Price is 2.0 (2x entry, 20% below max) — both TP and trailing could fire
    # take_profit should fire first in the code
    result = check_close_conditions(
        pos, Decimal("2.00"), is_rug=False, now=datetime.now(UTC)
    )
    assert result == "take_profit"


# ── Liquidity removed (with Phase 37 price-coherence guard) ───────────


def test_close_liq_removed_price_crashed():
    """Zero liq + price crashed below 50% → liquidity_removed."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(hours=1),
    )
    result = check_close_conditions(
        pos, Decimal("0.10"), is_rug=False, now=now,
        liquidity_usd=0.0,
    )
    assert result == "liquidity_removed"


def test_close_liq_low_price_crashed():
    """Low liq ($2K) + price crashed below 50% → liquidity_removed."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(hours=1),
    )
    result = check_close_conditions(
        pos, Decimal("0.30"), is_rug=False, now=now,
        liquidity_usd=2000.0,
    )
    assert result == "liquidity_removed"


def test_skip_liq_removed_when_price_healthy():
    """Phase 37: Zero liq but price at entry → skip liq_removed (bad data, not rug).
    Uses young position (<25min) to avoid stagnation triggering.
    """
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(minutes=10),
    )
    result = check_close_conditions(
        pos, Decimal("1.00"), is_rug=False, now=now,
        liquidity_usd=0.0,
    )
    # Price healthy (100% of entry >= 50%), liq data unreliable → skip
    assert result is None


def test_skip_liq_removed_when_price_above_50pct():
    """Phase 37: Near-zero liq but price at 60% of entry → skip liq_removed.
    Disables stagnation to isolate the price-coherence test."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),  # no trailing stop
        opened_at=now - timedelta(hours=1),  # past early-stop window
    )
    result = check_close_conditions(
        pos, Decimal("0.60"), is_rug=False, now=now,
        liquidity_usd=50.0,
        stagnation_timeout_min=0,  # disable stagnation for this test
    )
    # Price healthy (60% >= 50%) → skip liq_removed
    # -40% loss doesn't hit stop_loss (-50%) or early_stop (>30min)
    assert result is None


def test_close_liq_removed_price_at_49pct():
    """Price at 49% of entry + low liq → liquidity_removed (below 50% threshold)."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(hours=1),
    )
    result = check_close_conditions(
        pos, Decimal("0.49"), is_rug=False, now=now,
        liquidity_usd=100.0,
    )
    assert result == "liquidity_removed"


def test_skip_liq_removed_profitable_position():
    """Phase 37: Low liq but profitable position → skip (SXAL/LOBCHURCH case)."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("0.00003"),
        max_price=Decimal("0.00004"),  # max below 1.5x so no trailing_stop
        opened_at=now - timedelta(minutes=10),
    )
    # Price above entry — token is alive, Birdeye liq data is bonding curve
    result = check_close_conditions(
        pos, Decimal("0.00004"), is_rug=False, now=now,
        liquidity_usd=0.02,  # Birdeye bonding curve liq
    )
    assert result is None


def test_close_liq_removed_with_take_profit_level_price():
    """At 3x price with zero liq → price is healthy, skip liq_removed, fire take_profit."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("3.00"),
        opened_at=now - timedelta(hours=1),
    )
    result = check_close_conditions(
        pos, Decimal("3.00"), is_rug=False, now=now,
        liquidity_usd=0.0,
    )
    # Price healthy → skip liq_removed → take_profit fires
    assert result == "take_profit"


def test_close_liquidity_none_does_not_trigger():
    """None liquidity should NOT trigger close (unknown, not zero)."""
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    result = check_close_conditions(
        pos, Decimal("1.00"), is_rug=False, now=datetime.now(UTC),
        liquidity_usd=None,
    )
    assert result is None


def test_close_liquidity_above_threshold_no_close():
    """Liquidity above $5000 should NOT trigger close."""
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    result = check_close_conditions(
        pos, Decimal("1.00"), is_rug=False, now=datetime.now(UTC),
        liquidity_usd=5000.0,
    )
    assert result is None


# ── Phase 36: Grace period (still works within Phase 37) ──────────────


def test_close_liq_zero_fresh_grace_period():
    """Fresh position (<90s) with zero liquidity → NOT closed (indexing lag)."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.20"),
        opened_at=now - timedelta(seconds=30),
    )
    result = check_close_conditions(
        pos, Decimal("1.20"), is_rug=False, now=now,
        liquidity_usd=0.0,
    )
    assert result is None


def test_close_liq_zero_after_grace_price_crashed():
    """Past grace + zero liq + price crashed → liquidity_removed."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(seconds=200),
    )
    result = check_close_conditions(
        pos, Decimal("0.10"), is_rug=False, now=now,
        liquidity_usd=0.0,
    )
    assert result == "liquidity_removed"


def test_close_liq_zero_after_grace_price_healthy():
    """Past grace + zero liq but price healthy → skip (Phase 37)."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(seconds=200),
    )
    result = check_close_conditions(
        pos, Decimal("1.00"), is_rug=False, now=now,
        liquidity_usd=0.0,
    )
    assert result is None


def test_close_liq_zero_at_exactly_90s():
    """Position exactly at 90s with zero liq → still within grace (<=90)."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(seconds=90),
    )
    result = check_close_conditions(
        pos, Decimal("1.00"), is_rug=False, now=now,
        liquidity_usd=0.0,
    )
    assert result is None


def test_close_liq_none_no_close():
    """None liquidity (no data at all) → never triggers close."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.50"),
        opened_at=now - timedelta(seconds=5),
    )
    result = check_close_conditions(
        pos, Decimal("1.50"), is_rug=False, now=now,
        liquidity_usd=None,
    )
    assert result is None


def test_close_custom_grace_period_45s_price_crashed():
    """Custom grace 45s: position at 50s + price crashed → liquidity_removed."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(seconds=50),
    )
    result = check_close_conditions(
        pos, Decimal("0.10"), is_rug=False, now=now,
        liquidity_usd=0.0,
        liquidity_grace_period_sec=45,
    )
    assert result == "liquidity_removed"


def test_close_liq_zero_no_opened_at_price_crashed():
    """Zero liq + no opened_at + price crashed → liquidity_removed."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=None,
    )
    result = check_close_conditions(
        pos, Decimal("0.10"), is_rug=False, now=now,
        liquidity_usd=0.0,
    )
    assert result == "liquidity_removed"


def test_close_liq_zero_no_opened_at_price_healthy():
    """Zero liq + no opened_at but price healthy → skip (Phase 37)."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=None,
    )
    result = check_close_conditions(
        pos, Decimal("1.00"), is_rug=False, now=now,
        liquidity_usd=0.0,
    )
    assert result is None


# ── Phase 37: Edge cases for price-coherence guard ────────────────────


def test_phase37_exactly_50pct_price_is_healthy():
    """Price at exactly 50% of entry is the boundary — considered healthy.

    Price-coherence guard skips liq_removed, but -50% PnL hits stop_loss.
    Use 0.51 to stay above stop_loss threshold and prove liq check is skipped.
    Disables stagnation to isolate the price-coherence test.
    """
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(hours=1),
    )
    result = check_close_conditions(
        pos, Decimal("0.51"), is_rug=False, now=now,
        liquidity_usd=0.0,
        stagnation_timeout_min=0,  # disable stagnation for this test
    )
    # 0.51 >= 1.00 * 0.5 → price_healthy → skip liq_removed
    # pnl=-49% → doesn't hit stop_loss (-50%)
    assert result is None


def test_phase37_just_below_50pct_triggers_close():
    """Price at 49.9% of entry + zero liq → liquidity_removed."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(hours=1),
    )
    result = check_close_conditions(
        pos, Decimal("0.499"), is_rug=False, now=now,
        liquidity_usd=0.0,
    )
    assert result == "liquidity_removed"


def test_phase37_zero_entry_price_no_coherence_check():
    """Zero entry_price can't compute coherence → falls through to liq check."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("0"),
        max_price=Decimal("0"),
        opened_at=now - timedelta(hours=1),
    )
    result = check_close_conditions(
        pos, Decimal("0.50"), is_rug=False, now=now,
        liquidity_usd=0.0,
    )
    # entry_price=0 → price_healthy=False → zero liq, no opened_at issue → close
    assert result == "liquidity_removed"


def test_phase37_liq_4999_price_healthy_skips():
    """Liq=$4999 (just below $5K) but price healthy → skip.

    Use max_price=1.40 (below 1.5x trailing threshold) to avoid trailing_stop.
    """
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.40"),  # below 1.5x → no trailing stop
        opened_at=now - timedelta(hours=1),
    )
    result = check_close_conditions(
        pos, Decimal("1.20"), is_rug=False, now=now,
        liquidity_usd=4999.0,
    )
    assert result is None


def test_phase37_liq_4999_price_crashed_closes():
    """Liq=$4999 + price at 30% → liquidity_removed."""
    now = datetime.now(UTC)
    pos = _make_position(
        entry_price=Decimal("1.00"),
        max_price=Decimal("1.00"),
        opened_at=now - timedelta(hours=1),
    )
    result = check_close_conditions(
        pos, Decimal("0.30"), is_rug=False, now=now,
        liquidity_usd=4999.0,
    )
    assert result == "liquidity_removed"
