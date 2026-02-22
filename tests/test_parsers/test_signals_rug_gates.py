"""Test rug protection hard gates (Phase 30).

Hard gates block signal evaluation early when token metrics indicate
high rug pull probability. Based on backtest of 52 real+paper trades:
- LIQ < $30K: precision 80%, caught all 3 real rugs
- MCap/Liq > 10x: precision 100%, caught Conor-type pump & dump
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


# --- HG1: Low Liquidity Gate ($30K) ---


class TestLowLiquidityGate:
    """HG1: tokens with liquidity < $30K are hard-blocked."""

    def test_liq_20k_blocked(self):
        """$20K liq → hard avoid (Mr., HAPEPE territory)."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("20000"),
            score=55,
            buys_1h=500,
            sells_1h=100,
        )
        result = evaluate_signals(snapshot, _make_security())
        assert result.action == "avoid"
        assert result.net_score == -10
        assert "low_liquidity_gate" in result.reasons
        # Should only have the gate rule, no other rules evaluated
        assert len(result.rules_fired) == 1

    def test_liq_5k_blocked(self):
        """$5K liq → hard avoid."""
        snapshot = _make_snapshot(liquidity_usd=Decimal("5000"), score=70)
        result = evaluate_signals(snapshot, _make_security())
        assert result.action == "avoid"
        assert "low_liquidity_gate" in result.reasons

    def test_liq_29999_blocked(self):
        """$29,999 = just below threshold → blocked."""
        snapshot = _make_snapshot(liquidity_usd=Decimal("29999"))
        result = evaluate_signals(snapshot, _make_security())
        assert result.action == "avoid"
        assert "low_liquidity_gate" in result.reasons

    def test_liq_30000_passes(self):
        """$30,000 = at threshold → passes gate (evaluated normally)."""
        snapshot = _make_snapshot(liquidity_usd=Decimal("30000"))
        result = evaluate_signals(snapshot, _make_security())
        assert "low_liquidity_gate" not in result.reasons

    def test_liq_50k_passes(self):
        """$50K liq → passes gate, normal evaluation."""
        snapshot = _make_snapshot(liquidity_usd=Decimal("50000"))
        result = evaluate_signals(snapshot, _make_security())
        assert "low_liquidity_gate" not in result.reasons

    def test_liq_zero_passes(self):
        """$0 liq (no data) → passes gate (not 0 < 0 < 30K).
        We don't want to block tokens where liq data is missing.
        The tiny_liquidity rule R13 handles < $5K separately.
        """
        snapshot = _make_snapshot(liquidity_usd=None, dex_liquidity_usd=None)
        result = evaluate_signals(snapshot, _make_security())
        assert "low_liquidity_gate" not in result.reasons

    def test_dex_liq_fallback_blocked(self):
        """When primary liq is None, dex_liq used — and blocked if < $30K."""
        snapshot = _make_snapshot(
            liquidity_usd=None,
            dex_liquidity_usd=Decimal("15000"),
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

    def test_high_score_still_blocked(self):
        """Even with score=70 + smart money, low liq blocks everything."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("20000"),
            score=70,
            smart_wallets_count=5,
            buys_1h=1000,
            sells_1h=100,
        )
        result = evaluate_signals(snapshot, _make_security(), holder_velocity=100.0)
        assert result.action == "avoid"
        assert result.net_score == -10
        assert "low_liquidity_gate" in result.reasons
        # Bullish rules NOT evaluated
        assert "high_score" not in result.reasons
        assert "smart_money" not in result.reasons


# --- HG2: Extreme MCap/Liq Ratio Gate (10x) ---


class TestMcapLiqRatioGate:
    """HG2: tokens with MCap/Liq > 10x are hard-blocked."""

    def test_conor_pattern_blocked(self):
        """MCap $888K / Liq $20K = 44.4x → hard avoid."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("20000"),
            market_cap=Decimal("888000"),
        )
        # This would also trigger LIQ<30K, but let's test with higher liq
        # to isolate the MCap/Liq gate
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("40000"),
            market_cap=Decimal("500000"),  # 12.5x ratio
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
        """Token with liq < $30K AND MCap/Liq > 10x → LIQ gate fires first."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("15000"),
            market_cap=Decimal("800000"),  # ratio = 53x
        )
        result = evaluate_signals(snapshot, _make_security())
        assert result.action == "avoid"
        # LIQ gate fires first (early return), MCap/Liq never reached
        assert "low_liquidity_gate" in result.reasons
        assert "extreme_mcap_liq_gate" not in result.reasons

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
        # Normal rules should fire
        assert result.net_score != -10


# --- Backtest Validation ---


class TestBacktestValidation:
    """Replicate real rug pull tokens to verify gates work as expected."""

    def test_mr_token_blocked(self):
        """Mr. — real rug ($23K liq, score 39) → blocked by LIQ gate."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("23317"),
            market_cap=Decimal("18242"),
            score=39,
            holders_count=418,
            buys_1h=1393,
            sells_1h=1029,
        )
        result = evaluate_signals(snapshot, _make_security())
        assert result.action == "avoid"
        assert "low_liquidity_gate" in result.reasons

    def test_conor_token_blocked(self):
        """Conor — real rug ($20K liq, $889K mcap, 44.7x) → blocked by LIQ gate."""
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
        # LIQ gate catches first
        assert "low_liquidity_gate" in result.reasons

    def test_hapepe_token_blocked(self):
        """HAPEPE — real rug ($22K liq, score 46) → blocked by LIQ gate."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("22105"),
            market_cap=Decimal("17210"),
            score=46,
            holders_count=450,
            buys_1h=767,
            sells_1h=377,
        )
        result = evaluate_signals(snapshot, _make_security())
        assert result.action == "avoid"
        assert "low_liquidity_gate" in result.reasons

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
