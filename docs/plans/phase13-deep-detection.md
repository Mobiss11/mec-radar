# Phase 13: Deep Detection — On-Chain Analysis + Unused Data

**Дата**: 2026-02-19
**Статус**: Phase 12 complete (334 теста, 28 правил)
**Цель**: Фундаментально усилить детекцию паттернов в первые минуты жизни токена

---

## Бюджет

### Текущие расходы
| Сервис | Стоимость | Что даёт |
|--------|-----------|----------|
| Birdeye Lite | $39/мес | 1.5M CU, 15 rps, token/security/OHLCV/trades |
| Helius Developer | $49/мес | 10M credits, 50 RPS, RPC+DAS+parsing |
| **Итого** | **$88/мес** | |

### Все бесплатные API (уже используем или добавим)
| Сервис | Стоимость | Rate Limit | Статус |
|--------|-----------|-----------|--------|
| PumpPortal WS | $0 | Unlimited WS | ✅ Работает |
| Meteora DBC WS | $0 | Unlimited WS | ✅ Работает |
| GMGN (token/security/holders) | $0 | ~2 rps | ✅ Работает |
| DexScreener | $0 | 300 rps pairs, 60 rps profiles | ✅ Работает |
| Jupiter Quote v6 | $0 | 10 rps | ✅ Работает (price + sell sim) |
| Rugcheck | $0 | ~5 rps | ✅ Работает |
| GoPlus Security v1 | $0 | 30 req/min | ✅ Работает |
| Pump.fun Frontend API | $0 | ~2 rps | ✅ Работает (creator history) |
| Raydium v3 | $0 | ~5 rps | ✅ Работает (LP verification) |
| Solana RPC (via Helius) | $0 | Входит в Helius | ✅ Работает (mint parsing) |
| **SolSniffer Free** | **$0** | **100 calls/мес** | 🆕 Phase 13 |

### SolSniffer — анализ целесообразности
| План | Цена | Calls/мес | Цена за call |
|------|------|-----------|-------------|
| Free | $0 | 100 | $0 |
| Starter | $47/мес | 5,000 | $0.0094 |
| Boost | $197/мес | 30,000 | $0.0066 |
| Pro | $997/мес | 230,000 | $0.0043 |

**Вывод**: Free tier (100 calls) — хватит для deep audit подозрительных токенов.
При >100 токенов/день — нужен Starter ($47). Но у нас GoPlus бесплатный с 30 req/min — он покрывает 95% того же.
**Решение**: Free tier SolSniffer для кросс-валидации top подозрительных.

### Bubblemaps Data API [Beta]
- Поддерживает Solana
- Даёт: top-80 holders, clusters, relationships (SOL transfers между holders), decentralization_score
- Pricing: B2B, нужно запрашивать API key (contact@bubblemaps.io)
- **Решение**: Запросить бесплатный Beta доступ. Если получим — мощнейший сигнал для holder clustering.

### Оставшийся бюджет: $500 - $88 = $412/мес
**Для будущего обсуждения** (не в Phase 13):
| Сервис | Цена | Когда нужен |
|--------|------|------------|
| SolSniffer Starter | $47/мес | При >100 подозрительных/день |
| Birdeye Starter | $99/мес | При >1.5M CU/мес |
| Twitter/X (RapidAPI) | ~$25/мес | Социальный анализ (финальная фаза) |
| Telegram мониторинг | ~$25/мес | Community анализ (финальная фаза) |

---

## Чеклист Phase 13

### ГРУППА A: On-Chain Improvements ($0, максимальный ROI)

#### 1. Fee Payer Clustering — Sybil Attack Detection
**ROI: CRITICAL** — 10 "разных" покупателей с одним fee payer = 1 человек
**Файл**: `src/parsers/fee_payer_cluster.py` (новый)

- [ ] `cluster_by_fee_payer(helius, token_address) → FeePayerClusterResult`
  - Из bundled_buy_detector уже есть first_block_txs
  - Извлечь fee_payer из каждой TX (уже в `fee_payer` field Helius parsed tx)
  - Группировать покупателей по fee_payer → clusters
  - `FeePayerClusterResult`: clusters (list), largest_cluster_size, unique_payers, sybil_score
  - sybil_score = 1 - (unique_payers / total_buyers)
  - sybil_score > 0.5 → risk +25
  - sybil_score > 0.3 → risk +15
- [ ] Интеграция в worker.py: Stage INITIAL, после bundled_buy
- [ ] `src/models/token.py` — `TokenSecurity`: + `fee_payer_sybil_score: Mapped[float | None]`
- [ ] Tests: 5 тестов (single payer sybil, multiple payers clean, partial overlap, no helius, edge cases)

#### 2. Multi-hop Funding Trace (3 hops вместо 1)
**ROI: CRITICAL** — scammers используют 2-3 промежуточных кошелька
**Файл**: модификация `src/parsers/funding_trace.py`

- [ ] Расширить `trace_creator_funding()`:
  - Текущий: creator ← funder (1 hop)
  - Новый: creator ← hop1 ← hop2 ← hop3 (3 hops)
  - Каждый hop: `helius.get_signatures_for_address(wallet, limit=10)` → найти входящие SOL transfers
  - Если на любом hop найден known rugger → risk = 90
  - Если все hops < 1h old → risk = 70 (fresh chain = coordinated)
  - Если funding chain > 2 hops через <24h wallets → risk = 60
- [ ] `FundingTraceResult`: + `hops: list[FundingHop]`, `chain_depth: int`, `chain_age_hours: float`
- [ ] `FundingHop`: address, age_hours, tx_count, risk_score
- [ ] Ограничение: max 2-3 доп. RPC calls per creator (~$0.003)
- [ ] Tests: 4 теста (3-hop rugger chain, 1-hop still works, deep fresh chain, no trace)

#### 3. Convergence Analysis — Token Destination Tracking
**ROI: HIGH** — если все first-block buyers шлют токены на 1 кошелёк = dev consolidation
**Файл**: `src/parsers/convergence_analyzer.py` (новый)

- [ ] `analyze_convergence(helius, token_mint, buyers: list[str]) → ConvergenceResult`
  - Для каждого buyer из first_block: проверить исходящие token transfers за первые 10 минут
  - Если >50% buyers → 1 destination = convergence detected
  - `ConvergenceResult`: converging (bool), convergence_pct (float), main_destination (str|None), destinations (dict)
  - converging = True → risk +35 (guaranteed rug signal)
- [ ] Интеграция в worker.py: Stage INITIAL, после bundled_buy (используем buyers из BundledBuyResult)
- [ ] Tests: 3 теста (convergence detected, no convergence, partial convergence)

#### 4. Token Metadata Scoring — Use Existing Unused Data
**ROI: HIGH** — данные УЖЕ в БД, не используются
**Файл**: модификация `src/parsers/scoring.py` + `scoring_v3.py`

- [ ] Новый scoring component: `metadata_score`
  - `description` present + len > 20 → +2 pts
  - `website` present + valid URL → +3 pts
  - `twitter` present → +2 pts
  - `telegram` present → +2 pts
  - No socials at all → -3 pts
  - description = generic/copypasted (contains "token", "coin" only) → -1 pt
  - Max: +9 pts, Min: -4 pts
- [ ] Добавить в `evaluate_signals()`: R29 `no_socials` (-1) — ни одного social link
- [ ] Tests: 3 теста (full socials, no socials, partial)

#### 5. Holder PnL Analysis — Wash Trading Detection
**ROI: HIGH** — pnl данные в TokenTopHolder, НИКОГДА не используются
**Файл**: `src/parsers/holder_pnl_analyzer.py` (новый)

- [ ] `analyze_holder_pnl(holders: list[TokenTopHolder]) → HolderPnLResult`
  - Считаем: holders_in_loss (pnl < 0), holders_in_profit (pnl > 0)
  - loss_ratio = holders_in_loss / total_with_pnl
  - Если loss_ratio > 0.8 + price rising → wash trading signal
  - Если loss_ratio > 0.9 → massive dump incoming
  - `HolderPnLResult`: loss_ratio (float), avg_pnl (float), wash_trading_suspected (bool), dump_risk (bool)
- [ ] Добавить в `evaluate_signals()`: R30 `wash_trading_pnl` (-3) — >80% holders в убытке + цена растёт
- [ ] Интеграция в scoring: wash_trading_suspected → -8 pts
- [ ] Tests: 3 теста (wash trading detected, healthy distribution, no pnl data)

#### 6. GoPlus Full Report Parsing — Use All Fields
**ROI: MEDIUM-HIGH** — берём только is_honeypot, а GoPlus даёт 15+ полей
**Файл**: модификация `src/parsers/goplus/client.py`

- [ ] Расширить парсинг GoPlusReport:
  - `is_open_source` → bonus if True (+1 pt)
  - `is_proxy` → warning if True (-2 pts)
  - `owner_can_change_balance` → critical if True (-5 pts)
  - `can_take_back_ownership` → critical if True (-5 pts)
  - `transfer_pausable` → warning if True (-3 pts)
  - `trading_cooldown` → warning if True (-2 pts)
  - `is_airdrop_scam` → critical if True (-8 pts)
  - `buy_tax` / `sell_tax` → cross-validate with GMGN
- [ ] Добавить R31 `goplus_critical_risk` (-5) — any of: owner_can_change_balance, can_take_back_ownership, is_airdrop_scam
- [ ] Tests: 4 теста (critical flags, clean token, partial data, tax cross-validation)

#### 7. Rugcheck Risks Parsing — Currently Stored as Unparsed String
**ROI: MEDIUM** — rugcheck_risks в БД = строка, никогда не парсится
**Файл**: модификация `src/parsers/rugcheck/` + scoring

- [ ] Парсить `rugcheck_risks` string → structured risk list
  - Каждый risk: name, level (danger/warning/info), description
  - Count danger-level risks
  - danger_count >= 3 → risk +20
  - danger_count >= 2 → risk +10
  - Check specific: "Mutable metadata", "Freeze authority", "Low liquidity"
- [ ] Добавить R32 `rugcheck_multi_danger` (-3) — ≥3 danger-level risks
- [ ] Tests: 3 теста (multiple dangers, clean, partial)

### ГРУППА B: Бесплатные API Improvements

#### 8. SolSniffer Free Tier — Cross-Validation
**ROI: MEDIUM** — 100 calls/мес, только для top подозрительных
**Файл**: `src/parsers/solsniffer/` (новый пакет)

- [ ] `src/parsers/solsniffer/__init__.py`
- [ ] `src/parsers/solsniffer/models.py`
  - `SolSnifferReport`: snifscore (0-100), security_features (dict), liquidity_pool (dict), token_data (dict), top_holders (list)
- [ ] `src/parsers/solsniffer/client.py`
  - GET `https://solsniffer.com/api/v2/token/{mint}` (предположительно, нужно проверить после регистрации)
  - `SolSnifferClient(api_key, max_rps=0.1)` — экономим 100 calls/мес
  - Вызывать ТОЛЬКО для токенов с score 30-60 (серая зона, нужна кросс-валидация)
- [ ] Интеграция в worker.py: Stage MIN_2 (не INITIAL — экономия calls)
  - Условие: if score in [30, 60] AND settings.enable_solsniffer AND monthly_calls < 100
- [ ] `config/settings.py`: `enable_solsniffer: bool = False`, `solsniffer_api_key: str = ""`
- [ ] Tests: 4 теста (report parsing, selective calling, rate limit, monthly cap)

#### 9. Bubblemaps Holder Clustering (если получим API key)
**ROI: HIGH** — уникальные данные, нет аналогов
**Файл**: `src/parsers/bubblemaps/` (новый пакет)

- [ ] `src/parsers/bubblemaps/__init__.py`
- [ ] `src/parsers/bubblemaps/models.py`
  - `BubblemapsReport`: decentralization_score (float), clusters (list[Cluster]), top_holders (list[Holder]), relationships (list[Relationship])
  - `Cluster`: share (float), amount (int), holder_count (int), holders (list[str])
  - `Relationship`: from_address, to_address, total_value, total_transfers
- [ ] `src/parsers/bubblemaps/client.py`
  - GET `https://api.bubblemaps.io/maps/solana/{mint}?return_clusters=true&return_decentralization_score=true&return_relationships=true&return_nodes=true`
  - Header: `X-ApiKey: {key}`
  - `BubblemapsClient(api_key, max_rps=1.0)`
  - `get_map_data(mint) → BubblemapsReport | None`
- [ ] Scoring integration:
  - decentralization_score < 0.3 → risk +15 (highly concentrated)
  - largest_cluster.share > 0.4 → risk +20 (coordinated holding)
  - relationships with transfers > $10K between top holders → risk +10
- [ ] Добавить R33 `low_decentralization` (-3) — decentralization_score < 0.3
- [ ] `config/settings.py`: `enable_bubblemaps: bool = False`, `bubblemaps_api_key: str = ""`
- [ ] Tests: 5 тестов (high concentration, decentralized, cluster analysis, relationships, no key)

### ГРУППА C: Architecture + New Rules

#### 10. Sub-second PRE_SCAN (T+100ms → T+500ms)
**ROI: MEDIUM** — reject очевидный скам на 4.5s быстрее
**Файл**: модификация `src/parsers/enrichment_types.py` + `worker.py`

- [ ] Трёхфазный PRE_SCAN:
  ```
  T+100ms:  INSTANT_CHECK (mint authority, freeze, Token2022)
            → Hard reject: mint+freeze active, permanentDelegate
  T+250ms:  CREATOR_CHECK (DB lookup + 1-hop funding)
            → Hard reject: creator rug_rate > 75% + 10+ launches
  T+500ms:  FIRST_BLOCK_CHECK (bundled buys check if helius available)
            → Soft flag: bundled > 50%
  ```
- [ ] `EnrichmentStage`: PRE_SCAN split into 3 sub-phases (или оставить 1 stage но с early exits)
- [ ] Метрика: `prescan_duration_ms` в логах
- [ ] Tests: 4 теста (instant reject, creator reject, first block flag, timing)

#### 11. New Signal Rules R29-R36
**Файл**: модификация `src/parsers/signals.py`

- [ ] R29: `no_socials` (-1) — ни одного social link (из п.4)
- [ ] R30: `wash_trading_pnl` (-3) — >80% holders в убытке + цена растёт (из п.5)
- [ ] R31: `goplus_critical_risk` (-5) — owner_can_change_balance / can_take_back_ownership / is_airdrop_scam (из п.6)
- [ ] R32: `rugcheck_multi_danger` (-3) — ≥3 danger-level risks (из п.7)
- [ ] R33: `low_decentralization` (-3) — Bubblemaps score < 0.3 (из п.9)
- [ ] R34: `fee_payer_sybil` (-4) — sybil_score > 0.5 (из п.1)
- [ ] R35: `funding_chain_suspicious` (-4) — multi-hop fresh wallet chain (из п.2)
- [ ] R36: `token_convergence` (-5) — >50% buyers converge to 1 wallet (из п.3)
- [ ] Tests: 8 тестов (по 1 на правило)

#### 12. Scoring Integration + DB Migration
**Файл**: `src/parsers/scoring.py`, `scoring_v3.py`, `persistence.py`

- [ ] Новые scoring parameters:
  - `fee_payer_sybil_score: float | None` → -25 pts max
  - `funding_chain_depth: int | None` → -10 to 0 pts
  - `convergence_detected: bool` → -15 pts
  - `metadata_score: int` → -4 to +9 pts
  - `wash_trading_suspected: bool` → -8 pts
  - `goplus_critical_flags: int` → -15 pts max
  - `rugcheck_danger_count: int` → -20 pts max
  - `solsniffer_score: int | None` → cross-validation adjustment
  - `bubblemaps_decentralization: float | None` → -15 pts max
- [ ] Alembic migration `phase13_deep_detection`:
  - `token_security`: + `fee_payer_sybil_score FLOAT`, + `convergence_detected BOOLEAN`, + `solsniffer_score INTEGER`, + `bubblemaps_decentralization FLOAT`, + `rugcheck_dangers INTEGER`
  - `creator_profiles`: + `funding_chain_depth INTEGER`, + `funding_chain_age_hours FLOAT`
- [ ] `persistence.py`: update save functions
- [ ] Tests: 4 теста (scoring all params, migration, partial data, cross-validation)

---

## Порядок реализации

```
ГРУППА A (on-chain, $0):
  1. Fee Payer Clustering       (standalone, uses Helius)       — Item 1  [4h]
  2. Multi-hop Funding (3 hops) (extends funding_trace.py)     — Item 2  [3h]
  3. Convergence Analysis       (standalone, uses Helius)       — Item 3  [3h]
  4. Token Metadata Scoring     (uses existing DB data)         — Item 4  [2h]
  5. Holder PnL Analysis        (uses existing DB data)         — Item 5  [2h]
  6. GoPlus Full Report         (extends goplus client)         — Item 6  [2h]
  7. Rugcheck Risks Parsing     (extends rugcheck parsing)      — Item 7  [2h]

ГРУППА B (free APIs):
  8. SolSniffer Free            (new package)                   — Item 8  [4h]
  9. Bubblemaps Integration     (new package, needs API key)    — Item 9  [4h]

ГРУППА C (architecture + rules):
  10. Sub-second PRE_SCAN       (architecture change)           — Item 10 [5h]
  11. Signal Rules R29-R36      (depends on 1-9)                — Item 11 [3h]
  12. Scoring + Migration       (depends on all)                — Item 12 [3h]
```

## Ожидаемый результат

### До Phase 13 (текущий):
```
Token → PRE_SCAN (T+5s, 2 checks) → INITIAL (T+45s, 12+ sources) → Score
28 правил, 334 теста
```

### После Phase 13:
```
Token → PRE_SCAN (T+100ms, 3 sub-phases) → INITIAL (T+45s, 15+ sources) → Score
36 правил, ~385 тестов

Новые детекции:
- Sybil через fee payer clustering
- Multi-hop funding chains (3 hops, rug rings)
- Token convergence (guaranteed rug signal)
- Wash trading через holder PnL
- GoPlus 15+ security fields (не только honeypot)
- Rugcheck structured risks
- Metadata scoring (socials presence)
- SolSniffer cross-validation (серая зона)
- Bubblemaps decentralization + holder clusters
```

### Дополнительные расходы Phase 13: $0/мес
(SolSniffer Free, Bubblemaps Beta бесплатный, всё остальное on-chain через существующий Helius)
