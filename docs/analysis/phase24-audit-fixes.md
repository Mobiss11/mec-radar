# Phase 24: Deep Audit P0-P1 Fixes (2026-02-20)

## Context

Full deep audit with 4 parallel agents covering:
- Worker core loop (gRPC, WS, Meteora, enrichment workers)
- All 14 API clients (retry logic, timeouts, error handling)
- Scoring v2/v3, 54 signal rules, paper trader, signal decay
- Persistence, models, queue, rate limiter, settings

System rated **8/10** overall. Ready for paper trading, 7 P0-P1 fixes identified.

## P0 Fixes (Critical)

### 1. Signal Dedup Race Condition
**Problem:** 3 parallel enrichment workers used SELECT→INSERT pattern without FOR UPDATE.
Two workers processing the same token could both see "no existing signal" and INSERT duplicates.

**Fix:**
- Added partial unique index `uq_signals_token_status_active` on `(token_id, status)` WHERE `status IN ('strong_buy', 'buy', 'watch')`
- Replaced SELECT→INSERT with atomic PostgreSQL `INSERT ... ON CONFLICT DO UPDATE` (pg_insert upsert)
- Upsert updates score, reasons, price, mcap, liquidity, updated_at on conflict

**Files:** `src/models/signal.py`, `src/parsers/worker.py`
**Migration:** `l2m3n4o5p6q7_signal_dedup_indexes.py`

### 2. Alert Dedup Blocks Status Upgrades
**Problem:** Alert dedup keyed by `token_address` only. A `watch` alert blocked subsequent `buy` alert for the same token within 300s cooldown window.

**Fix:** Changed dedup key from `address` to `(address, action)` tuple. Each action type has independent cooldown.

**File:** `src/parsers/alerts.py`

### 3. Entry Slippage Missing in Paper Trader
**Problem:** Exit slippage was calculated in `_close_position()`, but entry had no slippage estimation. Paper P&L was optimistic — real trades would get worse entry prices on low-liquidity tokens.

**Fix:**
- Added `liquidity_usd` and `sol_price_usd` parameters to `PaperTrader.on_signal()`
- If invest_usd > 2% of liquidity, effective entry price increases proportionally (capped at 50%)
- Worker passes `snapshot.liquidity_usd` and `get_sol_price_safe()` to on_signal
- Trade and Position record effective_price (with slippage), not raw market price

**Files:** `src/parsers/paper_trader.py`, `src/parsers/worker.py`

## P1 Fixes (Important)

### 4. Missing Database Indexes
**Problem:** Common query patterns lacked composite indexes, causing sequential scans.

**Fix (single migration):**
- `idx_snapshots_token_stage` on `token_snapshots(token_id, stage)` — enrichment pipeline lookups
- `idx_positions_token_status` on `positions(token_id, status)` — paper trader position queries

**Files:** `src/models/token.py`, `src/models/trade.py`
**Migration:** `l2m3n4o5p6q7_signal_dedup_indexes.py`

### 5. SharedRateLimiter Documentation
**Problem:** `get_or_create()` classmethod appeared race-prone but is actually safe in asyncio (no await between check and set = no preemption point).

**Fix:** Added documentation clarifying thread-safety guarantees and requirement to call before spawning workers. Minor code cleanup.

**File:** `src/parsers/rate_limiter.py`

### 6. GMGN asyncio.to_thread() Timeout
**Problem:** `tls_client.Session.get/post` via `asyncio.to_thread()` had no timeout. A hanging SOCKS5 proxy connection could block a worker indefinitely.

**Fix:** Wrapped `asyncio.to_thread()` in `asyncio.wait_for(timeout=30.0)`. TimeoutError triggers retry with backoff, same as other failures.

**File:** `src/parsers/gmgn/client.py`

### 7. Worker Loop Top-Level Exception Handler
**Problem:** If `enrichment_queue.get()` or `enrichment_queue.put()` threw an exception (e.g. Redis connection lost), the enrichment worker would die silently with no recovery.

**Fix:** Wrapped entire `while True` loop body in outer `try/except`. On unexpected error: log, sleep 5s, continue. Inner try/except for _enrich_token preserved for per-task error handling.

**File:** `src/parsers/worker.py`

## False Positives Identified

These were flagged by audit agents but verified as correct:
- **Scoring v3 volume acceleration guard** — `vol_1h > vol_5m * 3` correctly filters young tokens, `accel = vol_5m * 12 / vol_1h >= 2.0` correctly identifies acceleration
- **Dev holds penalty v2 vs v3 difference** — by design (v3 is intentionally harsher for A/B testing)
- **LLM temperature 0.1** — correct for structured JSON risk score output
- **Birdeye 5xx retry "fallthrough"** — `raise_for_status()` handles it

## Test Results

499 tests passed, 0 failed (24.60s)
