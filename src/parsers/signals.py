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

    # HG1: Minimum liquidity — tokens with < $21K liq have high rug rate
    # in our data. ALL 3 real rugs had liq $19-23K. Lowered from $30K to $21K
    # to capture tokens in $21-30K range that may still pump.
    if 0 < liq < 21_000:
        gate_rule = SignalRule(
            "low_liquidity_gate", -10,
            f"Hard gate: liquidity ${liq:,.0f} < $21K (high rug risk)",
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

    # --- PHASE 11 BEARISH RULES ---

    # R15: Rugcheck danger — reduced from -4 to -2 because ~99% of fresh
    # PumpFun memecoins score >= 50 ("Low LP Providers", "Mutable Metadata").
    # At -4 this rule is noise that blocks all buy signals. Real protection
    # comes from rugcheck_multi_danger (-3) for truly dangerous tokens.
    if rugcheck_score is not None and rugcheck_score >= 50:
        r = SignalRule("rugcheck_danger", -2, f"Rugcheck score {rugcheck_score} (dangerous)")
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
    if pumpfun_dead_tokens is not None and pumpfun_dead_tokens >= 5:
        r = SignalRule(
            "serial_deployer", -3,
            f"Creator has {pumpfun_dead_tokens} dead tokens on pump.fun",
        )
        fired.append(r)
        reasons[r.name] = r.description

    # R27: LP not burned (Raydium verified)
    if raydium_lp_burned is not None and not raydium_lp_burned:
        if security and not security.lp_burned and not security.lp_locked:
            r = SignalRule(
                "lp_not_burned", -1,
                "LP not burned or locked (Raydium verified)",
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

    # --- COMPUTE RESULT ---
    bullish = sum(r.weight for r in fired if r.weight > 0)
    bearish = abs(sum(r.weight for r in fired if r.weight < 0))
    net = bullish - bearish

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
