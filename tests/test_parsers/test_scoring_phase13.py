"""Tests for Phase 13 scoring parameters in compute_score and compute_score_v3."""

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


class TestPhase13ScoringV2:
    def test_sybil_high_penalty(self) -> None:
        """High sybil score (>0.7) = -15 pts."""
        base = compute_score(_make_snapshot(), _make_security())
        penalized = compute_score(
            _make_snapshot(), _make_security(), fee_payer_sybil_score=0.8
        )
        assert penalized == base - 15

    def test_sybil_medium_penalty(self) -> None:
        """Medium sybil score (0.5-0.7) = -8 pts."""
        base = compute_score(_make_snapshot(), _make_security())
        penalized = compute_score(
            _make_snapshot(), _make_security(), fee_payer_sybil_score=0.6
        )
        assert penalized == base - 8

    def test_funding_chain_high_risk(self) -> None:
        """High funding chain risk (>=80) = -12 pts."""
        base = compute_score(_make_snapshot(), _make_security())
        penalized = compute_score(
            _make_snapshot(), _make_security(), funding_chain_risk=90
        )
        assert penalized == base - 12

    def test_convergence_penalty(self) -> None:
        """Token convergence = -15 pts."""
        base = compute_score(_make_snapshot(), _make_security())
        penalized = compute_score(
            _make_snapshot(), _make_security(), convergence_detected=True
        )
        assert penalized == base - 15

    def test_metadata_bonus(self) -> None:
        """Full socials (score >= 7) = +4 pts."""
        base = compute_score(_make_snapshot(), _make_security())
        bonus = compute_score(
            _make_snapshot(), _make_security(), metadata_score=9
        )
        assert bonus == min(base + 4, 100)

    def test_metadata_penalty(self) -> None:
        """No socials (score <= -3) = -3 pts."""
        base = compute_score(_make_snapshot(), _make_security())
        penalized = compute_score(
            _make_snapshot(), _make_security(), metadata_score=-3
        )
        assert penalized == base - 3

    def test_wash_trading_penalty(self) -> None:
        """Wash trading suspected = -10 pts."""
        base = compute_score(_make_snapshot(), _make_security())
        penalized = compute_score(
            _make_snapshot(), _make_security(), wash_trading_suspected=True
        )
        assert penalized == base - 10

    def test_rugcheck_danger_high(self) -> None:
        """3+ rugcheck dangers = -12 pts."""
        base = compute_score(_make_snapshot(), _make_security())
        penalized = compute_score(
            _make_snapshot(), _make_security(), rugcheck_danger_count=3
        )
        assert penalized == base - 12

    def test_bubblemaps_low_decentralization(self) -> None:
        """Low decentralization (<0.3) = -8 pts."""
        base = compute_score(_make_snapshot(), _make_security())
        penalized = compute_score(
            _make_snapshot(), _make_security(), bubblemaps_decentralization=0.2
        )
        assert penalized == base - 8

    def test_bubblemaps_high_decentralization(self) -> None:
        """High decentralization (>=0.7) = +3 pts."""
        base = compute_score(_make_snapshot(), _make_security())
        bonus = compute_score(
            _make_snapshot(), _make_security(), bubblemaps_decentralization=0.8
        )
        assert bonus == min(base + 3, 100)

    def test_solsniffer_high_score_bonus(self) -> None:
        """SolSniffer score >= 80 = +3 pts."""
        base = compute_score(_make_snapshot(), _make_security())
        bonus = compute_score(
            _make_snapshot(), _make_security(), solsniffer_score=85
        )
        assert bonus == min(base + 3, 100)

    def test_solsniffer_low_score_penalty(self) -> None:
        """SolSniffer score < 30 = -5 pts."""
        base = compute_score(_make_snapshot(), _make_security())
        penalized = compute_score(
            _make_snapshot(), _make_security(), solsniffer_score=20
        )
        assert penalized == base - 5


class TestPhase13ScoringV3:
    def test_sybil_high_penalty_harsher(self) -> None:
        """V3: high sybil (>0.7) = -18 pts (harsher)."""
        base = compute_score_v3(_make_snapshot(), _make_security())
        penalized = compute_score_v3(
            _make_snapshot(), _make_security(), fee_payer_sybil_score=0.8
        )
        assert penalized == base - 18

    def test_convergence_harsher(self) -> None:
        """V3: convergence = -18 pts (harsher)."""
        base = compute_score_v3(_make_snapshot(), _make_security())
        penalized = compute_score_v3(
            _make_snapshot(), _make_security(), convergence_detected=True
        )
        assert penalized == base - 18

    def test_wash_trading_harsher(self) -> None:
        """V3: wash trading = -12 pts."""
        base = compute_score_v3(_make_snapshot(), _make_security())
        penalized = compute_score_v3(
            _make_snapshot(), _make_security(), wash_trading_suspected=True
        )
        assert penalized == base - 12

    def test_combined_phase13_penalties_floor(self) -> None:
        """Combined Phase 13 penalties floor at 0."""
        score = compute_score(
            _make_snapshot(liquidity_usd=Decimal("5000"), holders_count=10, smart_wallets_count=0),
            _make_security(lp_burned=False, contract_renounced=False),
            fee_payer_sybil_score=0.9,
            convergence_detected=True,
            wash_trading_suspected=True,
            rugcheck_danger_count=5,
            bubblemaps_decentralization=0.1,
        )
        assert score == 0
