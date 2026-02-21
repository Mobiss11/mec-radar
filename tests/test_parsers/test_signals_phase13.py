"""Tests for Phase 13 signal rules R29-R36."""

from unittest.mock import MagicMock

from src.parsers.signals import evaluate_signals


def _make_snapshot(**overrides):
    snap = MagicMock()
    snap.liquidity_usd = 50000
    snap.dex_liquidity_usd = None
    snap.holders_count = 100
    snap.market_cap = 100000
    snap.score = 40
    snap.volume_1h = 10000
    snap.dex_volume_1h = None
    snap.volume_5m = 2000
    snap.dex_volume_5m = None
    snap.volume_24h = None
    snap.buys_1h = 50
    snap.sells_1h = 20
    snap.buys_5m = 10
    snap.sells_5m = 5
    snap.buys_24h = None
    snap.sells_24h = None
    snap.smart_wallets_count = 0
    snap.top10_holders_pct = None
    snap.price = 0.001
    snap.volatility_5m = None
    snap.lp_removed_pct = None
    for k, v in overrides.items():
        setattr(snap, k, v)
    return snap


def _make_security(**overrides):
    sec = MagicMock()
    sec.is_honeypot = False
    sec.lp_burned = False
    sec.lp_locked = False
    sec.contract_renounced = True
    sec.sell_tax = None
    sec.buy_tax = None
    for k, v in overrides.items():
        setattr(sec, k, v)
    return sec


class TestR29NoSocials:
    def test_fires_on_no_socials(self) -> None:
        result = evaluate_signals(
            _make_snapshot(), _make_security(), metadata_score=-3
        )
        names = [r.name for r in result.rules_fired]
        assert "no_socials" in names

    def test_no_fire_with_socials(self) -> None:
        result = evaluate_signals(
            _make_snapshot(), _make_security(), metadata_score=5
        )
        names = [r.name for r in result.rules_fired]
        assert "no_socials" not in names


class TestR30WashTrading:
    def test_fires_on_wash_trading(self) -> None:
        result = evaluate_signals(
            _make_snapshot(), _make_security(), wash_trading_suspected=True
        )
        names = [r.name for r in result.rules_fired]
        assert "wash_trading_pnl" in names
        rule = next(r for r in result.rules_fired if r.name == "wash_trading_pnl")
        assert rule.weight == -3


class TestR31GoPlusCritical:
    def test_fires_on_critical_flags(self) -> None:
        result = evaluate_signals(
            _make_snapshot(), _make_security(),
            goplus_critical_flags=["is_mintable", "owner_can_change_balance"],
        )
        names = [r.name for r in result.rules_fired]
        assert "goplus_critical_risk" in names
        rule = next(r for r in result.rules_fired if r.name == "goplus_critical_risk")
        assert rule.weight == -5

    def test_no_fire_on_empty_flags(self) -> None:
        result = evaluate_signals(
            _make_snapshot(), _make_security(), goplus_critical_flags=[]
        )
        names = [r.name for r in result.rules_fired]
        assert "goplus_critical_risk" not in names


class TestR32RugcheckMultiDanger:
    def test_fires_on_3_dangers(self) -> None:
        result = evaluate_signals(
            _make_snapshot(), _make_security(), rugcheck_danger_count=3
        )
        names = [r.name for r in result.rules_fired]
        assert "rugcheck_multi_danger" in names

    def test_no_fire_on_2_dangers(self) -> None:
        result = evaluate_signals(
            _make_snapshot(), _make_security(), rugcheck_danger_count=2
        )
        names = [r.name for r in result.rules_fired]
        assert "rugcheck_multi_danger" not in names


class TestR33LowDecentralization:
    def test_fires_on_low_score(self) -> None:
        result = evaluate_signals(
            _make_snapshot(), _make_security(), bubblemaps_decentralization=0.2
        )
        names = [r.name for r in result.rules_fired]
        assert "low_decentralization" in names

    def test_no_fire_on_good_score(self) -> None:
        result = evaluate_signals(
            _make_snapshot(), _make_security(), bubblemaps_decentralization=0.7
        )
        names = [r.name for r in result.rules_fired]
        assert "low_decentralization" not in names


class TestR34FeePayerSybil:
    def test_fires_on_high_sybil(self) -> None:
        result = evaluate_signals(
            _make_snapshot(), _make_security(), fee_payer_sybil_score=0.7
        )
        names = [r.name for r in result.rules_fired]
        assert "fee_payer_sybil" in names
        rule = next(r for r in result.rules_fired if r.name == "fee_payer_sybil")
        assert rule.weight == -6

    def test_no_fire_on_low_sybil(self) -> None:
        result = evaluate_signals(
            _make_snapshot(), _make_security(), fee_payer_sybil_score=0.2
        )
        names = [r.name for r in result.rules_fired]
        assert "fee_payer_sybil" not in names


class TestR35FundingChain:
    def test_fires_on_suspicious_chain(self) -> None:
        result = evaluate_signals(
            _make_snapshot(), _make_security(), funding_chain_risk=70
        )
        names = [r.name for r in result.rules_fired]
        assert "funding_chain_suspicious" in names

    def test_no_fire_on_low_risk(self) -> None:
        result = evaluate_signals(
            _make_snapshot(), _make_security(), funding_chain_risk=30
        )
        names = [r.name for r in result.rules_fired]
        assert "funding_chain_suspicious" not in names


class TestR36TokenConvergence:
    def test_fires_on_convergence(self) -> None:
        result = evaluate_signals(
            _make_snapshot(), _make_security(), convergence_detected=True
        )
        names = [r.name for r in result.rules_fired]
        assert "token_convergence" in names
        rule = next(r for r in result.rules_fired if r.name == "token_convergence")
        assert rule.weight == -5

    def test_no_fire_without_convergence(self) -> None:
        result = evaluate_signals(
            _make_snapshot(), _make_security(), convergence_detected=False
        )
        names = [r.name for r in result.rules_fired]
        assert "token_convergence" not in names
