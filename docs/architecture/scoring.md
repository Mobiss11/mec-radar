# Scoring & Signal System

## Score Computation (v2)

**Function:** `compute_score(snapshot, security, ...) -> int` (0-100)

### Hard Disqualifiers (instant 0)
- `is_honeypot` (GMGN or GoPlus)
- `jupiter_banned`

### Data Completeness Gate
If fewer than **3** of 6 core metrics available → score capped at **40**.
Core metrics: liquidity, holders, volume, security, smart_wallets, top10_holders.

### Component Breakdown

| Component | Range | Key Thresholds |
|-----------|-------|----------------|
| Liquidity | 0-30 | $10K=15, $25K=20, $50K=25, $100K=30 |
| Holders | 0-20 | 50=8, 100=12, 200=15, 500=20 |
| Volume/Liq ratio | 0-25 | 0.5x=10, 1x=15, 2x=20, 5x=25 |
| Security (LP/renounced/tax) | -15 to +25 | LP secured=+10, renounced=+5, mintable=-15 |
| Buy pressure | 0-10 | 1.5x=4, 2x=7, 3x=10 |
| Smart money | 0-15 | 1 wallet=5, 2=10, 3+=15 |
| Holder velocity | 0-10 | 20/min=4, 50=7, 100=10 |
| Creator risk | -20 to 0 | risk≥80=-20, ≥60=-12, ≥40=-5 |
| Whale dynamics | -10 to +8 | whale_score_impact from cross-token |
| LP lock | 0-5 | 30d=1, 90d=3, 365d=5 |
| Dev holds | -15 to +3 | ≥80%=-15, 10-25%=+3 |
| Volatility 5m | -5 to +2 | ≥50%=-5, <3%=+2 |
| Rugcheck | -15 to +5 | ≥50=-15, <10=+5 |
| PRE_SCAN risk | -45 to 0 | mint_risk_boost from prescan |
| Sell sim failed | -20 | Jupiter sell simulation failed |
| Bundled buy | -10 | Coordinated first-block buys |
| Serial deployer | -15 to 0 | ≥10 dead tokens=-15 |
| Fee payer sybil | -15 to 0 | >70%=-15, >50%=-8 |
| Convergence | -15 | Token flow convergence |
| Wash trading | -10 | Suspected wash trades |
| Bubblemaps | -8 to +3 | decentralization <0.3=-8, ≥0.7=+3 (DISABLED) |
| SolSniffer | -5 to +3 | score<30=-5, ≥80=+3 |
| Holder profit (Vybe) | -3 to +2 | ≥60% in profit=+2, ≤20%=-3 |
| Twitter | 0 to +9 | KOL mention=+5, 10+ mentions=+2, viral=+4 |
| Holder growth | -3 to +15 | ≥200%=+15, ≤-30%=-3 |
| Website | 0 to +3 | domain ≥30d=+3 |
| Telegram | 0 to +5 | ≥5K members=+5, ≥1K=+3 |
| LLM risk | -5 to +3 | risk≥80=-5, ≤20=+3 |

## Signal Rules (54 total)

### Bullish Rules (24 rules, max +55 pts)

| Rule | Weight | Condition |
|------|--------|-----------|
| R1 high_score | +3 | score >= 60 |
| R2 buy_pressure | +2 | buy/sell ratio >= 3.0 |
| R3 smart_money | +3 | 2+ smart wallets |
| R4 holder_velocity | +2 | velocity >= 50/min |
| R5 strong_liquidity | +2 | liquidity >= $50K |
| R6 volume_spike | +2 | vol/liq >= 2.0 |
| R7 safe_creator | +1 | creator risk < 20 |
| R8 security_cleared | +3 | 2+ security flags passed |
| R9 price_momentum | +2 | price +20%+ since last check |
| R15b solsniffer_safe | +2 | SolSniffer score >= 80 |
| R15b solsniffer_danger | -4 | SolSniffer score < 30 |
| R22 strong_momentum | +2 | +10% price, low vol, high buy ratio |
| R42 jupiter_verified | +3 | Jupiter STRICT verified |
| R43 explosive_buy_velocity | +3 | 50+ buys in 5m |
| R44 holder_acceleration | +3 | 25+ holders/min |
| R45 smart_money_early_entry | +4 | 3+ smart wallets in first 10 min |
| R46 volume_spike_ratio | +2 | 5m vol/liq >= 5x |
| R47 organic_buy_pattern | +2 | 20+ buys, <30% sells, 30+ holders |
| R48 active_tg_community | +2 | 500+ TG members |
| R50 established_website | +1 | domain age >= 30d |
| R52 llm_low_risk | +2 | LLM risk <= 25 |
| R53 explosive_holder_growth | +3 | holder growth +100%+ |

### Bearish Rules (30 rules, max -118 pts)

| Rule | Weight | Condition |
|------|--------|-----------|
| R10 honeypot | -10 | is_honeypot |
| R11 risky_creator | -3 | creator risk >= 60 |
| R12 high_concentration | -2 | top10 > 50% |
| R13 tiny_liquidity | -2 | liq < $5K |
| R14 high_sell_tax | -3 | sell tax > 10% |
| R15 rugcheck_danger | -2 | rugcheck score >= 50 (reduced from -4: fires on ~99% of fresh PumpFun tokens — structural, not scam) |
| R16 high_dev_holds | -2 | dev holds >= 50% |
| R17 price_manipulation | -3 | price divergence > 20% |
| R18 volume_dried_up | -2 | 1h/5m vol ratio > 12 (5m < 8% of 1h, meaning recent volume dried up) |
| R19 holder_deceleration | -1 | holders declining > 5% |
| R20 lp_removal_active | -4 | LP removed >= 20% |
| R21 cross_token_coordination | -3 | whale coordination detected |
| R23 token2022_danger | -3 | dangerous token extensions |
| R24 sell_sim_failed | -5 | Jupiter sell sim failed (excludes API errors — only fires on actual unsellable tokens) |
| R25 bundled_buy | -3 | coordinated first-block buys |
| R26 serial_deployer | -3 | 5+ dead tokens |
| R27 lp_not_burned | -1 | LP not secured |
| R28 goplus_honeypot | -10 | GoPlus honeypot confirm |
| R29 no_socials | -1 | metadata score <= -3 |
| R30 wash_trading_pnl | -3 | wash trading suspected |
| R31 goplus_critical_risk | -5 | critical flags |
| R32 rugcheck_multi_danger | -3 | 3+ danger risks |
| R33 low_decentralization | -3 | bubblemaps < 0.3 (DISABLED) |
| R34 fee_payer_sybil | -6 | >50% share fee payer |
| R35 funding_chain_suspicious | -4 | funding risk >= 60 |
| R36 token_convergence | -5 | >50% converge to 1 wallet |
| R37 jito_bundle_snipe | -6 | MEV bundle detected |
| R38 mutable_metadata | -1 | metadata mutable (reduced from -2: fresh tokens start mutable, renounce comes later) |
| R39 name_spoofing | -5 | homoglyphs detected |
| R40 high_insider_network | -4 | 30%+ insiders |
| R41 jupiter_banned | -10 | banned on Jupiter |
| R51 llm_high_risk | -1 | LLM risk >= 80 (reduced from -3: Gemini Flash Lite is overly conservative for memecoins) |
| R54 holder_exodus | -3 | holder growth <= -20% |

### Action Thresholds
- **strong_buy**: net >= 8
- **buy**: net >= 5
- **watch**: net >= 2
- **avoid**: net < 2

## Score v3 Differences (Momentum-Weighted)
- Momentum signals carry more weight (0-35 pts vs 0-25 in v2)
- Smart money strongest signal (0-20 pts vs 0-15)
- Volume acceleration (5m vs 1h): bonus 0-5 pts (unique to v3, guarded: skips young tokens where vol_1h ≈ vol_5m)
- Security is pass/fail gate (-30 to +10)
- Harsher penalties: mintable=-20, sell_sim_failed=-25, convergence=-18
- Holders (crowd validation) lower weight: 0-10 (vs 0-20 in v2)
- Data completeness gate: requires only 2 metrics (more lenient than v2)
- Currently v3 is tracked for A/B comparison only; signals use v2

## A/B Comparison
Both v2 and v3 are computed on every enrichment cycle and stored in `TokenSnapshot.score` / `TokenSnapshot.score_v3`. Divergence is logged in real-time:
- delta >= 15 → WARNING
- delta >= 8 → INFO

Offline comparison: `scripts/compare_scoring_models.py`
