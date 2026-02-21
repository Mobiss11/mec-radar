# Phase 12: Early Detection Pipeline — PRE_SCAN + Free APIs

## Context

Phase 11 complete: 258 tests, 22 signal rules, full spectrum analysis. Deep audit identified critical gap: **первые минуты жизни токена** — система ждёт 45s (INITIAL stage) и тратит ~5s на full enrichment, прежде чем отсеивать скам. 50%+ токенов — очевидный скам, который можно отсечь за 600ms без дорогих API вызовов.

**Цель**: Двухфазная INITIAL архитектура (PRE_SCAN → INITIAL). Мгновенный reject очевидного скама через бесплатные on-chain проверки. Новые бесплатные API (GoPlus, Jupiter Quote, Raydium, Pump.fun). Платные API — заглушки в конце.

**Бюджет API**: Все бесплатные API реализуем полностью. Платные — только stubs.

---

## Чеклист

### 1. Mint Parser — Direct On-Chain Token Inspection (~100ms)
**ROI: CRITICAL** — мгновенная проверка Token2022 extensions без API.
**Файл**: `src/parsers/mint_parser.py` (новый)

- [ ] `parse_mint_account(rpc_url, mint_address) → MintInfo`
  - RPC call: `getAccountInfo(mint)` с `encoding=base64`
  - Decode SPL Token / Token2022 mint layout (82 bytes standard, >82 = Token2022)
  - `MintInfo`: supply, decimals, mint_authority (null = renounced), freeze_authority (null = safe), is_token2022, extensions
  - Token2022 extensions detection: permanentDelegate, transferHook, transferFee, nonTransferable, defaultAccountState
  - Каждое опасное extension → risk flag
- [ ] Pure function, единственная зависимость — httpx для RPC
- [ ] Tests: 4 теста (standard token, token2022 with extensions, renounced mint, frozen)

### 2. Jupiter Sell Simulation — Instant Honeypot Check (~500ms)
**ROI: CRITICAL** — единственный способ проверить sellability за < 1s.
**Файл**: Расширение `src/parsers/jupiter/client.py`

- [ ] Новый метод `simulate_sell(mint, amount_tokens=1000) → SellSimResult`
  - GET `https://quote-api.jup.ag/v6/quote?inputMint={mint}&outputMint=So11...&amount={amount}&slippageBps=5000`
  - Если 200 + valid outAmount → sellable
  - Если error "No route found" → potential honeypot
  - Если error "Token account not found" → too early, retry later
  - `SellSimResult`: sellable (bool), output_amount (Decimal|None), price_impact_pct (float|None), error (str|None)
- [ ] Новая модель в `src/parsers/jupiter/models.py`: `SellSimResult`
- [ ] Rate limit: тот же JupiterClient, 10 RPS
- [ ] Tests: 4 теста (sellable, no route, timeout, high price impact)

### 3. PRE_SCAN Stage — Two-Phase INITIAL Architecture
**ROI: CRITICAL** — reject 50%+ скама за <2s, экономия API.
**Файлы**: `src/parsers/enrichment_types.py` (модификация), `src/parsers/worker.py` (модификация)

- [ ] `enrichment_types.py`:
  - Добавить `PRE_SCAN = -1` в `EnrichmentStage` (перед INITIAL=0)
  - Новый `StageConfig` для PRE_SCAN: `offset_sec=5` (run 5s after discovery)
  - Новые флаги в `StageConfig`: `run_prescan: bool = False`
  - `STAGE_SCHEDULE[PRE_SCAN]`: `offset_sec=5, run_prescan=True`
  - `NEXT_STAGE[PRE_SCAN] = INITIAL`
  - `EnrichmentTask`: + `instant_rejected: bool = False`

- [ ] `worker.py` — новая функция `_run_prescan()`:
  - Параллельно: `parse_mint_account()` + `jupiter.simulate_sell()`
  - Hard reject conditions (instant_rejected=True, не создаём INITIAL task):
    - mint_authority active + freeze_authority active
    - Token2022 с permanentDelegate или nonTransferable
    - Jupiter sell: "No route found" + mint_authority active
  - Soft flags (передаём в INITIAL для учёта в scoring):
    - Token2022 с transferFee → `extra_risk += 10`
    - Jupiter high price impact (>30%) → `extra_risk += 5`
    - freeze_authority active alone → `extra_risk += 15`
  - Логирование: `[PRE_SCAN] {mint} REJECTED: {reason}` или `[PRE_SCAN] {mint} PASSED (flags: ...)`

- [ ] `on_new_token()` scheduler change:
  - `scheduled_at=now + 5` (was now+30), `stage=PRE_SCAN`
  - INITIAL stage delay remains +45s from discovery (не от PRE_SCAN)

- [ ] Tests: 6 тестов (reject mint+freeze, reject permanentDelegate, pass clean token, soft flags, INITIAL scheduling after pass, metrics)

### 4. GoPlus Security API — Structured Token Security
**ROI: HIGH** — бесплатный (30 req/min), структурированные security данные.

- [ ] Новый файл: `src/parsers/goplus/__init__.py`
- [ ] Новый файл: `src/parsers/goplus/models.py`
  - `GoPlusReport`: is_open_source, is_proxy, is_mintable, owner_can_change_balance, can_take_back_ownership, is_honeypot, buy_tax, sell_tax, holder_count, lp_holder_count, is_true_token, is_airdrop_scam, transfer_pausable, trading_cooldown
- [ ] Новый файл: `src/parsers/goplus/client.py`
  - GET `https://api.gopluslabs.io/api/v1/solana/token_security/{mint}`
  - `GoPlusClient(max_rps=0.5)` — бесплатный tier, 30 req/min
  - Retry + circuit breaker
  - `get_token_security(mint) → GoPlusReport | None`
- [ ] Интеграция в `worker.py`:
  - Stage INITIAL: параллельно с GMGN security `if settings.enable_goplus`
  - Merge results: GoPlus is_honeypot overrides GMGN if True
  - GoPlus buy_tax/sell_tax cross-validates GMGN
- [ ] `config/settings.py`: `enable_goplus: bool = True`
- [ ] `scoring.py` + `scoring_v3.py`: GoPlus data feeds into existing security scoring (no new parameter — enhances existing security)
- [ ] Tests: 5 тестов (client mock, honeypot detection, tax parsing, merge with gmgn, rate limit)

### 5. Pump.fun Creator History — Serial Scammer Detection
**ROI: HIGH** — бесплатный API, мощный сигнал.

- [ ] Новый файл: `src/parsers/pumpfun/__init__.py`
- [ ] Новый файл: `src/parsers/pumpfun/models.py`
  - `PumpfunCreatorHistory`: total_tokens, recent_tokens (list), dead_token_count, avg_lifespan_minutes
  - `PumpfunToken`: mint, name, symbol, created_timestamp, market_cap, is_dead (mcap < $100 or age > 24h with mcap < $1000)
- [ ] Новый файл: `src/parsers/pumpfun/client.py`
  - GET `https://frontend-api-v3.pump.fun/coins/user-created-coins/{wallet}?limit=50`
  - `PumpfunClient(max_rps=2.0)` — без ключа
  - `get_creator_history(wallet) → PumpfunCreatorHistory | None`
  - Логика: fetch all tokens by creator → count dead (mcap < $100 or mcap=0)
  - dead_count >= 10 → serial scammer (risk +40)
  - dead_count >= 5 → suspicious (risk +20)
  - dead_count >= 3 → moderate (risk +10)
- [ ] Интеграция в `worker.py`:
  - Stage INITIAL: после assess_creator_risk, если token.source = "pumpportal"
  - Результат → boost к creator risk_score
- [ ] `config/settings.py`: `enable_pumpfun_history: bool = True`
- [ ] Модель `CreatorProfile`: + `pumpfun_dead_tokens: Mapped[int | None]`
- [ ] Tests: 4 теста (serial scammer, clean creator, no data, integration with creator_risk)

### 6. Bundled Buy Detection — Coordinated Sybil Attack
**ROI: HIGH** — uses existing Helius client.

- [ ] Новый файл: `src/parsers/bundled_buy_detector.py`
  - `detect_bundled_buys(helius, token_address, creator_address) → BundledBuyResult`
  - Логика:
    1. `helius.get_signatures_for_address(token_address, limit=30)` → first block txs
    2. Filter: txs in same slot as creation tx (or slot+1)
    3. For each buyer in first block: check if funded by creator (via native_transfers)
    4. `BundledBuyResult`: first_block_buyers (int), funded_by_creator (int), bundled_pct (float), is_bundled (bool)
    5. bundled_pct > 50% → is_bundled=True, risk +30
    6. bundled_pct > 25% → risk +15
  - `is_bundled` = dev created token AND multiple wallets bought in same block with common funder
- [ ] Интеграция в `worker.py`:
  - Stage INITIAL: if helius enabled + token has creator_address
- [ ] `src/models/token.py` — `TokenSecurity`: + `bundled_buy_detected: Mapped[bool | None]`
- [ ] Tests: 5 тестов (bundled detected, no bundle, no helius, creator only, mock txs)

### 7. Raydium LP Verification
**ROI: MEDIUM-HIGH** — бесплатный, проверить LP burned/held on-chain.

- [ ] Новый файл: `src/parsers/raydium/__init__.py`
- [ ] Новый файл: `src/parsers/raydium/models.py`
  - `RaydiumPoolInfo`: pool_id, base_mint, quote_mint, lp_mint, lp_supply, tvl, burn_percent
- [ ] Новый файл: `src/parsers/raydium/client.py`
  - GET `https://api-v3.raydium.io/pools/info/mint?mint1={mint}&poolType=standard&poolSortField=liquidity&sortType=desc&pageSize=1`
  - `RaydiumClient(max_rps=5.0)`
  - `get_pool_info(mint) → RaydiumPoolInfo | None`
  - Parse: pool TVL, LP mint, check LP burn % (burn address = "1111111111111111111111111111111")
- [ ] Интеграция в `worker.py`:
  - Stage INITIAL: after security, fetch Raydium LP verification
  - Cross-validate with GMGN lp_burned flag
- [ ] `config/settings.py`: `enable_raydium_lp: bool = True`
- [ ] `src/models/token.py` — `TokenSecurity`: + `lp_burned_pct_raydium: Mapped[Decimal | None]`
- [ ] Tests: 4 теста (LP burned, LP held, no pool, cross-validation)

### 8. New Signal Rules (R23-R28)
**ROI: HIGH** — новые правила из новых данных.

Модификация `src/parsers/signals.py`:

- [ ] R23: `token2022_danger` (-3) — Token2022 with dangerous extensions (permanentDelegate, nonTransferable)
- [ ] R24: `sell_sim_failed` (-5) — Jupiter sell simulation failed (no route)
- [ ] R25: `bundled_buy` (-3) — first-block bundled buys > 50%
- [ ] R26: `serial_deployer` (-3) — creator has 5+ dead tokens on pump.fun
- [ ] R27: `lp_not_burned` (-1) — LP not burned AND not locked (Raydium verified)
- [ ] R28: `goplus_honeypot` (-10) — GoPlus confirms honeypot (cross-validates existing R10)

Новые параметры для `evaluate_signals()`:
- [ ] `mint_info: MintInfo | None = None`
- [ ] `sell_sim_result: SellSimResult | None = None`
- [ ] `bundled_buy_detected: bool = False`
- [ ] `pumpfun_dead_tokens: int | None = None`
- [ ] `raydium_lp_burned: bool | None = None`
- [ ] `goplus_is_honeypot: bool | None = None`

- [ ] Tests: 6 тестов (по 1 на правило)

### 9. Scoring Integration + Schema Migration
**ROI: HIGH** — подключить все новые данные к scoring.

- [ ] `scoring.py` + `scoring_v3.py`:
  - `mint_risk_boost: int = 0` — из PRE_SCAN (Token2022 extensions, freeze authority)
  - `sell_sim_failed: bool = False` → -20 pts (hard penalty)
  - `bundled_buy_detected: bool = False` → -10 pts
  - `pumpfun_dead_tokens: int | None = None` → -5 to -15 pts based on count
  - `goplus_is_honeypot: bool | None = None` → instant 0 (same as existing honeypot)
  - `raydium_lp_burned: bool | None = None` → +3 pts if burned, -2 if not

- [ ] `src/parsers/persistence.py`:
  - Update `save_token_security()` to save new fields
  - New: `save_goplus_report(session, token_id, report)`

- [ ] Alembic migration `phase12_prescan`:
  - `token_security`: + `bundled_buy_detected BOOLEAN`, + `lp_burned_pct_raydium NUMERIC`, + `goplus_score TEXT`
  - `creator_profiles`: + `pumpfun_dead_tokens INTEGER`

- [ ] `worker.py`: передать все новые параметры в `compute_score()`, `compute_score_v3()`, `evaluate_signals()`
- [ ] Tests: 4 теста (scoring penalties, migration, persistence)

### 10. Paid API Placeholders (Stubs Only)
**ROI: LOW** — заглушки для будущих платных API.

- [ ] Новый файл: `src/parsers/solscan/__init__.py` — stub
- [ ] Новый файл: `src/parsers/solscan/client.py` — `SolscanClient` с NotImplementedError
- [ ] Новый файл: `src/parsers/dune/__init__.py` — stub
- [ ] Новый файл: `src/parsers/dune/client.py` — `DuneClient` с NotImplementedError
- [ ] Новый файл: `src/parsers/twitter/__init__.py` — stub
- [ ] Новый файл: `src/parsers/twitter/client.py` — `TwitterClient` с NotImplementedError
- [ ] `config/settings.py`: `solscan_api_key: str = ""`, `dune_api_key: str = ""`, `twitter_bearer_token: str = ""`
- [ ] Tests: 3 теста (import stubs, NotImplementedError raised)

---

## Ключевые файлы для модификации

| Файл | Текущее сост. | Что меняется |
|------|--------------|-------------|
| `src/parsers/enrichment_types.py` (131 lines) | 11 stages INITIAL→HOUR_24 | + PRE_SCAN=-1, + run_prescan flag, + instant_rejected field |
| `src/parsers/worker.py` (~1350 lines) | INITIAL=first stage | + _run_prescan(), + PRE_SCAN dispatch, + GoPlus/Pumpfun/Raydium/Bundled integration |
| `src/parsers/signals.py` (288 lines) | R1-R22 | + R23-R28, + new params |
| `src/parsers/scoring.py` (~250 lines) | Phase 11 params | + mint_risk_boost, sell_sim_failed, bundled, pumpfun, goplus, raydium |
| `src/parsers/scoring_v3.py` (~250 lines) | Same | Same additions |
| `config/settings.py` (83 lines) | Phase 11 flags | + enable_goplus, enable_pumpfun_history, enable_raydium_lp, + paid API keys |
| `src/models/token.py` (247 lines) | Phase 11 fields | + bundled_buy_detected, lp_burned_pct_raydium, goplus_score, pumpfun_dead_tokens |
| `src/parsers/persistence.py` | Phase 11 | + save_goplus_report, update save_token_security |
| `src/parsers/jupiter/client.py` (117 lines) | Price API only | + simulate_sell() method |
| `src/parsers/jupiter/models.py` | JupiterPrice only | + SellSimResult |

## Новые файлы (16)

| Файл | Назначение |
|------|-----------|
| `src/parsers/mint_parser.py` | Direct on-chain mint account parsing |
| `src/parsers/goplus/__init__.py` | GoPlus package |
| `src/parsers/goplus/client.py` | GoPlus Security API client |
| `src/parsers/goplus/models.py` | GoPlus data models |
| `src/parsers/pumpfun/__init__.py` | Pump.fun package |
| `src/parsers/pumpfun/client.py` | Pump.fun creator history client |
| `src/parsers/pumpfun/models.py` | Pump.fun data models |
| `src/parsers/bundled_buy_detector.py` | First-block bundled buy detection |
| `src/parsers/raydium/__init__.py` | Raydium package |
| `src/parsers/raydium/client.py` | Raydium LP verification client |
| `src/parsers/raydium/models.py` | Raydium data models |
| `src/parsers/solscan/client.py` | Stub for paid Solscan API |
| `src/parsers/solscan/__init__.py` | Stub package |
| `src/parsers/dune/client.py` | Stub for paid Dune API |
| `src/parsers/dune/__init__.py` | Stub package |
| `src/parsers/twitter/client.py` | Stub for paid Twitter API |
| `src/parsers/twitter/__init__.py` | Stub package |

## Новые тест-файлы (9)

| Файл | Тесты |
|------|-------|
| `tests/test_parsers/test_mint_parser.py` | 4 |
| `tests/test_parsers/test_jupiter_sell_sim.py` | 4 |
| `tests/test_parsers/test_prescan.py` | 6 |
| `tests/test_parsers/test_goplus.py` | 5 |
| `tests/test_parsers/test_pumpfun.py` | 4 |
| `tests/test_parsers/test_bundled_buy.py` | 5 |
| `tests/test_parsers/test_raydium.py` | 4 |
| `tests/test_parsers/test_signals_phase12.py` | 6 |
| `tests/test_parsers/test_scoring_phase12.py` | 4 |

## Порядок реализации

```
1. Mint Parser         (standalone, 0 deps)              — Item 1
2. Jupiter Sell Sim    (extends existing client)          — Item 2
3. PRE_SCAN Stage      (depends on 1+2)                  — Item 3
4. GoPlus Security     (standalone API)                   — Item 4
5. Pump.fun Creator    (standalone API)                   — Item 5
6. Raydium LP          (standalone API)                   — Item 7
7. Bundled Buy         (uses existing Helius)             — Item 6
8. Signal Rules R23-28 (depends on 1-7)                   — Item 8
9. Scoring + Migration (depends on all above)             — Item 9
10. Paid API Stubs     (standalone, no deps)              — Item 10
```

## API Requirements

| API | Pricing | Rate Limit | Needed For |
|-----|---------|-----------|-----------|
| **Solana RPC** (getAccountInfo) | FREE (existing) | N/A | Mint parsing |
| **Jupiter Quote v6** | FREE (no key) | 10 RPS | Sell simulation |
| **GoPlus** | FREE (no key) | 30 req/min | Token security |
| **Pump.fun Frontend API** | FREE (no key) | ~2 RPS | Creator history |
| **Raydium v3** | FREE (no key) | ~5 RPS | LP verification |
| **Helius** (already have key) | Existing plan | 10 RPS | Bundled buy detection |

**Итого доп. расходов: $0** — все API бесплатные.

## Верификация

1. `poetry run pytest tests/ -x -v` — 258 старых + ~42 новых ≈ 300 тестов
2. PRE_SCAN: `[PRE_SCAN] {mint} REJECTED: mint_authority+freeze_authority` в логах
3. Jupiter sell sim: `[JUPITER] Sell sim for {mint}: sellable=True, impact=X%`
4. GoPlus: `[GOPLUS] Security for {mint}: honeypot=False, sell_tax=0%`
5. Pump.fun: `[PUMPFUN] Creator {addr}: 12 tokens, 10 dead → serial_scammer`
6. Raydium: `[RAYDIUM] LP for {mint}: burned=85%, TVL=$50K`
7. Bundled: `[BUNDLED] {mint}: 5/8 first-block buyers funded by creator`
8. Signals R23-R28: `[SIGNAL] R24 sell_sim_failed fired (-5)`
9. Scoring: `mint_risk_boost`, `sell_sim_failed` видны в scoring breakdown

## Expected Pipeline Flow After Phase 12

```
Token discovered (PumpPortal WS)
    ↓ +5s
PRE_SCAN (~600ms):
    ├── parse_mint_account() — mint/freeze authority, Token2022
    └── jupiter.simulate_sell() — sellability check
    ↓
    ├── REJECTED → log + skip (no INITIAL task created)
    └── PASSED → schedule INITIAL at +45s
         ↓ +45s
    INITIAL (full enrichment, ~5s):
        ├── GMGN/Birdeye info + security
        ├── GoPlus security (parallel)
        ├── Pump.fun creator history
        ├── Raydium LP verification
        ├── Bundled buy detection (Helius)
        ├── Rugcheck + Jupiter price
        ├── Top holders + smart money
        └── Scoring + Signals → Alert
```
