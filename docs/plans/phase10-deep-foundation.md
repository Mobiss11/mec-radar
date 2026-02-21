# Phase 10: Deep Foundation — Strengthening the Core

## Context

180 tests passing, 11-stage enrichment pipeline, dual scoring (v2/v3), paper trading, signal decay, wallet clustering. System works but has gaps identified in deep audit.

**Goal**: Maximum data quality, early rug detection, better pattern recognition. Social metrics — later.

**Status**: COMPLETED — 204 tests passing (was 180)

---

## Checklist (ordered by ROI)

### 1. Early Outcome Tracking (HOUR_4 + HOUR_8)
- [x] Add intermediate outcome checks at HOUR_4 and HOUR_8 (not just HOUR_24)
- [x] Update `upsert_token_outcome()` to run at HOUR_4, HOUR_8, HOUR_24
- [x] Track `is_rug` earlier — if token dropped 90%+ by HOUR_4, mark immediately
- [x] Add `outcome_stage` field to TokenOutcome (which stage detected outcome)

### 2. LP Exit Detection
- [x] New file: `src/parsers/lp_monitor.py`
- [x] Compare 2 most recent snapshots for liquidity drop
- [x] Distinguish LP removal from organic selling (price vs liquidity correlation)
- [x] Score impacts: -8 (20-30%), -15 (30-50%), -25 (50%+)
- [x] Cumulative LP removal from peak via `get_lp_removal_pct()`
- [x] `lp_removed_pct` field in TokenSnapshot
- [x] Integrated into worker.py at all non-INITIAL stages
- [x] Tests: 7 new tests (test_lp_monitor.py)

### 3. Extended OHLCV + Volatility
- [x] `compute_volatility()` — std dev of returns as percentage
- [x] `get_volatility_metrics()` — 5m and 1H volatility from stored candles
- [x] Store `volatility_5m`, `volatility_1h` in TokenSnapshot
- [x] Extended OHLCV collection at MIN_5 (was not fetched)
- [x] Integrated into worker.py at all non-INITIAL stages
- [x] Tests: 5 new tests (volatility in test_ohlcv_patterns.py)

### 4. Cross-Token Whale Correlation
- [x] New file: `src/parsers/cross_token_whales.py`
- [x] Compare top holders across tokens discovered in last 2 hours
- [x] If ≥ 3 shared addresses across ≥ 2 tokens → coordinated pump flag
- [x] Score impact: -10 to -15 based on wallet count
- [x] Runs at MIN_15, HOUR_1, HOUR_2
- [x] Tests: 3 new tests (test_cross_token_whales.py)

### 5. Default Creator Risk for Unknown Wallets
- [x] Change default risk from 0 → 25 for first-time creators (`DEFAULT_FIRST_LAUNCH_RISK = 25`)
- [x] `is_first_launch` flag in CreatorProfile
- [x] Configurable via `settings.default_creator_risk`

### 6. MIN_2 Enhancement (Quick Price Check)
- [x] Enable GMGN fetch at MIN_2 stage (`fetch_gmgn_info=True`)
- [x] DexScreener cross-validation continues at MIN_2

### 7. Creator Funding Trace (1 hop back)
- [x] New file: `src/parsers/creator_trace.py`
- [x] `assess_creator_risk()` — returns (risk_score, is_first_launch) from DB profile
- [x] `update_creator_funding()` — updates profile with funding trace data
- [x] `check_funding_source_risk()` — checks if funder is known rugger (0-80 risk)
- [x] Integrated into worker.py at INITIAL stage
- [x] Tests: 7 new tests (test_creator_trace.py)

### 8. Scoring Hardening
- [x] No more `None` returns — always `int` (0 when no liquidity)
- [x] `lp_removed_pct` parameter added to both `compute_score()` and `compute_score_v3()`
- [x] LP removal penalty: -8/-15/-25 pts based on removal %
- [x] Data completeness cap: < 3/6 key metrics available → cap score at 40
- [x] Tests: 2 new tests (scoring LP penalty + data cap)

### 9. Enrichment Timing Optimization
- [x] INITIAL delay: 30s → 45s (better data quality)
- [x] MIN_2: GMGN enabled (quick price check)
- [x] MIN_5: OHLCV collection added, pruning raised 15 → 20
- [x] MIN_15: pruning raised 20 → 25
- [x] Config params for LP monitor, cross-whale, creator risk

---

## Schema Changes (Alembic migration `b2c3d4e5f6a7`)

1. `token_outcomes`: + `outcome_stage VARCHAR(20)`
2. `token_snapshots`: + `volatility_5m NUMERIC`, + `volatility_1h NUMERIC`, + `lp_removed_pct NUMERIC`
3. `creator_profiles`: + `is_first_launch BOOLEAN`, + `funded_by VARCHAR(64)`, + `funding_risk_score INTEGER`

## New Files
- `src/parsers/lp_monitor.py` — LP exit detection
- `src/parsers/cross_token_whales.py` — Cross-token whale correlation
- `src/parsers/creator_trace.py` — Creator funding trace + risk assessment
- `tests/test_parsers/test_lp_monitor.py` — 7 tests
- `tests/test_parsers/test_cross_token_whales.py` — 3 tests
- `tests/test_parsers/test_creator_trace.py` — 7 tests

## Modified Files
- `src/parsers/worker.py` — all Phase 10 integrations (LP check, cross-whale, creator trace, volatility, lp_removed_pct in scoring)
- `src/parsers/scoring.py` — LP penalty, data completeness cap, return int not None
- `src/parsers/scoring_v3.py` — same changes as v2
- `src/parsers/ohlcv_patterns.py` — `compute_volatility()`, `get_volatility_metrics()`
- `src/parsers/enrichment_types.py` — timing and threshold changes
- `src/models/token.py` — new snapshot/outcome/creator columns
- `config/settings.py` — LP monitor, cross-whale, creator risk settings
- `tests/test_parsers/test_scoring.py` — updated for int returns + new LP/cap tests
- `tests/test_parsers/test_scoring_v3.py` — updated for int returns
- `tests/test_parsers/test_enrichment_types.py` — updated for new thresholds
- `tests/test_parsers/test_ohlcv_patterns.py` — 5 new volatility tests

## Verification
- `poetry run pytest tests/ -x -v` — **204 tests passing** (was 180)
- Alembic migration `b2c3d4e5f6a7` applied successfully
