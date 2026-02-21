# Phase 21: Deep Audit P0-P2 Fixes (2026-02-20)

## Summary
Deep audit of the entire codebase by 6 parallel audit agents identified 12 bugs across P0-P2 priorities. All fixed and tested (499 tests passing).

## P0 Fixes (Critical — Incorrect Behavior)

### P0-1: R24 sell_sim_failed — false honeypot rejection
**File:** `src/parsers/signals.py:329`
**Bug:** R24 fired when Jupiter API returned errors (timeout, 429, 5xx), incorrectly penalizing tokens as unsellable.
**Fix:** Added `and not sell_sim_result.api_error` check. Now only fires on actual sell simulation failures.

### P0-2: R18 volume_dried_up — inverted logic
**File:** `src/parsers/signals.py:254`
**Bug:** Condition `vol_1h / vol_5m < 3` fired on HIGH recent volume (bullish) instead of dried-up volume.
**Fix:** Changed to `vol_1h / vol_5m > 12` — fires when 5m volume is <8% of 1h, meaning recent activity dried up.

### P0-3: Jito bundle detection calling non-existent API
**File:** `src/parsers/jito_bundle.py`
**Bug:** Called `helius.get_parsed_transaction(sig)` (singular) which doesn't exist. Used raw dict parsing.
**Fix:** Complete rewrite using `helius.get_parsed_transactions(sigs)` batch API + `HeliusTransaction` models. Removed dead helper functions. R37 jito_bundle_snipe (-6 pts) now actually fires.

### P0-4: Metaplex checker calling non-existent API
**File:** `src/parsers/metaplex_checker.py`
**Bug:** Called `HeliusClient.get_asset()` which didn't exist.
**Fix:** Added `get_asset()` method to `HeliusClient` using Helius DAS `getAsset` JSON-RPC API on RPC endpoint, with retry/backoff and rate limiting.

## P1 Fixes (High — Data Quality)

### P1-1: Scoring v3 volume acceleration bias
**File:** `src/parsers/scoring_v3.py:132`
**Bug:** Young tokens where `vol_1h ≈ vol_5m` got free +5 pts because acceleration ratio was meaningless.
**Fix:** Added guard `vol_1h > vol_5m * 3` to skip young tokens where the ratio is unreliable.

### P1-2: Signal decay using wrong timestamp
**Files:** `src/models/signal.py`, `src/parsers/signal_decay.py`
**Bug:** Decay used `created_at` — re-confirmed signals still decayed based on original creation time.
**Fix:** Added `updated_at` column (auto-set on update), decay now uses `updated_at`. Re-confirmed signals reset their TTL timer. Alembic migration `h8i9j0k1l2m3`.

### P1-3: Paid API clients missing retry logic
**Files:** `src/parsers/vybe/client.py`, `src/parsers/twitter/client.py`
**Bug:** Vybe and Twitter clients had ZERO retry on transient errors — paid services with immediate failure.
**Fix:** Added 2 retries with 1s/3s delays on timeout, connect error, 429, and 5xx responses (same pattern as Birdeye).

### P1-4: gRPC channel leak on reconnect
**File:** `src/parsers/chainstack/grpc_client.py`
**Bug:** On reconnect, old gRPC channel was never closed, leaking file descriptors.
**Fix:** Added `channel.close()` in `finally` block of the reconnect loop.

## P2 Fixes (Medium — Observability & Safety)

### P2-1: Bare `except: pass` in worker.py
**File:** `src/parsers/worker.py`
**Bug:** 4 bare `except: pass` blocks silently swallowed all errors including critical ones.
**Fix:** Changed to `except Exception as e: logger.debug(...)` with descriptive messages.

### P2-2: No timeout on asyncio.gather() calls
**File:** `src/parsers/worker.py`
**Bug:** 3 `asyncio.gather()` calls (13+12+6 API calls) could hang indefinitely.
**Fix:** Wrapped with `asyncio.wait_for()`: 45s for INITIAL batches, 30s for non-INITIAL.

### P2-3: Paper trader race condition — duplicate open positions
**Files:** `src/models/trade.py`, `src/parsers/paper_trader.py`
**Bug:** Concurrent enrichment workers could create duplicate open positions for the same token.
**Fix:** Added partial unique index `uq_positions_open_paper` on `(token_id, is_paper) WHERE status='open'` + `IntegrityError` handling. Alembic migration `i9j0k1l2m3n4`.

### P2-4: Silent alert failures
**File:** `src/parsers/paper_trader.py`
**Bug:** Alert failures logged at `debug` level — invisible in production.
**Fix:** Changed to `logger.warning` for both open and close trade alert failures.

## Migrations
1. `h8i9j0k1l2m3_signal_updated_at.py` — adds `updated_at` column + index to signals table
2. `i9j0k1l2m3n4_position_unique_open.py` — adds partial unique index on positions table

## Test Results
- 499 tests passing, 0 failed
- Jito bundle tests rewritten for new Helius batch API
- R18 test fixed for correct (non-inverted) logic
- Signal decay tests updated for `updated_at` semantics
