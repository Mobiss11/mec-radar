# Phase 37: Fix False `liquidity_removed` Closes

## Context

57% of positions today (387/681) closed as `liquidity_removed` on LIVE tokens. Root cause: **Birdeye `multi_price` with `include_liquidity=true` returns bonding curve liquidity** (near-zero after DEX migration), not the real DEX pool liquidity. Price is correct (from DEX), but liquidity field references the dead bonding curve.

Evidence from SXAL/LOBCHURCH:
- SXAL: liq=$64K (enrichment snapshot) → $0.02 (Birdeye multi_price) while price ROSE
- LOBCHURCH: liq=$50K → $0.07 while price stable
- Grace period doesn't help: $0.02 ≠ 0.0, bypasses exact-zero check

## Root Cause

In `_paper_price_loop` (worker.py:3698):
```python
if bp and bp.liquidity is not None:
    live_liq_map[pos.token_id] = float(bp.liquidity)  # BAD: bonding curve liq
```

Birdeye `multi_price` aggregates ALL pools for a token. If the bonding curve pool still exists (it does for most pump.fun tokens), `liquidity` can reflect only that pool's near-zero value instead of the active Raydium pool's real liquidity.

## Fix Strategy: Two-layer defense

### Layer 1: DexScreener liquidity ALWAYS takes priority (worker.py)

Currently DexScreener liquidity is only used when Birdeye doesn't return it (`if _tid not in live_liq_map`). Fix: **always fetch DexScreener liquidity for ALL open positions** (up to 20), and let it OVERRIDE Birdeye's unreliable multi_price liquidity.

DexScreener `get_token_pairs()` returns per-pair data — we already pick `max_liq` across all pairs. This is the most reliable liquidity source because it shows actual DEX pool liquidity, not bonding curve artifacts.

**Change in `_paper_price_loop`** (worker.py ~3722-3765):
- Fetch DexScreener for ALL positions (not just missing price/liq)
- DexScreener liq OVERRIDES Birdeye liq (not just fills gaps)
- Same for `_real_price_loop`

### Layer 2: Price-coherence guard in close_conditions.py

If Birdeye/DexScreener both fail, add a safety net: if price is stable/rising but liquidity suddenly drops to near-zero — it's data noise, not a real rug.

**In `close_conditions.py`**, before returning `liquidity_removed`:
- If `current_price >= entry_price * 0.7` (price hasn't crashed 30%+), the token is likely alive
- A real LP removal crashes price to near-zero instantly
- Skip `liquidity_removed` when price is coherent → let other conditions (stop loss, timeout) handle naturally

## Files

| File | Changes |
|------|---------|
| `src/trading/close_conditions.py` | Add price-coherence guard before `liquidity_removed` |
| `src/parsers/worker.py` | DexScreener liq overrides Birdeye in both price loops |

## Detailed Changes

### 1. `src/trading/close_conditions.py` — Price-coherence guard

Replace the current liquidity block (lines 41-52):

```python
# Liquidity critically low — pool drained, can't sell without massive slippage
if liquidity_usd is not None and liquidity_usd < 5_000:
    # Phase 37: Price-coherence check.
    # Birdeye multi_price often returns bonding-curve liq (near-zero)
    # instead of real DEX pool liq. A real LP removal crashes price instantly.
    # If price is still >= 70% of entry, token is alive — data is wrong.
    price_ok = (
        pos.entry_price
        and pos.entry_price > 0
        and current_price >= pos.entry_price * Decimal("0.5")
    )
    if price_ok and liquidity_usd < 100:
        # Near-zero liq but price healthy → bad data, skip
        pass
    elif liquidity_usd == 0.0 and pos.opened_at:
        # Exact zero on fresh position → indexing lag grace period
        age_sec = (now - pos.opened_at).total_seconds()
        if age_sec <= liquidity_grace_period_sec:
            pass
        else:
            # Exact zero on mature position — still check price coherence
            if price_ok:
                pass  # Price alive, liq data stale
            else:
                return "liquidity_removed"
    else:
        # Non-zero low liq ($100-$5K) with price crash → genuine LP drain
        if price_ok:
            pass  # Price still healthy, liq data likely wrong
        else:
            return "liquidity_removed"
```

Logic summary:
- `liq < $100 AND price >= 50% of entry` → skip (bad data)
- `liq == 0 AND fresh position` → skip (indexing lag)
- `liq == 0 AND mature AND price >= 50% of entry` → skip (stale data)
- `liq < $5K AND price < 50% of entry` → `liquidity_removed` (real rug/drain)

The 50% threshold is conservative: real LP removals crash price 90%+. A 50% drop WITH low liquidity is a genuine emergency.

### 2. `src/parsers/worker.py` — DexScreener liq priority

In `_paper_price_loop` (~line 3722): Change DexScreener fetch scope from "missing only" to "all positions", and let DexScreener OVERRIDE Birdeye liq:

```python
# DexScreener: fetch for ALL open positions (not just missing)
# DexScreener gives per-DEX-pair liquidity — more reliable than Birdeye multi_price
# which can return bonding curve liq (near-zero) for migrated tokens
_dex_fetch_addrs = list(dict.fromkeys(
    [p.token_address for p in positions if p.token_id not in token_prices]
    + list(addresses)  # ALL addresses for liquidity
))[:20]
```

And change `if _tid and _tid not in live_liq_map:` to unconditionally store:
```python
# DexScreener liq overrides Birdeye (more reliable for DEX pool data)
if _tid:
    live_liq_map[_tid] = max_liq
```

Same changes in `_real_price_loop`.

### 3. Copycat tracking guard (worker.py ~3817)

Update the rugged symbol tracking to also respect price-coherence:
```python
if _pos_liq is not None and _pos_liq < 5_000 and pos.symbol:
    # Only track as rug if price also crashed (not just bad liq data)
    _pos_price = token_prices.get(pos.token_id)
    _price_crashed = (
        _pos_price is not None
        and pos.entry_price
        and pos.entry_price > 0
        and float(_pos_price / pos.entry_price) < 0.5
    )
    if not _price_crashed:
        continue  # Price healthy, liq data unreliable — don't mark as rug
    ...existing logic...
```

## Tests

Update `tests/test_trading/test_close_conditions.py`:
- Existing tests: adjust expectations for price-coherent positions
- New tests:
  - `test_skip_liq_removed_when_price_healthy` — liq=$0.02, price=entry → None
  - `test_close_liq_removed_when_price_crashed` — liq=$0.02, price=10% of entry → "liquidity_removed"
  - `test_skip_near_zero_liq_price_above_50pct` — liq=$50, price=60% of entry → None
  - `test_close_low_liq_price_below_50pct` — liq=$2K, price=30% of entry → "liquidity_removed"

## Verification

```bash
# Tests
.venv/bin/python -m pytest tests/test_trading/test_close_conditions.py -v

# Deploy and monitor
# After deploy: check PM2 logs for liquidity_removed rate
# Before: 57% of closes are liquidity_removed
# After: should drop to <10% (only genuine rugs where price also crashed)
ssh root@178.156.247.90 "pm2 logs mec-radar --lines 100 --nostream | grep liquidity_removed | wc -l"
```
