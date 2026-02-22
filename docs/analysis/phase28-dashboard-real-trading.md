# Phase 28: Dashboard Real Trading UI + Documentation

**Date**: 2026-02-22
**Depends on**: Phase 27 (Real Trading Engine)

## Summary

Added real trading mode support to the dashboard portfolio page and updated all project documentation to reflect the real trading capabilities introduced in Phase 27.

## Changes

### Backend (`src/api/routers/portfolio.py`)

1. **Summary endpoint enrichment** — `GET /portfolio/summary?mode=paper|real|all`
   - Paper/real fast-path responses now include `mode`, `real_trading_enabled` fields
   - "all" mode fallback adds `wallet_balance`, `circuit_breaker_tripped`, `total_failures` when RealTrader is available
   - Graceful error handling for wallet balance queries

2. **Position tx_hash batch query** — `GET /portfolio/positions`
   - Replaced `getattr(p, "tx_hash", None)` (always None — Position has no tx_hash column) with batch Trade query
   - For real positions (`is_paper=0`), queries Trade table for buy-side tx_hash by token_id
   - Single batch query (IN clause) instead of N+1

### Frontend

1. **`types/api.ts`** — New types
   - `PortfolioMode = "paper" | "real" | "all"`
   - `PortfolioSummary` extended: `mode`, `real_trading_enabled`, `wallet_balance?`, `circuit_breaker_tripped?`, `total_failures?`
   - `PositionItem` extended: `is_paper: boolean`, `tx_hash: string | null`

2. **`lib/api.ts`** — Mode parameter
   - `portfolio.summary(mode)` — appends `?mode=` to URL
   - `portfolio.pnlHistory(days, mode)` — appends `&mode=` to URL

3. **`pages/portfolio-page.tsx`** — Full overhaul
   - **Mode tabs**: Paper 📄 / Real 💰 / All 📊 (top-right, pill style)
   - **Mode state**: `useState<PortfolioMode>("paper")`, resets cursor on mode change
   - **usePolling keys**: include mode for cache invalidation (`summary-${mode}`, `pos-${mode}-...`, `pnl-${mode}`)
   - **Conditional stat cards**: Wallet Balance (SOL) + Circuit Breaker status appear for real/all modes
   - **Circuit breaker visual**: red border + red bg when tripped, ShieldAlert icon
   - **REAL badge**: emerald dot + "REAL" label on positions with `is_paper === false`
   - **tx_hash links**: "tx" + ExternalLink icon → `https://solscan.io/tx/{hash}` for real trades
   - **Chart colors**: emerald (paper), amber (real), blue (all) — gradient IDs keyed by mode
   - **Dynamic subtitle**: "Paper trading performance" / "Real trading performance" / "All trading combined"

### Documentation

| File | Changes |
|------|---------|
| `CLAUDE.md` | Project description updated (paper + real), Key Paths added (`src/trading/*`), Phase 27+28 in Architecture Docs |
| `docs/architecture/overview.md` | Diagram updated (Real Trader), "Paper Trading" → "Trading Engine (Dual Mode)" with paper + real sections, Phase 27+28 in changelog |
| `docs/architecture/modules.md` | New "Trading Engine (`src/trading/`)" section (6 entries), Portfolio row updated |
| `MEMORY.md` | New "Real Trading Engine" section with full details |
| `docs/analysis/phase28-dashboard-real-trading.md` | This document |

## Files Modified

- `src/api/routers/portfolio.py`
- `frontend/src/types/api.ts`
- `frontend/src/lib/api.ts`
- `frontend/src/pages/portfolio-page.tsx`
- `CLAUDE.md`
- `docs/architecture/overview.md`
- `docs/architecture/modules.md`
- `MEMORY.md`

## Backward Compatibility

- Default mode is `"paper"` — existing behavior unchanged
- Paper mode shows identical UI to before (no wallet/circuit cards, no REAL badges)
- All existing API responses are backward-compatible (new fields are additive)
