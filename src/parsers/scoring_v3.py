"""Alternative scoring model v3 — momentum-weighted.

Differs from v2 (scoring.py) in how it weighs components:
- Momentum signals (buy pressure, volume spike, price change) carry more weight
- Security is a pass/fail gate rather than additive score
- Holder velocity is weighted more heavily
- Smart money is the strongest individual signal

Used in A/B comparison against v2 via scripts/compare_scoring_models.py.
"""

from decimal import Decimal

from src.models.token import CreatorProfile, TokenSecurity, TokenSnapshot


def compute_score_v3(
    snapshot: TokenSnapshot,
    security: TokenSecurity | None,
    *,
    creator_profile: CreatorProfile | None = None,
    holder_velocity: float | None = None,
    whale_score_impact: int = 0,
    bonding_curve_pct: float | None = None,
    dbc_launchpad: str | None = None,
    dbc_launchpad_score: int | None = None,
    lp_removed_pct: float | None = None,
    dev_holds_pct: float | None = None,
    volatility_5m: float | None = None,
    rugcheck_score: int | None = None,
    smart_money_weighted: float | None = None,
    # Phase 12 parameters
    mint_risk_boost: int = 0,
    sell_sim_failed: bool = False,
    bundled_buy_detected: bool = False,
    pumpfun_dead_tokens: int | None = None,
    goplus_is_honeypot: bool | None = None,
    raydium_lp_burned: bool | None = None,
    # Phase 13 parameters
    fee_payer_sybil_score: float | None = None,
    funding_chain_risk: int | None = None,
    convergence_detected: bool = False,
    metadata_score: int | None = None,
    wash_trading_suspected: bool = False,
    rugcheck_danger_count: int | None = None,
    bubblemaps_decentralization: float | None = None,
    solsniffer_score: int | None = None,
    # Phase 14B parameters
    jupiter_banned: bool = False,
    # Phase 15 parameters
    holders_in_profit_pct: float | None = None,
    twitter_mentions: int | None = None,
    twitter_kol_mentions: int | None = None,
    twitter_viral: bool = False,
    # Phase 16 parameters
    holder_growth_pct: float | None = None,
    has_website: bool | None = None,
    domain_age_days: int | None = None,
    tg_member_count: int | None = None,
    llm_risk_score: int | None = None,
) -> int:
    """Compute a 0-100 score — momentum-weighted variant.

    Returns 0 if insufficient data to score.

    Scoring breakdown:
    - Liquidity gate: 0-15 pts (lower weight, just a threshold check)
    - Momentum: 0-35 pts (buy pressure + volume ratio + price change)
    - Smart money: 0-20 pts (strongest single signal)
    - Holder velocity: 0-15 pts (growth rate)
    - Security gate: -30 to +10 pts (pass/fail style)
    - Creator risk: 0 to -15 pts
    - Holders: 0-10 pts (crowd validation)
    """
    liquidity_usd = snapshot.liquidity_usd or snapshot.dex_liquidity_usd
    if liquidity_usd is None:
        return 0

    # Hard disqualifier — honeypot (GMGN or GoPlus) or jupiter_banned
    if security is not None and security.is_honeypot is True:
        return 0
    if goplus_is_honeypot is True:
        return 0
    if jupiter_banned:
        return 0

    # Hard disqualifier — extreme rugcheck score (DATACLAW, NIP pattern)
    if rugcheck_score is not None and rugcheck_score > 20000:
        return 0

    # Hard disqualifier — single holder ownership (100% rug rate)
    if (
        security is not None
        and security.rugcheck_risks
        and "single holder ownership" in security.rugcheck_risks.lower()
    ):
        return 0

    score = 0
    liquidity = float(liquidity_usd)
    holders = snapshot.holders_count or 0

    # --- Liquidity gate (0-15 pts) ---
    # Minimum threshold + diminishing returns above $50k
    if liquidity >= 50_000:
        score += 15
    elif liquidity >= 25_000:
        score += 12
    elif liquidity >= 10_000:
        score += 8
    elif liquidity >= 5_000:
        score += 4

    # --- Momentum (0-35 pts) ---
    # Buy pressure (0-15 pts)
    buy_ratio = _buy_sell_ratio(snapshot)
    if buy_ratio is not None:
        if buy_ratio >= 4.0:
            score += 15
        elif buy_ratio >= 2.5:
            score += 12
        elif buy_ratio >= 1.8:
            score += 8
        elif buy_ratio >= 1.3:
            score += 4

    # Volume/liquidity ratio (0-15 pts)
    volume = _best_volume(snapshot)
    if volume is not None and liquidity > 0:
        ratio = volume / liquidity
        if ratio >= 3.0:
            score += 15
        elif ratio >= 1.5:
            score += 12
        elif ratio >= 0.8:
            score += 8
        elif ratio >= 0.3:
            score += 4

    # Volume acceleration (5m vs 1h extrapolated) — 0-5 pts
    # Guard: skip for young tokens where vol_1h ≈ vol_5m (ratio meaningless)
    vol_5m = float(snapshot.volume_5m or snapshot.dex_volume_5m or 0)
    vol_1h = float(snapshot.volume_1h or snapshot.dex_volume_1h or 0)
    if vol_5m > 0 and vol_1h > 100 and vol_1h > vol_5m * 3:
        # 5m volume extrapolated to 1h = 5m * 12
        accel = (vol_5m * 12) / vol_1h
        if accel >= 2.0:
            score += 5  # volume accelerating
        elif accel >= 1.5:
            score += 3

    # --- Smart money (0-20 pts) ---
    if smart_money_weighted is not None:
        if smart_money_weighted >= 2.5:
            score += 20
        elif smart_money_weighted >= 1.5:
            score += 14
        elif smart_money_weighted >= 0.5:
            score += 8
    else:
        sm = snapshot.smart_wallets_count or 0
        if sm >= 3:
            score += 20
        elif sm >= 2:
            score += 14
        elif sm >= 1:
            score += 8

    # --- Holder velocity (0-15 pts) ---
    if holder_velocity is not None:
        if holder_velocity >= 100:
            score += 15
        elif holder_velocity >= 50:
            score += 10
        elif holder_velocity >= 25:
            score += 6
        elif holder_velocity >= 10:
            score += 3

    # --- Holders (0-10 pts) — crowd validation ---
    if holders >= 300:
        score += 10
    elif holders >= 150:
        score += 7
    elif holders >= 50:
        score += 4

    # --- Security gate (-30 to +10 pts) ---
    if security is not None:
        # Positive gates
        if security.lp_burned is True or security.lp_locked is True:
            score += 5
        if security.contract_renounced is True:
            score += 3
        if (
            security.top10_holders_pct is not None
            and security.top10_holders_pct < Decimal("25")
        ):
            score += 2
        # Negative gates (harsh penalties)
        if security.is_mintable is True:
            score -= 20
        if security.sell_tax is not None and security.sell_tax > Decimal("10"):
            score -= 10

    # --- Creator risk (-15 pts max) ---
    if creator_profile is not None and creator_profile.risk_score is not None:
        if creator_profile.risk_score >= 80:
            score -= 15
        elif creator_profile.risk_score >= 60:
            score -= 8
        elif creator_profile.risk_score >= 40:
            score -= 3

    # --- Whale dynamics (-10 to +8 pts) ---
    score += whale_score_impact

    # --- LP lock duration bonus (0-5 pts) ---
    if security is not None and security.lp_lock_duration_days is not None:
        lock_days = security.lp_lock_duration_days
        if lock_days >= 365:
            score += 5
        elif lock_days >= 90:
            score += 3
        elif lock_days >= 30:
            score += 1

    # --- Buy tax penalty (0 to -5 pts) ---
    if security is not None and security.buy_tax is not None:
        if security.buy_tax > Decimal("10"):
            score -= 5
        elif security.buy_tax > Decimal("5"):
            score -= 2

    # --- Bonding curve maturity (0-5 pts) ---
    if bonding_curve_pct is not None:
        if bonding_curve_pct >= 80:
            score += 5
        elif bonding_curve_pct >= 50:
            score += 3
        elif bonding_curve_pct >= 25:
            score += 1

    # --- DBC launchpad reputation (-2 to +3 pts) ---
    if dbc_launchpad_score is not None:
        score += dbc_launchpad_score
    elif dbc_launchpad is not None:
        _trusted = {"believe", "letsbonk", "boop"}
        if dbc_launchpad.lower() in _trusted:
            score += 3
        else:
            score -= 2

    # --- LP removal penalty (0 to -25 pts) ---
    if lp_removed_pct is not None:
        if lp_removed_pct >= 50:
            score -= 25
        elif lp_removed_pct >= 30:
            score -= 15
        elif lp_removed_pct >= 20:
            score -= 8

    # --- Dev holds penalty (-20 to +3 pts) [Phase 11] ---
    if dev_holds_pct is not None:
        if dev_holds_pct >= 80:
            score -= 20
        elif dev_holds_pct >= 50:
            score -= 10
        elif dev_holds_pct >= 30:
            score -= 4
        elif dev_holds_pct < 5:
            score += 3

    # --- Volatility penalty (-8 to +2 pts) [Phase 11] ---
    if volatility_5m is not None:
        if volatility_5m >= 50:
            score -= 8
        elif volatility_5m >= 25:
            score -= 3
        elif volatility_5m < 3:
            score += 2

    # --- Rugcheck penalty (-20 to +5 pts) [Phase 11] ---
    if rugcheck_score is not None:
        if rugcheck_score >= 50:
            score -= 20
        elif rugcheck_score >= 30:
            score -= 10
        elif rugcheck_score < 10:
            score += 5

    # --- 24h sustained interest (-5 to +3 pts) [Phase 11] ---
    b24 = snapshot.buys_24h
    s24 = snapshot.sells_24h
    if b24 is not None and s24 is not None and s24 > 0:
        ratio_24h = b24 / s24
        if ratio_24h >= 2.0 and buy_ratio is not None and buy_ratio >= 2.0:
            score += 3
        elif ratio_24h < 0.3:
            score -= 5

    # --- Phase 12: PRE_SCAN risk boost (0 to -45 pts) ---
    score -= mint_risk_boost

    # --- Phase 12: Sell simulation failed (-25 pts, harsher in v3) ---
    if sell_sim_failed:
        score -= 25

    # --- Phase 12: Bundled buy detected (-12 pts) ---
    if bundled_buy_detected:
        score -= 12

    # --- Phase 12: Pump.fun serial deployer (-5 to -20 pts, harsher in v3) ---
    if pumpfun_dead_tokens is not None:
        if pumpfun_dead_tokens >= 10:
            score -= 20
        elif pumpfun_dead_tokens >= 5:
            score -= 12
        elif pumpfun_dead_tokens >= 3:
            score -= 5

    # --- Phase 12: Raydium LP verification (-3 to +3 pts) ---
    if raydium_lp_burned is not None:
        if raydium_lp_burned:
            score += 3
        else:
            score -= 3

    # --- Phase 13: Fee payer sybil (-10 to -18 pts, harsher in v3) ---
    if fee_payer_sybil_score is not None:
        if fee_payer_sybil_score > 0.7:
            score -= 18
        elif fee_payer_sybil_score > 0.5:
            score -= 10

    # --- Phase 13: Funding chain risk (0 to -15 pts) ---
    if funding_chain_risk is not None:
        if funding_chain_risk >= 80:
            score -= 15
        elif funding_chain_risk >= 60:
            score -= 8
        elif funding_chain_risk >= 40:
            score -= 3

    # --- Phase 13: Token convergence (-18 pts, harsher in v3) ---
    if convergence_detected:
        score -= 18

    # --- Phase 13: Metadata score (-3 to +4 pts) ---
    if metadata_score is not None:
        if metadata_score >= 7:
            score += 4
        elif metadata_score >= 4:
            score += 2
        elif metadata_score <= -3:
            score -= 3

    # --- Phase 13: Wash trading (-12 pts) ---
    if wash_trading_suspected:
        score -= 12

    # --- Phase 13: Rugcheck danger count (-6 to -15 pts, harsher in v3) ---
    if rugcheck_danger_count is not None:
        if rugcheck_danger_count >= 3:
            score -= 15
        elif rugcheck_danger_count >= 2:
            score -= 6

    # --- Phase 13: Bubblemaps decentralization (-10 to +3 pts) ---
    if bubblemaps_decentralization is not None:
        if bubblemaps_decentralization >= 0.7:
            score += 3
        elif bubblemaps_decentralization < 0.3:
            score -= 10
        elif bubblemaps_decentralization < 0.4:
            score -= 5

    # --- Phase 13: SolSniffer cross-validation (-6 to +3 pts) ---
    if solsniffer_score is not None:
        if solsniffer_score >= 80:
            score += 3
        elif solsniffer_score < 30:
            score -= 6

    # --- Phase 15: Vybe holder PnL (-3 to +2 pts) ---
    if holders_in_profit_pct is not None:
        if holders_in_profit_pct >= 60:
            score += 2
        elif holders_in_profit_pct <= 20:
            score -= 3

    # --- Phase 15: Twitter social signals (0 to +9 pts) ---
    if twitter_kol_mentions is not None and twitter_kol_mentions >= 1:
        score += 5
    elif twitter_mentions is not None and twitter_mentions >= 10:
        score += 2
    if twitter_viral:
        score += 4

    # --- Phase 16: Holder growth velocity (-3 to +15 pts) ---
    if holder_growth_pct is not None:
        if holder_growth_pct >= 200:
            score += 15
        elif holder_growth_pct >= 100:
            score += 10
        elif holder_growth_pct >= 50:
            score += 5
        elif holder_growth_pct <= -30:
            score -= 3

    # --- Phase 16: Website/domain (0 to +3 pts) ---
    if has_website is True and domain_age_days is not None:
        if domain_age_days >= 30:
            score += 3
        elif domain_age_days >= 7:
            score += 1

    # --- Phase 16: Telegram community (0 to +5 pts) ---
    if tg_member_count is not None:
        if tg_member_count >= 5000:
            score += 5
        elif tg_member_count >= 1000:
            score += 3
        elif tg_member_count >= 200:
            score += 1

    # --- Phase 16: LLM risk assessment (-5 to +3 pts) ---
    if llm_risk_score is not None:
        if llm_risk_score >= 80:
            score -= 5
        elif llm_risk_score >= 50:
            score -= 2
        elif llm_risk_score <= 20:
            score += 3

    # --- Data completeness cap ---
    _available = sum([
        snapshot.liquidity_usd is not None or snapshot.dex_liquidity_usd is not None,
        snapshot.holders_count is not None,
        snapshot.volume_1h is not None or snapshot.dex_volume_1h is not None,
        security is not None,
        snapshot.smart_wallets_count is not None,
        snapshot.top10_holders_pct is not None,
    ])
    if _available < 3:
        score = min(score, 40)

    return max(min(score, 100), 0)


def _best_volume(snapshot: TokenSnapshot) -> float | None:
    """Return best available volume metric (prefer 1h for ratio)."""
    for vol in (snapshot.volume_1h, snapshot.volume_5m, snapshot.volume_24h):
        if vol is not None:
            return float(vol)
    for vol in (snapshot.dex_volume_1h, snapshot.dex_volume_5m, snapshot.dex_volume_24h):
        if vol is not None:
            return float(vol)
    return None


def _buy_sell_ratio(snapshot: TokenSnapshot) -> float | None:
    """Compute buy/sell ratio from trade count data."""
    for buys, sells in [
        (snapshot.buys_1h, snapshot.sells_1h),
        (snapshot.buys_5m, snapshot.sells_5m),
    ]:
        if buys is not None and sells is not None and sells > 0:
            return buys / sells
    return None
