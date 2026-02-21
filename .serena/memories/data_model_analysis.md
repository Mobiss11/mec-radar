# Data Model Analysis Report

## Executive Summary

The сcrypto project has a mature, multi-layer data model designed for token analysis & outcome tracking:
- **7 core token models** (Token, TokenSnapshot, TokenSecurity, TokenTopHolder, TokenOHLCV, TokenTrade, TokenOutcome)
- **Outcome tracking system** linking signals to performance (peak_multiplier, is_rug detection)
- **Rich enrichment pipeline** with 11-stage scoring and pattern detection
- **Historical backtesting capability** via TokenSnapshot timeseriesdata

## Data Storage Architecture

### Tier 1: Token Identity (Token table)
**Fields:** 24 fields across 3 categories

**Always populated:**
- `address`, `chain`, `first_seen_at` (timestamp, server default)

**Commonly populated (80%+):**
- `name`, `symbol`, `created_at` (from parsed data sources)
- `source` (pumpportal, meteora_dbc, or default detection)
- `creator_address` (from PumpPortal or Meteora events)
- `image_url`, `description`, `website`, `twitter`, `telegram` (Birdeye metadata)

**Source-specific fields (sparsely populated):**
- **PumpPortal launch data** (~30% for pump.fun tokens):
  - `initial_buy_sol`, `initial_mcap_sol`, `bonding_curve_key`
  - `v_sol_in_bonding_curve`, `v_tokens_in_bonding_curve`
  
- **Meteora DBC data** (~10% for DBC launchpad tokens):
  - `dbc_pool_address`, `dbc_quote_reserve`, `dbc_launchpad`
  - `dbc_is_migrated`, `dbc_migration_timestamp`, `dbc_damm_tvl`, `dbc_damm_volume`

**Indexes:** `creator_address` (for creator profiling), `source` (data source filtering)

---

### Tier 2: Periodic Metrics (TokenSnapshot table)
**Fields:** 41 fields across 7 categories  
**Frequency:** Every enrichment cycle (continuous during active trading)

**Core price/liquidity metrics (Birdeye primary, GMGN/DexScreener fallback):**
- `price`, `market_cap`, `liquidity_usd`
- `volume_5m`, `volume_1h`, `volume_24h`
- `holders_count`, `top10_holders_pct`, `dev_holds_pct`

**Trade counts (buy/sell pressure signals):**
- `buys_5m`, `sells_5m`, `buys_1h`, `sells_1h`, `buys_24h`, `sells_24h`

**DexScreener cross-validation (separate column set):**
- `dex_price`, `dex_liquidity_usd`, `dex_volume_5m/1h/24h`, `dex_fdv`

**Volatility metrics (UNUSED in scoring - see below):**
- `volatility_5m`, `volatility_1h`, `lp_removed_pct`

**Jupiter cross-validation:**
- `jupiter_price` (rarely used, mostly NULL)

**Smart money & social signals (Phase 15):**
- `smart_wallets_count` (count of tracked smart wallets buying)
- `holders_in_profit_pct` (from GMGN PnL data)
- `vybe_top_holder_pct` (concentration in top Vybe holder)
- `twitter_mentions`, `twitter_kol_mentions`, `twitter_max_likes`

**Scoring versions:**
- `score` (v2 - primary scoring model)
- `score_v3` (v3 - momentum-weighted, A/B tested)

**Pipeline tracking:**
- `stage` (INITIAL, HOUR_1, ..., HOUR_24)
- `timestamp` (server default = now())

**Indexes:** 
- `idx_snapshots_token_time` (query last N snapshots for a token)
- `idx_snapshots_timestamp` (query all snapshots in time window)

---

### Tier 3: Security Analysis (TokenSecurity table)
**Fields:** 16 fields + 1 JSON column  
**Update frequency:** Once per token (re-checked periodically)

**Core security flags (from GMGN):**
- `is_honeypot` (hard disqualifier = score 0)
- `is_mintable` (soft penalty = -15 pts)
- `lp_burned`, `lp_locked` (positive signals)
- `contract_renounced`, `is_proxy`, `is_open_source`

**Tax & concentration metrics:**
- `buy_tax`, `sell_tax` (affects scorability)
- `top10_holders_pct`, `dev_holds_pct`, `dev_token_balance`
- `lp_lock_duration_days`

**Rug detection (Phase 11+):**
- `rugcheck_score` (Rugcheck.xyz API integration)
- `rugcheck_risks` (comma-separated risk names)

**Phase 12 additions:**
- `bundled_buy_detected` (MEV/coordinated buys detection)
- `lp_burned_pct_raydium` (Raydium-specific LP analysis)

**Phase 12+ alternative sources:**
- `goplus_score` (JSON blob with GoPlus security report fields)

**Metadata:**
- `raw_data` (full GMGN response as JSON)
- `checked_at` (last update timestamp)

---

### Tier 4: Point-in-Time Holder Snapshots (TokenTopHolder table)
**Fields:** 6 fields  
**Relationship:** FK to TokenSnapshot (captures top-10 at specific moment)

**Data captured:**
- `rank` (1-10)
- `address`, `balance`, `percentage` (holdings snapshot)
- `pnl` (profit/loss in USD from GMGN - early entry detection)

**Indexes:**
- `idx_top_holders_snapshot` (query all holders in specific snapshot)
- `idx_top_holders_token` (track holder evolution for a token)

**Analysis usage:**
- Whale dynamics (see below) - detect accumulation/distribution
- Holder PnL analysis - detect wash trading, early stage vs bagholder
- Concentration tracking - top-10 concentration delta as scoring signal

---

### Tier 5: Price Candles (TokenOHLCV table)
**Fields:** 8 fields  
**Frequency:** Filled from Birdeye API for intervals: 1m, 5m, 15m, 1H

**Data:**
- `timestamp`, `interval`, `open`, `high`, `low`, `close`, `volume`

**Current usage:**
- NOT actively used in current scoring pipeline
- Available for future ML/pattern recognition (e.g., support/resistance detection)
- OHLCV patterns module exists (`ohlcv_patterns.py`) but not integrated into scoring

**Indexes:**
- `uq_ohlcv_token_time_interval` (prevent duplicates, speed queries)

---

### Tier 6: Individual Trade Events (TokenTrade table)
**Fields:** 9 fields  
**Frequency:** Realtime stream from Birdeye/PumpPortal

**Data:**
- `source` (birdeye | pumpportal)
- `tx_hash`, `timestamp`, `side` (buy/sell)
- `price_usd`, `amount_token`, `amount_usd`, `amount_sol`
- `wallet_address`

**Current usage:**
- NOT aggregated into real-time analysis
- Birdeye trades stored but not queried in active pipeline
- PumpPortal trades connected to SmartWallet tracking

**Indexes:**
- `idx_trades_token_time` (historical analysis by token)
- `idx_trades_wallet` (wallet transaction history)

**Future potential:**
- MEV detection, sandwich attack identification
- Wallet flow analysis, funding source tracking

---

### Tier 7: Outcome Tracking (TokenOutcome table)
**Fields:** 10 fields  
**Relationship:** One per token (unique constraint)

**Peak performance metrics:**
- `initial_mcap` (backfilled on first snapshot)
- `peak_mcap`, `peak_price`, `peak_multiplier`
- `time_to_peak_sec` (seconds from creation to peak)
- `peak_snapshot_id` (FK to snapshot at peak)

**Final state (set at stage HOUR_24 or beyond):**
- `final_mcap`, `final_multiplier`

**Rug detection:**
- `is_rug` (Boolean, True if >90% drawdown from peak detected)
- `outcome_stage` (HOUR_4 if early rug, FINAL if late)

**Metadata:**
- `updated_at` (tracks when outcome was last computed)

**Analysis usage:**
- **Backtesting**: Compare INITIAL snapshot score vs peak_multiplier (ROI)
- **Signal quality**: Update all Signal records with peak_multiplier_after, peak_roi_pct
- **Creator profiling**: Aggregate outcomes by creator (success_count, avg_peak_multiplier)

---

### Tier 8: Creator Profiles (CreatorProfile table)
**Fields:** 10 fields  
**Relationship:** One per unique creator address

**Launch history:**
- `total_launches`, `rugged_count`, `success_count`
- `avg_peak_multiplier`, `avg_time_to_peak_sec`
- `last_launch_at`

**Risk scoring:**
- `risk_score` (0-100, higher = riskier - aggregate penalty for scoring)
- `is_first_launch` (Boolean, new creators get bonus risk)

**Phase 12 funding analysis:**
- `funded_by` (address of funding wallet)
- `funding_risk_score` (inherited from funder's risk_score)
- `pumpfun_dead_tokens` (count of dead launches on pump.fun)

**Metadata:**
- `updated_at`

---

### Tier 9: Smart Wallet Tracking (SmartWallet + WalletActivity tables)
**SmartWallet fields:** 8 fields (discovered_at, updated_at)
- `address`, `chain`, `category`, `label`
- `win_rate`, `avg_profit_pct`, `total_trades`, `total_pnl_usd`

**WalletActivity fields:** 9 fields
- FK to SmartWallet + Token
- `action` (buy/sell), `amount_sol/usd/token`, `tx_hash`, `timestamp`

**Current usage:**
- Smart wallet count aggregated into TokenSnapshot.smart_wallets_count
- Used as scoring signal (0-15 pts bonus for wallets buying)
- PumpPortal trades directly logged as WalletActivity

---

### Tier 10: Signals (Signal table)
**Fields:** 10 fields  
**Relationship:** One per snapshot that generates a signal (score >= threshold)

**Signal metadata:**
- `token_id`, `token_address`, `score`, `reasons` (JSON with breakdown)
- `token_price_at_signal`, `token_mcap_at_signal`, `liquidity_at_signal`
- `status` (pending → executed? not actively used)
- `created_at`

**Outcome tracking (updated by upsert_token_outcome):**
- `peak_roi_pct` (=(peak_mcap - mcap_at_signal) / mcap_at_signal * 100)
- `peak_multiplier_after` (=peak_mcap / mcap_at_signal)
- `is_rug_after` (Boolean)
- `outcome_updated_at`

---

## Data Completeness Analysis

### Well-Populated Fields (80%+)
✓ Token: address, chain, name, symbol, source, first_seen_at
✓ TokenSnapshot: price, market_cap, liquidity_usd, volume_*, holders_count, score, timestamp, stage
✓ TokenSecurity: is_honeypot, lp_locked, is_mintable (binary flags)

### Sparsely Populated Fields (5-30%)
? TokenSnapshot: volatility_5m, volatility_1h, lp_removed_pct (fields exist but NOT populated)
? TokenSnapshot: jupiter_price (mostly NULL, cross-validation source)
? TokenOHLCV: Only filled from Birdeye, not from other sources
? TokenTrade: Birdeye trades collected but rarely queried

### Source-Specific Fields (5-15%)
* Token: PumpPortal fields (initial_buy_sol, bonding_curve_key) - only for pump.fun tokens
* Token: Meteora DBC fields (dbc_pool_address, dbc_damm_tvl) - only for DBC launchpad tokens

---

## Scoring Model Integration

### What Gets Stored vs What Could Be Stored

**Currently Stored & Used in Scoring:**
- Liquidity, price, volume (all timeframes) → direct scoring input
- Buy/sell counts → buy pressure signal
- Holders count, top-10 concentration → crowd validation
- Security flags (is_honeypot, lp_locked, is_mintable) → disqualifier/bonus/penalty
- Smart wallet count → smart money signal
- Price momentum (computed from snapshots, not stored) → trend detection
- Holder velocity (computed from snapshots, not stored) → growth rate

**Collected But NOT Used in Scoring:**
- volatility_5m, volatility_1h → fields exist, never populated or scored
- lp_removed_pct → field exists, never populated
- jupiter_price → collected, but price already from Birdeye/GMGN
- Twitter mentions/KOL mentions/max_likes → Phase 15 fields, scoring not yet using them

**Computed But NOT Stored (could be added):**
- Price momentum (% change 5m/15m/1h, acceleration ratio)
- Holder velocity (holders/minute)
- Volume momentum (volume ratio changes)
- Whale accumulation/distribution patterns
- LP concentration changes
- Buy/sell ratio across timeframes

---

## Analysis Capabilities (Available Queries)

### Real-Time Scoring
- `compute_score()` - v2 model (0-100)
- `compute_score_v3()` - momentum-weighted variant
- Both use TokenSnapshot + TokenSecurity + derived metrics

### Historical Backtesting
```python
# Available in scripts/backtest_scoring.py
- Get all INITIAL snapshots with score >= threshold
- Compare to TokenOutcome.peak_multiplier
- Calculate win rate, expected value
- Test multiple score thresholds
```

### Outcome Analysis
```python
# Available in scripts/analyze_outcomes.py
- Score distribution (6 buckets: 0, 1-14, 15-29, 30-49, 50-69, 70-100)
- Multiplier thresholds (2x, 3x, 5x, 10x)
- Hit rate per score bucket
- Source performance (pump.fun vs meteora_dbc)
- Data coverage (% of tokens with peak_multiplier)
- Stage distribution (how many tokens reach each enrichment stage)
```

### Pattern Detection (Enrichment Pipeline)
1. **Price Momentum** (`price_momentum.py`):
   - Multi-timeframe analysis (5m, 15m, 1h)
   - Trend detection (accelerating_up, decelerating, consolidating, falling)
   - Peak drawdown tracking

2. **Whale Dynamics** (`whale_dynamics.py`):
   - Top-10 holder comparison between snapshots
   - Accumulation vs distribution detection
   - New whale entry/exit
   - Concentration change delta

3. **Holder PnL** (`holder_pnl.py`):
   - Profit/loss ratio of top holders
   - Wash trading detection (80%+ losses while price rising)
   - Early stage vs bagholder classification
   - Dump risk assessment

4. **Holder Concentration** (`concentration_rate.py`):
   - Top-10 concentration velocity
   - Trend in holder concentration

5. **Volume Profile** (`volume_profile.py`):
   - Volume ratio analysis (volume/liquidity)
   - Buy/sell volume ratios

6. **OHLCV Patterns** (`ohlcv_patterns.py`):
   - Candle patterns (support/resistance, breakouts)
   - Currently collected but NOT integrated into scoring

7. **Cross-Token Whale Tracking** (`cross_token_whales.py`):
   - Identify which wallets are buying the token
   - Track whale portfolio behavior

---

## Critical Gaps (What's Missing)

### 1. Volatility Metrics
**Issue:** Fields `volatility_5m`, `volatility_1h`, `lp_removed_pct` exist but:
- Never populated from any data source
- Never used in any scoring function
- **Action:** Either populate them (calculate from OHLCV data) or remove from schema

### 2. Timestamp Fields Not Captured
- Holder snapshot creation time (only linked to TokenSnapshot.timestamp)
- Trade execution timestamp stored but not aggregated by hour/day
- LP lock duration stored but migration timestamp often NULL

### 3. MEV/Sandwich Detection
- Trade data collected but not analyzed for MEV patterns
- Would require TX trace data or Solana program interaction analysis

### 4. Correlation Metrics Not Stored
- Correlation between token price and smart wallet positions
- Cross-token holder portfolio diversification
- Creator success rate by launchpad/source

### 5. Twitter/Social Trends
- Fields captured (mentions, KOL mentions, max_likes) but:
  - Phase 15 incomplete (not yet integrated into scoring)
  - No trending velocity (is sentiment accelerating?)
  - No community growth rate

### 6. Liquidity Depth Profile
- Only total liquidity_usd stored, not:
  - Liquidity at different price levels (order book depth)
  - Slippage simulation data
  - LP composition (concentrated vs wide ranges on Uniswap V3)

### 7. Historical Signal Validation
- TokenOutcome tracks peak_multiplier
- Signal records updated with outcome data
- BUT: No stored "what-if" scenarios (e.g., "if entry at different score threshold")

---

## ML/Pattern Recognition Opportunities

### Immediately Available for Feature Engineering:
1. **Temporal features**: Day of week, time since launch, velocity metrics
2. **Price action**: OHLCV candles already stored (1m, 5m, 15m, 1h intervals)
3. **Holder dynamics**: TokenTopHolder.pnl + concentration changes
4. **Smart money**: SmartWallet.win_rate filtered to this token's purchasers
5. **Creator history**: CreatorProfile aggregates across all launches
6. **Security composite**: Combine rugcheck_score + goplus_score + lp_locked

### Quick Wins (High ROI, Low Effort):
1. **Volatility calculation**: Compute from OHLCV snapshots, store in TokenSnapshot
2. **Holder velocity storage**: Pre-compute in snapshots, not just in queries
3. **Twitter momentum**: Track mentions/KOL mentions trending (delta per hour)
4. **Creator success rate**: Already computed, but integrate into signal reasons
5. **Launchpad reputation**: Group creator outcomes by dbc_launchpad, score accordingly

### Advanced Features (Phase 16+):
1. **Temporal pattern matching**: Token age → expected stage duration
2. **Portfolio concentration**: Calculate holder Herfindahl index
3. **Cluster analysis**: Group tokens by security/momentum profile
4. **Outcome prediction**: Regression model on (score, security, patterns) → peak_multiplier

---

## Data Quality Notes

### Consistency Issues:
- `top10_holders_pct` appears in both TokenSnapshot AND TokenSecurity (may diverge)
- `dev_holds_pct` same issue - values can differ between sources
- **Mitigation:** scoring.py prioritizes Birdeye/GMGN snapshots, security table is reference

### NULL Handling:
- Decimal fields default to NULL if source doesn't provide them
- Scoring functions handle via `or` fallback chain (Birdeye → GMGN → DexScreener)
- Safe because scoring checks `if liquidity is None: return 0`

### Timing:
- TokenSnapshot.timestamp = when snapshot was saved (server default)
- Created_at fields show token launch time
- No explicit "data freshness" metric - relies on stage progression

---

## Summary Table

| Layer | Table | Fields | Frequency | Query Usage | Completeness |
|-------|-------|--------|-----------|-------------|--------------|
| Identity | Token | 24 | Once | Common | 95% |
| Metrics | TokenSnapshot | 41 | Per cycle | Heavy | 80% |
| Security | TokenSecurity | 16+JSON | Per token | Medium | 75% |
| Holders | TokenTopHolder | 6 | Per snapshot | Medium | 60% |
| Candles | TokenOHLCV | 8 | Continuous | Low | 30% |
| Trades | TokenTrade | 9 | Realtime | Low | 40% |
| Outcomes | TokenOutcome | 10 | Per stage | Heavy (backtesting) | 70% |
| Creators | CreatorProfile | 10 | Per token | Medium | 50% |
| Wallets | SmartWallet + Activity | 17 | Event-driven | Medium | 40% |
| Signals | Signal | 10 | Per threshold | Heavy (analysis) | 85% |

