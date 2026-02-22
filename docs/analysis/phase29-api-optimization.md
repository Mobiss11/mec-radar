# Phase 29: API Credit Optimization

**Date**: 2026-02-22
**Branch**: `feat/phase29-api-optimization`
**Status**: Deployed (not started)

## Problem

За 1 час работы (~20,000 обнаруженных токенов):
- **Helius**: ~1,000,000 credits consumed (plan limit exhausted in ~1.5h)
- **Birdeye**: ~419,000 CU consumed (1.5M/mo plan → ~3.5h runtime)
- **Vybe**: ~250,000 credits (500K/mo Dev plan → ~2h runtime)

Root cause: 60-70% of tokens pass PRE_SCAN and enter INITIAL, where each token triggers:
- Helius: ~65 RPC calls (convergence=30, funding_trace=18, wallet_age=10, fee_payer=2, jito=2, metaplex=1, bundled_buy=2)
- Birdeye: 85 CU (overview=30, security=50, metadata=5)
- Vybe: ~6 API calls (analyze_holders_pnl with 5 holders + summary)

## Changes

### P0.2: GoPlus honeypot in PRE_SCAN
**Files**: `src/parsers/worker.py` (`_run_prescan`)

Added GoPlus `get_token_security()` to PRE_SCAN parallel coroutines. If `is_honeypot == True`, token is rejected immediately before reaching INITIAL.

- GoPlus is FREE, no API key needed
- Zero quality impact — honeypot tokens always score 0
- Expected savings: ~5% of tokens rejected earlier

### P0.3: Two-phase INITIAL gate
**File**: `src/parsers/worker.py` (between Batch 1 and Batch 2)

After Batch 1 (Birdeye + GoPlus + RugCheck + GMGN + Raydium), skip Batch 2 (Helius deep detection + Vybe + Twitter + LLM) if:
- `liquidity < $500` AND `holders < 5` (ultra-low quality)
- GoPlus `is_honeypot == True` (confirmed honeypot)
- RugCheck `danger_count >= 4` (multiple danger-level risks)

Safe defaults are set for all Batch 2 variables when skipped.

- Birdeye security ALWAYS runs (writes `top10_holders_pct`, `dev_holds_pct` needed for data completeness gate)
- Skipped tokens would score 0-20 regardless — zero quality loss
- Expected savings: **~65% Helius**, **~68% Vybe**

### P0.4: Conservative Helius parameter reduction
**Files**: `src/parsers/convergence_analyzer.py`, `src/parsers/funding_trace.py`, `src/parsers/worker.py`, `config/settings.py`

| Module | Was | Now | Savings per token |
|--------|-----|-----|-------------------|
| Convergence | 15 buyers × 2 calls | **10** buyers × 2 calls | -10 Helius calls |
| Funding trace | 3 hops × 6 calls | **2** hops × 6 calls | -6 Helius calls |
| Wallet age | 10 wallets × 1 call | **7** wallets × 1 call | -3 Helius calls |

All configurable via `.env`:
```
CONVERGENCE_MAX_BUYERS=10
FUNDING_TRACE_MAX_HOPS=2
WALLET_AGE_MAX_WALLETS=7
```

### P0.5: Vybe stricter gate
**Files**: `src/parsers/worker.py`, `config/settings.py`

- `prescan_risk_boost` gate: `>= 10` → `>= 5` (skip Vybe earlier for risky tokens)
- `max_holders`: `5` → `3` (3 holders enough for PnL signal)

Configurable:
```
VYBE_MAX_HOLDERS_PNL=3
VYBE_PRESCAN_RISK_GATE=5
```

## Expected Savings

| API | Was/hour | After | Reduction |
|-----|----------|-------|-----------|
| **Helius** | ~1,000K | ~350K | **-65%** |
| **Birdeye** | ~419K CU | ~350K CU | **-16%** |
| **Vybe** | ~250K | ~80K | **-68%** |

## Rejected Optimizations

| Optimization | Reason |
|-------------|--------|
| **P0.1 Credit cap tracking (Redis)** | Overspend acceptable, complexity not worth it |
| **P1.1 SharedRateLimiter Helius** | Deferred — not critical for credit savings |
| **P1.2 Share first-block data** | Deferred — requires refactor of 3 detectors |
| **P1.3 Dashboard credits panel** | Deferred |
| **P1.4 SOL price Jupiter primary** | 600 CU/hour negligible |
| **Skip Birdeye security in INITIAL** | **CRITICAL**: would break data completeness gate. Birdeye security writes `top10_holders_pct` and `dev_holds_pct`. Without them, ALL scores capped at 40 → zero buy signals → kills paper trading |
| **RugCheck danger in PRE_SCAN** | 99% of fresh PumpFun memecoins return danger status → would filter ALL tokens |
| **DexScreener liquidity < $500 PRE_SCAN** | pump.fun bonding curve starts at $200-800 → would reject profitable tokens pre-migration |
| **Convergence 15 → 5 buyers** | Risk: 8/20 buyers converging but first 5 clean → misses distributed attacks |
| **Funding trace 3 → 1 hop** | Hop 2 catches intermediate wallet laundering in ~10% of cases |

## Verification

1. `pytest tests/ -v` — all tests pass
2. Data completeness: `top10_holders_pct`, `dev_holds_pct` still populated (Birdeye security always runs)
3. Key KPI: paper trading win rate should NOT drop
4. Logs: look for `[ENRICH] skip deep detection` count
5. New settings visible in `config/settings.py` with `.env` override support
