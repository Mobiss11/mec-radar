# Phase 17: Deep Audit Fixes
**Date**: 2026-02-20
**Status**: COMPLETED
**Tests**: 498 passed, 0 failed

---

## Context

Deep system audit (5 parallel agents) identified 3 categories of issues.
Many were already fixed in the previous session; remaining ones fixed here.

---

## Phase 1: Critical Fixes

### 1.1 Import error in worker.py sweep_loop/report_loop
**Status**: Already fixed (previous session)
**Details**: `_paper_sweep_loop` and `_paper_report_loop` already use `from src.db.database import async_session_factory`.

### 1.2 PnL USD calculation in paper_trader.py
**Status**: Already fixed (previous session)
**Details**: Line 172 already multiplies by `_sol_usd`:
```python
pos.pnl_usd = pos.amount_sol_invested * pnl_pct / 100 * _sol_usd
```
SOL price comes from Jupiter (60s cache) with $150 fallback.

### 1.3 Slippage not connected in _paper_price_loop
**Status**: FIXED in this session
**What was wrong**: `_paper_price_loop` called `update_positions()` without `liquidity_usd`, making slippage estimation dead code in the price update path. Only the enrichment path passed liquidity.
**Fix**: Added DB query to fetch latest `TokenSnapshot.liquidity_usd` (or `dex_liquidity_usd` fallback) for each open position, then pass to `update_positions()`.
**File**: `src/parsers/worker.py` lines ~2989-3010

### 1.4 Chainstack gRPC streaming disabled
**Status**: Already enabled
**Details**: `enable_grpc_streaming: bool = True` in settings.py (line 107).

---

## Phase 2: Timing Improvement

### 2.1 INITIAL offset too slow (45s → 25s)
**Status**: FIXED
**What was wrong**: INITIAL enrichment waited 45s after discovery. For pump.fun memecoins, many already at 2-5x by T+45s.
**Fix**: Changed `offset_sec=45` → `offset_sec=25` in `enrichment_types.py` line 62.
**Impact**: Signal generation now at T+25s instead of T+45-65s. Data quality is acceptable at 25s — Birdeye API returns data by then.

### 2.2 No fast notification for clean tokens
**Status**: FIXED — added early_watch alert
**What was wrong**: User had to wait until INITIAL (+25s minimum) to know about any new token.
**Fix**: After PRE_SCAN passes with `risk_boost == 0` (clean token), dispatch `early_watch` alert via AlertDispatcher at T+5s. This is not a full signal (no score/price/mcap), but a fast heads-up.
**Files**: `src/parsers/worker.py` (after prescan_result), `src/parsers/alerts.py` (emoji map)

### 2.3 Scoring rules R1, R5, R8, R43
**Status**: Already correct (previous session)
- R1 (high_score): threshold at 60 (not 50 as audit said)
- R5 (strong_liquidity): weight +2 (not +1)
- R8 (security_cleared): weight +3 (not +1)
- R43 (explosive_buy_velocity): threshold at 50 buys/5m (not 100)

### 2.4 Data completeness cap
**Status**: Already correct
**Details**: `< 3` of 6 core metrics → score capped at 40. This is the correct threshold.

---

## Phase 3: Calibration

### 3.1 Take profit too high (5x → 3x)
**Status**: FIXED
**What was wrong**: `paper_take_profit_x: float = 5.0` in settings.py. Memecoins rarely sustain 5x; most paper positions timed out instead of hitting take profit.
**Fix**: Changed to `3.0`. Constructor default in `paper_trader.py` was already 3.0; settings override was wrong.
**File**: `config/settings.py` line 91

### 3.2 Position timeout too short (4h → 8h)
**Status**: FIXED
**What was wrong**: 4h timeout closed positions before they had time to develop. Some memecoins pump on 6-8h cycles.
**Fix**: Changed `paper_timeout_hours: int = 8` in settings.py line 93.

### 3.3 Bearish signal weights too weak (R34, R37)
**Status**: FIXED
- **R34 fee_payer_sybil**: `-4` → `-6` (sybil attacks are strong rug indicators)
- **R37 jito_bundle_snipe**: `-4` → `-6` (MEV bundle snipes in first block = coordinated attack)
**File**: `src/parsers/signals.py` lines 425, 454
**Test fix**: Updated `tests/test_parsers/test_signals_phase13.py` line 135 to expect -6.

---

## Summary of Changes

| File | Change |
|------|--------|
| `src/parsers/enrichment_types.py:62` | INITIAL offset 45s → 25s |
| `src/parsers/worker.py:~862` | early_watch alert after clean PRE_SCAN |
| `src/parsers/worker.py:~2989` | liquidity_usd pass-through in _paper_price_loop |
| `src/parsers/alerts.py:265` | early_watch emoji in format map |
| `src/parsers/signals.py:425` | R34 fee_payer_sybil weight -4 → -6 |
| `src/parsers/signals.py:454` | R37 jito_bundle_snipe weight -4 → -6 |
| `src/parsers/paper_trader.py:194,206` | Comments updated (3x/8h) |
| `config/settings.py:91` | paper_take_profit_x 5.0 → 3.0 |
| `config/settings.py:93` | paper_timeout_hours 4 → 8 |
| `tests/test_parsers/test_signals_phase13.py:135` | R34 test weight -4 → -6 |

## Current System State (post-fixes)

### Timing
- Token discovery: <1s (Chainstack gRPC) or 3-5s (PumpPortal WS fallback)
- PRE_SCAN: T+5s — instant reject + early_watch for clean tokens
- INITIAL signal: T+25s — full enrichment + scoring + signal generation
- **Total signal latency: T+25-30s** (was T+57-65s)

### Paper Trading
- Take profit: 3x (was 5x)
- Stop loss: -50%
- Trailing stop: 30% drawdown after 2x
- Early stop: -20% in first 30 min
- Timeout: 8h (was 4h)
- Slippage: active in both enrichment AND price loop paths
- PnL USD: correct SOL→USD conversion via Jupiter price

### Signal Weights (key changes)
- R34 fee_payer_sybil: -6 (was -4)
- R37 jito_bundle_snipe: -6 (was -4)

### Tests: 498 passed, 0 failed
