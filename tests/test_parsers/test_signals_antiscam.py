"""Test Phase 33/34 anti-scam filter rules.

Production data (24h, 2026-02-23):
- 356 closed paper positions, 176 (49.4%) closed as liquidity_removed (scam).
- Profitable trades: +63.56 SOL, scams: -119.91 SOL. Net: -56.35 SOL.

Phase 33 rules:
- R61: compound scam fingerprint (3+ red flags) → hard avoid
- R60: holders <= 5 → -3 penalty
- R62: unsecured LP on fresh token → -3
- R63: copycat rugged symbol → -6 (single rug)
- R64: serial deployer mild (2 dead tokens) → -2
- R26 threshold lowered 5→3, R27 weight -1→-2

Phase 34 rules:
- R63 upgraded: 2+ prior rugs → hard avoid (copycat_serial_scam)
- R63 single rug: net capped at watch (net≤4, cannot get buy/strong_buy)
- R65: abnormal buys_per_holder ratio ≥ 3.5 on fresh tokens → -3
- Redis-backed copycat dict (persistence across restarts)

Note: HG3 (holders <= 2 hard gate) was REMOVED after backtest showed
false positives: Golem +145%, PolyClaw +137%, SIXCODES +101.6% all had
1-2 holders at INITIAL. Low holders handled by R60 soft penalty only.
"""

from decimal import Decimal

from src.models.token import CreatorProfile, TokenSecurity, TokenSnapshot
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
        "lp_locked": False,
        "contract_renounced": True,
        "sell_tax": Decimal("0"),
        "top10_holders_pct": Decimal("20"),
    }
    defaults.update(kwargs)
    return TokenSecurity(**defaults)


# --- HG3 REMOVED: Low holders handled by R60 soft penalty only ---
# Backtest showed false positives: Golem +145% (2 holders),
# PolyClaw +137% (1 holder), SIXCODES +101.6% (1 holder).


class TestMinHoldersGateRemoved:
    """Verify HG3 hard gate is removed — low holders get R60 soft penalty only."""

    def test_holders_1_not_hard_blocked(self):
        """1 holder → NOT hard-blocked anymore (R60 soft penalty only)."""
        snapshot = _make_snapshot(holders_count=1)
        result = evaluate_signals(snapshot, _make_security())
        assert "min_holders_gate" not in result.reasons
        assert "low_holders" in result.reasons  # R60 fires instead

    def test_holders_2_not_hard_blocked(self):
        """2 holders → NOT hard-blocked anymore (R60 soft penalty only)."""
        snapshot = _make_snapshot(holders_count=2)
        result = evaluate_signals(snapshot, _make_security())
        assert "min_holders_gate" not in result.reasons
        assert "low_holders" in result.reasons  # R60 fires instead

    def test_holders_0_passes(self):
        """holders=0 → no data, no penalty at all."""
        snapshot = _make_snapshot(holders_count=0)
        result = evaluate_signals(snapshot, _make_security())
        assert "min_holders_gate" not in result.reasons
        assert "low_holders" not in result.reasons


# --- R60: Low Holders Penalty ---


class TestLowHoldersPenalty:
    """R60: holders 3-5 → -3 soft penalty."""

    def test_holders_3_penalty(self):
        """3 holders → -3."""
        snapshot = _make_snapshot(holders_count=3)
        result = evaluate_signals(snapshot, _make_security())
        assert "low_holders" in result.reasons
        low_holders_rule = [r for r in result.rules_fired if r.name == "low_holders"]
        assert len(low_holders_rule) == 1
        assert low_holders_rule[0].weight == -3

    def test_holders_5_penalty(self):
        """5 holders → -3."""
        snapshot = _make_snapshot(holders_count=5)
        result = evaluate_signals(snapshot, _make_security())
        assert "low_holders" in result.reasons

    def test_holders_6_no_penalty(self):
        """6 holders → no low_holders penalty."""
        snapshot = _make_snapshot(holders_count=6)
        result = evaluate_signals(snapshot, _make_security())
        assert "low_holders" not in result.reasons

    def test_holders_5_with_strong_bullish_can_buy(self):
        """holders=5 (-3) but strong bullish signals → can still buy."""
        snapshot = _make_snapshot(
            holders_count=5,
            score=70,  # R1 +3
            smart_wallets_count=3,  # R3 +3
            buys_5m=60,  # R43 +3
            buys_1h=60,
            sells_1h=15,  # R2 +2
            volume_5m=Decimal("100000"),  # R46 +2
        )
        sec = _make_security()
        result = evaluate_signals(
            snapshot, sec,
            token_age_minutes=2.0,
        )
        # low_holders -3, but bullish should be strong enough
        assert "low_holders" in result.reasons
        # Should still get positive net from bullish rules
        assert result.bullish_score > result.bearish_score


# --- R61: Compound Scam Fingerprint ---


class TestCompoundScamFingerprint:
    """R61: 3+ scam flags → hard avoid."""

    def test_three_flags_blocked(self):
        """LP unsecured + mintable + bundled → avoid."""
        snapshot = _make_snapshot()
        sec = _make_security(
            lp_burned=False, lp_locked=False,
            is_mintable=True,
        )
        result = evaluate_signals(
            snapshot, sec,
            raydium_lp_burned=False,
            bundled_buy_detected=True,
        )
        assert result.action == "avoid"
        assert "compound_scam_fingerprint" in result.reasons
        assert result.net_score == -10

    def test_four_flags_blocked(self):
        """LP unsecured + mintable + serial deployer + sybil → avoid."""
        snapshot = _make_snapshot()
        sec = _make_security(
            lp_burned=False, lp_locked=False,
            is_mintable=True,
        )
        result = evaluate_signals(
            snapshot, sec,
            raydium_lp_burned=False,
            pumpfun_dead_tokens=5,
            fee_payer_sybil_score=0.6,
        )
        assert result.action == "avoid"
        assert "compound_scam_fingerprint" in result.reasons

    def test_two_flags_not_blocked(self):
        """Only 2 flags → passes to normal evaluation."""
        snapshot = _make_snapshot()
        sec = _make_security(
            lp_burned=False, lp_locked=False,
            is_mintable=True,
        )
        result = evaluate_signals(
            snapshot, sec,
            raydium_lp_burned=False,
        )
        assert "compound_scam_fingerprint" not in result.reasons

    def test_zero_flags_passes(self):
        """Clean token → no compound gate."""
        snapshot = _make_snapshot()
        sec = _make_security()  # default: lp_burned=True, not mintable
        result = evaluate_signals(snapshot, sec)
        assert "compound_scam_fingerprint" not in result.reasons

    def test_profitable_token_not_hit(self):
        """Simulated profitable token (LP burned, renounced, clean) → NOT blocked."""
        snapshot = _make_snapshot(
            holders_count=50,
            score=65,
            smart_wallets_count=2,
        )
        sec = _make_security(
            lp_burned=True,
            contract_renounced=True,
            is_mintable=False,
        )
        result = evaluate_signals(
            snapshot, sec,
            raydium_lp_burned=True,
            bundled_buy_detected=False,
            pumpfun_dead_tokens=0,
            fee_payer_sybil_score=0.0,
            rugcheck_danger_count=0,
        )
        assert "compound_scam_fingerprint" not in result.reasons
        assert result.action != "avoid"


# --- R62: Unsecured LP on Fresh Token ---


class TestUnsecuredLpFresh:
    """R62: LP not burned + age < 10m + holders < 30 → -3."""

    def test_fresh_unsecured_penalty(self):
        """LP not burned, age=3m, holders=15 → -3."""
        snapshot = _make_snapshot(holders_count=15)
        sec = _make_security(lp_burned=False, lp_locked=False)
        result = evaluate_signals(
            snapshot, sec,
            raydium_lp_burned=False,
            token_age_minutes=3.0,
        )
        assert "unsecured_lp_fresh" in result.reasons
        rule = [r for r in result.rules_fired if r.name == "unsecured_lp_fresh"]
        assert rule[0].weight == -3

    def test_old_token_no_penalty(self):
        """LP not burned, age=15m → no penalty (age >= 10m)."""
        snapshot = _make_snapshot(holders_count=15)
        sec = _make_security(lp_burned=False, lp_locked=False)
        result = evaluate_signals(
            snapshot, sec,
            raydium_lp_burned=False,
            token_age_minutes=15.0,
        )
        assert "unsecured_lp_fresh" not in result.reasons

    def test_many_holders_no_penalty(self):
        """LP not burned, age=3m, holders=50 → no penalty (holders >= 30)."""
        snapshot = _make_snapshot(holders_count=50)
        sec = _make_security(lp_burned=False, lp_locked=False)
        result = evaluate_signals(
            snapshot, sec,
            raydium_lp_burned=False,
            token_age_minutes=3.0,
        )
        assert "unsecured_lp_fresh" not in result.reasons

    def test_lp_burned_no_penalty(self):
        """LP burned, age=3m, holders=15 → no penalty."""
        snapshot = _make_snapshot(holders_count=15)
        sec = _make_security(lp_burned=True)
        result = evaluate_signals(
            snapshot, sec,
            raydium_lp_burned=True,
            token_age_minutes=3.0,
        )
        assert "unsecured_lp_fresh" not in result.reasons


# --- R63: Copycat Rugged Symbol (Phase 33 + Phase 34 upgrade) ---


class TestCopycatRuggedSymbol:
    """R63: copycat detection — single rug -6 + cap, 2+ rugs hard avoid."""

    def test_copycat_single_rug_penalty(self):
        """Single prior rug → -6 penalty (not hard avoid)."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            copycat_rugged=True,
            copycat_rug_count=1,
        )
        assert "copycat_rugged_symbol" in result.reasons
        assert "copycat_serial_scam" not in result.reasons
        rule = [r for r in result.rules_fired if r.name == "copycat_rugged_symbol"]
        assert rule[0].weight == -6

    def test_copycat_serial_2_rugs_hard_avoid(self):
        """2 prior rugs → hard avoid (copycat_serial_scam)."""
        snapshot = _make_snapshot(
            score=70,
            holders_count=200,
            smart_wallets_count=5,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            copycat_rugged=True,
            copycat_rug_count=2,
        )
        assert result.action == "avoid"
        assert "copycat_serial_scam" in result.reasons
        assert result.net_score == -10
        # Hard avoid = early return, only 1 rule
        assert len(result.rules_fired) == 1

    def test_copycat_serial_9_rugs_hard_avoid(self):
        """9 prior rugs (CASH×9) → hard avoid."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            copycat_rugged=True,
            copycat_rug_count=9,
        )
        assert result.action == "avoid"
        assert "copycat_serial_scam" in result.reasons

    def test_copycat_single_rug_capped_at_watch(self):
        """Single rug + very strong bullish → capped at watch (net≤4)."""
        snapshot = _make_snapshot(
            score=75,  # +3
            holders_count=100,
            smart_wallets_count=5,  # +3
            buys_5m=80,  # +3 explosive_buy_velocity
            buys_1h=200,
            sells_1h=30,  # +2 buy_pressure
            volume_5m=Decimal("300000"),  # +2 volume_spike_ratio
        )
        sec = _make_security()
        result = evaluate_signals(
            snapshot, sec,
            copycat_rugged=True,
            copycat_rug_count=1,
            token_age_minutes=2.0,
        )
        assert "copycat_rugged_symbol" in result.reasons
        # Net should be capped at 4 (max watch)
        assert result.net_score <= 4
        assert result.action in ("watch", "avoid")
        # Definitely NOT buy or strong_buy
        assert result.action not in ("buy", "strong_buy")

    def test_no_copycat_no_penalty(self):
        """No copycat → no penalty."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            copycat_rugged=False,
        )
        assert "copycat_rugged_symbol" not in result.reasons
        assert "copycat_serial_scam" not in result.reasons

    def test_copycat_rug_count_0_no_penalty(self):
        """copycat_rugged=True but count=0 → treated as single rug."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            copycat_rugged=True,
            copycat_rug_count=0,
        )
        # count=0 < 2, so no serial hard avoid, but R63 single fires
        assert "copycat_rugged_symbol" in result.reasons


# --- R64: Serial Deployer Mild ---


class TestSerialDeployerMild:
    """R64: 2 dead tokens → -2. R26: 3+ → -3."""

    def test_two_dead_tokens_penalty(self):
        """2 dead tokens → R64 fires at -2."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            pumpfun_dead_tokens=2,
        )
        assert "serial_deployer_mild" in result.reasons
        assert "serial_deployer" not in result.reasons
        rule = [r for r in result.rules_fired if r.name == "serial_deployer_mild"]
        assert rule[0].weight == -2

    def test_one_dead_no_penalty(self):
        """1 dead token → neither R26 nor R64."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            pumpfun_dead_tokens=1,
        )
        assert "serial_deployer_mild" not in result.reasons
        assert "serial_deployer" not in result.reasons

    def test_three_dead_fires_r26(self):
        """3 dead tokens → R26 fires (-3), R64 does not."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            pumpfun_dead_tokens=3,
        )
        assert "serial_deployer" in result.reasons
        assert "serial_deployer_mild" not in result.reasons
        rule = [r for r in result.rules_fired if r.name == "serial_deployer"]
        assert rule[0].weight == -3


# --- R65: Abnormal Buys/Holder Ratio (Phase 34) ---


class TestAbnormalBuysPerHolder:
    """R65: buys_per_holder >= 3.5 on fresh token → -3."""

    def test_high_ratio_penalty(self):
        """buys=150, holders=30 → ratio 5.0 → -3."""
        snapshot = _make_snapshot(
            holders_count=30,
            buys_5m=150,
            sells_5m=10,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=2.0,
        )
        assert "abnormal_buys_per_holder" in result.reasons
        rule = [r for r in result.rules_fired if r.name == "abnormal_buys_per_holder"]
        assert rule[0].weight == -3

    def test_normal_ratio_no_penalty(self):
        """buys=40, holders=30 → ratio 1.33 → no penalty."""
        snapshot = _make_snapshot(
            holders_count=30,
            buys_5m=40,
            sells_5m=10,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=2.0,
        )
        assert "abnormal_buys_per_holder" not in result.reasons

    def test_exact_threshold_fires(self):
        """buys=35, holders=10 → ratio 3.5 → fires."""
        snapshot = _make_snapshot(
            holders_count=10,
            buys_5m=35,
            sells_5m=5,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=3.0,
        )
        assert "abnormal_buys_per_holder" in result.reasons

    def test_below_threshold_no_penalty(self):
        """buys=34, holders=10 → ratio 3.4 → no penalty."""
        snapshot = _make_snapshot(
            holders_count=10,
            buys_5m=34,
            sells_5m=5,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=3.0,
        )
        assert "abnormal_buys_per_holder" not in result.reasons

    def test_old_token_no_penalty(self):
        """buys=150, holders=30 → ratio 5.0 but age=10m → no penalty (age > 5m)."""
        snapshot = _make_snapshot(
            holders_count=30,
            buys_5m=150,
            sells_5m=10,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=10.0,
        )
        assert "abnormal_buys_per_holder" not in result.reasons

    def test_no_age_no_penalty(self):
        """buys=150, holders=30 → ratio 5.0 but no age info → no penalty."""
        snapshot = _make_snapshot(
            holders_count=30,
            buys_5m=150,
            sells_5m=10,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
        )
        assert "abnormal_buys_per_holder" not in result.reasons

    def test_zero_holders_no_penalty(self):
        """buys=50, holders=0 → skip (division guard)."""
        snapshot = _make_snapshot(
            holders_count=0,
            buys_5m=50,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=2.0,
        )
        assert "abnormal_buys_per_holder" not in result.reasons


# --- R27 Weight Change ---


class TestR27WeightChange:
    """R27: lp_not_burned weight changed from -1 to -2."""

    def test_r27_weight_is_minus_2(self):
        """LP not burned → weight=-2."""
        snapshot = _make_snapshot()
        sec = _make_security(lp_burned=False, lp_locked=False)
        result = evaluate_signals(
            snapshot, sec,
            raydium_lp_burned=False,
        )
        assert "lp_not_burned" in result.reasons
        rule = [r for r in result.rules_fired if r.name == "lp_not_burned"]
        assert rule[0].weight == -2


# --- R26 Threshold Change ---


class TestR26ThresholdChange:
    """R26: serial_deployer threshold lowered from 5 to 3."""

    def test_three_dead_fires(self):
        """3 dead tokens → R26 fires (was 5)."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            pumpfun_dead_tokens=3,
        )
        assert "serial_deployer" in result.reasons

    def test_four_dead_fires(self):
        """4 dead tokens → R26 fires."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            pumpfun_dead_tokens=4,
        )
        assert "serial_deployer" in result.reasons


# --- Backtest: Profitable Tokens Survive ---


class TestProfitableTokensSurvive:
    """Ensure known profitable tokens are NOT blocked by new rules."""

    def test_typical_profitable_high_holders(self):
        """Typical profitable token: 50+ holders, LP burned, clean → buy/strong_buy."""
        snapshot = _make_snapshot(
            holders_count=60,
            score=65,
            smart_wallets_count=2,
            buys_5m=40,
            sells_5m=10,
            buys_1h=40,
            sells_1h=10,
            volume_5m=Decimal("80000"),
        )
        sec = _make_security(
            lp_burned=True,
            contract_renounced=True,
            is_mintable=False,
        )
        result = evaluate_signals(
            snapshot, sec,
            raydium_lp_burned=True,
            bundled_buy_detected=False,
            pumpfun_dead_tokens=0,
            fee_payer_sybil_score=0.0,
            token_age_minutes=1.5,
        )
        assert "compound_scam_fingerprint" not in result.reasons
        assert "low_holders" not in result.reasons
        assert "abnormal_buys_per_holder" not in result.reasons
        assert result.action in ("buy", "strong_buy", "watch")

    def test_profitable_low_liq_token(self):
        """Low liquidity profitable token: liq=$14K, 30 holders → not blocked."""
        snapshot = _make_snapshot(
            liquidity_usd=Decimal("14000"),
            holders_count=30,
            score=60,
            buys_5m=25,
            sells_5m=5,
            buys_1h=25,
            sells_1h=5,
            volume_5m=Decimal("20000"),
        )
        sec = _make_security(
            lp_burned=False, lp_locked=False,
            contract_renounced=True,
            is_mintable=False,
        )
        result = evaluate_signals(
            snapshot, sec,
            raydium_lp_burned=False,
            token_age_minutes=2.0,
        )
        assert "compound_scam_fingerprint" not in result.reasons
        # unsecured_lp_fresh should NOT fire because holders >= 30
        assert "unsecured_lp_fresh" not in result.reasons

    def test_profitable_with_mutable_metadata(self):
        """Profitable token with mutable metadata (common for fresh memecoins)."""
        snapshot = _make_snapshot(
            holders_count=45,
            score=62,
            smart_wallets_count=3,
            buys_5m=50,
            sells_5m=12,
            buys_1h=50,
            sells_1h=12,
        )
        sec = _make_security(
            lp_burned=True,
            contract_renounced=False,
            is_mintable=False,
        )
        result = evaluate_signals(
            snapshot, sec,
            raydium_lp_burned=True,
            metaplex_mutable=True,
            token_age_minutes=1.0,
        )
        assert "compound_scam_fingerprint" not in result.reasons
        assert result.action != "avoid"

    def test_profitable_organic_high_buys(self):
        """Profitable token with high buys/holder but ratio < 3.5 → no R65."""
        snapshot = _make_snapshot(
            holders_count=50,
            score=68,
            smart_wallets_count=3,
            buys_5m=150,  # 150/50 = 3.0 < 3.5
            sells_5m=20,
            buys_1h=200,
            sells_1h=30,
        )
        sec = _make_security()
        result = evaluate_signals(
            snapshot, sec,
            token_age_minutes=1.5,
        )
        assert "abnormal_buys_per_holder" not in result.reasons
        assert result.action in ("buy", "strong_buy")


# --- Backtest: Known Scam Tokens Blocked ---


class TestScamTokensBlocked:
    """Verify known scam patterns ARE caught by new rules."""

    def test_scam_2_holders_penalized(self):
        """Scam with 2 holders → R60 low_holders penalty (-3) + LP penalties."""
        snapshot = _make_snapshot(
            holders_count=2,
            liquidity_usd=Decimal("180000"),
            market_cap=Decimal("270000"),
        )
        sec = _make_security(lp_burned=False, lp_locked=False)
        result = evaluate_signals(
            snapshot, sec,
            raydium_lp_burned=False,
            token_age_minutes=2.0,
        )
        # R60 soft penalty fires
        assert "low_holders" in result.reasons
        # lp_not_burned also fires
        assert "lp_not_burned" in result.reasons
        # unsecured_lp_fresh fires (age < 10m, holders < 30)
        assert "unsecured_lp_fresh" in result.reasons
        # Combined bearish should be heavy: low_holders(-3) + lp_not_burned(-2) + unsecured_lp_fresh(-3) = -8
        assert result.bearish_score >= 8

    def test_scam_compound_flags_blocked(self):
        """Scam: LP unsecured + mintable + bundled + sybil → compound gate."""
        snapshot = _make_snapshot(holders_count=15)
        sec = _make_security(
            lp_burned=False, lp_locked=False,
            is_mintable=True,
        )
        result = evaluate_signals(
            snapshot, sec,
            raydium_lp_burned=False,
            bundled_buy_detected=True,
            fee_payer_sybil_score=0.6,
        )
        assert result.action == "avoid"
        assert "compound_scam_fingerprint" in result.reasons

    def test_scam_copycat_single_rug_capped(self):
        """Copycat single rug + strong bullish → capped at watch."""
        snapshot = _make_snapshot(
            holders_count=100,
            score=70,
            smart_wallets_count=3,
            buys_5m=60,
            buys_1h=60,
            sells_1h=15,
        )
        sec = _make_security()
        result = evaluate_signals(
            snapshot, sec,
            copycat_rugged=True,
            copycat_rug_count=1,
            token_age_minutes=2.0,
        )
        assert "copycat_rugged_symbol" in result.reasons
        # Even with strong bullish, capped at watch
        assert result.action not in ("buy", "strong_buy")

    def test_scam_serial_copycat_hard_avoid(self):
        """Serial copycat (2+ rugs) → hard avoid regardless of bullish."""
        snapshot = _make_snapshot(
            holders_count=500,
            score=80,
            smart_wallets_count=5,
            buys_5m=200,
            buys_1h=500,
            sells_1h=50,
        )
        sec = _make_security()
        result = evaluate_signals(
            snapshot, sec,
            copycat_rugged=True,
            copycat_rug_count=4,
            token_age_minutes=1.0,
        )
        assert result.action == "avoid"
        assert "copycat_serial_scam" in result.reasons

    def test_scam_bot_farmed_buys(self):
        """Bot-farmed token: 200 buys from 20 holders (ratio 10.0) → R65 fires."""
        snapshot = _make_snapshot(
            holders_count=20,
            buys_5m=200,
            sells_5m=5,
        )
        result = evaluate_signals(
            snapshot, _make_security(),
            token_age_minutes=1.0,
        )
        assert "abnormal_buys_per_holder" in result.reasons
