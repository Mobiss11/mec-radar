"""Test rug protection hard gates.

Hard gates block signal evaluation early when token metrics indicate
high rug pull probability:
- HG1: LIQ < $5K: hard gate (extremely thin liquidity)
- HG2: MCap/Liq > 10x: hard gate (no exit liquidity, pump & dump)
- R10a: $5K-$20K soft penalty (-2) for low but tradable liquidity
"""

from decimal import Decimal

from src.models.token import TokenSecurity, TokenSnapshot
from src.parsers.signals import evaluate_signals


def _make_snapshot(**kwargs) -> TokenSnapshot:
    defaults = {
        "token_id": 1,
        "liquidity_usd": Decimal("50000"),
        "holders_count": 100,
        "volume_1h": Decimal("30000"),
        "score": 55,
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


# --- HG1: Low Liquidity Gate ($5K) ---


class TestLowLiquidityGate:
    """HG1: tokens with liquidity < $5K are hard-blocked."""

    def test_liq_3k_blocked(self):
        """$3K liq → hard avoid (extremely thin)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("3000"),
            score=55,
        )
        result = evaluate_signals(snapshot, _make_security())
        assert result.action == "avoid"
        assert result.net_score == -10
        assert "low_liquidity_gate" in result.reasons
        assert len(result.rules_fired) == 1

    def test_liq_4999_blocked(self):
        """$4,999 = just below threshold → blocked."""
        snapshot = _make_snapshot(liquidity_usd=Decimal("4999"), score=70)
        result = evaluate_signals(snapshot, _make_security())
        assert result.action == "avoid"
        assert "low_liquidity_gate" in result.reasons

    def test_liq_1000_blocked(self):
        """$1K liq → hard avoid."""
        snapshot = _make_snapshot(liquidity_usd=Decimal("1000"))
        result = evaluate_signals(snapshot, _make_security())
        assert result.action == "avoid"
        assert "low_liquidity_gate" in result.reasons

    def test_liq_5000_passes(self):
        """$5,000 = at threshold → passes gate (soft penalty instead)."""
        snapshot = _make_snapshot(liquidity_usd=Decimal("5000"))
        result = evaluate_signals(snapshot, _make_security())
        assert "low_liquidity_gate" not in result.reasons
        # Gets soft penalty R10a instead
        assert "low_liquidity_soft" in result.reasons

    def test_liq_20000_passes(self):
        """$20K liq → passes gate, no soft penalty."""
        snapshot = _make_snapshot(liquidity_usd=Decimal("20000"))
        result = evaluate_signals(snapshot, _make_security())
        assert "low_liquidity_gate" not in result.reasons
        assert "low_liquidity_soft" not in result.reasons

    def test_liq_50k_passes(self):
        """$50K liq → passes gate, normal evaluation."""
        snapshot = _make_snapshot(liquidity_usd=Decimal("50000"))
        result = evaluate_signals(snapshot, _make_security())
        assert "low_liquidity_gate" not in result.reasons

    def test_liq_zero_passes(self):
        """$0 liq (no data) → passes gate (not 0 < 0 < 5K).
        We don't want to block tokens where liq data is missing.
        The tiny_liquidity rule R13 handles < $5K separately.
        """
        snapshot = _make_snapshot(liquidity_usd=None, dex_liquidity_usd=None)
        result = evaluate_signals(snapshot, _make_security())
        assert "low_liquidity_gate" not in result.reasons

    def test_dex_liq_fallback_blocked(self):
        """When primary liq is None, dex_liq used — and blocked if < $5K."""
        snapshot = _make_snapshot(
            liquidity_usd=None,
            dex_liquidity_usd=Decimal("3000"),
        )
        result = evaluate_signals(snapshot, _make_security())
        assert result.action == "avoid"
        assert "low_liquidity_gate" in result.reasons

    def test_dex_liq_fallback_passes(self):
        """dex_liq = $60K → passes gate."""
        snapshot = _make_snapshot(
            liquidity_usd=None,
            dex_liquidity_usd=Decimal("60000"),
        )
        result = evaluate_signals(snapshot, _make_security())
        assert "low_liquidity_gate" not in result.reasons

    def test_high_score_still_blocked_under_5k(self):
        """Even with score=70 + smart money, liq < $5K blocks everything."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("4000"),
            score=70,
            smart_wallets_count=5,
            buys_1h=1000,
            sells_1h=100,
        )
        result = evaluate_signals(snapshot, _make_security(), holder_velocity=100.0)
        assert result.action == "avoid"
        assert result.net_score == -10
        assert "low_liquidity_gate" in result.reasons
        assert "high_score" not in result.reasons
        assert "smart_money" not in result.reasons


# --- Soft penalty R10a ($5K-$20K) ---


class TestLowLiquiditySoftPenalty:
    """R10a: tokens with $5K-$20K liq get -2 penalty but can still pass."""

    def test_soft_penalty_fires(self):
        """$15K liq → soft penalty -2 but not blocked."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("15000"),
            score=65,
        )
        result = evaluate_signals(snapshot, _make_security())
        assert "low_liquidity_soft" in result.reasons
        assert "low_liquidity_gate" not in result.reasons

    def test_soft_penalty_with_strong_bullish(self):
        """$10K liq + strong bullish → can still produce buy signal."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("10000"),
            score=65,
            smart_wallets_count=3,
            buys_1h=500,
            sells_1h=50,
            volume_1h=Decimal("50000"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_velocity=60.0,
        )
        assert "low_liquidity_soft" in result.reasons
        assert result.action in ("buy", "strong_buy", "watch")
        assert result.net_score > -10

    def test_no_penalty_at_20k(self):
        """$20K liq → no soft penalty."""
        snapshot = _make_snapshot(liquidity_usd=Decimal("20000"))
        result = evaluate_signals(snapshot, _make_security())
        assert "low_liquidity_soft" not in result.reasons


# --- HG2: Extreme MCap/Liq Ratio Gate (10x) ---


class TestMcapLiqRatioGate:
    """HG2: tokens with MCap/Liq > 10x are hard-blocked."""

    def test_conor_pattern_blocked(self):
        """MCap $500K / Liq $40K = 12.5x → hard avoid."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("40000"),
            market_cap=Decimal("500000"),
            score=60,
        )
        result = evaluate_signals(snapshot, _make_security())
        assert result.action == "avoid"
        assert "extreme_mcap_liq_gate" in result.reasons
        assert result.net_score == -10

    def test_ratio_11x_blocked(self):
        """MCap/Liq = 11x → blocked."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("50000"),
            market_cap=Decimal("550000"),
        )
        result = evaluate_signals(snapshot, _make_security())
        assert result.action == "avoid"
        assert "extreme_mcap_liq_gate" in result.reasons

    def test_ratio_10x_passes(self):
        """MCap/Liq = 10x exactly → passes (> 10, not >=)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("50000"),
            market_cap=Decimal("500000"),
        )
        result = evaluate_signals(snapshot, _make_security())
        assert "extreme_mcap_liq_gate" not in result.reasons

    def test_ratio_1x_passes(self):
        """MCap/Liq = 1x → healthy, passes gate."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("50000"),
            market_cap=Decimal("50000"),
        )
        result = evaluate_signals(snapshot, _make_security())
        assert "extreme_mcap_liq_gate" not in result.reasons

    def test_ratio_0_6x_passes(self):
        """MCap/Liq = 0.6x → typical good token, passes gate."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("60000"),
            market_cap=Decimal("36000"),
        )
        result = evaluate_signals(snapshot, _make_security())
        assert "extreme_mcap_liq_gate" not in result.reasons

    def test_zero_liq_passes(self):
        """Liq = 0 → skip ratio check (avoid division by zero)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("0"),
            market_cap=Decimal("500000"),
        )
        result = evaluate_signals(snapshot, _make_security())
        assert "extreme_mcap_liq_gate" not in result.reasons

    def test_zero_mcap_passes(self):
        """MCap = 0 → ratio = 0, passes."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("50000"),
            market_cap=Decimal("0"),
        )
        result = evaluate_signals(snapshot, _make_security())
        assert "extreme_mcap_liq_gate" not in result.reasons

    def test_no_mcap_data_passes(self):
        """MCap = None → skip ratio check."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("50000"),
            market_cap=None,
        )
        result = evaluate_signals(snapshot, _make_security())
        assert "extreme_mcap_liq_gate" not in result.reasons


# --- Gate Priority ---


class TestGatePriority:
    """Verify that LIQ gate is checked before MCap/Liq gate."""

    def test_both_gates_trigger_liq_first(self):
        """Token with liq < $5K AND MCap/Liq > 10x → LIQ gate fires first."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("2000"),
            market_cap=Decimal("800000"),  # ratio = 400x
        )
        result = evaluate_signals(snapshot, _make_security())
        assert result.action == "avoid"
        assert "low_liquidity_gate" in result.reasons
        assert "extreme_mcap_liq_gate" not in result.reasons

    def test_liq_above_5k_but_high_ratio(self):
        """Token with liq > $5K but MCap/Liq > 10x → MCap gate fires."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("15000"),
            market_cap=Decimal("800000"),  # ratio = 53x
        )
        result = evaluate_signals(snapshot, _make_security())
        assert result.action == "avoid"
        assert "extreme_mcap_liq_gate" in result.reasons

    def test_normal_token_no_gates(self):
        """Standard good token passes both gates → normal evaluation."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("70000"),
            market_cap=Decimal("45000"),
            score=55,
            buys_1h=500,
            sells_1h=200,
            volume_1h=Decimal("100000"),
        )
        result = evaluate_signals(snapshot, _make_security(), holder_velocity=40.0)
        assert "low_liquidity_gate" not in result.reasons
        assert "extreme_mcap_liq_gate" not in result.reasons
        assert result.net_score != -10


# --- Backtest Validation ---


class TestBacktestValidation:
    """Replicate real rug pull tokens to verify gates work as expected."""

    def test_mr_token_penalized(self):
        """Mr. — real rug ($23K liq, score 39) → soft penalty, no buy signal.
        With $23K liq, score 39 is below threshold (35) for signal eval anyway.
        The soft penalty ensures extra bearish pressure."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("23317"),
            market_cap=Decimal("18242"),
            score=39,
            holders_count=418,
            buys_1h=1393,
            sells_1h=1029,
        )
        result = evaluate_signals(snapshot, _make_security())
        # Passes hard gate but gets soft penalty
        assert "low_liquidity_gate" not in result.reasons
        # Net bearish (low score + soft penalty)
        assert result.action != "strong_buy"

    def test_conor_token_blocked(self):
        """Conor — real rug ($20K liq, $889K mcap, 44.7x) → blocked by MCap/Liq gate."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("19863"),
            market_cap=Decimal("888626"),
            score=42,
            holders_count=1071,
            buys_1h=221,
            sells_1h=205,
        )
        result = evaluate_signals(snapshot, _make_security())
        assert result.action == "avoid"
        # MCap/Liq = 44.7x → extreme_mcap_liq_gate fires
        assert "extreme_mcap_liq_gate" in result.reasons

    def test_hapepe_token_penalized(self):
        """HAPEPE — real rug ($22K liq, score 46) → soft penalty, no strong buy."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("22105"),
            market_cap=Decimal("17210"),
            score=46,
            holders_count=450,
            buys_1h=767,
            sells_1h=377,
        )
        result = evaluate_signals(snapshot, _make_security())
        # Passes hard gate (liq > $5K) but gets soft penalty
        assert "low_liquidity_gate" not in result.reasons
        # Should NOT be strong_buy despite buy pressure
        assert result.action != "strong_buy"

    def test_google_ai_passes(self):
        """Google AI — real +100% ($44K liq, score 59) → passes both gates."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("43543"),
            market_cap=Decimal("43949"),
            score=59,
            holders_count=2711,
            buys_1h=3330,
            sells_1h=670,
        )
        result = evaluate_signals(snapshot, _make_security())
        assert "low_liquidity_gate" not in result.reasons
        assert "extreme_mcap_liq_gate" not in result.reasons

    def test_zeptoclaw_passes(self):
        """ZEPTOCLAW — real +43% ($96K liq, score 59) → passes both gates."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("95503"),
            market_cap=Decimal("88148"),
            score=59,
        )
        result = evaluate_signals(snapshot, _make_security())
        assert "low_liquidity_gate" not in result.reasons
        assert "extreme_mcap_liq_gate" not in result.reasons

    def test_war_passes(self):
        """WAR — real +15% ($60K liq, score 64) → passes both gates."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("60059"),
            market_cap=Decimal("35297"),
            score=64,
        )
        result = evaluate_signals(snapshot, _make_security())
        assert "low_liquidity_gate" not in result.reasons
        assert "extreme_mcap_liq_gate" not in result.reasons

    def test_nameneko_passes(self):
        """NAMENEKO — real +41% ($81K liq, score 58) → passes both gates."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("81074"),
            market_cap=Decimal("50958"),
            score=58,
        )
        result = evaluate_signals(snapshot, _make_security())
        assert "low_liquidity_gate" not in result.reasons
        assert "extreme_mcap_liq_gate" not in result.reasons


# --- Phase 30: Fast Entry Rules ---


class TestFastEntryRules:
    """R55 + R56: Rules that fire on INITIAL without prev_snapshot."""

    def test_r55_early_organic_momentum(self):
        """R55 fires on fresh token with healthy metrics and no prev_snapshot."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("12000"),
            market_cap=Decimal("15000"),  # MCap/Liq = 1.25
            holders_count=25,
            buys_1h=40,
            sells_1h=10,
            score=55,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            prev_snapshot=None,
            token_age_minutes=1.5,
        )
        assert "early_organic_momentum" in result.reasons

    def test_r55_not_on_old_token(self):
        """R55 does NOT fire on token older than 3 minutes."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("12000"),
            market_cap=Decimal("15000"),
            holders_count=25,
            buys_1h=40,
            sells_1h=10,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            prev_snapshot=None,
            token_age_minutes=5.0,
        )
        assert "early_organic_momentum" not in result.reasons

    def test_r55_not_with_prev_snapshot(self):
        """R55 does NOT fire when prev_snapshot exists (later stages)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("12000"),
            market_cap=Decimal("15000"),
            holders_count=25,
            buys_1h=40,
            sells_1h=10,
        )
        prev = _make_snapshot(
            liquidity_usd=Decimal("10000"),
            price=Decimal("0.001"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            prev_snapshot=prev,
            token_age_minutes=1.5,
        )
        assert "early_organic_momentum" not in result.reasons

    def test_r55_not_on_pumped_ratio(self):
        """R55 does NOT fire when MCap/Liq >= 5 (pumped beyond liquidity)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("10000"),
            market_cap=Decimal("60000"),  # MCap/Liq = 6.0
            holders_count=25,
            buys_1h=40,
            sells_1h=10,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            prev_snapshot=None,
            token_age_minutes=1.5,
        )
        assert "early_organic_momentum" not in result.reasons

    def test_r55_not_on_few_holders(self):
        """R55 does NOT fire with < 15 holders."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("12000"),
            market_cap=Decimal("15000"),
            holders_count=10,
            buys_1h=40,
            sells_1h=10,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            prev_snapshot=None,
            token_age_minutes=1.5,
        )
        assert "early_organic_momentum" not in result.reasons

    def test_r56_fresh_volume_surge(self):
        """R56 fires on fresh token with high vol/liq."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("10000"),
            market_cap=Decimal("12000"),
            volume_5m=Decimal("8000"),  # vol/liq = 0.8
            score=55,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=1.0,
        )
        assert "fresh_volume_surge" in result.reasons

    def test_r56_not_on_old_token(self):
        """R56 does NOT fire on token older than 3 minutes."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("10000"),
            volume_5m=Decimal("8000"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=5.0,
        )
        assert "fresh_volume_surge" not in result.reasons

    def test_r56_not_on_low_volume(self):
        """R56 does NOT fire when vol/liq < 0.5."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("10000"),
            volume_5m=Decimal("3000"),  # vol/liq = 0.3
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=1.0,
        )
        assert "fresh_volume_surge" not in result.reasons

    def test_fast_entry_rules_enable_buy_signal_on_initial(self):
        """Combined R55+R56 push a clean fresh token from watch to buy on INITIAL."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("12000"),
            market_cap=Decimal("15000"),  # MCap/Liq = 1.25
            holders_count=25,
            buys_1h=40,
            sells_1h=10,
            buys_5m=35,
            sells_5m=8,
            volume_5m=Decimal("8000"),  # vol/liq = 0.67
            score=60,  # R1: +3
        )
        security = _make_security()  # LP burned + renounced + low tax → R8: +3
        result = evaluate_signals(
            snapshot, security,
            prev_snapshot=None,
            token_age_minutes=1.0,
        )
        # R1(+3) + R8(+3) + R55(+3) + R56(+2) = +11
        # minus R10a(-2 soft liq) + bearish noise ≈ net ~8+
        assert result.action in ("buy", "strong_buy")
        assert "early_organic_momentum" in result.reasons
        assert "fresh_volume_surge" in result.reasons
