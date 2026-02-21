"""Tests for Phase 15B bullish velocity signal rules R43-R47."""

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
    snap.jupiter_price = None
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


def _rule_names(result):
    return [r.name for r in result.rules_fired]


class TestR43ExplosiveBuyVelocity:
    """R43: explosive_buy_velocity — 50+ buys in 5m."""

    def test_fires_on_100_buys_5m(self) -> None:
        result = evaluate_signals(
            _make_snapshot(buys_5m=120, sells_5m=10),
            _make_security(),
        )
        assert "explosive_buy_velocity" in _rule_names(result)

    def test_no_fire_below_threshold(self) -> None:
        result = evaluate_signals(
            _make_snapshot(buys_5m=40, sells_5m=10),
            _make_security(),
        )
        assert "explosive_buy_velocity" not in _rule_names(result)

    def test_weight_is_3(self) -> None:
        result = evaluate_signals(
            _make_snapshot(buys_5m=200, sells_5m=5),
            _make_security(),
        )
        rule = next(r for r in result.rules_fired if r.name == "explosive_buy_velocity")
        assert rule.weight == 3


class TestR44HolderAcceleration:
    """R44: holder_acceleration — rapid holder growth vs token age."""

    def test_fires_on_fast_growth(self) -> None:
        # 100 holders in 2 minutes = 50 holders/min
        result = evaluate_signals(
            _make_snapshot(holders_count=100),
            _make_security(),
            token_age_minutes=2.0,
        )
        assert "holder_acceleration" in _rule_names(result)

    def test_no_fire_slow_growth(self) -> None:
        # 100 holders in 60 minutes = 1.67 holders/min
        result = evaluate_signals(
            _make_snapshot(holders_count=100),
            _make_security(),
            token_age_minutes=60.0,
        )
        assert "holder_acceleration" not in _rule_names(result)

    def test_no_fire_without_age(self) -> None:
        result = evaluate_signals(
            _make_snapshot(holders_count=200),
            _make_security(),
        )
        assert "holder_acceleration" not in _rule_names(result)

    def test_no_fire_too_few_holders(self) -> None:
        # 5 holders / 0.1 min = 50/min but holders < 10
        result = evaluate_signals(
            _make_snapshot(holders_count=5),
            _make_security(),
            token_age_minutes=0.1,
        )
        assert "holder_acceleration" not in _rule_names(result)


class TestR45SmartMoneyEarlyEntry:
    """R45: smart_money_early_entry — 3+ smart wallets within 10m."""

    def test_fires_on_early_smart_money(self) -> None:
        result = evaluate_signals(
            _make_snapshot(smart_wallets_count=4),
            _make_security(),
            token_age_minutes=5.0,
        )
        assert "smart_money_early_entry" in _rule_names(result)

    def test_no_fire_late_entry(self) -> None:
        # 4 smart wallets but token is 30 minutes old
        result = evaluate_signals(
            _make_snapshot(smart_wallets_count=4),
            _make_security(),
            token_age_minutes=30.0,
        )
        assert "smart_money_early_entry" not in _rule_names(result)

    def test_no_fire_too_few_smart_wallets(self) -> None:
        result = evaluate_signals(
            _make_snapshot(smart_wallets_count=2),
            _make_security(),
            token_age_minutes=3.0,
        )
        assert "smart_money_early_entry" not in _rule_names(result)

    def test_weight_is_4(self) -> None:
        result = evaluate_signals(
            _make_snapshot(smart_wallets_count=5),
            _make_security(),
            token_age_minutes=2.0,
        )
        rule = next(r for r in result.rules_fired if r.name == "smart_money_early_entry")
        assert rule.weight == 4


class TestR46VolumeSpikeRatio:
    """R46: volume_spike_ratio — extreme 5m vol/liq >= 5x."""

    def test_fires_on_extreme_spike(self) -> None:
        # vol_5m=300000, liq=50000 → 6x
        result = evaluate_signals(
            _make_snapshot(volume_5m=300000),
            _make_security(),
        )
        assert "volume_spike_ratio" in _rule_names(result)

    def test_no_fire_moderate_ratio(self) -> None:
        # vol_5m=100000, liq=50000 → 2x (below 5x)
        result = evaluate_signals(
            _make_snapshot(volume_5m=100000),
            _make_security(),
        )
        assert "volume_spike_ratio" not in _rule_names(result)

    def test_fires_on_dex_volume(self) -> None:
        # Uses dex_volume_5m fallback
        result = evaluate_signals(
            _make_snapshot(volume_5m=None, dex_volume_5m=500000),
            _make_security(),
        )
        assert "volume_spike_ratio" in _rule_names(result)

    def test_no_fire_zero_liquidity(self) -> None:
        result = evaluate_signals(
            _make_snapshot(volume_5m=500000, liquidity_usd=0, dex_liquidity_usd=None),
            _make_security(),
        )
        assert "volume_spike_ratio" not in _rule_names(result)


class TestR47OrganicBuyPattern:
    """R47: organic_buy_pattern — strong buys, minimal sells, holder diversity."""

    def test_fires_on_organic_pattern(self) -> None:
        result = evaluate_signals(
            _make_snapshot(buys_5m=50, sells_5m=5, holders_count=40),
            _make_security(),
        )
        assert "organic_buy_pattern" in _rule_names(result)

    def test_no_fire_too_many_sells(self) -> None:
        # sells_5m = 20, which is 40% of buys_5m (>30%)
        result = evaluate_signals(
            _make_snapshot(buys_5m=50, sells_5m=20, holders_count=40),
            _make_security(),
        )
        assert "organic_buy_pattern" not in _rule_names(result)

    def test_no_fire_too_few_buys(self) -> None:
        result = evaluate_signals(
            _make_snapshot(buys_5m=10, sells_5m=1, holders_count=40),
            _make_security(),
        )
        assert "organic_buy_pattern" not in _rule_names(result)

    def test_no_fire_too_few_holders(self) -> None:
        result = evaluate_signals(
            _make_snapshot(buys_5m=50, sells_5m=5, holders_count=20),
            _make_security(),
        )
        assert "organic_buy_pattern" not in _rule_names(result)

    def test_weight_is_2(self) -> None:
        result = evaluate_signals(
            _make_snapshot(buys_5m=100, sells_5m=5, holders_count=50),
            _make_security(),
        )
        rule = next(r for r in result.rules_fired if r.name == "organic_buy_pattern")
        assert rule.weight == 2


class TestVelocityRulesCombined:
    """Test combined bullish velocity boost in strong-signal scenario."""

    def test_strong_buy_on_combined_velocity_signals(self) -> None:
        """All velocity rules fire + high_score → should trigger strong_buy."""
        result = evaluate_signals(
            _make_snapshot(
                score=55,          # R1 fires (+3)
                buys_5m=150,       # R43 fires (+3)
                sells_5m=10,       # R47 fires (+2) — 6.7% sells
                holders_count=200, # R44 fires (+3) with age=3m → 66/min
                smart_wallets_count=4,  # R45 fires (+4) — early smart money
                volume_5m=300000,  # R46 fires (+2) — 6x vol/liq
                liquidity_usd=50000,
            ),
            _make_security(),
            token_age_minutes=3.0,
        )
        names = _rule_names(result)
        assert "explosive_buy_velocity" in names
        assert "holder_acceleration" in names
        assert "smart_money_early_entry" in names
        assert "volume_spike_ratio" in names
        assert "organic_buy_pattern" in names
        # Combined bullish: 3+3+3+4+2+2+3+1 = 21, bearish = volume_dried_up(-2) → net 19 → strong_buy
        assert result.action == "strong_buy"
        assert result.net_score >= 15
