# Phase 23: Deep Audit Fixes (2026-02-20)

Final production-readiness pass. All P0, P1, P2 issues from deep audit resolved.
499 tests passing, 0 failed.

## P0 — Critical Fixes

### P0-1: Global Birdeye Rate Limiter
**File**: `src/parsers/rate_limiter.py`, `src/parsers/worker.py`

- Added `SharedRateLimiter` class with singleton pattern (`get_or_create`)
- 3 parallel enrichment workers now share ONE 10 RPS limiter via `SharedRateLimiter.get_or_create("birdeye", max_rps)`
- Prevents 30 RPS burst (3 workers x 10 RPS each) that would trigger Birdeye 429s
- Uses `asyncio.Lock()` for serialized access across all workers

### P0-2: Signal Deduplication
**File**: `src/parsers/worker.py` (~line 2821)

- Replaced blind `session.add(signal_record)` with check-then-update-or-insert
- Queries existing Signal with same `(token_id, status)` — updates `updated_at=func.now()` if found
- Prevents: duplicate DB rows, duplicate Telegram alerts, duplicate paper positions
- Uses explicit `updated_at=func.now()` since ORM `onupdate` doesn't trigger on Core UPDATE

### P0-3: Prescan Fields Redis Serialization
**File**: `src/parsers/enrichment_queue.py`

- Added `instant_rejected`, `prescan_risk_boost`, `prescan_mint_info`, `prescan_sell_sim` to `_task_to_dict()` and `_dict_to_task()`
- Prescan objects serialized via `__dict__` with fallback
- Added queue deduplication: `put()` checks `zscore` before `zadd`, with `allow_update` parameter for stage progression

## P1 — Important Fixes

### P1-1: Vybe API Optimization
**File**: `src/parsers/worker.py` (`_fetch_vybe_pnl`)

- Reduced `max_holders` from 10 to 5 (halves credits per call)
- Added prescan gate: skip Vybe for `prescan_risk_boost >= 10`
- Estimated savings: 4.75M → ~1.3M credits/month (within 500K Dev plan)

### P1-2: GMGN Circuit Breaker Gradual Recovery
**File**: `src/parsers/gmgn/client.py`

- Changed success handler from `_circuit_trip_count = 0` to `_circuit_trip_count -= 1`
- Prevents oscillation: after long cooldown (trip=4, 240s), next trip uses trip=3 (120s)
- Old behavior: one success reset everything → 5 failures immediately re-trip at 60s

### P1-3: Twitter API Skip
**File**: `src/parsers/worker.py` (`_fetch_twitter`)

- Early return if `len(symbol) < 2 and len(name) < 3`
- Prevents wasting $10/1M credits on unsearchable tokens

### P1-4: SolSniffer Atomic Cap
**File**: `src/parsers/solsniffer/client.py`

- Replaced GET+check+INCR with atomic `_try_reserve_call()` using Redis INCR
- If over cap: `DECR` to undo reservation
- Prevents TOCTOU race condition across 3 workers on 5000/mo cap

### P1-5: HTML Escaping + Telegram Rate Limiting
**File**: `src/parsers/alerts.py`

- `html.escape()` on all user-controlled fields (token symbol/name) in Telegram messages
- Semaphore-based rate limiting: 25 msg/sec, 40ms min interval
- FloodWait 429 handling with Retry-After

### P1-6: gRPC Callback Timeouts
**File**: `src/parsers/chainstack/grpc_client.py`

- All 4 callbacks wrapped with `asyncio.wait_for(timeout=5.0)`
- Channel close has 5s timeout
- Added `_reconnect_count` and `_decode_errors` counters

### P1-7: Signal Index + Scoring Alignment
**Files**: `src/models/signal.py`, `src/parsers/scoring_v3.py`

- Added `Index("idx_signals_address", "token_address")` for fast lookups
- Alembic migration: `k1l2m3n4o5p6_signal_address_index.py`
- Scoring v3 data completeness gate aligned with v2: `< 3` → cap 40

## P2 — Improvements

### PRE_SCAN Timeout
**File**: `src/parsers/worker.py` (~line 969)

- Wrapped PRE_SCAN `asyncio.gather()` with `asyncio.wait_for(timeout=10.0)`
- Prevents slow RPC/Jupiter from blocking the enrichment queue

### Jupiter Price Skip (Non-INITIAL)
**File**: `src/parsers/worker.py` (~line 2174)

- Skip Jupiter price call if `birdeye_overview.price` is available
- Saves 1 Jupiter API call per non-INITIAL enrichment cycle

### Paper Trader Trailing Stop Gap Handling
**File**: `src/parsers/paper_trader.py`

- If trailing stop fires but actual PnL is worse than stop_loss threshold, report as `stop_loss`
- Prevents misleading close reasons when price gaps through both levels

### SOL Price Staleness Check
**File**: `src/parsers/sol_price.py`

- Added `get_sol_price_safe(max_stale_seconds=600)` → returns `None` if too stale
- Paper trader can skip PnL updates when price feed is unreliable

### Scoring V3 Volume Acceleration Guard
**File**: `src/parsers/scoring_v3.py`

- Changed `vol_1h > 0` to `vol_1h > 100` (minimum $100 threshold)
- Prevents extreme acceleration ratios from corrupted/minimal data

### gRPC Observability
**File**: `src/parsers/chainstack/grpc_client.py`

- Added periodic stats logging every 1000 messages (msgs, tokens, trades, decode errors)

## Migration Required

```bash
poetry run alembic upgrade head
```

New migration: `k1l2m3n4o5p6_signal_address_index.py` — adds index on `signals.token_address`
