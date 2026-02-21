# Deep Analysis: Data Collection System
**Date**: 2026-02-18
**Status**: SUPERSEDED — all issues from this analysis resolved. See [Phase 17 Audit Fixes](phase17-audit-fixes.md) for latest state.
**Original Status**: Production (alpha), running on local machine

---

## 1. Architecture Overview

```
                    DISCOVERY LAYER                          ENRICHMENT LAYER
              ┌──────────────────────┐              ┌─────────────────────────┐
              │  PumpPortal WS       │              │  Enrichment Pipeline    │
              │  (pump.fun tokens)   │──┐           │  11 stages: 30s → 24h  │
              ├──────────────────────┤  │   ┌───┐   │                         │
              │  Meteora DBC WS      │──┼──>│ Q │──>│  GMGN (price/security) │
              │  (Believe/Bonk/Boop) │  │   └───┘   │  DexScreener (cross)   │
              ├──────────────────────┤  │           │  Scoring (0-100)       │
              │  GMGN Polling        │──┘           │  Outcomes tracking     │
              │  (new_pairs/trending)│              └─────────────────────────┘
              └──────────────────────┘                         │
                                                               ▼
                                                    ┌─────────────────────┐
                                                    │  PostgreSQL         │
                                                    │  tokens             │
                                                    │  token_snapshots    │
                                                    │  token_security     │
                                                    │  token_top_holders  │
                                                    │  token_outcomes     │
                                                    └─────────────────────┘
```

### Components running in parallel:
1. **PumpPortal WebSocket** — real-time pump.fun new tokens + migrations to Raydium
2. **Meteora DBC WebSocket** — real-time DBC pools (logsSubscribe on Solana)
3. **GMGN Polling Loop** — new DEX pairs + pump trending (every 60s)
4. **Enrichment Worker** — priority queue, 11 stages, scoring + persistence
5. **Stats Reporter** — periodic connection health + message count logging

---

## 2. Current Database State

| Source | Tokens | Notes |
|--------|--------|-------|
| pumpportal | 655 | Real-time WS, ~10-20 new/min |
| gmgn_new_pairs | 566 | Polling every 60s, batch 50 |
| meteora_dbc | 43 | Real-time WS, ~5-15 events/min |
| meteora_dbc_migration | 1 | DAMM v2 migration events |
| **Total** | **1265** | |

### Enrichment Coverage

| Stage | Snapshots | % of Total |
|-------|-----------|------------|
| INITIAL (+30s) | 533 | 42% |
| MIN_2 (+2m) | 164 | 13% |
| MIN_5 → HOUR_24 | **0** | **0%** |

### Score Distribution (of 778 scored snapshots)

| Score Range | Count | % | Meaning |
|-------------|-------|---|---------|
| 0 (disqualified) | 401 | 52% | honeypot / is_mintable |
| 1-14 | 42 | 5% | Very weak, pruned at MIN_5 |
| 15-29 | 100 | 13% | Weak, pruned at MIN_15 |
| 30-49 | 29 | 4% | Medium — potentially interesting |
| 50-69 | 18 | 2% | Good quality |
| 70+ | 0 | 0% | None yet (needs market_cap fix) |
| no_score | 188 | 24% | Missing data for scoring |

### Security Analysis
- 611/1265 tokens (48%) have security data
- 12 DBC tokens identified as "believe" launchpad
- 449 snapshots have top 10 holders data

### Outcomes
- 532 outcome records, but **all with null peak_multiplier**
- 0 rugs detected, 0 tokens with 2x+, 5x+, 10x+
- **Root cause**: no snapshots beyond INITIAL → no price dynamics to compute outcomes

---

## 3. Critical Problems

### CRITICAL #1: Enrichment pipeline stops after INITIAL/MIN_2

**Symptom**: 533 INITIAL snapshots, 164 MIN_2, **zero** MIN_5 through HOUR_24.

**Impact**: Without multi-stage data we cannot:
- Track price dynamics (growth/dump curves)
- Compute outcomes (peak multiplier, time to peak)
- Validate scoring model against real results
- Detect patterns for entry timing

**Probable causes to investigate**:
- Enrichment tasks stored in memory (asyncio.Queue) — lost on restart
- MIN_5 pruning (score < 15) may be too aggressive at 52% disqualification rate
- Worker may have timing/scheduling bug for stages after MIN_2
- Score = 0 tokens should not be scheduled for further enrichment

### CRITICAL #2: market_cap is NULL for all snapshots

**Symptom**: Top 10 scored tokens all show `mcap=None`.

**Impact**:
- Scoring loses up to 30 points (liquidity-based scoring works, but mcap-based does not)
- Cannot compute market_cap outcomes (peak_mcap, initial_mcap)
- Makes fundamental analysis impossible

**Probable cause**: GMGN response structure — market_cap field may be nested differently than parser expects (e.g. inside `price` object).

### HIGH #3: Outcomes are empty shells

**Symptom**: 532 records, all fields null.

**Impact**: Cannot learn from historical data. Cannot validate any scoring model.

**Root cause**: Directly caused by Problem #1 — no later-stage snapshots to compute peaks.

### MEDIUM #4: 52% disqualification rate

**Symptom**: 401/778 scored tokens get score=0.

**Possible issues**:
- `is_mintable=True` for many fresh pump.fun tokens — they may become non-mintable after bonding curve completion
- GMGN security API may be overly aggressive for brand-new tokens
- Should we check security only at MIN_15+ (after token has stabilized)?

---

## 4. Data Sources: What We Collect

### 4.1 PumpPortal (pump.fun) — FREE
| Data | Quality | Notes |
|------|---------|-------|
| New token events (mint, name, symbol, creator) | Excellent | Real-time, <1s latency |
| Initial buy (SOL amount) | Excellent | Creator's first buy |
| Bonding curve reserves | Good | vSolInBondingCurve, vTokensInBondingCurve |
| Migration events (to Raydium) | Excellent | Instant notification |
| Buy/sell trades | Available but **NOT saved** | WS subscription exists, not wired to persistence |

### 4.2 Meteora DBC (Believe/LetsBonk/Boop) — FREE (Helius)
| Data | Quality | Notes |
|------|---------|-------|
| New pool events | Good | Real-time via logsSubscribe, 5-40s getTransaction delay |
| Pool address, base_mint, creator | Good | Extracted from transaction accounts |
| Launchpad identification | Good | via pool_config account mapping |
| Migration to DAMM v2 | Good | Real-time detection |
| VirtualPool reserves | Intermittent | Account often empty at creation, needs retry |
| DAMM v2 pool data (TVL, volume) | Available but **NOT used** | REST client exists |

### 4.3 GMGN — FREE (rate-limited)
| Data | Quality | Notes |
|------|---------|-------|
| Token price, liquidity | Good | Primary source, but market_cap parsing broken |
| Volume (5m/1h/24h) | Good | Key scoring input |
| Holders count | Good | Available for most tokens |
| Top 10 holders (address, %, PnL) | Good | 449 snapshots covered |
| Security (honeypot, mintable, LP, taxes) | Good | 48% coverage |
| New pairs discovery | Good | Polling every 60s, batch 50 |
| Pump trending tokens | Good | Complementary discovery |
| Smart wallets | **NOT connected** | Client + persistence ready, not wired in enrichment |
| Wallet info/trades | **NOT connected** | Methods exist in client, never called |

**Rate limit**: 1.5 RPS via TLS fingerprint bypass (fragile, Cloudflare-dependent)

### 4.4 DexScreener — FREE
| Data | Quality | Notes |
|------|---------|-------|
| Price (cross-validation) | Good | Independent from GMGN |
| Liquidity, FDV | Good | |
| Volume (5m/1h/24h) | Good | |
| Buy/sell transactions count | Available but **NOT saved** | txns.buys/sells fetched, not persisted |
| Boosted/trending tokens | Available but **NOT used** | Endpoint exists |

**Rate limit**: 4.0 RPS, public API, stable

---

## 5. What We DON'T Collect (and should)

### Tier 1 — High value, low effort (code exists or trivial)
| Data | Source | Effort | Value |
|------|--------|--------|-------|
| Real-time buy/sell trades | PumpPortal WS | Low — subscription API exists | Critical — order flow, buy pressure |
| Smart money purchases | GMGN | Low — client ready | High — follow smart money |
| DexScreener buy/sell count | DexScreener | Trivial — data already fetched | High — buy/sell ratio signal |
| market_cap (fix parsing) | GMGN | Trivial — fix response mapping | Critical — scoring fix |
| DAMM v2 pool data post-migration | Meteora REST | Low — client ready | Medium — post-graduation tracking |

### Tier 2 — High value, moderate effort
| Data | Source | Effort | Value |
|------|--------|--------|-------|
| Creator wallet history (prior launches) | Solana RPC / GMGN | Medium | High — serial scammer detection |
| On-chain holder velocity (new holders/min) | Solana RPC or GMGN polling | Medium | High — momentum signal |
| Bonding curve progress % | VirtualPool decoding | Low-Medium | Medium — graduation proximity |
| Token metadata (image, description, links) | GMGN / on-chain | Low | Medium — social credibility |

### Tier 3 — Valuable but complex
| Data | Source | Effort | Value |
|------|--------|--------|-------|
| Twitter/social mentions | Twitter API ($100/mo) or scraping | High | Medium-High — hype detection |
| Telegram group size/activity | Telegram API | High | Medium |
| Contract source analysis | Solana explorer | High | Medium — rug pattern detection |
| MEV/sandwich detection | Jito tips, on-chain | Very High | Medium |

---

## 6. Scoring Model Analysis

### Current formula (0-100):
```
Liquidity:   0-30 pts  ($10K → 15, $25K → 20, $50K → 25, $100K+ → 30)
Holders:     0-20 pts  (50 → 8, 100 → 12, 200 → 15, 500+ → 20)
Vol/Liq:     0-25 pts  (0.5 → 10, 1.0 → 15, 2.0 → 20, 5.0+ → 25)
Security:    0-25 pts  (LP burned +10, renounced +5, tax≤5% +5, top10<30% +5)
```

### Issues:
1. **No market_cap component** — top score possible is heavily liquidity-dependent
2. **No smart money signal** — missing entire "follow the money" dimension
3. **No velocity metrics** — static snapshot, no rate-of-change
4. **52% disqualified** — `is_mintable` and `is_honeypot` may be false positives for very new tokens
5. **Cannot validate** — outcomes are empty, no way to measure score accuracy

### Recommendations:
- Add market_cap tier scoring (after fixing parser)
- Add "momentum" component: holder growth rate, volume acceleration
- Consider soft penalty instead of hard 0 for `is_mintable` on tokens < 5 min old
- Add smart money buy signals as bonus points
- After 2+ weeks of full pipeline data: backtest score vs actual outcomes

---

## 7. Birdeye API Analysis (for Lite plan at $39/mo)

### Plan: Lite — $39/month
- **1.5M Compute Units** included
- **15 RPS** rate limit (10x better than GMGN's 1.5 RPS)
- Overage: $23 per 1M additional CUs
- **Stable, official API** — no TLS fingerprinting hacks needed

### Available endpoints on Lite:

| Endpoint | CU Cost | Useful for us | Priority |
|----------|---------|---------------|----------|
| Token Price (`/defi/price`) | 10 CU | Primary price source | HIGH |
| Token Overview (`/defi/token_overview`) | 30 CU | market_cap, liquidity, holders | HIGH |
| Token Security (`/defi/token_security`) | 50 CU | Honeypot, mintable, LP status | HIGH |
| Token Market Data (`/defi/v3/token/market-data`) | 15 CU | Volume, buy/sell data | HIGH |
| Token Trade Data (`/defi/v3/token/trade-data/single`) | 15 CU | Recent trades | MEDIUM |
| Token Metadata (`/defi/v3/token/meta-data/single`) | 5 CU | Name, symbol, image | LOW |
| OHLCV (`/defi/ohlcv`) | 40 CU | Price history candles | MEDIUM |
| Trades - Token (`/defi/txs/token`) | 10 CU | Individual trade events | MEDIUM |
| Token Top Traders | available | Whale tracking | HIGH |
| Token Holders | available (SOL only) | Holder list | MEDIUM |
| Token Trending | 50 CU | Discovery source | LOW |
| Token New Listing | available | Discovery source | LOW |
| Price - Multiple (`/defi/multi_price`) | batch | Batch price checks | HIGH |

### NOT available on Lite:
- Multiple/batch endpoints (except Price - Multiple)
- Token Market Data Multiple
- Token Trade Data Multiple

### CU Budget calculation (1.5M CU/month):

**Scenario: 1500 tokens/month, 11 enrichment stages per surviving token**

Assuming ~300 tokens survive pruning past INITIAL:

| Operation | Per token | Calls/month | CU/call | Total CU |
|-----------|-----------|-------------|---------|----------|
| Price (all stages) | 11 | 1500 × 1 + 300 × 10 | 10 | 45,000 |
| Token Overview (GMGN replacement) | 5 | 1500 × 1 + 300 × 4 | 30 | 81,000 |
| Security (3 stages) | 3 | 1500 × 1 + 300 × 2 | 50 | 105,000 |
| Market Data (buy/sell) | 5 | 300 × 5 | 15 | 22,500 |
| Trade Data | 3 | 300 × 3 | 15 | 13,500 |
| Top Traders | 2 | 300 × 2 | ~30 | 18,000 |
| **Total** | | | | **~285,000 CU** |

**Budget verdict: 285K of 1.5M CU — only 19% utilization. Lite plan is MORE than enough.**

Even if we 3x the volume (4500 tokens/month), we'd use ~855K CU — still within Lite.

### Integration strategy:
1. **Birdeye as PRIMARY** for: price, market_cap, token overview, security, market data
2. **GMGN as SECONDARY** for: smart money (unique data), top holders PnL
3. **DexScreener stays** for: cross-validation, trending
4. **Benefit**: 15 RPS (Birdeye) vs 1.5 RPS (GMGN) = **10x faster enrichment**

---

## 8. Can We Catch Entry Patterns?

### Current state: NO

**Reasons:**
1. Pipeline stops at INITIAL — no price dynamics data
2. Outcomes empty — no historical success/failure data
3. market_cap null — scoring is incomplete
4. No trade flow — no buy/sell pressure metrics

### After fixes (2-4 weeks): YES, these patterns become detectable:

| Pattern | Data needed | When detectable | Expected signal strength |
|---------|-------------|-----------------|--------------------------|
| High initial score → early entry | Score + 24h outcome | After scoring fix + 2 weeks | Medium |
| Smart money early buy | GMGN smart wallets | After wiring existing code | High |
| Volume spike in first 5 min | Volume_5m at MIN_2/MIN_5 | After pipeline fix | Medium-High |
| Holder velocity > 100/min | Holders at MIN_2 vs INITIAL | After pipeline fix | Medium |
| Believe launchpad + high liq | DBC data + Birdeye liq | After Birdeye integration | Medium |
| Creator serial launcher | Creator wallet history | After wallet profiling | High |
| LP burned/locked + renounced | Security data | Already collecting | Medium |
| Buy/sell ratio > 3:1 in first 10 min | PumpPortal trades or Birdeye | After trade storage | High |
| Graduation to Raydium → momentum | Migration events + price | After pipeline fix | Medium |

### Minimum viable pattern detection:
1. Fix enrichment pipeline (stages work through HOUR_24)
2. Fix market_cap parsing
3. Integrate Birdeye for stable, fast data
4. Wire smart money tracking
5. Run 2 weeks → analyze outcomes vs initial scores

---

## 9. API Cost / Value Comparison

| Service | Cost | RPS | Stability | Unique Value |
|---------|------|-----|-----------|--------------|
| PumpPortal | Free | WS | Good | Real-time pump.fun events |
| Helius (Solana RPC) | Free tier | 10 | Excellent | Meteora DBC monitoring |
| GMGN | Free (hacked) | 1.5 | **Fragile** | Smart money, wallet PnL |
| DexScreener | Free | 4 | Good | Cross-validation |
| **Birdeye Lite** | **$39/mo** | **15** | **Excellent** | **Price, mcap, security, trades** |

**$39/mo is the highest-value investment we can make right now.** It:
- Eliminates GMGN as single point of failure for core data
- 10x rate improvement (15 vs 1.5 RPS)
- Adds market_cap, trade data, OHLCV we don't have
- Official API — no TLS bypass fragility
- 1.5M CU/mo is ~5x our projected needs

---

## 10. Roadmap / Checklist

### Phase 4A: Fix Critical Pipeline Issues
- [x] **P0**: Debug enrichment pipeline — why stages after MIN_2 don't execute
- [x] **P0**: Fix market_cap parsing from GMGN response
- [x] **P0**: Verify outcomes computation works once later stages run
- [x] **P1**: Review `is_mintable` disqualification — too aggressive for fresh tokens?
- [x] **P1**: Add persistence for enrichment queue (survive restarts)

### Phase 4B: Birdeye Integration ($39/mo Lite)
- [x] **P0**: Add Birdeye API client (`src/parsers/birdeye/client.py`)
- [x] **P0**: Birdeye models + response parsing (price, overview, security, market data)
- [x] **P0**: Settings: `birdeye_api_key`, `enable_birdeye` feature flag
- [x] **P1**: Replace GMGN as primary for: price, market_cap, liquidity, volume
- [x] **P1**: Add Birdeye token_security as primary (GMGN security as fallback)
- [x] **P1**: Add market data (buy/sell counts) to snapshots
- [x] **P2**: Add OHLCV fetching for post-graduation price history
- [x] **P2**: Add Birdeye trade data for individual trade events

### Phase 4C: Wire Existing Unused Code
- [x] **P1**: Connect smart money tracking (GMGN) to enrichment pipeline
- [x] **P1**: Save DexScreener buy/sell transaction counts to snapshots
- [x] **P2**: Save PumpPortal trade events (buy/sell) to new table
- [x] **P2**: Activate DAMM v2 pool data fetching post-migration
- [x] **P2**: DexScreener boosts integration

### Phase 4D: Enhanced Signals
- [x] **P2**: Creator wallet profiling (prior launches, success rate)
- [x] **P2**: Holder velocity metric (new holders per minute)
- [x] **P2**: Bonding curve progress % for DBC tokens
- [x] **P3**: Token metadata enrichment (image, social links)
- [x] **P3**: Scoring model v2 (add momentum, smart money, velocity components)

### Phase 5: Pattern Detection & Backtesting
- [x] **P2**: Score vs outcome correlation analysis (after 2 weeks data)
- [x] **P2**: Define entry signal rules based on historical patterns
- [x] **P3**: Backtesting framework for scoring model
- [x] **P3**: A/B scoring models (compare accuracy)
- [x] **P4**: Real-time alert system for high-score + signal match tokens

### Phase 6: Operational Reliability
- [x] **P2**: Enrichment queue persistence (Redis or DB-backed)
- [x] **P2**: Health monitoring dashboard
- [x] **P3**: GMGN fallback to Birdeye on circuit breaker
- [x] **P3**: Metrics: enrichment latency, coverage, API error rates
- [x] **P4**: Alerting on system health degradation

---

## 11. Priority Matrix

```
                    HIGH VALUE
                       │
    ┌──────────────────┼──────────────────┐
    │  Fix pipeline    │  Birdeye integ.  │
    │  Fix market_cap  │  Smart money     │
    │  Fix outcomes    │  Buy/sell ratio  │
    │                  │                  │
LOW ├──────────────────┼──────────────────┤ HIGH
EFFORT │               │                  │ EFFORT
    │  Save DexSc txns │  Creator profile │
    │  DBC progress %  │  Pattern detect  │
    │  DAMM v2 data    │  Alert system    │
    │                  │                  │
    └──────────────────┼──────────────────┘
                       │
                    LOW VALUE
```

**Start with top-left quadrant**: high value, low effort.

---

## 12. Estimated Timeline

| Phase | Tasks | Dependencies |
|-------|-------|-------------|
| 4A (Pipeline fixes) | 5 tasks | None — START HERE |
| 4B (Birdeye) | 8 tasks | Birdeye API key |
| 4C (Wire existing) | 5 tasks | 4A (pipeline must work first) |
| 4D (Enhanced signals) | 5 tasks | 4B + 4C |
| 5 (Patterns) | 5 tasks | 4A + 2 weeks of data |
| 6 (Reliability) | 5 tasks | Any time |

**Critical path**: 4A → 4C → 5 (pipeline → data collection → pattern detection)
**Parallel track**: 4B (Birdeye can be integrated independently)
