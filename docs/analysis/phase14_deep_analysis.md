# Deep Analysis: Detection Pipeline State & Roadmap

> **SUPERSEDED**: All bugs from this analysis have been fixed. See [Phase 17 Audit Fixes](phase17-audit-fixes.md) for latest state.

## CRITICAL: Текущая система СЛОМАНА (FIXED)

### Bug #1: `jupiter_price_val` UnboundLocalError
**worker.py:1031** — `jupiter_price_val` используется в `save_token_snapshot()`, но объявляется только на **строке 1178**. Python видит присвоение позже в функции и считает переменную локальной → `UnboundLocalError` при каждом `enriched=True`.

**Эффект:** Каждый enrichment pass крашится, `except` на строке 695 ловит ошибку, токен тихо выбрасывается. **Вся scoring/signal pipeline не работает.**

### Bug #2: Phase 13 — полностью мёртвый код
Все 7 модулей Phase 13 написаны, протестированы (403 теста), но **ни один не подключён к worker.py**:
- `fee_payer_cluster.py` — не импортирован
- `convergence_analyzer.py` — не импортирован
- `metadata_scorer.py` — не импортирован
- `rugcheck_risk_parser.py` — не импортирован
- `solsniffer/client.py` — не импортирован
- `bubblemaps/client.py` — не импортирован
- `holder_pnl.py` (wash trading) — не вызывается

**Результат:** 8 новых scoring параметров в `scoring.py`/`scoring_v3.py` всегда `None`/`False`. 8 signal rules R29-R36 никогда не срабатывают.

### Bug #3: Signal Rules R23-R24 тоже мёртвые
`mint_info` и `sell_sim_result` из PRE_SCAN не передаются в `evaluate_signals()`. Правила R23 (`token2022_danger`) и R24 (`sell_sim_failed`) никогда не сработают.

### Bug #4: 3 готовых модуля не подключены к worker
- `wallet_cluster.py` — координация трейдеров (DB queries only, no API)
- `wallet_age.py` — детекция sybil через fresh wallets (needs Helius)
- `lp_events.py` — on-chain LP add/remove events (needs Helius)

### Bug #5: `validate_price_consistency` — импортирован но не вызывается
Строка 91 worker.py: импорт есть, вызова нет.

---

## Что работает сейчас (фактически)

### PRE_SCAN (+5s после обнаружения, ~600ms)
- `parse_mint_account()` — mint/freeze authority, Token2022 extensions
- `jupiter.simulate_sell()` — sellability check
- Hard reject: mint+freeze active, permanentDelegate, no route
- Soft flags: transferFee (+10), high impact (+5), freeze alone (+15)

### INITIAL (+45s, ~5s total enrichment)
- GMGN info + security
- Birdeye metadata (name, desc, socials — НО socials не используются в scoring!)
- DexScreener data
- GoPlus security check
- Pump.fun creator history
- Raydium LP verification
- Bundled buy detection (Helius)
- Rugcheck report + Jupiter price
- Top holders + smart money analysis
- Whale dynamics + OHLCV + trade flow + volume profile
- Cross-token whale correlation
- Creator risk assessment (1-hop funding trace)

### Scoring (36 rules, но 10 мёртвых)
- R1-R22: работают (Phase 10-11)
- R23-R24: МЁРТВЫЕ (mint_info, sell_sim не передаются)
- R25-R28: работают (Phase 12)
- R29-R36: МЁРТВЫЕ (Phase 13 не подключена)

---

## Чего НЕТ в системе (из исследования рынка)

### CRITICAL MISSING (бесплатные, высокий ROI)

| # | Что отсутствует | API/Инструмент | Стоимость | Latency |
|---|----------------|----------------|-----------|---------|
| 1 | **Jito bundle detection** — обнаружение self-snipe в первом блоке | RPC `getBlock` + 8 Jito tip accounts | $0 | <1s |
| 2 | **Metaplex metadata analysis** — isMutable, URI type (HTTP vs IPFS), name spoofing | Helius DAS `getAsset` (10 credits) | $0 | <500ms |
| 3 | **RugCheck Insider Networks** — граф связей между top holders | RugCheck API `/tokens/{id}/insiders/graph` | $0 | ~1s |
| 4 | **Solana Tracker Risk API** — sniper count, insider %, risk 1-10 | `solanatracker.io/data-api` | $0 | ~1s |
| 5 | **Jupiter VERIFY status** — verified/strict/community badge | `tokens.jup.ag/token/{mint}` | $0 | <200ms |
| 6 | **DeFade 14-module analysis** — rug probability, smart money, sniper detection | `defade.org` API | $0 | ~2s |

### HIGH VALUE MISSING (платные, очень высокий ROI)

| # | Что отсутствует | API/Инструмент | Стоимость | Что даёт |
|---|----------------|----------------|-----------|----------|
| 7 | **Moralis Token Holders API** — holder distribution by size, acquisition method, PnL | Moralis Free: 40K CU/day | $0-$49/mo | Whale/shark/dolphin categorization |
| 8 | **Nansen Smart Money** — 250M+ wallet labels, AI signals | Nansen Free: 100 API credits | $0-$69/mo | Smart money rotation tracking |
| 9 | **Helius Developer tier** — 10M credits, 50 RPS (текущий Free: 1M, 10 RPS) | Helius | $49/mo | 10x capacity для on-chain analysis |
| 10 | **Bitquery Pump.fun lifecycle** — bonding curve progress, migration, GraphQL streaming | Bitquery | $0 (free tier) | Full Pump.fun lifecycle tracking |

### ARCHITECTURE MISSING

| # | Проблема | Решение |
|---|---------|---------|
| 11 | **Нет параллелизма в INITIAL** — API вызовы последовательны | `asyncio.gather()` для независимых: Birdeye, DexScreener, GoPlus, Rugcheck, Jupiter, Raydium |
| 12 | **INITIAL стадия в +45s — слишком поздно** | Двухфазный INITIAL: быстрые проверки (+10s), медленные (+45s) |
| 13 | **Нет WebSocket стриминга новых пулов** — полагаемся на PumpPortal polling | QuickNode Metis `/new-pools` или Shyft gRPC |
| 14 | **Нет real-time LP мониторинга** | WebSocket подписка на LP pool accounts |
| 15 | **1-hop funding trace** вместо 3-hop | Модуль уже поддерживает `max_hops=3`, worker вызывает с default (1) |

---

## Бюджет: текущий vs. рекомендуемый

### Текущий ($88/мес)
| Сервис | Стоимость | Используется |
|--------|-----------|-------------|
| Birdeye | $39/мес | Metadata, prices, security |
| Helius Free | $0 (1M credits) | Transactions, signatures |
| GoPlus | $0 | Security scan |
| RugCheck | $0 | Risk score |
| Jupiter | $0 | Price, sell sim |
| Raydium | $0 | LP verification |
| Pump.fun | $0 | Creator history |
| **Итого** | **$88/мес** | |

### Рекомендуемый ($186/мес, +$98)
| Сервис | Стоимость | Зачем |
|--------|-----------|-------|
| Birdeye | $39/мес | Без изменений |
| **Helius Developer** | **$49/мес** | 10M credits (10x), 50 RPS (5x), Enhanced WebSocket |
| **Moralis Pro** | **$49/мес** | Holder distribution, PnL, categorization |
| GoPlus | $0 | Без изменений |
| RugCheck | $0 | + Insider Networks API |
| Jupiter | $0 | + VERIFY API |
| Solana Tracker | $0 | + Risk API (sniper count) |
| DeFade | $0 | + 14-module analysis |
| Moralis Free | $0 | Holder trends (40K CU/day) |
| Bitquery Free | $0 | Pump.fun lifecycle |
| **Итого** | **$186/мес** | **+$98 от текущего** |

### Опциональные (будущее)
| Сервис | Стоимость | Когда |
|--------|-----------|-------|
| SolSniffer Starter | $47/мес | Когда нужно 5K calls/mo |
| Nansen | $69/мес | Для smart money rotation |
| Bubblemaps | B2B (по запросу) | Когда дадут API key |
| QuickNode Metis | ~$50/мес | Для real-time pool detection |

---

## Приоритеты реализации

### PHASE 14A: FIXES (срочно, 0 API, 0 денег)

1. **FIX jupiter_price_val bug** — переместить объявление выше строки 1031
2. **Wire metadata_scorer** — уже есть desc/website/twitter/telegram в Token, 0 API calls
3. **Wire rugcheck_risk_parser** — уже есть rugcheck_risks string в DB, 0 API calls
4. **Wire all Phase 13 params** в compute_score() и evaluate_signals()
5. **Forward mint_info + sell_sim_result** из PRE_SCAN в signals (R23-R24)
6. **Wire wallet_cluster + wallet_age + lp_events** — уже написаны
7. **Wire validate_price_consistency** — уже импортирован
8. **Enable max_hops=3 in funding trace** — параметр уже поддерживается
9. **Параллелизм asyncio.gather()** для независимых API вызовов в INITIAL

### PHASE 14B: FREE APIS (0 денег)

10. **Jito bundle detection** — 8 tip accounts, RPC getBlock, <1s
11. **Metaplex metadata deep check** — isMutable, URI type, name spoofing (Helius DAS getAsset)
12. **RugCheck Insider Networks** — `/tokens/{id}/insiders/graph`
13. **Solana Tracker Risk API** — sniper count, insider %, risk score
14. **Jupiter VERIFY status** — verified/strict/community badge
15. **Bitquery Pump.fun lifecycle** — bonding curve tracking (GraphQL free tier)

### PHASE 14C: PAID APIS ($98/мес)

16. **Helius Developer upgrade** ($49/мес) — 10x credits для fee_payer_cluster + convergence + wallet_age
17. **Moralis Holder Distribution** ($49/мес) — whale/shark/dolphin categorization, acquisition method

### PHASE 14D: ARCHITECTURE (после 14A-C)

18. **asyncio.gather() parallel enrichment** — сократить INITIAL с 5s до ~1.5s
19. **Split INITIAL на FAST_CHECK (+10s) и FULL_ENRICH (+45s)**
20. **WebSocket pool monitoring** — real-time LP changes

---

## Ожидаемый pipeline после всех фиксов

```
Token discovered (PumpPortal WS)
    ↓ +5s
PRE_SCAN (~600ms):
    ├── parse_mint_account() — mint/freeze authority, Token2022
    ├── jupiter.simulate_sell() — sellability
    └── Jito bundle detection — self-snipe check [NEW]
    ↓
    ├── REJECTED → log + skip
    └── PASSED → schedule FAST_CHECK at +10s
         ↓ +10s
    FAST_CHECK (~1s) [NEW]:
        ├── Metaplex metadata (isMutable, URI, name spoof)
        ├── Jupiter VERIFY status
        ├── Solana Tracker Risk (sniper count)
        ├── metadata_scorer (socials check)
        └── Quick reject if 3+ red flags
         ↓ +45s
    INITIAL (full enrichment, ~1.5s with parallel):
        ├── asyncio.gather():
        │   ├── GMGN info + security
        │   ├── Birdeye metadata
        │   ├── DexScreener data
        │   ├── GoPlus security
        │   ├── Rugcheck + insider networks [NEW]
        │   ├── Jupiter price
        │   ├── Raydium LP
        │   └── Solana Tracker full report [NEW]
        ├── Sequential (depends on above):
        │   ├── Pump.fun creator history
        │   ├── Bundled buy detection (Helius)
        │   ├── Fee payer clustering (Helius) [WIRED]
        │   ├── Convergence analysis (Helius) [WIRED]
        │   ├── Wallet age check (Helius) [WIRED]
        │   ├── Wallet cluster check [WIRED]
        │   ├── 3-hop funding trace (was 1-hop) [UPGRADED]
        │   ├── Moralis holder distribution [NEW]
        │   ├── Rugcheck risk parsing [WIRED]
        │   └── Price consistency validation [WIRED]
        ├── Scoring (36 rules, ALL active):
        │   ├── R1-R28 (existing, now all working)
        │   └── R29-R36 (Phase 13, now wired)
        └── Signal + Alert
```

## Ключевой вывод

**Проблема не в количестве модулей — у нас их достаточно. Проблема в том что они не подключены.** Из 36 signal rules работают только 24. Из ~15 scoring параметров Phase 12-13 подключено только 6. Критический баг с `jupiter_price_val` ломает весь pipeline.

Перед добавлением ЧЕГО-ЛИБО нового нужно:
1. Починить jupiter_price_val bug
2. Подключить ВСЕ существующие модули к worker.py
3. Убедиться что 36 правил реально работают
4. Только потом — новые API и модули
