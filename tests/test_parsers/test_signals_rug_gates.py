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
        """$5,000 = at threshold → passes gate (very_low_liquidity -3 penalty)."""
        snapshot = _make_snapshot(liquidity_usd=Decimal("5000"))
        result = evaluate_signals(snapshot, _make_security())
        assert "low_liquidity_gate" not in result.reasons
        # Phase 35: $5K-$8K → very_low_liquidity -3 (was low_liquidity_soft -2)
        assert "very_low_liquidity" in result.reasons

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

    def test_r55_blocked_by_extreme_rugcheck(self):
        """R55 does NOT fire when rugcheck_score >= 5000 (extreme danger)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("174000"),
            market_cap=Decimal("261000"),  # MCap/Liq = 1.5
            holders_count=100,
            buys_1h=80,
            sells_1h=10,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            prev_snapshot=None,
            token_age_minutes=1.0,
            rugcheck_score=11500,
        )
        assert "early_organic_momentum" not in result.reasons

    def test_r55_allowed_with_low_rugcheck(self):
        """R55 fires when rugcheck < 5000 (normal noise level)."""
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
            token_age_minutes=1.5,
            rugcheck_score=500,
        )
        assert "early_organic_momentum" in result.reasons

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


# --- Phase 31: Anti-Rug Rules ---


class TestAntiRugRules:
    """R15 tiered, R57 graduation_rug_pattern, R58 bot_holder_farming."""

    # --- R15 Tiered Rugcheck ---

    def test_r15_low_rugcheck_penalty_minus2(self):
        """Rugcheck 200 (typical noise) → -2 penalty."""
        snapshot = _make_snapshot()
        result = evaluate_signals(snapshot, _make_security(), rugcheck_score=200)
        assert "rugcheck_danger" in result.reasons
        rule = next(r for r in result.rules_fired if r.name == "rugcheck_danger")
        assert rule.weight == -2

    def test_r15_extreme_rugcheck_penalty_minus5(self):
        """Rugcheck 11500 (scam-level) → -5 penalty."""
        snapshot = _make_snapshot()
        result = evaluate_signals(snapshot, _make_security(), rugcheck_score=11500)
        assert "rugcheck_danger" in result.reasons
        rule = next(r for r in result.rules_fired if r.name == "rugcheck_danger")
        assert rule.weight == -5

    def test_r15_boundary_5000_is_extreme(self):
        """Rugcheck exactly 5000 → extreme tier (-5)."""
        snapshot = _make_snapshot()
        result = evaluate_signals(snapshot, _make_security(), rugcheck_score=5000)
        rule = next(r for r in result.rules_fired if r.name == "rugcheck_danger")
        assert rule.weight == -5

    def test_r15_boundary_4999_is_high_danger(self):
        """Rugcheck 4999 → high danger tier (-4, Phase 39)."""
        snapshot = _make_snapshot()
        result = evaluate_signals(snapshot, _make_security(), rugcheck_score=4999)
        rule = next(r for r in result.rules_fired if r.name == "rugcheck_danger")
        assert rule.weight == -4

    def test_r15_boundary_3000_is_high_danger(self):
        """Rugcheck 3000 → high danger tier (-4, Phase 39)."""
        snapshot = _make_snapshot()
        result = evaluate_signals(snapshot, _make_security(), rugcheck_score=3000)
        rule = next(r for r in result.rules_fired if r.name == "rugcheck_danger")
        assert rule.weight == -4

    def test_r15_boundary_2999_is_normal(self):
        """Rugcheck 2999 → normal tier (-2)."""
        snapshot = _make_snapshot()
        result = evaluate_signals(snapshot, _make_security(), rugcheck_score=2999)
        rule = next(r for r in result.rules_fired if r.name == "rugcheck_danger")
        assert rule.weight == -2

    def test_r15_no_rugcheck(self):
        """No rugcheck data → rule does not fire."""
        snapshot = _make_snapshot()
        result = evaluate_signals(snapshot, _make_security(), rugcheck_score=None)
        assert "rugcheck_danger" not in result.reasons

    # --- R57a: Structural Graduation Rug (Phase 31b) ---

    def test_r57a_structural_graduation_174k(self):
        """Real drain: $174K liq, mcap/liq=1.5, age<1m → R57a fires (-7)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("174544"),
            market_cap=Decimal("261877"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=0.8,
        )
        assert "graduation_rug_structural" in result.reasons
        rule = next(r for r in result.rules_fired if r.name == "graduation_rug_structural")
        assert rule.weight == -7

    def test_r57a_structural_graduation_221k(self):
        """Real drain: TrenchHous $221K liq → R57a fires without rugcheck."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("221474"),
            market_cap=Decimal("332154"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=0.8,
            rugcheck_score=None,  # No rugcheck data
        )
        assert "graduation_rug_structural" in result.reasons

    def test_r57a_not_on_low_liq(self):
        """Profitable token: $14K liq → R57a does NOT fire."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("14444"),
            market_cap=Decimal("8389"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=0.8,
        )
        assert "graduation_rug_structural" not in result.reasons

    def test_r57a_not_on_diverged_ratio(self):
        """R57a does NOT fire when mcap/liq > 2.0 (traded, diverged)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("174000"),
            market_cap=Decimal("500000"),  # ratio 2.87x
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=0.8,
        )
        assert "graduation_rug_structural" not in result.reasons

    def test_r57a_not_on_old_token(self):
        """R57a does NOT fire on token older than 3 minutes."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("174000"),
            market_cap=Decimal("261000"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=4.0,
        )
        assert "graduation_rug_structural" not in result.reasons

    def test_r57a_vole_drain(self):
        """Real drain: VOLE $174K, drained in 5 seconds."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("174628"),
            market_cap=Decimal("261969"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=0.8,
        )
        assert "graduation_rug_structural" in result.reasons

    # --- R57b: Original Graduation Rug (elif, $50-100K) ---

    def test_r57b_medium_pool_with_rugcheck(self):
        """$75K pool with high rugcheck → R57b fires (-5)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("75000"),
            market_cap=Decimal("90000"),  # ratio 1.2x
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            rugcheck_score=11500,
            token_age_minutes=1.0,
        )
        assert "graduation_rug_pattern" in result.reasons
        rule = next(r for r in result.rules_fired if r.name == "graduation_rug_pattern")
        assert rule.weight == -5

    def test_r57b_not_on_low_rugcheck(self):
        """$75K pool with low rugcheck → R57b does NOT fire."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("75000"),
            market_cap=Decimal("90000"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            rugcheck_score=500,
            token_age_minutes=1.0,
        )
        assert "graduation_rug_pattern" not in result.reasons

    def test_r57a_preempts_r57b(self):
        """$174K pool → R57a fires, R57b does NOT (elif)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("174000"),
            market_cap=Decimal("261000"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            rugcheck_score=11500,
            token_age_minutes=0.8,
        )
        assert "graduation_rug_structural" in result.reasons
        assert "graduation_rug_pattern" not in result.reasons

    def test_r57_not_on_small_pool(self):
        """R57b does NOT fire on small pools < $50K liq."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("15000"),
            market_cap=Decimal("15000"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            rugcheck_score=11500,
            token_age_minutes=1.0,
        )
        assert "graduation_rug_pattern" not in result.reasons
        assert "graduation_rug_structural" not in result.reasons

    # --- R58 Bot Holder Farming (Phase 31b: removed rugcheck, added liq floor) ---

    def test_r58_bot_farming_detected(self):
        """Holder growth +5100% in 0.8m on $174K pool → suspicious."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("174000"),
            market_cap=Decimal("261000"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_growth_pct=5100.0,
            token_age_minutes=0.8,
        )
        assert "bot_holder_farming" in result.reasons
        rule = next(r for r in result.rules_fired if r.name == "bot_holder_farming")
        assert rule.weight == -3

    def test_r58_fires_without_rugcheck(self):
        """R58 fires WITHOUT rugcheck (Phase 31b: removed dependency)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("50000"),
            market_cap=Decimal("75000"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            rugcheck_score=None,  # No rugcheck!
            holder_growth_pct=600.0,
            token_age_minutes=1.0,
        )
        assert "bot_holder_farming" in result.reasons

    def test_r58_not_on_low_liq(self):
        """R58 does NOT fire on low-liq pool (floor $20K prevents false positives)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("14000"),
            market_cap=Decimal("8000"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_growth_pct=800.0,
            token_age_minutes=1.0,
        )
        assert "bot_holder_farming" not in result.reasons

    def test_r58_not_on_low_growth(self):
        """R58 does NOT fire on moderate holder growth (<500%)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("174000"),
            market_cap=Decimal("261000"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_growth_pct=300.0,
            token_age_minutes=1.0,
        )
        assert "bot_holder_farming" not in result.reasons

    def test_r58_not_on_old_token(self):
        """R58 does NOT fire on token older than 2 minutes."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_growth_pct=5000.0,
            token_age_minutes=3.0,
        )
        assert "bot_holder_farming" not in result.reasons

    # --- R59: Extreme Graduation Growth (Phase 31b NEW) ---

    def test_r59_extreme_growth_on_graduation(self):
        """Holder growth +6650% on $174K pool → R59 fires (-6)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("174000"),
            market_cap=Decimal("261000"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_growth_pct=6650.0,
            token_age_minutes=0.8,
        )
        assert "extreme_graduation_growth" in result.reasons
        rule = next(r for r in result.rules_fired if r.name == "extreme_graduation_growth")
        assert rule.weight == -6

    def test_r59_derp_drain(self):
        """Real drain: derp — growth +9650% on $214K pool → R59 fires."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("214739"),
            market_cap=Decimal("322148"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_growth_pct=9650.0,
            token_age_minutes=0.8,
        )
        assert "extreme_graduation_growth" in result.reasons

    def test_r59_not_on_low_liq(self):
        """Profitable Sandcat — growth +2031% but liq=$13.6K → NOT caught."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("13600"),
            market_cap=Decimal("11000"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_growth_pct=2031.0,
            token_age_minutes=0.8,
        )
        assert "extreme_graduation_growth" not in result.reasons

    def test_r59_not_on_moderate_growth(self):
        """R59 does NOT fire when holder growth < 3000%."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("174000"),
            market_cap=Decimal("261000"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_growth_pct=2500.0,
            token_age_minutes=0.8,
        )
        assert "extreme_graduation_growth" not in result.reasons

    def test_r59_not_on_old_token(self):
        """R59 does NOT fire on token older than 3 minutes."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("174000"),
            market_cap=Decimal("261000"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_growth_pct=6000.0,
            token_age_minutes=4.0,
        )
        assert "extreme_graduation_growth" not in result.reasons

    # --- Graduation Zone Net Cap (Phase 31b) ---

    def test_graduation_cap_limits_net(self):
        """Even with net=15 from bullish, graduation zone caps to net=2 (watch)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("174000"),
            market_cap=Decimal("261000"),
            holders_count=200,
            buys_1h=500,
            sells_1h=50,
            score=65,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=0.8,
            holder_velocity=200.0,
        )
        assert result.net_score <= 2
        assert result.action in ("watch", "avoid")

    def test_graduation_cap_not_on_low_liq(self):
        """$40K pool → no graduation cap, bullish rules can win."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("40000"),
            market_cap=Decimal("25000"),
            holders_count=200,
            buys_1h=500,
            sells_1h=50,
            score=65,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=0.8,
            holder_velocity=200.0,
        )
        # No cap applied — can reach buy/strong_buy
        assert result.net_score > 2

    def test_graduation_cap_not_on_old_token(self):
        """$174K pool but age > 3min → no cap (token has traded, diverged)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("174000"),
            market_cap=Decimal("261000"),
            holders_count=200,
            buys_1h=500,
            sells_1h=50,
            score=65,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=5.0,
            holder_velocity=200.0,
        )
        # R57a won't fire (age > 3min), cap won't apply
        assert "graduation_rug_structural" not in result.reasons

    # --- Combined: Real Scenario Replays ---

    def test_agent1c_graduation_drain_blocked(self):
        """Real drain: agent1c at $201K, holder_growth +5450% → fully blocked.

        R57a(-7) + R59(-6) + R58(-3) + graduation cap = impossible to buy.
        """
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("201018"),
            market_cap=Decimal("301576"),
            holders_count=111,
            buys_1h=66,
            sells_1h=10,
            score=52,
        )
        prev = _make_snapshot(
            liquidity_usd=Decimal("201000"),
            holders_count=2,
            price=Decimal("0.00030"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            prev_snapshot=prev,
            token_age_minutes=0.8,
            holder_velocity=198.0,
            holder_growth_pct=5450.0,
        )
        assert result.action in ("watch", "avoid")
        assert "graduation_rug_structural" in result.reasons
        assert "extreme_graduation_growth" in result.reasons
        assert result.net_score <= 2

    def test_pankun_full_scenario_blocked(self):
        """Pan-kun graduation: liq=$174K → blocked by R57a + cap.

        Phase 31b: R57a(-7) alone is heavy. With graduation cap → max watch.
        """
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("174190"),
            market_cap=Decimal("261324"),
            holders_count=104,
            buys_1h=185,
            sells_1h=20,
            score=56,
        )
        prev = _make_snapshot(
            liquidity_usd=Decimal("174000"),
            holders_count=2,
            price=Decimal("0.00017"),
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            prev_snapshot=prev,
            rugcheck_score=9601,
            token_age_minutes=0.8,
            holder_velocity=185.0,
            holder_growth_pct=5100.0,
        )
        assert result.action in ("watch", "avoid")
        assert "graduation_rug_structural" in result.reasons
        assert result.net_score <= 2

    # --- Backtest: Profitable Tokens NOT Blocked ---

    def test_profitable_hua_hua_capped_at_low_liq(self):
        """Hua Hua: liq=$14K + rugcheck=11500 + velocity=511 = bot farm profile.
        Phase 41 R72 correctly caps this — 36% of positions with this profile
        are rugs in production. At higher liq ($25K), cap doesn't apply."""
        # Low liq profile — R72 caps bullish, R69 fires compound penalty
        snapshot_low = _make_snapshot(
            liquidity_usd=Decimal("14444"),
            market_cap=Decimal("8389"),
            holders_count=299,
            buys_1h=359,
            sells_1h=60,
            score=53,
        )
        result_low = evaluate_signals(
            snapshot_low, _make_security(),
            rugcheck_score=11500,
            holder_growth_pct=1561.0,
            token_age_minutes=0.8,
            holder_velocity=511.0,
        )
        # R72 correctly caps bot-farm-like velocity on micro-liq
        assert "low_liq_velocity_cap" in result_low.reasons

        # Same velocity at higher liq ($35K) → R72 cap AND R69 compound don't apply
        # (R69 requires liq < $30K)
        snapshot_high = _make_snapshot(
            liquidity_usd=Decimal("35000"),
            market_cap=Decimal("25000"),
            holders_count=299,
            buys_1h=359,
            sells_1h=60,
            score=53,
        )
        result_high = evaluate_signals(
            snapshot_high, _make_security(),
            rugcheck_score=11500,
            holder_growth_pct=1561.0,
            token_age_minutes=0.8,
            holder_velocity=511.0,
        )
        assert "low_liq_velocity_cap" not in result_high.reasons
        assert "velocity_danger_compound" not in result_high.reasons
        assert result_high.action in ("buy", "strong_buy", "watch")

    def test_profitable_cash_not_blocked(self):
        """Real profit: CASH +111% at liq=$12.6K → NOT blocked."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("12611"),
            market_cap=Decimal("59224"),
            holders_count=96,
            buys_1h=137,
            sells_1h=32,
            score=40,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            rugcheck_score=2001,
            token_age_minutes=0.8,
        )
        assert "graduation_rug_structural" not in result.reasons
        assert "bot_holder_farming" not in result.reasons

    def test_profitable_pankun_lowliq_not_blocked(self):
        """Real profit: Pan-kun +107% at liq=$13.8K → NOT blocked."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("13801"),
            market_cap=Decimal("30429"),
            holders_count=90,
            buys_1h=128,
            sells_1h=20,
            score=53,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            rugcheck_score=11500,
            holder_growth_pct=429.0,
            token_age_minutes=0.8,
            holder_velocity=133.0,
        )
        assert "graduation_rug_structural" not in result.reasons
        assert "extreme_graduation_growth" not in result.reasons
        # R58: growth 429% < 500 → doesn't fire
        assert "bot_holder_farming" not in result.reasons

    def test_profitable_agent1c_lowliq_not_blocked(self):
        """Real profit: agent1c +124% at liq=$14.3K → NOT blocked."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("14274"),
            market_cap=Decimal("12611"),
            holders_count=269,
            buys_1h=339,
            sells_1h=58,
            score=53,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            rugcheck_score=15357,
            token_age_minutes=0.8,
            holder_velocity=194.0,
        )
        assert "graduation_rug_structural" not in result.reasons
        assert "bot_holder_farming" not in result.reasons


# --- Phase 39: R69 Velocity Danger Compound ---


class TestVelocityDangerCompound:
    """R69: holder_velocity >= 200 + rugcheck >= 3000 + liq < $30K → -6."""

    def test_r69_moltgen_pattern_fires(self):
        """MOLTGEN: 417 holders/min + rugcheck 3501 + $13K liq → R69 fires."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("13000"),
            market_cap=Decimal("13000"),
            holders_count=139,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_velocity=417.0,
            rugcheck_score=3501,
            token_age_minutes=0.5,
        )
        assert "velocity_danger_compound" in result.reasons
        rule = next(r for r in result.rules_fired if r.name == "velocity_danger_compound")
        assert rule.weight == -6

    def test_r69_not_on_clean_rugcheck(self):
        """High velocity + clean rugcheck (<3000) → R69 does NOT fire."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("13000"),
            market_cap=Decimal("13000"),
            holders_count=200,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_velocity=300.0,
            rugcheck_score=2000,
            token_age_minutes=1.0,
        )
        assert "velocity_danger_compound" not in result.reasons

    def test_r69_not_on_high_liq(self):
        """High velocity + rugcheck 3500 + $50K liq → R69 does NOT fire."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("50000"),
            market_cap=Decimal("50000"),
            holders_count=500,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_velocity=400.0,
            rugcheck_score=3500,
            token_age_minutes=0.5,
        )
        assert "velocity_danger_compound" not in result.reasons

    def test_r69_not_on_low_velocity(self):
        """Moderate velocity (150/min) + rugcheck 3500 + low liq → R69 does NOT fire."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("13000"),
            market_cap=Decimal("13000"),
            holders_count=100,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_velocity=150.0,
            rugcheck_score=3500,
            token_age_minutes=1.0,
        )
        assert "velocity_danger_compound" not in result.reasons

    def test_r69_boundary_exactly_200_and_3000(self):
        """Exact boundary: velocity=200, rugcheck=3000, liq=$29K → fires."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("29000"),
            market_cap=Decimal("29000"),
            holders_count=150,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_velocity=200.0,
            rugcheck_score=3000,
            token_age_minutes=1.0,
        )
        assert "velocity_danger_compound" in result.reasons

    def test_r69_profitable_agent1c_not_hit(self):
        """Safety: agent1c +124% had velocity=194 + rugcheck=15357 + liq=$14K.
        R69 requires rugcheck 3000-4999. Score 15357 >= 5000 → out of range.
        (The 5000+ tier is handled by R15 -5 penalty instead.)"""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("14274"),
            market_cap=Decimal("12611"),
            holders_count=269,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_velocity=194.0,
            rugcheck_score=15357,
            token_age_minutes=0.8,
        )
        # R69 checks rugcheck_score >= 3000 (no upper bound), so 15357 qualifies.
        # But velocity 194 < 200 → does NOT fire.
        assert "velocity_danger_compound" not in result.reasons


class TestHolderConcentrationDanger:
    """R70: Holder concentration penalty (Phase 40).

    Production data (2026-02-24):
    - rugcheck >= 20000 WITHOUT LP Unlocked: 36.8% rug rate, 50.9% win
    - rugcheck >= 20000 WITH LP Unlocked: 100% win rate, 0 rugs (7 trades)
    - Holder concentration risk: 34.2% rug rate vs 14.8% without (2.3x)
    """

    def test_r70_fires_holder_risk_high_rugcheck_no_lp_unlocked(self):
        """Classic rug pattern: high rugcheck + holder/ownership risk, no LP Unlocked."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("15000"),
            market_cap=Decimal("30000"),
            holders_count=50,
        )
        security = _make_security(
            rugcheck_score=21000,
            rugcheck_risks="Top 10 holders high ownership, Single holder ownership, Low Liquidity",
        )
        result = evaluate_signals(
            snapshot, security,
            rugcheck_score=21000,
        )
        assert "holder_concentration_danger" in result.reasons

    def test_r70_weight_is_minus_4(self):
        """R70 penalty is -4 (soft, not fatal)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("20000"),
            market_cap=Decimal("40000"),
            holders_count=80,
        )
        security = _make_security(
            rugcheck_score=22000,
            rugcheck_risks="Top 10 holders high ownership, Low Liquidity",
        )
        result = evaluate_signals(
            snapshot, security,
            rugcheck_score=22000,
        )
        r70_rules = [r for r in result.rules_fired if r.name == "holder_concentration_danger"]
        assert len(r70_rules) == 1
        assert r70_rules[0].weight == -4

    def test_r70_safe_with_lp_unlocked(self):
        """LP Unlocked present = PumpFun standard. 100% win rate at high rugcheck.
        R70 must NOT fire when LP Unlocked is in rugcheck_risks."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("15000"),
            market_cap=Decimal("30000"),
            holders_count=50,
        )
        security = _make_security(
            rugcheck_score=21000,
            rugcheck_risks="Large Amount of LP Unlocked, Top 10 holders high ownership, Low Liquidity",
        )
        result = evaluate_signals(
            snapshot, security,
            rugcheck_score=21000,
        )
        assert "holder_concentration_danger" not in result.reasons

    def test_r70_safe_low_rugcheck(self):
        """rugcheck_score < 20000 → R70 does NOT fire even with holder risks."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("20000"),
            market_cap=Decimal("40000"),
            holders_count=80,
        )
        security = _make_security(
            rugcheck_score=15000,
            rugcheck_risks="Top 10 holders high ownership, Single holder ownership",
        )
        result = evaluate_signals(
            snapshot, security,
            rugcheck_score=15000,
        )
        assert "holder_concentration_danger" not in result.reasons

    def test_r70_safe_no_holder_risk_keywords(self):
        """High rugcheck but no holder/ownership keywords → R70 does NOT fire."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("20000"),
            market_cap=Decimal("40000"),
            holders_count=80,
        )
        security = _make_security(
            rugcheck_score=25000,
            rugcheck_risks="Low Liquidity, Mutable Metadata, No Socials",
        )
        result = evaluate_signals(
            snapshot, security,
            rugcheck_score=25000,
        )
        assert "holder_concentration_danger" not in result.reasons

    def test_r70_safe_no_security(self):
        """No security object → R70 does NOT fire."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("20000"),
            market_cap=Decimal("40000"),
            holders_count=80,
        )
        result = evaluate_signals(
            snapshot, None,
            rugcheck_score=25000,
        )
        assert "holder_concentration_danger" not in result.reasons

    def test_r70_safe_no_rugcheck_risks(self):
        """Security exists but rugcheck_risks is None → R70 does NOT fire."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("20000"),
            market_cap=Decimal("40000"),
            holders_count=80,
        )
        security = _make_security(
            rugcheck_score=25000,
            rugcheck_risks=None,
        )
        result = evaluate_signals(
            snapshot, security,
            rugcheck_score=25000,
        )
        assert "holder_concentration_danger" not in result.reasons

    def test_r70_boundary_rugcheck_exactly_20000(self):
        """Exact boundary: rugcheck_score=20000 + holder risk + no LP Unlocked → fires."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("20000"),
            market_cap=Decimal("40000"),
            holders_count=80,
        )
        security = _make_security(
            rugcheck_score=20000,
            rugcheck_risks="Top 10 holders high ownership",
        )
        result = evaluate_signals(
            snapshot, security,
            rugcheck_score=20000,
        )
        assert "holder_concentration_danger" in result.reasons

    def test_r70_boundary_rugcheck_19999(self):
        """Just below boundary: rugcheck_score=19999 → R70 does NOT fire."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("20000"),
            market_cap=Decimal("40000"),
            holders_count=80,
        )
        security = _make_security(
            rugcheck_score=19999,
            rugcheck_risks="Top 10 holders high ownership",
        )
        result = evaluate_signals(
            snapshot, security,
            rugcheck_score=19999,
        )
        assert "holder_concentration_danger" not in result.reasons

    def test_r70_ownership_keyword_variant(self):
        """'ownership' keyword also triggers R70 (not just 'holder')."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("15000"),
            market_cap=Decimal("30000"),
            holders_count=50,
        )
        security = _make_security(
            rugcheck_score=22000,
            rugcheck_risks="Single ownership concentration, Low Liquidity",
        )
        result = evaluate_signals(
            snapshot, security,
            rugcheck_score=22000,
        )
        assert "holder_concentration_danger" in result.reasons

    def test_r70_case_insensitive(self):
        """Risk string matching is case-insensitive."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("15000"),
            market_cap=Decimal("30000"),
            holders_count=50,
        )
        security = _make_security(
            rugcheck_score=22000,
            rugcheck_risks="TOP 10 HOLDERS HIGH OWNERSHIP, LOW LIQUIDITY",
        )
        result = evaluate_signals(
            snapshot, security,
            rugcheck_score=22000,
        )
        assert "holder_concentration_danger" in result.reasons

    def test_r70_lp_unlocked_case_insensitive(self):
        """LP Unlocked exemption is case-insensitive."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("15000"),
            market_cap=Decimal("30000"),
            holders_count=50,
        )
        security = _make_security(
            rugcheck_score=22000,
            rugcheck_risks="lp unlocked, top 10 holders high ownership",
        )
        result = evaluate_signals(
            snapshot, security,
            rugcheck_score=22000,
        )
        assert "holder_concentration_danger" not in result.reasons

    def test_r70_as_compound_scam_flag(self):
        """R70 pattern also adds to compound scam flags in R61.
        If holder_concentration + 2 other flags = 3 → compound_scam_fingerprint (hard avoid)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("15000"),
            market_cap=Decimal("30000"),
            holders_count=50,
        )
        security = _make_security(
            rugcheck_score=22000,
            rugcheck_risks="Top 10 holders high ownership, Low Liquidity",
            is_mintable=True,      # compound flag: mintable
            lp_burned=False,       # compound flag: LP_unsecured
            lp_locked=False,
        )
        result = evaluate_signals(
            snapshot, security,
            rugcheck_score=22000,
            bundled_buy_detected=True,  # compound flag: bundled_buy
            raydium_lp_burned=False,    # needed for LP_unsecured flag
        )
        # 4 flags: mintable + bundled_buy + LP_unsecured + holder_concentration = hard avoid
        assert result.action == "avoid"
        assert "compound_scam_fingerprint" in result.reasons

    def test_r70_compound_flag_not_added_with_lp_unlocked(self):
        """Holder concentration compound flag does NOT fire when LP Unlocked present."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("15000"),
            market_cap=Decimal("30000"),
            holders_count=50,
        )
        security = _make_security(
            rugcheck_score=22000,
            rugcheck_risks="Large Amount of LP Unlocked, Top 10 holders high ownership",
            is_mintable=True,
            lp_burned=False,
            lp_locked=False,
        )
        result = evaluate_signals(
            snapshot, security,
            rugcheck_score=22000,
            bundled_buy_detected=True,
            raydium_lp_burned=False,
        )
        # Only 3 flags: mintable + bundled_buy + LP_unsecured (no holder_concentration)
        # Still 3 = compound, but holder_concentration is NOT one of them
        fired_names = [r.name for r in result.rules_fired]
        if "compound_scam_fingerprint" in result.reasons:
            # Compound fired but holder_concentration is NOT a reason
            desc = result.reasons["compound_scam_fingerprint"]
            assert "holder_concentration" not in desc


class TestLowLiqVelocityCap:
    """R72: Low-liquidity velocity cap (Phase 41).

    Production data (2026-02-24):
    - Bot farms stack 6 velocity rules for +15 bullish on liq < $20K
    - 75.5% of rugged positions had liq < $20K
    - Cap bullish at +8 when liq < $20K → bot-farmed scams can't overwhelm bearish
    """

    def test_r72_caps_bullish_at_8_on_low_liq(self):
        """Bot farm pattern: liq $15K, 80 buys, velocity 200/min → +15 bullish.
        With cap: bullish capped at 8, so bearish -7 → net +1 → avoid."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("15000"),
            market_cap=Decimal("9000"),
            holders_count=80,
            volume_1h=Decimal("10000"),
            buys_5m=80,
            sells_5m=2,
            buys_1h=80,
            sells_1h=2,
            score=50,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_velocity=200.0,
            rugcheck_score=11500,
            token_age_minutes=0.5,
            holder_growth_pct=200.0,
        )
        # Bullish should be capped at 8
        assert result.bullish_score <= 8
        assert "low_liq_velocity_cap" in result.reasons

    def test_r72_not_at_20k_liq(self):
        """At exactly $20K liq, cap does NOT apply."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("20000"),
            market_cap=Decimal("15000"),
            holders_count=80,
            volume_1h=Decimal("10000"),
            buys_5m=80,
            sells_5m=2,
            buys_1h=80,
            sells_1h=2,
            score=50,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_velocity=200.0,
            token_age_minutes=0.5,
            holder_growth_pct=200.0,
        )
        assert "low_liq_velocity_cap" not in result.reasons

    def test_r72_not_when_bullish_under_8(self):
        """If bullish <= 8 naturally, cap doesn't apply (no capping needed)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("15000"),
            market_cap=Decimal("9000"),
            holders_count=30,
            volume_1h=Decimal("3000"),
            score=45,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=2.0,
        )
        assert "low_liq_velocity_cap" not in result.reasons

    def test_r72_scam_pattern_becomes_avoid(self):
        """Full FROG-like scam: +15 bullish - 7 bearish = net +8 (strong_buy).
        With R72 cap: +8 - 7 = net +1 → avoid."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("18000"),
            market_cap=Decimal("9500"),
            holders_count=75,
            volume_1h=Decimal("10000"),
            buys_5m=83,
            sells_5m=5,
            buys_1h=83,
            sells_1h=5,
            score=46,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_velocity=176.0,
            rugcheck_score=11500,
            token_age_minutes=0.5,
            holder_growth_pct=200.0,
        )
        # Bullish capped at 8, rugcheck -5 + low_liq -2 = -7 bearish
        # Net = 8 - 7 = +1 → avoid
        assert result.bullish_score <= 8
        assert result.action in ("avoid", "watch")

    def test_r72_preserves_clean_low_liq_token(self):
        """Clean low-liq token with moderate bullish — buy should still work.
        Example: liq=$15K, score=60, 30 holders, no velocity stack. bullish ~5."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("15000"),
            market_cap=Decimal("15000"),
            holders_count=30,
            volume_1h=Decimal("35000"),
            buys_5m=25,
            sells_5m=10,
            buys_1h=40,
            sells_1h=20,
            score=62,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=5.0,
        )
        # Clean token — bullish shouldn't be extremely high,
        # no cap needed, can still get buy signal
        assert result.action in ("buy", "strong_buy", "watch")

    def test_r72_cap_fires_on_50k_liq_boundary(self):
        """At liq=$19999 (just below $20K), cap applies if bullish > 8."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("19999"),
            market_cap=Decimal("12000"),
            holders_count=80,
            volume_1h=Decimal("10000"),
            buys_5m=80,
            sells_5m=2,
            buys_1h=80,
            sells_1h=2,
            score=50,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_velocity=200.0,
            token_age_minutes=0.5,
            holder_growth_pct=200.0,
        )
        assert result.bullish_score <= 8


class TestFakeLiquidityTrap:
    """R74: Fake liquidity trap — high liq without proportional volume."""

    def test_r74_fires_strong_liq_no_volume_surge(self):
        """$50K liq + low volume (vol_5m/liq < 0.5) → penalty fires."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("50000"),
            market_cap=Decimal("30000"),
            holders_count=47,
            volume_1h=Decimal("6900"),
            volume_5m=Decimal("6900"),
            buys_5m=40,
            sells_5m=11,
            buys_1h=40,
            sells_1h=11,
            score=45,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_velocity=60.0,
            token_age_minutes=0.4,
            holder_growth_pct=57.0,
        )
        rule_names = [r.name for r in result.rules_fired]
        assert "fake_liquidity_trap" in rule_names
        assert "strong_liquidity" in rule_names
        assert "fresh_volume_surge" not in rule_names

    def test_r74_skips_when_volume_surge_present(self):
        """$50K liq + high volume (vol_5m/liq >= 0.5) → no penalty."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("50000"),
            market_cap=Decimal("30000"),
            holders_count=59,
            volume_1h=Decimal("50000"),
            volume_5m=Decimal("30000"),
            buys_5m=86,
            sells_5m=48,
            buys_1h=86,
            sells_1h=48,
            score=50,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_velocity=80.0,
            token_age_minutes=0.4,
            holder_growth_pct=64.0,
        )
        rule_names = [r.name for r in result.rules_fired]
        assert "fake_liquidity_trap" not in rule_names
        assert "strong_liquidity" in rule_names
        assert "fresh_volume_surge" in rule_names

    def test_r74_skips_low_liq_tokens(self):
        """$8K liq → no strong_liquidity → no fake_liquidity_trap."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("8000"),
            market_cap=Decimal("8000"),
            holders_count=30,
            volume_1h=Decimal("3000"),
            volume_5m=Decimal("3000"),
            buys_5m=30,
            sells_5m=10,
            buys_1h=30,
            sells_1h=10,
            score=45,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_velocity=50.0,
            token_age_minutes=0.4,
        )
        rule_names = [r.name for r in result.rules_fired]
        assert "fake_liquidity_trap" not in rule_names
        assert "strong_liquidity" not in rule_names

    def test_r74_penalty_weight_is_minus5(self):
        """R74 has weight -5."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("55000"),
            market_cap=Decimal("30000"),
            holders_count=40,
            volume_1h=Decimal("5000"),
            volume_5m=Decimal("5000"),
            buys_5m=35,
            sells_5m=5,
            buys_1h=35,
            sells_1h=5,
            score=45,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            holder_velocity=60.0,
            token_age_minutes=0.4,
            holder_growth_pct=50.0,
        )
        trap_rules = [r for r in result.rules_fired if r.name == "fake_liquidity_trap"]
        assert len(trap_rules) == 1
        assert trap_rules[0].weight == -5
