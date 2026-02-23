# Phase 38: Anti-Scam — Compound Copycat Guard + Fresh Unsecured LP Cap

## Context

After Phase 37 fixed false `liquidity_removed` from bad liquidity data, the **remaining problem is positions OPENING on scam tokens**:

- MEMELORD: 6+ copycat tokens, all scored 35-63, got buy/strong_buy despite `copycat_rugged_symbol` AND `rugcheck_danger` (score 11500) firing
- CRIME: 85 launches in 6 hours, 12 positions taken — industrial copycat spam
- NIKOLAICH, NoLimit: same pattern — LP removed within seconds of position open
- Root cause: bull rules (+12 total from explosive_buy_velocity, holder_acceleration, organic_buy_pattern etc.) overwhelm bear rules (copycat -8 + rugcheck_danger -2 = -10)
- 52% of liquidity_removed positions close within 30 seconds

**Key insight**: the fix belongs in the **signal layer** — prevent positions from opening on scam tokens. Don't change close/price logic.

## Strategy: Three combinatorial guards

All guards are **combinatorial** (require 2+ conditions), not single-factor. This prevents false positives on legitimate tokens.

### Guard 1: Add `copycat_rugged` to compound scam fingerprint (R61)

**File**: `src/parsers/signals.py` line 175 (after sybil flag)

Currently 6 flags: LP_unsecured, mintable, bundled_buy, rugcheck_2+_dangers, serial_3+_dead, sybil_0.3+. Add 7th:

```python
if copycat_rugged:
    _scam_flags += 1
    _scam_details.append(f"copycat_{copycat_rug_count}x_rugged")
```

**Threshold stays at 3.** So copycat alone = 1 flag (harmless). But:
- MEMELORD: copycat(1) + LP_unsecured(1) + rugcheck_2+_dangers(1) = 3 → **hard avoid**
- CRIME: copycat(1) + serial_deployer(1) + LP_unsecured(1) = 3 → **hard avoid**
- CLEUS +140%: copycat(1) + LP_burned + no_serial = 1 flag → **not affected** ✅

### Guard 2: New R66 — copycat + serial deployer compound (-4)

**File**: `src/parsers/signals.py` line 901 (after R63)

When BOTH copycat AND serial deployer fire, apply extra -4:

```python
# R66: Copycat + serial deployer compound — Phase 38
if copycat_rugged and pumpfun_dead_tokens is not None and pumpfun_dead_tokens >= 2:
    r = SignalRule(
        "copycat_serial_compound", -4,
        f"Copycat symbol + creator has {pumpfun_dead_tokens} dead tokens",
    )
    fired.append(r)
    reasons[r.name] = r.description
```

With this: copycat(-6) + serial(-3) + compound(-4) = -13 vs bull +12 = net -1 = **avoid**.
CLEUS: `pumpfun_dead_tokens=None` → R66 doesn't fire ✅

### Guard 3: Fresh unsecured LP cap (< 2 min + LP unlocked → max watch)

**File**: `src/parsers/signals.py` line 1006 (after graduation_zone cap)

```python
# Phase 38: Fresh unsecured LP cap
_is_fresh_unsecured_lp = (
    token_age_minutes is not None and token_age_minutes < 2
    and raydium_lp_burned is not None and not raydium_lp_burned
    and security is not None and not security.lp_burned and not security.lp_locked
)
if _is_fresh_unsecured_lp and net > 2:
    net = 2
```

Token gets another chance at MIN_5 (5 min). Legit tokens survive; scams are rugged by then.
- CLEUS: LP burned → not affected ✅
- punchDance: LP burned → not affected ✅
- All test profitable tokens: either LP burned or age ≥ 2 min ✅

## Files

| File | Changes |
|------|---------|
| `src/parsers/signals.py` | Guard 1 (line 175), Guard 2 (line 901), Guard 3 (line 1006) |
| `tests/test_parsers/test_signals_antiscam.py` | 10 new tests: scam blocking + profitable token safety |

## Expected Impact

- MEMELORD pattern → **hard avoid** (compound scam fingerprint)
- CRIME pattern → **hard avoid** (compound scam fingerprint)
- Fresh copycats with unlocked LP → max **watch** (no position opened)
- CLEUS +140%, punchDance +127% etc. → **unchanged** ✅

## Verification

```bash
# Tests
.venv/bin/python -m pytest tests/test_parsers/test_signals_antiscam.py -v

# Also verify existing profitable tests pass
.venv/bin/python -m pytest tests/test_parsers/test_signals_antiscam.py -k "profitable" -v

# Deploy and monitor
# Check liquidity_removed rate drops
ssh root@178.156.247.90 "psql -U mec_radar -d mec_radar -c \"SELECT close_reason, COUNT(*) FROM positions WHERE closed_at > NOW() - INTERVAL '1 hour' GROUP BY close_reason ORDER BY COUNT(*) DESC;\""
```
