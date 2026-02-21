# Phase 8: System Completion — 100% Working Pipeline

**Date**: 2026-02-18
**Status**: Complete
**Context**: Phase 7 done (131 тест). Аудит выявил мёртвый код, неиспользуемые данные, потерянные сигналы.
**Result**: 155 тестов проходят. Все данные используются в scoring pipeline.

---

## 8A: Wire Dead Code (мёртвый код который УЖЕ написан но не используется)

- [x] **WIRE-1**: Передать whale_patterns score_impact в scoring (compute_score / compute_score_v3)
- [x] **WIRE-2**: Передать prev_snapshot в evaluate_signals() из worker.py → Rule R9 price_momentum заработает
- [x] **WIRE-3**: Добавить lp_lock_duration_days в scoring (бонус за длинный lock)
- [x] **WIRE-4**: Добавить buy_tax в scoring (penalty как sell_tax)
- [x] **WIRE-5**: Использовать smart wallet win_rate/pnl для взвешивания (не просто count)

## 8B: Use Collected Data (данные которые собираем но не анализируем)

- [x] **USE-1**: OHLCV pattern detection — pump/dump, consolidation, volume spike из token_ohlcv
- [x] **USE-2**: Trade flow analysis — whale buy volume из token_trades
- [x] **USE-3**: Bonding curve progress в scoring (PumpPortal: v_sol_in_bonding_curve)
- [x] **USE-4**: DBC launchpad reputation в scoring (Meteora: dbc_launchpad)

## 8C: Tests + Final Verification

- [x] **TEST-1**: Тесты на whale_patterns в scoring (test_score_whale_dynamics_impact)
- [x] **TEST-2**: Тесты на prev_snapshot / R9 price_momentum (test_price_momentum в test_signals.py)
- [x] **TEST-3**: Тесты на lp_lock + buy_tax + bonding_curve + dbc_launchpad в scoring (6 новых тестов)
- [x] **TEST-4**: Тесты на OHLCV pattern detection (8 тестов) + trade flow (9 тестов)
- [x] **TEST-5**: Полный прогон test suite — **155 тестов проходят, 0 warnings**

---

## Что сделано

### Изменённые файлы
- `src/parsers/scoring.py` — +whale_score_impact, +lp_lock_duration, +buy_tax, +bonding_curve_pct, +dbc_launchpad
- `src/parsers/scoring_v3.py` — аналогичные изменения
- `src/parsers/worker.py` — интеграция OHLCV patterns (шаг 13b), trade flow (шаг 13c), bonding curve + DBC launchpad → compute_score()
- `src/parsers/smart_money.py` — get_wallet_quality() для взвешивания smart wallets
- `src/parsers/persistence.py` — get_prev_snapshot() для R9 price_momentum
- `src/parsers/trade_flow.py` — datetime.utcnow() → datetime.now(UTC)

### Новые файлы
- `src/parsers/ohlcv_patterns.py` — detect_ohlcv_patterns() (pump, dump, volume_spike, consolidation, steady_rise)
- `src/parsers/trade_flow.py` — analyse_trade_flow() (buy/sell volumes, whale activity, unique wallets)
- `tests/test_parsers/test_ohlcv_patterns.py` — 8 тестов
- `tests/test_parsers/test_trade_flow.py` — 9 тестов

### Scoring breakdown (v2, всего до ~163 теоретических очков до clamp):
| Компонент | Очки | Источник |
|-----------|------|----------|
| Liquidity | 0-30 | Birdeye/DexScreener |
| Holders | 0-20 | GMGN |
| Volume/Liquidity ratio | 0-25 | Birdeye |
| Security bonuses | -15 to +25 | Birdeye/GMGN |
| Buy pressure | 0-10 | Birdeye trades |
| Smart money (quality-weighted) | 0-15 | GMGN + Redis win_rate |
| Holder velocity | 0-10 | Snapshot diff |
| Creator risk | 0 to -20 | Creator profiler |
| Whale dynamics | ~-10 to +8 | Top holders diff |
| OHLCV patterns | ~-8 to +14 | Birdeye candles |
| Trade flow | ~-7 to +10 | Birdeye/PP trades |
| LP lock duration | 0-5 | GMGN security |
| Buy tax penalty | 0 to -5 | GMGN security |
| Bonding curve maturity | 0-5 | PumpPortal |
| DBC launchpad rep | -2 to +3 | Meteora DBC |

### Data flow: 100% utilization
```
Token Discovery (PumpPortal/DexScreener/Meteora)
    ↓
Enrichment Pipeline (11 stages: 30s → 24h)
    ├── Birdeye: price, mcap, liquidity, volume, OHLCV, trades, security
    ├── GMGN: token info, security, top holders, smart wallets
    ├── DexScreener: fallback price/volume/liquidity
    ├── Creator Profiler: risk score from history
    └── Redis: smart wallet quality cache
    ↓
Analysis Layer
    ├── Whale Dynamics: holder diff between snapshots
    ├── OHLCV Patterns: pump/dump/spike/consolidation/steady_rise  ← NEW
    ├── Trade Flow: buy/sell volume, whale trades, unique wallets  ← NEW
    └── Holder Velocity: growth rate
    ↓
Scoring (v2/v3 — 15 components, all wired)
    ├── Bonding curve maturity  ← NEW
    └── DBC launchpad reputation  ← NEW
    ↓
Signal Generation (14 rules, all firing)
    ↓
Outcome Tracking (peak ROI, rug detection)
    ↓
Auto-Calibration (threshold optimization)
```

---

## После Phase 8: Что ещё нужно добавить/доработать (backlog)

### Высокий приоритет (влияет на качество сигналов)
1. **Paper trading engine** — автоматическое "входить/выходить" по сигналам, отслеживать P&L. Таблицы trades/positions уже в БД, нужна логика
2. **Telegram bot** — отправка strong_buy/buy сигналов в реальном времени (alert_dispatcher уже есть, нужен TG transport)
3. **A/B тест scoring v2 vs v3** — запустить compare_scoring_models.py на реальных данных после 24-48 часов
4. **Auto-calibrate на реальных данных** — запустить scripts/auto_calibrate.py после накопления outcome данных

### Средний приоритет (расширение охвата)
5. **Social metrics** — Telegram группа (members, activity), Twitter (followers, mentions). Нет стандартных API, нужен либо scraper либо платный сервис
6. **On-chain monitoring** — real-time подписка на buy/sell транзакции через Solana WS. Текущие trade данные зависят от Birdeye polling
7. **Wallet clustering** — связать кошельки одного владельца (same funding source, coordinated trades). Улучшает whale detection

### Низкий приоритет (оптимизации)
8. **Удалить неиспользуемые таблицы** — `wallet_activity` не пишется и не читается. `trades` и `positions` ждут paper trading engine
9. **dev_holds_pct** — колонка в TokenSecurity всегда NULL. Нужно парсить из GMGN если данные есть
10. **Score decay** — текущий score статичен. Можно добавить time-based decay для старых сигналов
11. **ML scoring** — заменить rule-based scoring на модель после накопления outcome данных (нужно минимум 500+ labeled examples)
