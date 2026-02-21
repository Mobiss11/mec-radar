# Phase 22: Deep Audit P0-P2 Fixes (2026-02-20)

## Summary
Follow-up deep audit focused on signal decay correctness, SolSniffer quota conservation, Telegram bot security, and data integrity. All fixed and tested (499 tests passing).

## P0 Fixes (Critical)

### P0-1: Signal decay `updated_at` not updating on Core-level UPDATE
**File:** `src/parsers/signal_decay.py`
**Bug:** SQLAlchemy Core-level `update(Signal).where(...).values(status="buy")` does NOT trigger ORM `onupdate=func.now()` on the `updated_at` column. This caused signal decay to cascade too fast: full cycle was T+12h instead of T+22h (4h+6h+12h), because `updated_at` was never reset between decay steps.
**Fix:** Added explicit `updated_at=func.now()` to all three `.values()` calls (strong_buy->buy, buy->watch, watch->expired).

## P1 Fixes (High)

### P1-1: Telegram bot — no authentication
**File:** `src/bot/handlers.py`
**Bug:** Any Telegram user could call all bot commands (/signals, /portfolio, /token, /stats) — no access control.
**Fix:** Added `AdminFilter(BaseFilter)` applied at router level. Checks `message.from_user.id == settings.telegram_admin_id`. When `telegram_admin_id=0` (dev mode), all users allowed.

### P1-2: SOL price staleness — silent failure
**File:** `src/parsers/sol_price.py`
**Bug:** When both Birdeye and Jupiter fail for extended periods, the system silently used stale cached price (default $150) with only `logger.debug` — invisible in production.
**Fix:** Added staleness warning: if price hasn't updated for >5 minutes, logs `logger.warning` with stale duration instead of `logger.debug`.

### P1-3: FK CASCADE missing on snapshot references
**Files:** `src/models/token.py`, `alembic/versions/j0k1l2m3n4o5_fk_cascade_snapshots.py`
**Bug:** `cleanup_old_data()` deletes old snapshots (7d retention), but `TokenTopHolder.snapshot_id` and `TokenOutcome.peak_snapshot_id` FK had no CASCADE rules. This would cause FK constraint violations or orphaned references.
**Fix:**
- `TokenTopHolder.snapshot_id` → `ON DELETE CASCADE` (delete holders with snapshot)
- `TokenOutcome.peak_snapshot_id` → `ON DELETE SET NULL` (keep outcome, clear reference)

### P1-4: SolSniffer called for EVERY token — quota wasted
**File:** `src/parsers/worker.py`
**Bug:** SolSniffer (5000 calls/month cap) was called in Batch 1 for every token regardless of security status. At 300+ tokens/day, monthly cap would be exhausted in ~16 days.
**Fix:** Moved SolSniffer from Batch 1 parallel gather to a conditional call after Batch 1 results are processed. Now skips:
- Tokens where GoPlus confirmed honeypot
- Tokens where Jupiter is banned
- Tokens with high PRE_SCAN risk (prescan_risk_boost >= 15)
- Tokens already clearly safe (Raydium LP burned + RugCheck score >= 80)
Expected savings: ~50-70% of API calls, extending cap to full month.

## P2 Fixes (Medium)

### P2-1: HTML injection via token names in Telegram messages
**File:** `src/bot/formatters.py`
**Bug:** `token.symbol` and `token.name` (user-controlled blockchain data) were inserted directly into HTML messages without escaping. Malicious token names with `<script>` or `<b>` tags could break formatting.
**Fix:** Added `html.escape()` to all `token.symbol` and `token.name` insertions in `format_signals()`, `format_portfolio()`, and `format_token_detail()`.

## Migrations
1. `j0k1l2m3n4o5_fk_cascade_snapshots.py` — FK CASCADE/SET NULL on snapshot references

## False Alarms (Verified Correct)
- `cleanup_old_data()` commit — caller in worker.py already does `await session.commit()`
- Paper trader timeout 4h vs 8h — `settings.paper_timeout_hours = 8`, worker passes it correctly

## Token Sources & Coverage
Both PumpFun and Meteora DBC tokens use the same 11-stage enrichment pipeline:
- **PumpFun:** gRPC (primary, <1s) + PumpPortal WS (fallback, ~2-3s) → PRE_SCAN at +5s
- **Meteora DBC:** Helius logsSubscribe (~1-2s) → PRE_SCAN at +30s (bonding curve slower)
- **Migrations:** Detected instantly, processed with priority=MIGRATION (queue bypass)
  - PumpFun → Raydium: gRPC detects, re-enrichment at INITIAL stage
  - Meteora → DAMM v2: logsSubscribe detects MigrateToDammV2, fetches post-graduation metrics

## Test Results
- 499 tests passing, 0 failed
