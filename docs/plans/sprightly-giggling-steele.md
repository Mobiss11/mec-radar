# Phase 33: Anti-Scam Filter v2 — усиление фильтрации rug pull токенов

## Контекст

Продакшн-данные за последние 24ч (23 февраля):
- **356** закрытых paper позиций
- **176 (49.4%)** закрыты как `liquidity_removed` — скам/rug pull
- Профитные трейды: **+63.56 SOL**, скамы: **-119.91 SOL**
- Нетто: **-56.35 SOL**. Убрав скамы → было бы **+63 SOL**

### Паттерны скама (из анализа 176 rug позиций vs 180 нормальных)

| Паттерн | Скам | Нормальные | Ratio |
|---------|------|------------|-------|
| holders ≤ 5 на INITIAL | **21.8%** | 3.7% | **5.9x** |
| holders ≤ 2 | ~8% | **0%** | ∞ |
| rugcheck > 25K | **24%** | 7.1% | **3.4x** |
| Дренаж < 1 мин | **47.7%** | — | — |
| Дренаж < 5 мин | **62.8%** | — | — |
| Copycat (CASH×9, ELSTONKS×8) | массовый | единичный | — |
| "Creator history of rugged" | 14 скамов | 3 нормальных | **4.7x** |

**Ключевой инсайт**: bullish-правила (`explosive_holder_growth`, `organic_buy_pattern`) одинаково срабатывают на скамах и профитных — боты имитируют органическую активность. Нужна bearish-сторона.

## Изменения

### Файл 1: `src/parsers/signals.py`

#### 1.1 HG3: Hard gate — минимум холдеров (после HG2, ~line 137)

```python
# HG3: Minimum holders — tokens with 0-2 real holders are empty shells.
# Production: 0% good tokens had holders ≤ 2. Hard gate = zero false positives.
if 0 < holders <= 2:
    gate_rule = SignalRule(
        "min_holders_gate", -10,
        f"Hard gate: {holders} holders (no real market participants)",
    )
    return SignalResult(...)  # early return, avoid
```

#### 1.2 R61: Compound scam fingerprint — hard avoid (после HG3)

Считаем "scam flags":
1. LP не burned/locked (Raydium + security confirm)
2. `is_mintable == True`
3. `bundled_buy_detected`
4. `rugcheck_danger_count >= 2`
5. `pumpfun_dead_tokens >= 3`
6. `fee_payer_sybil_score > 0.3`

**Если 3+ флагов → hard avoid (early return)**. Каждый флаг сам по себе слабый, но 3 одновременно = статистически невозможно на хороших токенах.

#### 1.3 R60: Low holders penalty (bearish, после R14)

```python
# holders ≤ 5 → -3 (soft penalty, не hard gate)
# 21.8% скамов vs 3.7% хороших — strong signal
```

#### 1.4 R26: serial_deployer — снижаем порог 5 → 3

```python
if pumpfun_dead_tokens >= 3:  # было 5
    fired.append(SignalRule("serial_deployer", -3, ...))
```

#### 1.5 R64: serial_deployer_mild (elif под R26)

```python
elif pumpfun_dead_tokens >= 2:
    fired.append(SignalRule("serial_deployer_mild", -2, ...))
```

#### 1.6 R27: lp_not_burned — вес -1 → -2

```python
SignalRule("lp_not_burned", -2, ...)  # было -1
```

#### 1.7 R62: Unsecured LP + fresh token (после R27)

```python
# LP not burned AND age < 10min AND holders < 30 → -3
# Стакается с R27 (-2): суммарно -5 для свежего токена без LP lock
```

#### 1.8 R63: Copycat rugged symbol (новый param `copycat_rugged: bool`)

```python
# Символ совпадает с недавно заруженным → -6
# CASH×9, ELSTONKS×8 — повторные деплои одного символа
```

Новый параметр: `copycat_rugged: bool = False` добавляется в `evaluate_signals()`.

### Файл 2: `src/parsers/worker.py`

#### 2.1 Module-level dict для copycat tracking

```python
_RUGGED_SYMBOLS: dict[str, float] = {}  # symbol.upper() -> monotonic timestamp
_RUGGED_SYMBOLS_TTL = 7200  # 2 hours
```

#### 2.2 В `_paper_price_loop`: записываем rugged символы

После закрытия позиции с `close_reason == "liquidity_removed"`:
```python
_RUGGED_SYMBOLS[pos.symbol.upper()] = time.monotonic()
```

Аналогично для `_real_price_loop`.

#### 2.3 В `_enrich_token`: проверяем copycat перед `evaluate_signals()`

```python
_copycat = False
if token.symbol:
    _ts = _RUGGED_SYMBOLS.get(token.symbol.upper())
    if _ts and (time.monotonic() - _ts) < _RUGGED_SYMBOLS_TTL:
        _copycat = True

# Pass to evaluate_signals:
result = evaluate_signals(..., copycat_rugged=_copycat)
```

### Файл 3: `tests/test_parsers/test_signals_antiscam.py` (новый)

| Группа | Тестов | Проверка |
|--------|--------|----------|
| HG3 | 5 | holders 0,1,2 → blocked; 3 → passes; None → passes |
| R60 | 4 | holders 3-5 → -3; 6+ → нет; strong bullish → can override |
| R61 | 5 | 3+ flags → avoid; 2 flags → passes; profitable tokens safe |
| R62 | 4 | fresh+unsecured → -3; old/many holders/burned → нет |
| R63 | 2 | copycat=True → -6; False → нет |
| R64 | 3 | dead=2 → -2; dead=1 → нет; dead=3 → R26 fires |
| R27 weight | 1 | weight=-2 |
| R26 threshold | 1 | dead=3 → fires; dead=4 → fires |
| Backtest profitable | 5 | Hua Hua, CASH, Pan-kun, nanoclaw, BabyAliens → NOT blocked |
| Backtest scam | 3 | copycat CASH, low holders, compound flags → blocked |
| **Итого** | **~33** | |

## Ожидаемый эффект

| Правило | Скамов блокирует | False positives |
|---------|-----------------|-----------------|
| HG3 (holders≤2) | ~5-10% | **0%** |
| R61 (compound 3+) | ~15-20% | **~0%** |
| R60 (holders≤5, -3) | ослабляет ~22% | 3.7% получают -3 (преодолимо) |
| R62 (LP+fresh, -3) | ~30% | минимум (age+holder guard) |
| R63 (copycat, -6) | повторные атаки | редко |
| R64 (2 dead, -2) | ~10-15% | низко |
| R27 (-1→-2) | усиление | минимально |
| R26 (5→3) | больше serial | низко |

**Консервативная оценка**: блокировка 40-60% из 176 rug позиций → экономия **48-72 SOL** из -119.91. Нетто с -56 SOL → **+0 до +16 SOL** (breakeven → profit).

## Порядок имплементации

1. `signals.py`: R27 weight -1→-2 (1 строка)
2. `signals.py`: R26 threshold 5→3 (1 строка)
3. `signals.py`: HG3 holders≤2 gate (10 строк)
4. `signals.py`: R60 holders≤5 penalty (8 строк)
5. `signals.py`: R64 elif serial_deployer_mild (8 строк)
6. `signals.py`: R62 unsecured LP fresh (12 строк)
7. `signals.py`: R61 compound scam fingerprint (30 строк)
8. `signals.py` + `worker.py`: R63 copycat name (new param + dict + tracking)
9. `test_signals_antiscam.py`: ~33 теста

## Верификация

```bash
# 1. Тесты
.venv/bin/python -m pytest tests/test_parsers/test_signals_antiscam.py -v
.venv/bin/python -m pytest tests/test_parsers/test_signals_rug_gates.py -v
.venv/bin/python -m pytest tests/ -v

# 2. Деплой + мониторинг
pm2 logs mec-radar --lines 200
# Проверить: новые avoid с причинами min_holders_gate, compound_scam_fingerprint, etc.
# Проверить: профитные токены (score 60+, holders 30+) по-прежнему проходят

# 3. Через 1-2 часа: сравнить % liquidity_removed в закрытых позициях
# Цель: < 30% (сейчас 49.4%)
```
