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
- R63 tiered: 1-2 rugs → -6, 3+ rugs → -8 (NEVER hard avoid — backtest
  showed 86/153 profitable positions share symbols with 2+ rugs)
- R65: abnormal buys_per_holder ratio ≥ 3.5 on fresh tokens → -3
- Redis-backed copycat dict (persistence across restarts)
- Copycat cap/hard-avoid REMOVED: popular memes produce both scams and profits

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
    """R63: copycat detection — tiered penalty, NEVER hard avoid.

    Backtest: 86/153 profitable positions share symbols with 2+ rugs.
    Popular memes (MOSS, CLAW, agent1c, Grok-1) are both scam and legit.
    Hard avoid was REMOVED — tiered soft penalty only.
    """

    def test_copycat_single_rug_penalty(self):
        """Single prior rug → -6 penalty (soft, overridable)."""
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

    def test_copycat_serial_3_rugs_heavy_penalty(self):
        """3+ prior rugs → -8 penalty (heavy but NOT hard avoid)."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            copycat_rugged=True,
            copycat_rug_count=3,
        )
        assert "copycat_serial_scam" in result.reasons
        rule = [r for r in result.rules_fired if r.name == "copycat_serial_scam"]
        assert rule[0].weight == -8
        # NOT hard avoid — token continues normal evaluation
        assert len(result.rules_fired) > 1  # other rules also fire

    def test_copycat_serial_9_rugs_heavy_penalty(self):
        """9 prior rugs (CASH×9) → -8 penalty, still soft."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            copycat_rugged=True,
            copycat_rug_count=9,
        )
        assert "copycat_serial_scam" in result.reasons
        rule = [r for r in result.rules_fired if r.name == "copycat_serial_scam"]
        assert rule[0].weight == -8

    def test_copycat_10_rugs_extreme_penalty(self):
        """Phase 35: 10 prior rugs → -10 (new tier)."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            copycat_rugged=True,
            copycat_rug_count=10,
        )
        assert "copycat_extreme_scam" in result.reasons
        rule = [r for r in result.rules_fired if r.name == "copycat_extreme_scam"]
        assert rule[0].weight == -10

    def test_copycat_50_rugs_mass_penalty(self):
        """Phase 35: 50+ prior rugs → -12 (max tier)."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            copycat_rugged=True,
            copycat_rug_count=50,
        )
        assert "copycat_mass_scam" in result.reasons
        rule = [r for r in result.rules_fired if r.name == "copycat_mass_scam"]
        assert rule[0].weight == -12

    def test_copycat_126_rugs_cleus_case(self):
        """Phase 35: CLEUS with 126 rugged addresses → -12 mass scam."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            copycat_rugged=True,
            copycat_rug_count=126,
        )
        assert "copycat_mass_scam" in result.reasons
        rule = [r for r in result.rules_fired if r.name == "copycat_mass_scam"]
        assert rule[0].weight == -12

    def test_copycat_2_rugs_still_single_tier(self):
        """2 prior rugs → -6 (single tier, serial starts at 3+)."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            copycat_rugged=True,
            copycat_rug_count=2,
        )
        assert "copycat_rugged_symbol" in result.reasons
        assert "copycat_serial_scam" not in result.reasons
        rule = [r for r in result.rules_fired if r.name == "copycat_rugged_symbol"]
        assert rule[0].weight == -6

    def test_copycat_strong_bullish_can_still_buy(self):
        """Copycat -6 + strong bullish → can still be buy/strong_buy.

        Backtest: CLEUS +140.4% had copycat flag, was strong_buy.
        This is INTENTIONAL — popular meme symbols produce profits too.
        """
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
        # Strong bullish CAN override copycat penalty
        assert result.action in ("buy", "strong_buy")

    def test_no_copycat_no_penalty(self):
        """No copycat → no penalty."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            copycat_rugged=False,
        )
        assert "copycat_rugged_symbol" not in result.reasons
        assert "copycat_serial_scam" not in result.reasons

    def test_copycat_rug_count_0_fires_single(self):
        """copycat_rugged=True but count=0 → single tier -6."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            copycat_rugged=True,
            copycat_rug_count=0,
        )
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

    def test_scam_copycat_with_weak_bullish_becomes_avoid(self):
        """Copycat -6 on weak token → bearish wins → avoid."""
        snapshot = _make_snapshot(
            holders_count=15,
            score=45,
        )
        sec = _make_security(lp_burned=False, lp_locked=False)
        result = evaluate_signals(
            snapshot, sec,
            copycat_rugged=True,
            copycat_rug_count=1,
            raydium_lp_burned=False,
            token_age_minutes=1.0,
        )
        assert "copycat_rugged_symbol" in result.reasons
        # Weak bullish + heavy bearish (copycat -6 + lp_not_burned -2 + unsecured_lp_fresh -3)
        assert result.bearish_score >= 6

    def test_scam_serial_copycat_heavy_penalty(self):
        """Serial copycat (3-9 rugs) → -8 penalty (but NOT hard avoid).

        Backtest showed 86/153 profitable positions had 2+ rugs.
        Popular memes (MOSS, CLAW) produce both scams and profits.
        """
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
        assert "copycat_serial_scam" in result.reasons
        # Heavy penalty but bullish can still override
        rule = [r for r in result.rules_fired if r.name == "copycat_serial_scam"]
        assert rule[0].weight == -8

    def test_scam_mass_copycat_126_becomes_avoid(self):
        """Phase 35: Mass copycat 126 rugs (-12) on weak token → avoid."""
        snapshot = _make_snapshot(
            holders_count=50,
            score=50,
        )
        sec = _make_security(lp_burned=False, lp_locked=False)
        result = evaluate_signals(
            snapshot, sec,
            copycat_rugged=True,
            copycat_rug_count=126,
            raydium_lp_burned=False,
            token_age_minutes=1.0,
        )
        assert "copycat_mass_scam" in result.reasons
        # -12 copycat + -2 lp_not_burned + -3 unsecured_lp_fresh = -17 bearish
        assert result.bearish_score >= 12
        assert result.action == "avoid"

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


# ── Phase 35: Liquidity split $5K-$8K vs $8K-$20K ──────────────────────


class TestLiquiditySplit:
    """Phase 35: R10a split — $5K-$8K → -3, $8K-$20K → -2."""

    def test_liq_6k_very_low_penalty(self):
        """$6K liquidity → very_low_liquidity -3."""
        snapshot = _make_snapshot(liquidity_usd=6000)
        result = evaluate_signals(snapshot, _make_security())
        assert "very_low_liquidity" in result.reasons
        rule = [r for r in result.rules_fired if r.name == "very_low_liquidity"]
        assert len(rule) == 1
        assert rule[0].weight == -3

    def test_liq_8k_low_soft_penalty(self):
        """$8K liquidity → low_liquidity_soft -2 (not -3)."""
        snapshot = _make_snapshot(liquidity_usd=8000)
        result = evaluate_signals(snapshot, _make_security())
        assert "low_liquidity_soft" in result.reasons
        rule = [r for r in result.rules_fired if r.name == "low_liquidity_soft"]
        assert len(rule) == 1
        assert rule[0].weight == -2

    def test_liq_12k_low_soft_penalty(self):
        """$12K liquidity → low_liquidity_soft -2."""
        snapshot = _make_snapshot(liquidity_usd=12000)
        result = evaluate_signals(snapshot, _make_security())
        assert "low_liquidity_soft" in result.reasons

    def test_liq_25k_no_penalty(self):
        """$25K liquidity → no liquidity penalty."""
        snapshot = _make_snapshot(liquidity_usd=25000)
        result = evaluate_signals(snapshot, _make_security())
        assert "very_low_liquidity" not in result.reasons
        assert "low_liquidity_soft" not in result.reasons

    def test_liq_4999_hard_reject(self):
        """$4,999 liquidity → below $5K hard gate."""
        snapshot = _make_snapshot(liquidity_usd=4999)
        result = evaluate_signals(snapshot, _make_security())
        # Below $5K → low_liquidity_gate fires (-10)
        assert "low_liquidity_gate" in result.reasons
        assert result.action == "avoid"


# ── Phase 35: Backtest profitable tokens survive new rules ──────────────


class TestPhase35BacktestProfitable:
    """Backtest: verify profitable tokens are NOT blocked by Phase 35 changes.

    Each test recreates real conditions from production profitable positions.
    """

    def test_punchdance_8k_liq_survives(self):
        """punchDance: liq=$8,281, +127% profit → must NOT be blocked.

        With R10a split: $8K-$20K → -2 (unchanged). Token had strong bullish.
        """
        snapshot = _make_snapshot(
            liquidity_usd=8281,
            holders_count=200,
            score=65,
            smart_wallets_count=3,
            buys_5m=80,
            buys_1h=300,
            sells_1h=30,
        )
        sec = _make_security()
        result = evaluate_signals(snapshot, sec, token_age_minutes=2.0)
        # Must not be avoid
        assert result.action != "avoid", (
            f"punchDance-like token blocked! action={result.action}, "
            f"net={result.net_score}, rules={result.reasons}"
        )

    def test_cleus_1_rug_survives(self):
        """CLEUS with 1 rug count (+140% profit) → copycat -6, but strong bullish wins."""
        snapshot = _make_snapshot(
            holders_count=300,
            score=75,
            smart_wallets_count=4,
            buys_5m=100,
            buys_1h=400,
            sells_1h=40,
        )
        sec = _make_security()
        result = evaluate_signals(
            snapshot, sec,
            copycat_rugged=True,
            copycat_rug_count=1,
            token_age_minutes=2.0,
        )
        assert result.action != "avoid", (
            f"CLEUS-like (1 rug) blocked! action={result.action}, "
            f"net={result.net_score}, rules={result.reasons}"
        )

    def test_mirror_score0_survives(self):
        """MIRROR: score=0 at INITIAL, +146% profit → must pass.

        Score=0 is normal for most tokens at INITIAL stage.
        """
        snapshot = _make_snapshot(
            liquidity_usd=30000,
            holders_count=150,
            score=0,
            buys_5m=50,
            buys_1h=200,
            sells_1h=20,
        )
        sec = _make_security()
        result = evaluate_signals(snapshot, sec, token_age_minutes=1.0)
        assert result.action != "avoid", (
            f"MIRROR-like (score=0) blocked! action={result.action}, "
            f"net={result.net_score}, rules={result.reasons}"
        )

    def test_slc_low_liq_survives(self):
        """SLC: liq=$7,200, +113% profit → $5K-$8K → -3, but bullish wins."""
        snapshot = _make_snapshot(
            liquidity_usd=7200,
            holders_count=250,
            score=70,
            smart_wallets_count=3,
            buys_5m=90,
            buys_1h=350,
            sells_1h=35,
        )
        sec = _make_security()
        result = evaluate_signals(snapshot, sec, token_age_minutes=2.0)
        assert result.action != "avoid", (
            f"SLC-like token blocked! action={result.action}, "
            f"net={result.net_score}, rules={result.reasons}"
        )

    def test_cleus_126_rugs_blocked(self):
        """CLEUS ×126 (scam): 126 rugs → -12, weak token → avoid."""
        snapshot = _make_snapshot(
            holders_count=50,
            score=45,
            buys_5m=30,
        )
        sec = _make_security(lp_burned=False, lp_locked=False)
        result = evaluate_signals(
            snapshot, sec,
            copycat_rugged=True,
            copycat_rug_count=126,
            raydium_lp_burned=False,
            token_age_minutes=1.0,
        )
        assert result.action == "avoid", (
            f"CLEUS×126 NOT blocked! action={result.action}, "
            f"net={result.net_score}, rules={result.reasons}"
        )


# --- Phase 38: Compound Copycat Guard + Fresh Unsecured LP Cap ---


class TestPhase38CompoundCopycatGuard:
    """Phase 38: copycat in compound fingerprint + R66 + fresh unsecured LP cap."""

    # -- Guard 1: Copycat in compound scam fingerprint --

    def test_copycat_lp_unsecured_rugcheck_compound_avoid(self):
        """MEMELORD pattern: copycat + LP_unsecured + rugcheck 2+ dangers → avoid."""
        snapshot = _make_snapshot(
            holders_count=50,
            score=50,
            buys_5m=80,
            sells_5m=15,
            buys_1h=80,
            sells_1h=15,
        )
        sec = _make_security(lp_burned=False, lp_locked=False)
        result = evaluate_signals(
            snapshot, sec,
            copycat_rugged=True,
            copycat_rug_count=6,
            raydium_lp_burned=False,
            rugcheck_danger_count=2,
            token_age_minutes=1.0,
        )
        assert result.action == "avoid"
        assert "compound_scam_fingerprint" in result.reasons

    def test_copycat_serial_lp_unsecured_compound_avoid(self):
        """CRIME pattern: copycat + serial_deployer + LP_unsecured → avoid."""
        snapshot = _make_snapshot(
            holders_count=50,
            score=50,
            buys_5m=80,
            buys_1h=80,
            sells_1h=15,
        )
        sec = _make_security(lp_burned=False, lp_locked=False)
        result = evaluate_signals(
            snapshot, sec,
            copycat_rugged=True,
            copycat_rug_count=3,
            raydium_lp_burned=False,
            pumpfun_dead_tokens=5,
            token_age_minutes=1.0,
        )
        assert result.action == "avoid"
        assert "compound_scam_fingerprint" in result.reasons

    def test_copycat_alone_not_compound(self):
        """Copycat alone (1 flag) does NOT trigger compound scam."""
        snapshot = _make_snapshot()
        sec = _make_security()  # LP burned = clean
        result = evaluate_signals(
            snapshot, sec,
            copycat_rugged=True,
            copycat_rug_count=3,
        )
        assert "compound_scam_fingerprint" not in result.reasons

    def test_copycat_plus_one_flag_not_compound(self):
        """Copycat + 1 other flag = 2 total < 3 threshold → not compound."""
        snapshot = _make_snapshot()
        sec = _make_security(lp_burned=False, lp_locked=False)
        result = evaluate_signals(
            snapshot, sec,
            copycat_rugged=True,
            copycat_rug_count=1,
            raydium_lp_burned=False,
        )
        # 2 flags: copycat + LP_unsecured < 3
        assert "compound_scam_fingerprint" not in result.reasons

    # -- Guard 2: R66 Copycat + serial deployer compound --

    def test_copycat_serial_compound_fires(self):
        """Copycat + serial deployer 2+ dead → R66 -4."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            copycat_rugged=True,
            copycat_rug_count=3,
            pumpfun_dead_tokens=3,
        )
        assert "copycat_serial_compound" in result.reasons
        rule = [r for r in result.rules_fired if r.name == "copycat_serial_compound"]
        assert len(rule) == 1
        assert rule[0].weight == -4

    def test_copycat_serial_compound_threshold_2(self):
        """Copycat + 2 dead tokens (threshold) → R66 fires."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            copycat_rugged=True,
            copycat_rug_count=1,
            pumpfun_dead_tokens=2,
        )
        assert "copycat_serial_compound" in result.reasons

    def test_copycat_no_serial_no_compound(self):
        """Copycat alone (no serial deployer) → R66 doesn't fire."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            copycat_rugged=True,
            copycat_rug_count=3,
            pumpfun_dead_tokens=0,
        )
        assert "copycat_serial_compound" not in result.reasons

    def test_serial_no_copycat_no_compound(self):
        """Serial deployer alone (no copycat) → R66 doesn't fire."""
        snapshot = _make_snapshot()
        result = evaluate_signals(
            snapshot, _make_security(),
            copycat_rugged=False,
            pumpfun_dead_tokens=5,
        )
        assert "copycat_serial_compound" not in result.reasons

    # -- Guard 3: Fresh unsecured LP cap REMOVED after backtest --
    # PumpFun tokens ALWAYS have lp_burned=NULL, lp_locked=false.
    # Backtest showed 56.7% of profitable positions would be blocked.
    # Guards 1+2 are sufficient.

    # -- Safety: Profitable tokens NOT affected --

    def test_cleus_140pct_not_blocked_by_phase38(self):
        """CLEUS +140%: copycat=1, LP burned → NOT blocked by any Phase 38 guard."""
        snapshot = _make_snapshot(
            holders_count=300,
            score=75,
            smart_wallets_count=4,
            buys_5m=100,
            buys_1h=400,
            sells_1h=40,
        )
        sec = _make_security()  # LP burned by default
        result = evaluate_signals(
            snapshot, sec,
            copycat_rugged=True,
            copycat_rug_count=1,
            token_age_minutes=2.0,
            raydium_lp_burned=True,
        )
        assert result.action != "avoid", (
            f"CLEUS +140% blocked by Phase 38! action={result.action}, "
            f"net={result.net_score}, rules={result.reasons}"
        )
        assert "compound_scam_fingerprint" not in result.reasons
        assert "copycat_serial_compound" not in result.reasons

    def test_punchdance_127pct_not_blocked(self):
        """punchDance +127%: LP burned, no copycat → unaffected."""
        snapshot = _make_snapshot(
            liquidity_usd=8281,
            holders_count=200,
            score=70,
            smart_wallets_count=3,
            buys_5m=60,
            buys_1h=250,
            sells_1h=25,
        )
        sec = _make_security()
        result = evaluate_signals(snapshot, sec, token_age_minutes=2.0)
        assert result.action != "avoid", (
            f"punchDance +127% blocked! action={result.action}, "
            f"net={result.net_score}"
        )

    def test_memelord_scam_blocked_by_phase38(self):
        """MEMELORD scam: copycat×6 + LP unsecured + rugcheck + serial → avoid."""
        snapshot = _make_snapshot(
            holders_count=50,
            score=50,
            buys_5m=80,
            sells_5m=15,
            buys_1h=80,
            sells_1h=15,
        )
        sec = _make_security(lp_burned=False, lp_locked=False)
        result = evaluate_signals(
            snapshot, sec,
            copycat_rugged=True,
            copycat_rug_count=6,
            raydium_lp_burned=False,
            rugcheck_danger_count=3,
            pumpfun_dead_tokens=5,
            token_age_minutes=1.0,
        )
        assert result.action == "avoid", (
            f"MEMELORD scam NOT blocked! action={result.action}, "
            f"net={result.net_score}, rules={result.reasons}"
        )
