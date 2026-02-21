"""Tests for Phase 12 scoring parameters in compute_score and compute_score_v3."""

from decimal import Decimal
from unittest.mock import MagicMock

from src.parsers.scoring import compute_score
from src.parsers.scoring_v3 import compute_score_v3


def _make_snapshot(**overrides):
    snap = MagicMock()
    snap.liquidity_usd = Decimal("50000")
    snap.dex_liquidity_usd = None
    snap.holders_count = 200
    snap.volume_1h = Decimal("60000")
    snap.dex_volume_1h = None
    snap.volume_5m = Decimal("5000")
    snap.dex_volume_5m = None
    snap.volume_24h = None
    snap.dex_volume_24h = None
    snap.buys_1h = 50
    snap.sells_1h = 20
    snap.buys_5m = 10
    snap.sells_5m = 5
    snap.buys_24h = None
    snap.sells_24h = None
    snap.smart_wallets_count = 2
    snap.top10_holders_pct = Decimal("25")
    snap.price = Decimal("0.001")
    snap.volatility_5m = None
    snap.lp_removed_pct = None
    for k, v in overrides.items():
        setattr(snap, k, v)
    return snap


def _make_security(**overrides):
    sec = MagicMock()
    sec.is_honeypot = False
    sec.is_mintable = False
    sec.lp_burned = True
    sec.lp_locked = False
    sec.contract_renounced = True
    sec.sell_tax = Decimal("0")
    sec.buy_tax = Decimal("0")
    sec.top10_holders_pct = Decimal("25")
    sec.lp_lock_duration_days = None
    for k, v in overrides.items():
        setattr(sec, k, v)
    return sec


class TestPhase12ScoringV2:
    def test_goplus_honeypot_instant_zero(self) -> None:
        """GoPlus honeypot = instant 0."""
        score = compute_score(
            _make_snapshot(), _make_security(), goplus_is_honeypot=True
        )
        assert score == 0

    def test_sell_sim_failed_penalty(self) -> None:
        """Sell sim failed = -20 pts penalty."""
        base = compute_score(_make_snapshot(), _make_security())
        penalized = compute_score(
            _make_snapshot(), _make_security(), sell_sim_failed=True
        )
        assert penalized == base - 20

    def test_bundled_buy_penalty(self) -> None:
        """Bundled buy = -10 pts penalty."""
        base = compute_score(_make_snapshot(), _make_security())
        penalized = compute_score(
            _make_snapshot(), _make_security(), bundled_buy_detected=True
        )
        assert penalized == base - 10

    def test_pumpfun_serial_deployer_penalty(self) -> None:
        """Serial deployer (10+ dead) = -15 pts."""
        base = compute_score(_make_snapshot(), _make_security())
        penalized = compute_score(
            _make_snapshot(), _make_security(), pumpfun_dead_tokens=10
        )
        assert penalized == base - 15

    def test_pumpfun_moderate_penalty(self) -> None:
        """Moderate dead tokens (5) = -10 pts."""
        base = compute_score(_make_snapshot(), _make_security())
        penalized = compute_score(
            _make_snapshot(), _make_security(), pumpfun_dead_tokens=5
        )
        assert penalized == base - 10

    def test_raydium_lp_burned_bonus(self) -> None:
        """Raydium LP burned = +3 pts."""
        base = compute_score(_make_snapshot(), _make_security())
        bonus = compute_score(
            _make_snapshot(), _make_security(), raydium_lp_burned=True
        )
        assert bonus == min(base + 3, 100)

    def test_raydium_lp_not_burned_penalty(self) -> None:
        """Raydium LP not burned = -2 pts."""
        base = compute_score(_make_snapshot(), _make_security())
        penalized = compute_score(
            _make_snapshot(), _make_security(), raydium_lp_burned=False
        )
        assert penalized == base - 2

    def test_mint_risk_boost(self) -> None:
        """PRE_SCAN risk boost subtracts from score."""
        base = compute_score(_make_snapshot(), _make_security())
        penalized = compute_score(
            _make_snapshot(), _make_security(), mint_risk_boost=15
        )
        assert penalized == base - 15


class TestPhase12ScoringV3:
    def test_goplus_honeypot_instant_zero(self) -> None:
        score = compute_score_v3(
            _make_snapshot(), _make_security(), goplus_is_honeypot=True
        )
        assert score == 0

    def test_sell_sim_failed_penalty(self) -> None:
        base = compute_score_v3(_make_snapshot(), _make_security())
        penalized = compute_score_v3(
            _make_snapshot(), _make_security(), sell_sim_failed=True
        )
        assert penalized == base - 25  # v3 is harsher

    def test_bundled_buy_penalty(self) -> None:
        base = compute_score_v3(_make_snapshot(), _make_security())
        penalized = compute_score_v3(
            _make_snapshot(), _make_security(), bundled_buy_detected=True
        )
        assert penalized == base - 12  # v3 penalty

    def test_combined_penalties_floor_at_zero(self) -> None:
        """Multiple penalties should floor at 0, not go negative."""
        score = compute_score(
            _make_snapshot(liquidity_usd=Decimal("5000"), holders_count=10),
            _make_security(),
            sell_sim_failed=True,
            bundled_buy_detected=True,
            pumpfun_dead_tokens=12,
            mint_risk_boost=30,
        )
        assert score == 0
