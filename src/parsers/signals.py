"""Entry signal detection — rule-based pattern matching on enriched data.

Evaluates a freshly-enriched token snapshot against a set of rules.
If enough rules fire, a Signal record is created.

Pure function: no IO, returns a list of matched rules + recommended action.
"""

from dataclasses import dataclass
from decimal import Decimal

from src.models.token import CreatorProfile, TokenSecurity, TokenSnapshot
from src.parsers.jupiter.models import SellSimResult
from src.parsers.mint_parser import MintInfo


@dataclass
class SignalRule:
    """A named rule that fires when its condition is met."""

    name: str
    weight: int  # positive = bullish, negative = bearish
    description: str


@dataclass
class SignalResult:
    """Result of evaluating all rules against a snapshot."""

    rules_fired: list[SignalRule]
    bullish_score: int
    bearish_score: int
    net_score: int
    action: str  # "strong_buy", "buy", "watch", "avoid"
    reasons: dict[str, str]


def evaluate_signals(
    snapshot: TokenSnapshot,
    security: TokenSecurity | None,
    *,
    creator_profile: CreatorProfile | None = None,
    holder_velocity: float | None = None,
    prev_snapshot: TokenSnapshot | None = None,
    rugcheck_score: int | None = None,
    dev_holds_pct: float | None = None,
    jupiter_price: float | None = None,
    lp_removed_pct: float | None = None,
    cross_whale_detected: bool = False,
    volatility_5m: float | None = None,
    # Phase 12 parameters
    mint_info: MintInfo | None = None,
    sell_sim_result: SellSimResult | None = None,
    bundled_buy_detected: bool = False,
    pumpfun_dead_tokens: int | None = None,
    raydium_lp_burned: bool | None = None,
    goplus_is_honeypot: bool | None = None,
    # Phase 13 parameters
    fee_payer_sybil_score: float | None = None,
    funding_chain_risk: int | None = None,
    convergence_detected: bool = False,
    metadata_score: int | None = None,
    wash_trading_suspected: bool = False,
    goplus_critical_flags: list[str] | None = None,
    rugcheck_danger_count: int | None = None,
    bubblemaps_decentralization: float | None = None,
    # Phase 14B parameters
    jito_bundle_detected: bool = False,
    metaplex_mutable: bool | None = None,
    metaplex_has_homoglyphs: bool = False,
    rugcheck_insider_pct: float | None = None,
    solana_tracker_risk_score: int | None = None,
    jupiter_banned: bool = False,
    jupiter_strict: bool = False,
    # Phase 15B parameters — bullish velocity detection
    token_age_minutes: float | None = None,
    # Phase 16 parameters — community/social/LLM signals
    tg_member_count: int | None = None,
    has_website: bool | None = None,
    domain_age_days: int | None = None,
    llm_risk_score: int | None = None,
    holder_growth_pct: float | None = None,
    # Cross-validation
    solsniffer_score: int | None = None,
    # Phase 33/34 — anti-scam v2/v3
    copycat_rugged: bool = False,
    copycat_rug_count: int = 0,
    # Phase 46 — duplicate symbol detection
    symbol_frequency_1h: int = 0,
) -> SignalResult:
    """Evaluate all signal rules against enriched data.

    Returns a SignalResult with fired rules and recommended action.
    """
    fired: list[SignalRule] = []
    reasons: dict[str, str] = {}

    liq = float(snapshot.liquidity_usd or snapshot.dex_liquidity_usd or 0)
    holders = snapshot.holders_count or 0
    mcap = float(snapshot.market_cap or 0)
    score = snapshot.score or 0

    # --- HARD GATES (early reject — skip all other rules) ---
    # Backtest precision: LIQ<30K = 80%, MCap/Liq>10x = 100%.
    # All 3 real rugs (Mr., HAPEPE, Conor) + YABOOZARU dump caught.
    # Zero false positives on successful trades > $30K liq.

    # HG1: Minimum liquidity — hard gate only for very low liq (< $5K).
    # Many PumpFun tokens launch with $5-15K liq and pump to $50K+ mcap.
    # Old gate at $21K killed Gapple (liq=$14K, pumped to $52K mcap = +223% missed).
    # Soft penalty for $5K-$20K range instead of hard block.
    if 0 < liq < 5_000:
        gate_rule = SignalRule(
            "low_liquidity_gate", -10,
            f"Hard gate: liquidity ${liq:,.0f} < $5K (extremely thin)",
        )
        return SignalResult(
            rules_fired=[gate_rule],
            bullish_score=0,
            bearish_score=10,
            net_score=-10,
            action="avoid",
            reasons={gate_rule.name: gate_rule.description},
        )

    # HG2: Extreme MCap/Liq ratio — empty order book, no exit liquidity.
    # Conor: MCap $889K on $20K liq (44.7x) = classic pump & dump.
    # 100% precision in backtest (only caught losers, zero false positives).
    if liq > 0 and mcap > 0 and mcap / liq > 10:
        gate_rule = SignalRule(
            "extreme_mcap_liq_gate", -10,
            f"Hard gate: MCap/Liq ratio {mcap/liq:.1f}x > 10 (no exit liquidity)",
        )
        return SignalResult(
            rules_fired=[gate_rule],
            bullish_score=0,
            bearish_score=10,
            net_score=-10,
            action="avoid",
            reasons={gate_rule.name: gate_rule.description},
        )

    # HG3: Extreme rugcheck score — DATACLAW (41 tokens/hr, all rugs), NIP pattern.
    # Production backtest (34 real trades): 15K threshold catches DATACLAW ($-20.31)
    # without cutting any profitable tokens. Old 20K missed it (rc=17550).
    # Net benefit: +$28.68 (5 losses blocked, 3 profits lost via recheck).
    if rugcheck_score is not None and rugcheck_score > 15000:
        gate_rule = SignalRule(
            "extreme_rugcheck_gate", -10,
            f"Hard gate: rugcheck score {rugcheck_score} > 15000 (extreme scam)",
        )
        return SignalResult(
            rules_fired=[gate_rule],
            bullish_score=0,
            bearish_score=10,
            net_score=-10,
            action="avoid",
            reasons={gate_rule.name: gate_rule.description},
        )

    # HG4: Single holder ownership — one wallet holds majority of supply.
    # Production: 100% rug rate. Always a scam when combined with any risk.
    if (
        security is not None
        and security.rugcheck_risks
        and "single holder ownership" in security.rugcheck_risks.lower()
    ):
        gate_rule = SignalRule(
            "single_holder_gate", -10,
            "Hard gate: single holder ownership (one wallet controls supply)",
        )
        return SignalResult(
            rules_fired=[gate_rule],
            bullish_score=0,
            bearish_score=10,
            net_score=-10,
            action="avoid",
            reasons={gate_rule.name: gate_rule.description},
        )

    # HG5: Velocity scam gate — bot-farmed buys + high rugcheck + thin liquidity.
    # Real NIP: 1103 buys/5m, rugcheck 11400, liq $22K → -100% rug.
    # Backtest: catches ONLY Real NIP (-$20.37), zero false positives.
    # Bots nuke 200+ buys in 5 min to fake organic growth on scam tokens.
    buys_5m_val = snapshot.buys_5m or 0
    if (
        rugcheck_score is not None
        and rugcheck_score >= 5000
        and buys_5m_val >= 200
        and liq < 30_000
    ):
        gate_rule = SignalRule(
            "velocity_scam_gate", -10,
            f"Hard gate: velocity scam — {buys_5m_val} buys/5m + "
            f"rugcheck {rugcheck_score} + liq ${liq:,.0f}",
        )
        return SignalResult(
            rules_fired=[gate_rule],
            bullish_score=0,
            bearish_score=10,
            net_score=-10,
            action="avoid",
            reasons={gate_rule.name: gate_rule.description},
        )

    # HG5-old REMOVED: Duplicate symbol frequency gate (5+/hr) had too many false positives.
    # Backtest: blocked $157 profits vs $84 losses — net negative.

    # HG3 REMOVED: holders <= 2 hard gate had false positives in production
    # (Golem +145%, PolyClaw +137%, SIXCODES +101.6% — all had 1-2 holders).
    # Low holders are now handled by R60 soft penalty (-3 for holders <= 5).

    # R61: Compound scam fingerprint — if 3+ red flags fire simultaneously,
    # the token is almost certainly a rug. Each flag is individually weak
    # (common on fresh tokens), but 3+ combined is statistically impossible
    # on good tokens. Production: 0% false positive rate at 3+ flags.
    _scam_flags = 0
    _scam_details: list[str] = []

    if (raydium_lp_burned is not None and not raydium_lp_burned
            and security and not security.lp_burned and not security.lp_locked):
        _scam_flags += 1
        _scam_details.append("LP_unsecured")

    if security and security.is_mintable is True:
        _scam_flags += 1
        _scam_details.append("mintable")

    if bundled_buy_detected:
        _scam_flags += 1
        _scam_details.append("bundled_buy")

    if rugcheck_danger_count is not None and rugcheck_danger_count >= 2:
        _scam_flags += 1
        _scam_details.append(f"rugcheck_{rugcheck_danger_count}_dangers")

    # Phase 39: High rugcheck score as compound flag.
    # MOLTGEN: rugcheck 3501 but only 1 danger_count — missed the >= 2 gate above.
    # Production: 0 profitable tokens had rugcheck 3000-4999, all profits were 5000+ or <3000.
    # This flag stacks with LP_unsecured (MOLTGEN had both) → 2 flags from rug indicators.
    if rugcheck_score is not None and rugcheck_score >= 3000 and rugcheck_score < 5000:
        _scam_flags += 1
        _scam_details.append(f"rugcheck_score_{rugcheck_score}")

    if pumpfun_dead_tokens is not None and pumpfun_dead_tokens >= 3:
        _scam_flags += 1
        _scam_details.append(f"serial_{pumpfun_dead_tokens}_dead")

    if fee_payer_sybil_score is not None and fee_payer_sybil_score > 0.3:
        _scam_flags += 1
        _scam_details.append(f"sybil_{fee_payer_sybil_score:.0%}")

    # Phase 38: Copycat rugged symbol — adds combinatorial power.
    # Alone = 1 flag (harmless). But copycat + LP_unsecured + rugcheck = 3 → avoid.
    # MEMELORD: 6+ copycats all scored buy despite -8 penalty. With compound: hard avoid.
    if copycat_rugged:
        _scam_flags += 1
        _scam_details.append(f"copycat_{copycat_rug_count}x_rugged")

    # Phase 40/47: Holder concentration as compound flag.
    # Production: 34.2% rug rate with holder concentration vs 14.8% without (2.3x).
    # rugcheck_score >= 13000 WITHOUT "LP Unlocked" = concentrated ownership + no bonding curve.
    # Lowered from 20K to 13K (Phase 47) to catch CHILLHOUSE (rc=13500) pattern.
    if (
        security is not None
        and security.rugcheck_risks
        and rugcheck_score is not None
        and rugcheck_score >= 13000
    ):
        _r_lower = security.rugcheck_risks.lower()
        if (
            ("holder" in _r_lower or "ownership" in _r_lower)
            and "lp unlocked" not in _r_lower
        ):
            _scam_flags += 1
            _scam_details.append("holder_concentration")

    if _scam_flags >= 3:
        gate_rule = SignalRule(
            "compound_scam_fingerprint", -10,
            f"Compound scam: {_scam_flags} flags ({', '.join(_scam_details)})",
        )
        return SignalResult(
            rules_fired=[gate_rule],
            bullish_score=0,
            bearish_score=10,
            net_score=-10,
            action="avoid",
            reasons={gate_rule.name: gate_rule.description},
        )

    # --- BULLISH RULES ---

    # R1: High score (scoring model already validated this)
    if score >= 60:
        r = SignalRule("high_score", 3, f"Score {score} >= 60")
        fired.append(r)
        reasons[r.name] = r.description

    # R2: Strong buy pressure (buy/sell ratio)
    buys = snapshot.buys_1h or snapshot.buys_5m or 0
    sells = snapshot.sells_1h or snapshot.sells_5m or 0
    if sells > 0 and buys / sells >= 3.0:
        r = SignalRule("buy_pressure", 2, f"Buy/sell ratio {buys/sells:.1f}x")
        fired.append(r)
        reasons[r.name] = r.description

    # R3: Smart money presence
    sm = snapshot.smart_wallets_count or 0
    if sm >= 2:
        r = SignalRule("smart_money", 3, f"{sm} smart wallets in top holders")
        fired.append(r)
        reasons[r.name] = r.description

    # R4: Rapid holder growth
    if holder_velocity is not None and holder_velocity >= 50:
        r = SignalRule("holder_velocity", 2, f"Holder velocity {holder_velocity:.0f}/min")
        fired.append(r)
        reasons[r.name] = r.description

    # R5: Good liquidity
    if liq >= 50_000:
        r = SignalRule("strong_liquidity", 2, f"Liquidity ${liq:,.0f}")
        fired.append(r)
        reasons[r.name] = r.description

    # R6: Volume spike (high vol/liq ratio)
    vol = float(
        snapshot.volume_1h or snapshot.dex_volume_1h or snapshot.volume_5m or 0
    )
    if liq > 0 and vol / liq >= 2.0:
        r = SignalRule("volume_spike", 2, f"Vol/liq ratio {vol/liq:.1f}x")
        fired.append(r)
        reasons[r.name] = r.description

    # R7: Safe creator (low risk)
    if creator_profile and creator_profile.risk_score is not None:
        if creator_profile.risk_score < 20:
            r = SignalRule("safe_creator", 1, f"Creator risk {creator_profile.risk_score}")
            fired.append(r)
            reasons[r.name] = r.description

    # R8: Security cleared (LP burned/locked + renounced)
    if security:
        sec_flags = []
        if security.lp_burned or security.lp_locked:
            sec_flags.append("LP secured")
        if security.contract_renounced:
            sec_flags.append("renounced")
        if security.sell_tax is not None and security.sell_tax <= Decimal("5"):
            sec_flags.append("low tax")
        if len(sec_flags) >= 2:
            r = SignalRule("security_cleared", 3, ", ".join(sec_flags))
            fired.append(r)
            reasons[r.name] = r.description

    # R9: Price momentum (if we have previous snapshot)
    if prev_snapshot and prev_snapshot.price and snapshot.price:
        prev_p = float(prev_snapshot.price)
        curr_p = float(snapshot.price)
        if prev_p > 0:
            change_pct = (curr_p - prev_p) / prev_p * 100
            if change_pct >= 20:
                r = SignalRule("price_momentum", 2, f"Price +{change_pct:.0f}% since last check")
                fired.append(r)
                reasons[r.name] = r.description

    # --- BEARISH RULES ---

    # R10a: Soft low-liquidity penalty — tiered (Phase 35).
    # $5K-$8K: very thin pool, high rug risk → -3
    # $8K-$20K: risky but many profitable trades here → -2 (unchanged)
    # Gapple case: liq=$14K, scored 68, pumped 3x. Hard gate killed it.
    # Backtest: 14 profitable with liq<$10K (punchDance +127%, SLC +113%).
    if 5_000 <= liq < 8_000:
        r = SignalRule(
            "very_low_liquidity", -3,
            f"Very low liquidity ${liq:,.0f} (thin pool, high rug risk)",
        )
        fired.append(r)
        reasons[r.name] = r.description
    elif 8_000 <= liq < 20_000:
        r = SignalRule(
            "low_liquidity_soft", -2,
            f"Low liquidity ${liq:,.0f} (risky, but may pump)",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R10: Honeypot
    if security and security.is_honeypot:
        r = SignalRule("honeypot", -10, "Token is a honeypot")
        fired.append(r)
        reasons[r.name] = r.description

    # R11: Risky creator
    if creator_profile and creator_profile.risk_score is not None:
        if creator_profile.risk_score >= 60:
            r = SignalRule("risky_creator", -3, f"Creator risk score {creator_profile.risk_score}")
            fired.append(r)
            reasons[r.name] = r.description

    # R12: High concentration
    # NOTE: snapshot.top10_holders_pct is always NULL for PumpFun tokens (not populated
    # by enrichment). security.top10_holders_pct has data but on PumpFun top10 >= 95%
    # is the NORM (bonding curve). Phase 45 backtest showed any penalty here blocks
    # 72% of profitable positions. Kept as snapshot-only (effectively dormant until
    # enrichment populates the field with post-migration holder data).
    top10 = snapshot.top10_holders_pct
    if top10 is not None and top10 > Decimal("50"):
        r = SignalRule("high_concentration", -2, f"Top 10 hold {float(top10):.0f}%")
        fired.append(r)
        reasons[r.name] = r.description

    # R13: Tiny liquidity
    if 0 < liq < 5000:
        r = SignalRule("tiny_liquidity", -2, f"Liquidity only ${liq:,.0f}")
        fired.append(r)
        reasons[r.name] = r.description

    # R14: High sell tax
    if security and security.sell_tax is not None and security.sell_tax > Decimal("10"):
        r = SignalRule("high_sell_tax", -3, f"Sell tax {float(security.sell_tax):.0f}%")
        fired.append(r)
        reasons[r.name] = r.description

    # R60: Low holder count — very few real participants at evaluation time.
    # Production: 21.8% scam vs 3.7% good at holders <= 5 (5.9x ratio).
    # -3 is strong but not a hard gate — strong bullish can overcome.
    if 0 < holders <= 5:
        r = SignalRule(
            "low_holders", -3,
            f"Very few holders ({holders}) — likely bot-inflated or empty",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # --- PHASE 11 BEARISH RULES ---

    # R15: Rugcheck danger — tiered penalty based on severity.
    # Phase 39 tiers (MOLTGEN post-mortem: 3501 scored only -2, token rugged):
    #   50-2999:  -2 (noise — "Low LP Providers", "Mutable Metadata")
    #   3000-4999: -4 (high danger — ALL production rugs had 3500+, 0 profits in this range)
    #   5000+:    -5 (extreme — multiple critical dangers, rug pull certain)
    # Production data: 0 profitable tokens with rugcheck 3000-4999, 2 rugs (-100%).
    if rugcheck_score is not None and rugcheck_score >= 50:
        if rugcheck_score >= 5000:
            r = SignalRule(
                "rugcheck_danger", -5,
                f"Rugcheck score {rugcheck_score} (extreme danger)",
            )
        elif rugcheck_score >= 3000:
            r = SignalRule(
                "rugcheck_danger", -4,
                f"Rugcheck score {rugcheck_score} (high danger — rug pull range)",
            )
        else:
            r = SignalRule(
                "rugcheck_danger", -2,
                f"Rugcheck score {rugcheck_score} (dangerous)",
            )
        fired.append(r)
        reasons[r.name] = r.description

    # R15b: SolSniffer cross-validation
    if solsniffer_score is not None:
        if solsniffer_score < 30:
            r = SignalRule("solsniffer_danger", -4, f"SolSniffer score {solsniffer_score} (dangerous)")
            fired.append(r)
            reasons[r.name] = r.description
        elif solsniffer_score >= 80:
            r = SignalRule("solsniffer_safe", 2, f"SolSniffer score {solsniffer_score} (safe)")
            fired.append(r)
            reasons[r.name] = r.description

    # R16: High dev holds
    if dev_holds_pct is not None and dev_holds_pct >= 50:
        r = SignalRule("high_dev_holds", -2, f"Dev holds {dev_holds_pct:.0f}%")
        fired.append(r)
        reasons[r.name] = r.description

    # R17: Price manipulation (cross-source divergence)
    if jupiter_price is not None and snapshot.price is not None:
        gmgn_p = float(snapshot.price)
        if gmgn_p > 0:
            div = abs(gmgn_p - jupiter_price) / gmgn_p * 100
            if div > 20:
                r = SignalRule(
                    "price_manipulation", -3,
                    f"Price divergence {div:.0f}% (gmgn={gmgn_p:.8f}, jup={jupiter_price:.8f})",
                )
                fired.append(r)
                reasons[r.name] = r.description

    # R18: Volume dried up (skip for tokens younger than 30 minutes —
    # vol_1h physically can't exceed vol_5m by much when the token just launched)
    vol_5m_val = float(snapshot.volume_5m or snapshot.dex_volume_5m or 0)
    vol_1h_val = float(snapshot.volume_1h or snapshot.dex_volume_1h or 0)
    if vol_5m_val > 0 and vol_1h_val > 0:
        _skip_volume_check = (
            token_age_minutes is not None and token_age_minutes < 30
        )
        if not _skip_volume_check and vol_1h_val / vol_5m_val > 12:
            r = SignalRule(
                "volume_dried_up", -2,
                f"Volume dying: 1h/5m ratio {vol_1h_val/vol_5m_val:.1f}x (5m < 8% of 1h)",
            )
            fired.append(r)
            reasons[r.name] = r.description

    # R19: Holder deceleration
    if prev_snapshot and prev_snapshot.holders_count and snapshot.holders_count:
        prev_h = prev_snapshot.holders_count
        curr_h = snapshot.holders_count
        if prev_h > 0 and holder_velocity is not None:
            # Compare current holder growth to implied previous growth
            growth_rate = (curr_h - prev_h) / prev_h * 100
            if growth_rate < -5:
                r = SignalRule(
                    "holder_deceleration", -1,
                    f"Holders declining: {prev_h} -> {curr_h} ({growth_rate:+.1f}%)",
                )
                fired.append(r)
                reasons[r.name] = r.description

    # R20: LP removal active
    if lp_removed_pct is not None and lp_removed_pct >= 20:
        severity = "critical" if lp_removed_pct >= 50 else "warning"
        r = SignalRule(
            "lp_removal_active", -4,
            f"LP removed {lp_removed_pct:.0f}% ({severity})",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R21: Cross-token whale coordination
    if cross_whale_detected:
        r = SignalRule(
            "cross_token_coordination", -3,
            "Cross-token whale activity detected (coordinated pump suspected)",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # --- PHASE 11 BULLISH RULES ---

    # R22: Strong momentum (healthy growth pattern)
    if (
        volatility_5m is not None
        and volatility_5m < 10
        and snapshot.price is not None
        and prev_snapshot is not None
        and prev_snapshot.price is not None
        and float(prev_snapshot.price) > 0
    ):
        price_change = (float(snapshot.price) - float(prev_snapshot.price)) / float(prev_snapshot.price) * 100
        if price_change >= 10 and buys > 0 and sells > 0 and buys / sells >= 2.0:
            r = SignalRule(
                "strong_momentum", 2,
                f"Healthy growth: +{price_change:.0f}%, low vol ({volatility_5m:.0f}%), buy ratio {buys/sells:.1f}x",
            )
            fired.append(r)
            reasons[r.name] = r.description

    # --- PHASE 12 BEARISH RULES ---

    # R23: Token2022 with dangerous extensions
    if mint_info and mint_info.has_dangerous_extensions:
        ext_list = ", ".join(mint_info.dangerous_extensions)
        r = SignalRule(
            "token2022_danger", -3,
            f"Token2022 dangerous extensions: {ext_list}",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R24: Jupiter sell simulation failed
    if sell_sim_result and not sell_sim_result.sellable and sell_sim_result.error and not sell_sim_result.api_error:
        r = SignalRule(
            "sell_sim_failed", -5,
            f"Jupiter sell simulation failed: {sell_sim_result.error}",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R25: Bundled buy (coordinated first-block purchases)
    if bundled_buy_detected:
        r = SignalRule(
            "bundled_buy", -3,
            "Bundled buys detected: first-block buyers funded by creator",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R26: Serial deployer (creator has many dead tokens)
    # Phase 33: lowered threshold from 5 to 3 — production data shows
    # "Creator history of rugged tokens" in 14 scams vs 3 good (4.7x ratio).
    if pumpfun_dead_tokens is not None and pumpfun_dead_tokens >= 3:
        r = SignalRule(
            "serial_deployer", -3,
            f"Creator has {pumpfun_dead_tokens} dead tokens on pump.fun",
        )
        fired.append(r)
        reasons[r.name] = r.description
    # R64: Mild serial deployer — 2 dead tokens is suspicious but not conclusive.
    elif pumpfun_dead_tokens is not None and pumpfun_dead_tokens >= 2:
        r = SignalRule(
            "serial_deployer_mild", -2,
            f"Creator has {pumpfun_dead_tokens} dead tokens (suspicious)",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R27: LP not burned (Raydium verified) — Phase 33: weight -1 → -2.
    # -1 was trivially overcome by any single bullish rule. -2 is meaningful.
    if raydium_lp_burned is not None and not raydium_lp_burned:
        if security and not security.lp_burned and not security.lp_locked:
            r = SignalRule(
                "lp_not_burned", -2,
                "LP not burned or locked (Raydium verified)",
            )
            fired.append(r)
            reasons[r.name] = r.description

    # R62: Unsecured LP on fresh token — much stronger penalty than R27 alone.
    # Stacks with R27 (-2): combined -5 for fresh unsecured LP tokens.
    # Age < 10min AND holders < 30 prevents hitting established tokens.
    if (
        raydium_lp_burned is not None and not raydium_lp_burned
        and security and not security.lp_burned and not security.lp_locked
        and token_age_minutes is not None and token_age_minutes < 10
        and holders < 30
    ):
        r = SignalRule(
            "unsecured_lp_fresh", -3,
            f"Unsecured LP on fresh token ({token_age_minutes:.1f}m, {holders} holders)",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R28: GoPlus honeypot confirmation
    if goplus_is_honeypot is True:
        r = SignalRule(
            "goplus_honeypot", -10,
            "GoPlus confirms token is a honeypot",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # --- PHASE 13 BEARISH RULES ---

    # R29: No socials (from metadata scoring)
    if metadata_score is not None and metadata_score <= -3:
        r = SignalRule(
            "no_socials", -1,
            "No social links found (website, twitter, telegram)",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R30: Wash trading suspected via holder PnL analysis
    if wash_trading_suspected:
        r = SignalRule(
            "wash_trading_pnl", -3,
            "Wash trading suspected: most holders at loss while price rises",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R31: GoPlus critical risk flags (beyond honeypot)
    if goplus_critical_flags:
        flags_str = ", ".join(goplus_critical_flags[:3])
        r = SignalRule(
            "goplus_critical_risk", -5,
            f"GoPlus critical flags: {flags_str}",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R32: Multiple rugcheck danger-level risks
    if rugcheck_danger_count is not None and rugcheck_danger_count >= 3:
        r = SignalRule(
            "rugcheck_multi_danger", -3,
            f"Rugcheck: {rugcheck_danger_count} danger-level risks detected",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R33: Low decentralization (Bubblemaps)
    if bubblemaps_decentralization is not None and bubblemaps_decentralization < 0.3:
        r = SignalRule(
            "low_decentralization", -3,
            f"Bubblemaps decentralization score {bubblemaps_decentralization:.2f} (< 0.3)",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R34: Fee payer sybil attack
    if fee_payer_sybil_score is not None and fee_payer_sybil_score > 0.5:
        r = SignalRule(
            "fee_payer_sybil", -6,
            f"Sybil attack: {fee_payer_sybil_score:.0%} buyers share fee payer",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R35: Suspicious multi-hop funding chain
    if funding_chain_risk is not None and funding_chain_risk >= 60:
        r = SignalRule(
            "funding_chain_suspicious", -4,
            f"Suspicious funding chain: risk score {funding_chain_risk}",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R36: Token convergence (buyers send to one wallet)
    if convergence_detected:
        r = SignalRule(
            "token_convergence", -5,
            "Token convergence: >50% first-block buyers send to same destination",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # --- PHASE 14B BEARISH RULES ---

    # R37: Jito bundle snipe detected
    # Reduced from -6 to -3: many legit tokens have small snipers (2-3 bots)
    # which shouldn't kill the signal entirely. Large coordinated attacks
    # are still caught by fee_payer_sybil (-6) and other rules.
    if jito_bundle_detected:
        r = SignalRule(
            "jito_bundle_snipe", -3,
            "Jito MEV bundle snipe detected in first block",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R38: Mutable metadata (Metaplex) — reduced from -2 to -1 because
    # most fresh tokens start with mutable metadata; renounce comes later.
    # Not a scam indicator alone, just a minor risk factor.
    if metaplex_mutable is True:
        r = SignalRule(
            "mutable_metadata", -1,
            "Token metadata is mutable (creator can change name/image)",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R39: Name spoofing via homoglyphs
    if metaplex_has_homoglyphs:
        r = SignalRule(
            "name_spoofing", -5,
            "Homoglyph characters detected in token name (name spoofing)",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R40: High insider network (RugCheck insiders API)
    if rugcheck_insider_pct is not None and rugcheck_insider_pct >= 30:
        r = SignalRule(
            "high_insider_network", -4,
            f"RugCheck insider network: {rugcheck_insider_pct:.0f}% of top holders are insiders",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R41: Jupiter banned token
    if jupiter_banned:
        r = SignalRule(
            "jupiter_banned", -10,
            "Token is BANNED on Jupiter token list",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # --- PHASE 14B BULLISH RULES ---

    # R42: Jupiter strict verified
    if jupiter_strict:
        r = SignalRule(
            "jupiter_verified", 3,
            "Token is Jupiter STRICT verified (highest trust)",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # --- PHASE 15B BULLISH VELOCITY RULES ---

    # R43: Explosive buy velocity — 50+ buys in 5m = 10+ buys/min
    buys_5m = snapshot.buys_5m or 0
    sells_5m = snapshot.sells_5m or 0
    if buys_5m >= 50:
        r = SignalRule(
            "explosive_buy_velocity", 3,
            f"Explosive buy velocity: {buys_5m} buys in 5m ({buys_5m/5:.0f}/min)",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R44: Holder acceleration — rapid holder growth relative to token age
    if token_age_minutes is not None and token_age_minutes > 0 and holders >= 10:
        holders_per_min = holders / token_age_minutes
        if holders_per_min >= 25:
            r = SignalRule(
                "holder_acceleration", 3,
                f"Holder acceleration: {holders_per_min:.0f} holders/min "
                f"({holders} holders in {token_age_minutes:.1f}m)",
            )
            fired.append(r)
            reasons[r.name] = r.description

    # R45: Smart money early entry — 3+ smart wallets in first 10 minutes
    if (
        sm >= 3
        and token_age_minutes is not None
        and token_age_minutes <= 10
    ):
        r = SignalRule(
            "smart_money_early_entry", 4,
            f"{sm} smart wallets entered within {token_age_minutes:.0f}m of launch",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R46: Volume spike ratio — extreme 5m volume vs liquidity (>= 5x)
    vol_5m_check = float(snapshot.volume_5m or snapshot.dex_volume_5m or 0)
    if liq > 0 and vol_5m_check > 0 and vol_5m_check / liq >= 5.0:
        r = SignalRule(
            "volume_spike_ratio", 2,
            f"Extreme volume spike: 5m vol/liq = {vol_5m_check/liq:.1f}x",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R47: Organic buy pattern — strong buys with minimal sells and holder diversity
    if buys_5m >= 20 and sells_5m < buys_5m * 0.3 and holders >= 30:
        sell_ratio = sells_5m / buys_5m * 100 if buys_5m > 0 else 0
        r = SignalRule(
            "organic_buy_pattern", 2,
            f"Organic buying: {buys_5m} buys, {sell_ratio:.0f}% sells, {holders} holders",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # --- PHASE 34: BOT FARMING DETECTION ---

    # R65: Abnormal buys/holder ratio — bots generate many buys from few holders.
    # Production: scam avg 2.76 buys/holder vs profit avg 1.87.
    # Threshold 3.5+ catches extreme bot farming with minimal false positives.
    # Only on fresh tokens (age <= 5min) where bots haven't dispersed yet.
    if (
        holders > 0
        and buys_5m > 0
        and token_age_minutes is not None
        and token_age_minutes <= 5
    ):
        _bph = buys_5m / holders
        if _bph >= 3.5:
            r = SignalRule(
                "abnormal_buys_per_holder", -3,
                f"Bot-like activity: {_bph:.1f} buys/holder "
                f"({buys_5m} buys, {holders} holders)",
            )
            fired.append(r)
            reasons[r.name] = r.description

    # --- PHASE 16 COMMUNITY & LLM RULES ---

    # R48: Active Telegram community (bullish)
    if tg_member_count is not None and tg_member_count >= 500:
        r = SignalRule(
            "active_tg_community", 2,
            f"Active Telegram community: {tg_member_count:,} members",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R49: No community presence — DISABLED (most fresh memecoins have none;
    # penalizing absence creates bearish bias against early-stage tokens)

    # R50: Established website (bullish)
    if has_website is True and domain_age_days is not None and domain_age_days >= 30:
        r = SignalRule(
            "established_website", 1,
            f"Established website (domain age: {domain_age_days}d)",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R51: LLM high risk (bearish) — reduced from -3 to -1 because
    # Gemini Flash Lite is overly conservative for memecoins. It rates
    # nearly ALL memecoins as high risk (they ARE high risk by trad-fi
    # standards). At -3 it's noise. Real LLM value is in llm_low_risk (+2).
    if llm_risk_score is not None and llm_risk_score >= 80:
        r = SignalRule(
            "llm_high_risk", -1,
            f"LLM analysis: high risk score {llm_risk_score}/100",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R52: LLM low risk (bullish)
    if llm_risk_score is not None and llm_risk_score <= 25:
        r = SignalRule(
            "llm_low_risk", 2,
            f"LLM analysis: low risk score {llm_risk_score}/100",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R53: Explosive holder growth (bullish)
    if holder_growth_pct is not None and holder_growth_pct >= 100:
        r = SignalRule(
            "explosive_holder_growth", 3,
            f"Holder growth +{holder_growth_pct:.0f}% since last snapshot",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R54: Holder exodus (bearish)
    if holder_growth_pct is not None and holder_growth_pct <= -20:
        r = SignalRule(
            "holder_exodus", -3,
            f"Holder exodus: {holder_growth_pct:.0f}% since last snapshot",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # --- PHASE 30 FAST ENTRY RULES ---
    # These rules are designed to fire on INITIAL (T+12s) when prev_snapshot
    # is not available. They detect early organic momentum using absolute
    # metrics that don't require historical comparison.

    # R55: Early organic momentum — healthy MCap/Liq ratio + decent holders
    # + more buys than sells on a very young token. This compensates for
    # R9 price_momentum which NEVER fires on INITIAL (no prev_snapshot).
    # MCap/Liq < 5 = healthy ratio, holders >= 15 at T+12s = organic growth.
    # Phase 31: Skip if rugcheck >= 5000 — extreme danger tokens should NOT
    # get bullish boost (graduation rug pattern).
    if (
        prev_snapshot is None  # Only fires when no previous snapshot (INITIAL)
        and token_age_minutes is not None
        and token_age_minutes <= 3  # Very fresh token
        and liq > 0 and mcap > 0
        and mcap / liq < 5  # Healthy ratio (not pumped beyond liquidity)
        and holders >= 15  # Decent holder count for <3 min old token
        and buys > sells  # Net buying pressure
        and (rugcheck_score is None or rugcheck_score < 5000)  # Not extreme danger
    ):
        r = SignalRule(
            "early_organic_momentum", 3,
            f"Early momentum: {holders} holders in {token_age_minutes:.1f}m, "
            f"MCap/Liq={mcap/liq:.1f}x, {buys}B/{sells}S",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R56: Fresh volume surge — high 5m volume relative to liquidity on
    # a very young token. At T+12s vol_5m is essentially "volume since launch".
    # Vol/Liq >= 0.5 means significant trading activity for a brand-new token.
    if (
        token_age_minutes is not None
        and token_age_minutes <= 3
        and liq > 0 and vol_5m_check > 0
        and vol_5m_check / liq >= 0.5
    ):
        r = SignalRule(
            "fresh_volume_surge", 2,
            f"Fresh volume surge: 5m vol/liq = {vol_5m_check/liq:.1f}x "
            f"at {token_age_minutes:.1f}m age",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # --- PHASE 33/34 COPYCAT RULE ---

    # R63: Copycat rugged symbol — same symbol was already rugged recently.
    # Production: CASH×9, ELSTONKS×8, Tahlequah×9, UUSD1×7 — all -100%.
    #
    # IMPORTANT: Hard avoid for 2+ rugs was REMOVED after backtest showed
    # 86/153 profitable positions share symbols with 2+ rugs (agent1c, Grok-1,
    # MOSS, CLAW, Tahlequah etc). Popular meme symbols get deployed by BOTH
    # scammers AND legit creators — symbol alone cannot distinguish them.
    #
    # Tiered penalty: higher rug_count = heavier weight, but NEVER hard avoid.
    # Phase 35: Logarithmic scaling — higher rug_count = heavier penalty.
    # CLEUS had 126 rugged addresses; -8 was overcome by bullish +12.
    # New tiers: 50+ → -12, 10+ → -10, 3+ → -8, 1-2 → -6.
    # Backtest: CLEUS +140% had rug_count ~1 → -6 (unchanged, not affected).
    if copycat_rugged:
        if copycat_rug_count >= 50:
            r = SignalRule(
                "copycat_mass_scam", -12,
                f"Symbol rugged {copycat_rug_count}x — mass deployment scam",
            )
        elif copycat_rug_count >= 10:
            r = SignalRule(
                "copycat_extreme_scam", -10,
                f"Symbol rugged {copycat_rug_count}x — extreme serial scam",
            )
        elif copycat_rug_count >= 3:
            r = SignalRule(
                "copycat_serial_scam", -8,
                f"Symbol rugged {copycat_rug_count}x — high-frequency scam symbol",
            )
        else:
            r = SignalRule(
                "copycat_rugged_symbol", -6,
                "Symbol matches a recently rugged token (copycat scam)",
            )
        fired.append(r)
        reasons[r.name] = r.description

    # R66: Copycat + serial deployer compound — Phase 38.
    # When BOTH copycat AND creator has dead tokens, individual penalties
    # (-6/-8 copycat + -3 serial = -11) are overcome by bot-farmed bull rules (+12 to +15).
    # Extra -4 makes total -15 to -17, ensuring scam net stays negative.
    # CLEUS +140%: pumpfun_dead_tokens=None → not affected.
    if (
        copycat_rugged
        and pumpfun_dead_tokens is not None
        and pumpfun_dead_tokens >= 2
    ):
        r = SignalRule(
            "copycat_serial_compound", -4,
            f"Copycat symbol + creator has {pumpfun_dead_tokens} dead tokens "
            f"(high-confidence scam pattern)",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # --- PHASE 31/31b ANTI-RUG RULES ---
    # Production data (2026-02-23 10:21-11:18 UTC): graduation drain pattern.
    # PumpFun tokens graduate to Raydium with ~85/$98 SOL ($174K/$201K/$221K liq),
    # mcap/liq ≈ 1.5x, rugger bots holders/buys to trigger bullish signals,
    # then instantly drain the pool in 5-19 seconds.
    # Phase 31b backtest: 0/11 profitable entries blocked, 4/4 drains caught.

    # Helper: detect graduation zone (used by multiple rules + cap)
    _is_graduation_zone = (
        liq > 100_000
        and mcap > 0
        and liq > 0
        and 1.0 <= mcap / liq <= 2.0
        and token_age_minutes is not None
        and token_age_minutes <= 3
    )

    # R57a: Structural graduation rug fingerprint — NO rugcheck needed.
    # All graduation drains have liq=$174K/$201K/$221K, mcap/liq≈1.5, age<1min.
    # All profitable entries had liq $6.8K-$23.8K — never in this zone.
    if _is_graduation_zone:
        r = SignalRule(
            "graduation_rug_structural", -7,
            f"Graduation bomb: liq=${liq:,.0f}, MCap/Liq={mcap/liq:.2f}x, "
            f"age={token_age_minutes:.1f}m",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R57b: Original R57 for $50-100K pools with high rugcheck (elif — only
    # fires if R57a didn't).
    elif (
        liq > 50_000
        and mcap > 0
        and liq > 0
        and 0.8 <= mcap / liq <= 1.8
        and rugcheck_score is not None
        and rugcheck_score >= 3000
        and token_age_minutes is not None
        and token_age_minutes <= 5
    ):
        r = SignalRule(
            "graduation_rug_pattern", -5,
            f"Graduation trap: MCap/Liq={mcap/liq:.2f}x, "
            f"rugcheck={rugcheck_score}, age={token_age_minutes:.1f}m",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R58: Bot-farmed holders — removed rugcheck dependency, added liq floor.
    # Production showed rugcheck bypasses (score=2401, or missing).
    # Liq floor $20K prevents penalizing organic low-liq tokens where
    # holder growth % is high simply from tiny starting count (5→30 = +500%).
    # Phase 39 note: considered lowering to $8K with prev_holders guard, but
    # production data showed 22 profitable tokens in $8-20K range with
    # init_holders >= 10 and growth >= 500% — all take_profit. Reverted.
    # MOLTGEN ($13K) caught by R15 (-4), R69 (-6), compound flag instead.
    if (
        holder_growth_pct is not None
        and holder_growth_pct >= 500
        and token_age_minutes is not None
        and token_age_minutes <= 2
        and liq > 20_000  # Floor: organic low-liq tokens safe
    ):
        r = SignalRule(
            "bot_holder_farming", -3,
            f"Suspicious holder growth +{holder_growth_pct:.0f}% in "
            f"{token_age_minutes:.1f}m, liq=${liq:,.0f}",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R59: Extreme holder growth on graduation pool — catches tokens where
    # R57a fires but botted holders also push bullish rules.
    # Threshold 3000% has 1000% margin above max profitable (Sandcat 2031%).
    # Liq > $50K ensures low-liq organics ($5-14K) never hit this.
    if (
        holder_growth_pct is not None
        and holder_growth_pct >= 3000
        and liq > 50_000
        and token_age_minutes is not None
        and token_age_minutes <= 3
    ):
        r = SignalRule(
            "extreme_graduation_growth", -6,
            f"Extreme growth +{holder_growth_pct:.0f}% on ${liq:,.0f} pool "
            f"at {token_age_minutes:.1f}m age",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # --- PHASE 30b SYBIL & GHOST DETECTION ---

    # R67: Sybil holder inflation — holders vastly exceed actual buys.
    # Scam pattern: airdrop tokens to thousands of wallets to fake "holder count",
    # triggering bullish holder_velocity/acceleration rules.
    # PEPSTEIN: 2771 holders / 6 buys = 461 holders/buy (pure sybil).
    # All 56 profitable positions: max holders/buy = 0.61 (holders < buys).
    # Threshold 5.0 gives 8x safety margin from max profit ratio.
    _total_buys_r67 = buys_5m + (snapshot.buys_1h or 0)
    if holders > 100 and _total_buys_r67 > 0:
        _holders_per_buy = holders / _total_buys_r67
        if _holders_per_buy > 5.0:
            r = SignalRule(
                "sybil_holder_inflation", -8,
                f"Sybil holders: {holders} holders / {_total_buys_r67} buys "
                f"= {_holders_per_buy:.1f} holders/buy (airdrop farming)",
            )
            fired.append(r)
            reasons[r.name] = r.description

    # R68: Ghost mcap — high market cap with near-zero trading volume.
    # Scam pattern: pre-minted tokens with inflated supply, no real trading.
    # PEPSTEIN: $481K mcap, $431 vol_1h = 0.09% turnover (dead token).
    # All profitable positions: min vol/mcap = 5.3% — 10x safety margin.
    # Only check when mcap > $100K (low-mcap tokens naturally have low vol).
    if mcap > 100_000 and vol_1h_val >= 0 and buys_5m < 10:
        _vol_mcap_pct = (vol_1h_val / mcap * 100) if mcap > 0 else 0
        if _vol_mcap_pct < 1.0:
            r = SignalRule(
                "ghost_mcap", -5,
                f"Ghost mcap: ${mcap:,.0f} cap with ${vol_1h_val:,.0f} vol "
                f"({_vol_mcap_pct:.2f}% turnover, {buys_5m} buys)",
            )
            fired.append(r)
            reasons[r.name] = r.description

    # --- PHASE 39 COMPOUND VELOCITY SCAM ---

    # R69: Velocity + danger compound — extreme holder velocity on a token
    # that also has high rugcheck score AND low liquidity.
    # MOLTGEN post-mortem: 417 holders/min + rugcheck 3501 + $13K liq = obvious scam,
    # but system rewarded velocity with +8 bullish (R4+R44+R53) and only penalized -2.
    # This rule fires ONLY when multiple red flags combine with high velocity,
    # preventing false positives on legit high-velocity tokens (which have clean rugcheck).
    # Production: 0 profitable tokens had rugcheck >= 3000 (all 143 profitable had 5000+
    # or <3000). The rugcheck 3000+ guard makes this rule impossible to hit on winners.
    if (
        holder_velocity is not None
        and holder_velocity >= 200
        and rugcheck_score is not None
        and rugcheck_score >= 3000
        and liq < 30_000
    ):
        r = SignalRule(
            "velocity_danger_compound", -6,
            f"Velocity scam: {holder_velocity:.0f} holders/min + "
            f"rugcheck {rugcheck_score} + liq ${liq:,.0f}",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # --- PHASE 40: HOLDER CONCENTRATION PENALTY ---

    # R70: Holder concentration penalty — production data (2026-02-24):
    # rugcheck_score >= 20000 WITHOUT "LP Unlocked" = 36.8% rug rate (vs 14.8% base).
    # Tokens with high score + LP Unlocked = 100% win rate (7/7) — PumpFun standard.
    # The REAL danger: "Top 10 holders high ownership" / "Single holder ownership"
    # combined with high rugcheck score, but WITHOUT the LP Unlocked safety signal.
    # This catches concentrated holder tokens that lack PumpFun bonding curve protection.
    # Soft -4: strong but not fatal — 29 profitable tokens in this bucket would be penalized
    # but most still have enough bullish rules to survive.
    _has_holder_concentration_risk = False
    if (
        security is not None
        and security.rugcheck_risks
        and rugcheck_score is not None
        and rugcheck_score >= 13000
    ):
        _risks_lower = security.rugcheck_risks.lower()
        _has_holder_risk = (
            "holder" in _risks_lower
            or "ownership" in _risks_lower
        )
        _has_lp_unlocked = "lp unlocked" in _risks_lower
        if _has_holder_risk and not _has_lp_unlocked:
            _has_holder_concentration_risk = True
            r = SignalRule(
                "holder_concentration_danger", -4,
                f"Holder concentration risk: rugcheck {rugcheck_score} + "
                f"holder/ownership risks WITHOUT LP Unlocked safety",
            )
            fired.append(r)
            reasons[r.name] = r.description

    # --- PHASE 41: BOT FARM DETECTION ---

    # R72: Low-liquidity velocity cap — production data (2026-02-24):
    # Bot farms stack 6 velocity rules for +15 bullish on micro-liq tokens.
    # Rugcheck -5 + liq penalty -2 = only -7 bearish → net +8 → strong_buy on scams.
    # 75.5% of rugged positions had liq < $20K.
    # Cap bullish at +8 when liq < $20K so bot-farmed velocity can't overwhelm bearish.
    # At +8 bullish vs -7 bearish → net +1 → avoid. With clean token -2 bearish → net +6 → buy.
    # This preserves legitimate low-liq tokens (they have fewer bearish flags) while
    # stopping bot farms that always trigger both velocity bullish AND danger bearish.
    _velocity_capped = False

    # R73: Sell stagnation REMOVED after backtest (2026-02-24).
    # At MIN_2 stage (T+42s), sells_1h ≈ sells_5m for ALL tokens (both profitable
    # and rugged) because not enough time has passed. This metric only discriminates
    # at MIN_5+ stages. Backtest showed 23 profitable false positives vs 14 rugged
    # caught — unacceptable 0.6 ratio. Sell stagnation is a real signal but requires
    # later-stage evaluation, not MIN_2.

    # --- PHASE 43: FAKE LIQUIDITY DETECTION ---

    # R74: Fake liquidity trap — production data (2026-02-24):
    # COCA Cola (-100%), Soala (-100%), Hikikomori (-100%) all had:
    #   - High liquidity ($50K) injected by scammer to look legitimate
    #   - Low trading volume relative to liquidity (vol_5m/liq < 0.5)
    #   - No fresh_volume_surge fired (real organic tokens have vol surge)
    # Pattern: scammer creates token, injects $50K liquidity, bots buy to
    # inflate holder count, then drains LP after 5 minutes.
    # Key insight: legitimate high-liq tokens ALWAYS have proportional volume.
    # If liq >= $50K but vol/liq is low, the liquidity is likely fake bait.
    # Backtest (29 positions): blocks 3 scams (-$114.95 saved), loses 2 profits
    # (SPARK +49%, Zoe +52% = -$38.60 lost). Net: +$73.67.
    _has_strong_liq = "strong_liquidity" in reasons
    _has_vol_surge = "fresh_volume_surge" in reasons
    if _has_strong_liq and not _has_vol_surge:
        r = SignalRule(
            "fake_liquidity_trap", -5,
            f"Fake liquidity trap: ${liq:,.0f} liq without volume surge "
            f"(vol_5m/liq = {vol_5m_check/liq:.2f}x)" if liq > 0 else
            f"Fake liquidity trap: ${liq:,.0f} liq without volume surge",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # --- PHASE 44: LOW-LIQ BOT WASH DETECTION ---

    # R75: Bot wash on low-liquidity tokens — production data (2026-02-24):
    # Volume/3NU2 (-100%): buys=451, sells=55, bs_ratio=8.2x, liq=$13K
    # Volume/FM9f (-100%): buys=317, sells=2, bs_ratio=158.5x, liq=$13K
    # Pattern: scammer deploys with ~$13K liq, bots flood buys with almost
    # zero sells to inflate velocity metrics, then drains LP after 5-10 min.
    # Key insight: on REAL organic low-liq tokens, sell_pct is 30-85%.
    # Bot farms have <15% sells because bots only buy, never sell.
    # Threshold: liq < $20K AND buy/sell ratio >= 8x (sell_pct < 12.5%).
    # Backtest (45 positions): blocks 2 scams (-$76.57 saved),
    # loses 1 edge-case Volume +20% (-$7.65). Net: +$68.92.
    # All take_profit positions PASS (nelt +72%, think +79%, Volume +88%).
    _has_low_liq_soft = "low_liquidity_soft" in reasons
    if _has_low_liq_soft and buys_5m >= 20:
        _bs_ratio_r75 = buys_5m / sells_5m if sells_5m > 0 else float("inf")
        if _bs_ratio_r75 >= 8.0:
            _sell_pct_r75 = (sells_5m / buys_5m * 100) if buys_5m > 0 else 0
            r = SignalRule(
                "low_liq_bot_wash", -5,
                f"Bot wash: {buys_5m}B/{sells_5m}S (ratio {_bs_ratio_r75:.1f}x, "
                f"{_sell_pct_r75:.0f}% sells) on ${liq:,.0f} liq",
            )
            fired.append(r)
            reasons[r.name] = r.description

    # R76: REMOVED (Phase 45b) — rugcheck-concentration compound was destructive.
    # On PumpFun, rugcheck>=5000 + top10>=95% is the NORM. The compound rule
    # blocked 72% of profitable positions. ket is genuinely indistinguishable
    # from profitable tokens by these metrics alone.

    # --- COMPUTE RESULT ---
    bullish = sum(r.weight for r in fired if r.weight > 0)
    bearish = abs(sum(r.weight for r in fired if r.weight < 0))

    # R72: Apply velocity cap AFTER computing raw scores.
    # Cap bullish at +8 when liq < $20K. This prevents bot-farmed velocity
    # from overwhelming bearish signals on micro-liq tokens.
    # Production: 91 profitable low-liq positions → 61 had bullish <= 8 (67%, unaffected).
    # 30 had bullish > 8 → capped, but most had low bearish → still buy/strong_buy.
    if liq > 0 and liq < 20_000 and bullish > 8:
        _velocity_capped = True
        _original_bullish = bullish
        bullish = 8
        r = SignalRule(
            "low_liq_velocity_cap", 0,
            f"Velocity cap: bullish {_original_bullish}→8 (liq ${liq:,.0f} < $20K)",
        )
        fired.append(r)
        reasons[r.name] = r.description

    net = bullish - bearish

    # Phase 34: Copycat cap REMOVED after backtest.
    # 86/153 profitable positions share symbols with 2+ rugs. Popular meme
    # symbols (MOSS, CLAW, agent1c, Grok-1) produce both scams and profits.
    # CLEUS +140.4% had copycat flag — would have been capped at watch.
    # R63 -6/-8 penalty is sufficient; bullish override is INTENTIONAL here.

    # Graduation zone hard cap: even if bullish rules stack to net=15,
    # a freshly-graduated token with liq>$100K and mcap/liq≈1.5 can NOT
    # get buy/strong_buy. Max "watch" (net=2). Safety net for edge cases
    # where bearish rules don't accumulate enough weight.
    if _is_graduation_zone and net > 2:
        net = 2

    # Phase 38: Fresh unsecured LP cap REMOVED after backtest.
    # PumpFun tokens ALWAYS have lp_burned=NULL, lp_locked=false (bonding curve).
    # Backtest showed 56.7% (148/261) of profitable positions would be blocked.
    # LP unsecured is NOT discriminative for PumpFun tokens — all have it.
    # Guards 1+2 (compound fingerprint + R66) are sufficient.

    if net >= 8:
        action = "strong_buy"
    elif net >= 5:
        action = "buy"
    elif net >= 2:
        action = "watch"
    else:
        action = "avoid"

    return SignalResult(
        rules_fired=fired,
        bullish_score=bullish,
        bearish_score=bearish,
        net_score=net,
        action=action,
        reasons=reasons,
    )
