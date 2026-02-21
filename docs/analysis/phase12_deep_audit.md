# Deep Analysis: Early Detection Pipeline — Phase 12 Complete

**Date**: 2026-02-19
**Status**: Phase 12 complete, 334 tests passing
**Budget**: $500/month total infrastructure

---

## 1. Current System State

**334 tests, 28 signal rules, two-phase architecture PRE_SCAN → INITIAL.**

### Timeline
```
T+0     Discovery (PumpPortal WS / Meteora / GMGN polling)
T+5s    PRE_SCAN: mint parsing + Jupiter sell sim → reject 50% scam in <2s
T+45s   INITIAL: full enrichment (12+ API sources, 5-15s)
T+2m    MIN_2 → ... → HOUR_24: re-enrichment pipeline
```

### Current Data Sources (15 total)
| Source | Cost | What It Does |
|--------|------|-------------|
| PumpPortal WS | Free | Real-time new token discovery |
| Meteora DBC WS | Free | DBC pool creation events |
| GMGN (token/security/holders) | Free | Price, security, top holders |
| Birdeye (overview/security/OHLCV/trades) | ~$39/mo | Primary market data |
| DexScreener | Free | Cross-validation, liquidity |
| Jupiter (price + sell sim) | Free | Honeypot detection |
| Rugcheck | Free | Token audit score |
| GoPlus Security | Free | Structured security data |
| Pump.fun creator history | Free | Serial scammer detection |
| Raydium LP | Free | LP burn verification |
| Helius (bundled buy + wallet age) | ~$49/mo | On-chain analysis |
| Solana RPC | Via Helius | Mint parsing |
| Smart Money (GMGN cache) | Free | Smart wallet tracking |
| Wallet Cluster (DB) | Free | Coordinated trader grouping |
| Funding Trace (Helius) | Via Helius | Creator funding analysis |

---

## 2. Critical Gaps — What The System Cannot See

### TIER 1: Blind Spots (FREE to fix, on-chain data)

#### 2.1. Fee Payer Clustering
- **Problem**: 10 "different" buyers may use the SAME fee payer = 1 person
- **Impact**: Sybil attacks invisible without this
- **Cost**: $0 (data already in Helius transactions)
- **Implementation**: New module, ~4h

#### 2.2. Multi-hop Funding Trace (3 hops instead of 1)
- **Problem**: Scammers use intermediary wallets (2-3 hops)
- **Impact**: Organized rug rings invisible with 1 hop
- **Cost**: 2-3 extra Helius RPC calls per creator (~$0.003)
- **Implementation**: Extend existing funding_trace.py, ~3h

#### 2.3. Convergence Analysis (where do tokens go after purchase)
- **Problem**: If first-block buyers send tokens to ONE wallet = dev consolidation
- **Impact**: Guaranteed rug signal, currently invisible
- **Cost**: 1-2 Helius RPC calls per buyer
- **Implementation**: New module, ~3h

#### 2.4. Sub-second PRE_SCAN (T+100ms instead of T+5s)
- **Problem**: Waiting 5s unnecessarily; mint data available in 100ms
- **Impact**: Can reject obvious scam 4.9s faster
- **Cost**: $0, architecture change
- **Implementation**: Split PRE_SCAN into 3 time-boxed phases, ~5h

#### 2.5. Token Metadata Scoring
- **Problem**: We collect website/twitter/telegram but DON'T score them
- **Impact**: Missing easy signal (no socials = -2, full socials = +3)
- **Cost**: $0, data already in DB
- **Implementation**: ~2h

#### 2.6. Holder PnL Analysis
- **Problem**: We collect holder PnL data but DON'T use it
- **Impact**: 80% holders in loss + price rising = wash trading signal
- **Cost**: $0, data already in DB
- **Implementation**: ~2h

### TIER 2: API Improvements (some paid)

#### 2.7. SolSniffer — Specialized Solana Security Scoring
- **What**: 20+ security indicators, top-50 holders with cluster analysis
- **Unique**: Wallet clustering by SOL transfers between holders
- **Cost**: Free tier available, paid for batch API
- **URL**: https://solsniffer.com/api-service

#### 2.8. Bubblemaps — Holder Graph Connections
- **What**: Top-150 holders with historical SOL transaction graph
- **Unique**: Only API showing direct transfers between holders
- **Cost**: Free API with rate limits per token
- **URL**: https://bubblemaps.io

#### 2.9. Pump.fun Full API (BankkRoll spec)
- **What**: Bonding curve position per buyer, comments, creator activity
- **Unique**: Dev entry at <10% bonding curve = guaranteed dump
- **Cost**: Free, JWT auth
- **URL**: https://github.com/BankkRoll/pumpfun-apis

#### 2.10. GoPlus Transaction Simulation (Aug 2025 update)
- **What**: Simulate TX before sending, catch hidden honeypots
- **Cost**: Free core API
- **URL**: https://github.com/GoPlusSecurity/goplus-mcp

#### 2.11. Jito Bundle Detection
- **What**: Detect if token was sniped via Jito bundles
- **Unique**: Smart snipers entering = positive signal
- **Cost**: Free gRPC monitoring

### TIER 3: Paid APIs (for future phases)

#### 2.12. SolSniffer Pro — Batch API
- **What**: Batch 100 tokens/request, deep audit with cluster analysis
- **Cost**: TBD (contact for pricing)
- **Need**: When processing >500 tokens/day

#### 2.13. Solscan Pro — Holder History
- **What**: 3-year holder history, transfer analysis
- **Cost**: Subscription (contact for pricing)
- **Need**: For deep investigation mode, not real-time

#### 2.14. Birdeye WebSocket + PnL
- **What**: Real-time new listings, wallet PnL
- **Cost**: Already paying Birdeye, need Starter+ plan
- **Need**: When we need <1s discovery latency

---

## 3. What Works Well

| Component | Rating | Note |
|-----------|--------|------|
| PRE_SCAN mint parsing | Excellent | Token2022 extensions, 100ms |
| Jupiter sell simulation | Excellent | Instant honeypot check |
| Creator profiler | Good | But only 1 hop funding trace |
| Bundled buy detection | Good | But no fee payer clustering |
| Smart money tracking | Good | Depends on GMGN wallet quality |
| Whale dynamics | Good | Diff + patterns, cross-token |
| Volume profile / wash trading | Good | 3 heuristics, no circular |
| Wallet age check | Good | Sybil via Helius |
| Signal rules (R1-R28) | Good | Wide coverage |
| Scoring v2/v3 | Good | Multi-factor, completeness cap |

---

## 4. Architecture Improvements

### 4.1. Three-phase PRE_SCAN
```
T+100ms:  INSTANT_SCORE (mint authority, freeze, Token2022)
          → Hard reject: mint+freeze active, permanentDelegate

T+250ms:  CREATOR_SCORE (DB lookup + 1-hop funding)
          → Hard reject: creator rug_rate > 75% + 10+ launches

T+500ms:  FIRST_BLOCK_SCORE (bundled buys, fee payer clustering)
          → Soft flag: bundled > 50%, same fee payer
```

### 4.2. Use Existing Unused Data
- Token metadata (description/website/twitter/telegram) → score impact
- Holder PnL → wash trading detection
- Bonding curve position → dev entry analysis
- GoPlus full report (not just is_honeypot) → multi-field scoring

### 4.3. ML Model (future)
- XGBoost on all scoring components → probability of rug
- MemeTrans dataset (41,470 tokens, 122 features) for training
- A/B test ML score vs rule-based score

---

## 5. Data Collected But Not Used

### In Database
- `pnl` in TokenTopHolder — never queried
- `balance` in TokenTopHolder — only `percentage` used
- `bonding_curve_key` in Token — never read
- `v_tokens_in_bonding_curve` — not scored
- `description`, `website`, `twitter`, `telegram` — not scored
- `raw_data` JSON in TokenSecurity — only specific fields extracted
- `rugcheck_risks` string — never parsed
- `goplus_score` string — only is_honeypot used
- `dbc_quote_reserve`, `dbc_damm_tvl`, `dbc_damm_volume` — not used

### Collected by APIs But Not Saved
- Jupiter `depth` field
- DexScreener `fdv` (prefer market_cap)
- Birdeye full metadata social links

---

## 6. Recommended Phase 13 Plan

### Priority by ROI (implement first → last)

| # | Component | Type | Cost | Time | Tests |
|---|-----------|------|------|------|-------|
| 1 | Fee Payer Clustering | On-chain | $0 | 4h | 5 |
| 2 | Multi-hop Funding (3 hops) | On-chain | ~$0 | 3h | 4 |
| 3 | Convergence Analysis | On-chain | ~$0 | 3h | 4 |
| 4 | Sub-second PRE_SCAN | Architecture | $0 | 5h | 6 |
| 5 | Token Metadata Scoring | Existing data | $0 | 2h | 3 |
| 6 | Holder PnL Analysis | Existing data | $0 | 2h | 3 |
| 7 | SolSniffer Integration | API (free) | $0 | 4h | 5 |
| 8 | Bubblemaps Cluster Detection | API (free) | $0 | 4h | 4 |
| 9 | Pump.fun Bonding Curve | API (free) | $0 | 3h | 3 |
| 10 | GoPlus TX Simulation | API (free) | $0 | 3h | 4 |
| 11 | New Signal Rules R29-R36 | Rules | $0 | 3h | 8 |
| 12 | Scoring Integration | Scoring | $0 | 3h | 6 |

---

## 7. Budget Analysis

### Current Monthly Costs
| Service | Cost | What We Get |
|---------|------|-------------|
| Birdeye | $39/mo | 1.5M CU, token data |
| Helius | $49/mo | RPC + DAS + parsing |
| **Total** | **$88/mo** | |

### Remaining Budget: $500 - $88 = $412/mo available

### Potential Paid Additions
| Service | Cost | Priority | Need |
|---------|------|----------|------|
| SolSniffer Pro | TBD | Medium | Batch API when >500 tokens/day |
| Solscan Pro | TBD | Low | Historical holder data |
| Birdeye Premium | ~$99/mo | Low | More CU + WebSocket |
| Twitter/X API (RapidAPI) | ~$25/mo | Future | Social sentiment |
| Telegram monitoring | ~$25/mo | Future | Community analysis |

---

## 8. Key Research Sources

- [SolSniffer API](https://solsniffer.com/api-service)
- [SolSniffer GitHub](https://github.com/Solsniffer-com/Solana-Security-Analysis-API-s-by-Solsniffer)
- [Bubblemaps](https://bubblemaps.io/)
- [GoPlus Security API](https://gopluslabs.io/token-security-api)
- [GoPlus MCP Server](https://github.com/GoPlusSecurity/goplus-mcp)
- [MemeTrans Dataset](https://arxiv.org/abs/2602.13480)
- [Pump.fun Full API Spec](https://github.com/BankkRoll/pumpfun-apis)
- [Helius Pricing](https://helius.dev/pricing)
- [Birdeye Pricing](https://bds.birdeye.so/pricing)
- [Solscan Pro API](https://solscan.io/apis)
- [DexScreener API](https://docs.dexscreener.com/api/reference)
- [Rugcheck Swagger](https://api.rugcheck.xyz/swagger/index.html)
- [Dune Solana Meme Analysis](https://dune.com/pseudocode88_aux/solana-meme-token-analysis)
- [Pumpfun Deployer Dashboard](https://github.com/oladeeayo/Pumpfun-Token-Deployer-Records-Dashboard)
- [Jito MEV Bot](https://github.com/jito-labs/mev-bot)
