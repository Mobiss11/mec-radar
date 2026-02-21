# Phase 25: Production Fixes + Signal Calibration (2026-02-21)

## Context

System was discovering tokens (gRPC + WS + Meteora DBC all active), enriching through all 11 stages, scoring correctly — but producing ZERO buy/strong_buy signals and ZERO paper trades. Two critical bugs in the enrichment queue + one fundamental signal calibration issue were identified and fixed.

**Result:** First buy signals ever generated. First paper trades opened and closed profitably.

## P0 Fixes (Critical)

### 1. Enrichment Queue Stage Starvation

**Problem:** `_sort_score()` in `enrichment_queue.py` used a weighted formula:
```
priority * 1e12 + stage_weight * 1e8 + scheduled_at
```
where `_stage_weight()` assigned higher weights to early stages (PRE_SCAN=100, INITIAL=90, etc.). With ~30 new tokens/minute, PRE_SCAN and INITIAL tasks always had higher scores than MIN_2+ tasks. Later stages were **permanently starved** — tokens got discovered and pre-scanned but never completed the full pipeline.

**Fix:** Removed `_stage_weight()` entirely. Simplified to pure FIFO within priority:
```python
score = task.priority * 1e12 + task.scheduled_at
```
Now all stages within the same priority are processed in arrival order.

**File:** `src/parsers/enrichment_queue.py`

### 2. MintInfo Dict Deserialization from Redis

**Problem:** When enrichment tasks were serialized to Redis (JSON), `MintInfo` dataclass became a plain dict. `_dict_to_task()` already reconstructed `SellSimResult` from dicts, but `MintInfo` was missed. When `evaluate_signals()` tried to access `mint_info.has_dangerous_extensions`, it crashed with `AttributeError: 'dict' object has no attribute 'has_dangerous_extensions'`, silently preventing ALL signal generation for tokens that went through Redis serialization.

**Fix:** Added MintInfo reconstruction in `_dict_to_task()`:
```python
if mint_info_raw and isinstance(mint_info_raw, dict):
    task.mint_info = MintInfo(**mint_info_raw)
```

**File:** `src/parsers/enrichment_queue.py`

### 3. Signal Rule Calibration (Noise Reduction)

**Problem:** Three bearish rules fired on ~99% of fresh PumpFun memecoins, creating a permanent bear baseline of -8:
- `rugcheck_danger` (-4): RugCheck scores nearly all fresh tokens >=50 ("Low LP Providers", "Mutable Metadata" — structural properties, not scam indicators)
- `llm_high_risk` (-3): Gemini Flash Lite rates ALL memecoins as high risk (they ARE high risk by traditional standards)
- `no_socials` (-1): Fresh tokens rarely have social links in first minutes

With bear=-8, even a strong bull score of 12 only yielded net=4 (watch), making buy (>=5) and strong_buy (>=8) mathematically unreachable for most tokens.

**Fix:** Reduced weights of "noise" rules while keeping real danger signals unchanged:

| Rule | Old Weight | New Weight | Rationale |
|------|-----------|------------|-----------|
| R15 `rugcheck_danger` | -4 | -2 | Structural, not scam. `rugcheck_multi_danger` (-3) catches real danger |
| R51 `llm_high_risk` | -3 | -1 | LLM too conservative for memecoins. `llm_low_risk` (+2) is the real value |
| R38 `mutable_metadata` | -2 | -1 | Fresh tokens start mutable; renounce comes later |

**Unchanged real danger rules:** honeypot (hard 0), `jito_bundle_snipe` (-6), `fee_payer_sybil` (-6), `rugcheck_multi_danger` (-3), `concentrated_holders` (-4), `dev_sold_all` (-5)

**New bear baseline:** ~-4 (was ~-8). Buy signals now achievable with moderate bull evidence.

**File:** `src/parsers/signals.py` (lines 215, 465, 594)

## P1 Fixes (Important)

### 4. Portfolio API is_paper Type Mismatch

**Problem:** Dashboard Portfolio page showed summary stats (OPEN: 1, CLOSED: 1, PNL: $25.72) but positions table showed "No open positions". Root cause: `Position.is_paper` column is Integer (0/1), but query used `Position.is_paper == True` (Python boolean). PostgreSQL `integer = boolean` comparison silently returns no rows.

**Fix:** Changed to `Position.is_paper == 1` in two places:
- Line 72: positions list endpoint
- Line 177: PnL history endpoint

Note: summary endpoint already used `== 1` (was correct), which is why summary stats worked.

**File:** `src/api/routers/portfolio.py`

## Results

### First Paper Trades
| Token | Score | Net | Action | Entry | Peak | Result |
|-------|-------|-----|--------|-------|------|--------|
| nanoclaw | 54 | 6 | buy | $0.0000372 | $0.000157 | +322% (4.22x), past 3x TP |
| BabyAliens | 43 | 6 | buy | — | — | +45.9%, closed by trailing_stop |

### Signal Distribution (first 30 min after calibration)
- 2 buy signals (nanoclaw, BabyAliens)
- 5 watch signals (4CLAW, POPO, USRX, LOBSTER, others)
- 0 strong_buy (expected — need more bull evidence)
- 0 avoid (expected — honeypot/scam tokens still blocked)

### System Health
- All token sources active (gRPC, PumpPortal WS, Meteora DBC WS)
- All 11 enrichment stages processing (no more starvation)
- 503 tests passing, 0 failed
- Dashboard API serving on port 8080
