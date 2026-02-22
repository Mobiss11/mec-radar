# сcrypto

## О проекте
Async Python platform for detecting, scoring and trading Solana memecoins (paper + real via Jupiter swaps).
Integrates 20+ data sources, runs an 11-stage enrichment pipeline, generates trading signals (54 rules), and auto-trades (paper + real on-chain).

## Стек
- Backend: Python 3.12, asyncio, httpx, websockets, grpcio
- Dashboard API: FastAPI + uvicorn (same-process, shared event loop)
- Dashboard UI: React 18 + TypeScript + Vite + shadcn/ui + TailwindCSS + Recharts
- DB: PostgreSQL (SQLAlchemy 2.0 async), Redis (enrichment queue, cache)
- Bot: aiogram 3.x (Telegram alerts + commands)
- Migrations: Alembic
- Settings: Pydantic v2 (config/settings.py ← .env)

## Architecture Docs
- [Overview & Data Flow](docs/architecture/overview.md)
- [Module Reference](docs/architecture/modules.md)
- [Scoring & Signal System](docs/architecture/scoring.md)
- [Phase 17 Audit Fixes](docs/analysis/phase17-audit-fixes.md) — audit results (2026-02-20)
- Phase 18: GMGN proxy, parallel enrichment workers, data cleanup, batch queries
- Phase 19: SolSniffer API key configured, Birdeye retry with backoff, launchpad reputation bounded cache
- Phase 20: Jupiter→Birdeye migration (SOL price, paper price loop, sell sim graceful degradation)
- [Phase 21 Audit Fixes](docs/analysis/phase21-audit-fixes.md) — deep audit P0-P2 fixes (R24 api_error, R18 volume inversion, Jito/Metaplex Helius, signal decay updated_at, retries, gRPC leak, gather timeouts, paper trader race condition)
- [Phase 22 Audit Fixes](docs/analysis/phase22-audit-fixes.md) — signal decay Core UPDATE fix, SolSniffer gray-zone filter, Telegram AdminFilter, FK CASCADE, HTML escape
- [Phase 23 Audit Fixes](docs/analysis/phase23-audit-fixes.md) — SharedRateLimiter (Birdeye), signal dedup, prescan Redis persistence, GMGN gradual recovery, SolSniffer atomic cap, gRPC timeouts, scoring v2/v3 alignment, PRE_SCAN timeout, trailing stop gap fix
- [Phase 24 Audit Fixes](docs/analysis/phase24-audit-fixes.md) — Signal atomic upsert (pg ON CONFLICT), alert dedup (address,action), entry slippage, DB indexes, GMGN timeout, worker loop resilience
- [Phase 25 Production Fixes](docs/analysis/phase25-production-fixes.md) — Stage starvation fix, MintInfo deserialization, signal calibration (first buy signals + paper trades), portfolio is_paper type fix, dashboard API
- Phase 27: Real trading engine (Jupiter swaps, wallet, risk manager, circuit breaker, 108 tests)
- Phase 28: Dashboard real trading UI (Paper/Real/All tabs, wallet balance, tx links), documentation updates
- [Phase 29 API Credit Optimization](docs/analysis/phase29-api-optimization.md) — GoPlus prescan, two-phase INITIAL gate, Helius param reduction (convergence 15→10, funding 3→2 hops, wallet_age 10→7), Vybe stricter gate (risk≥5, max_holders 3)

## Key Paths
- `src/parsers/worker.py` — main event loop (WS clients + enrichment pipeline, ~3000 lines)
- `src/parsers/scoring.py` / `scoring_v3.py` — token scoring v2/v3 (0-100, A/B comparison)
- `src/parsers/signals.py` — 54 signal rules → strong_buy/buy/watch/avoid
- `src/parsers/paper_trader.py` — auto paper trading (2x TP, -50% SL, 8h timeout, trailing stop)
- `src/trading/real_trader.py` — real trading engine (Jupiter swaps, risk manager, circuit breaker)
- `src/trading/wallet.py` — Solana wallet (keypair, balance, ATA)
- `src/trading/jupiter_swap.py` — Jupiter V6 swap execution pipeline
- `src/trading/risk_manager.py` — pre-trade risk checks + circuit breaker
- `src/trading/close_conditions.py` — shared close logic (TP/SL/trailing, paper + real)
- `src/parsers/persistence.py` — all DB write operations
- `src/parsers/enrichment_queue.py` — Redis-backed priority queue
- `src/parsers/alerts.py` — Telegram alert dispatch (signals + paper trades)
- `src/parsers/signal_decay.py` — TTL-based signal downgrade (4h/6h/12h)
- `src/parsers/sol_price.py` — SOL/USD price cache (Birdeye primary, Jupiter fallback, 60s)
- `src/models/token.py` — Token, TokenSnapshot, TokenSecurity, TokenOutcome
- `src/models/signal.py` — Signal model
- `src/models/trade.py` — Trade, Position models
- `config/settings.py` — all settings + feature flags (42 .env keys)
- `src/bot/` — Telegram bot (handlers, formatters)
- `src/api/` — Dashboard API (FastAPI, JWT auth, metrics, portfolio, tokens, signals, analytics)
- `frontend/` — Dashboard UI (React 18, TypeScript, shadcn/ui, Recharts)

## External Services (ВСЕ ключи есть в .env)

### Paid (API ключи настроены)
| Service | Setting | Cost | Purpose |
|---------|---------|------|---------|
| Birdeye | `BIRDEYE_API_KEY` | $39/mo | PRIMARY data: liq, vol, holders, security, OHLCV |
| Chainstack gRPC | `CHAINSTACK_GRPC_ENDPOINT` + `_TOKEN` | $49/mo | Sub-second token discovery (Yellowstone) |
| Vybe Network | `VYBE_API_KEY` | Dev plan | Top holders, wallet PnL |
| TwitterAPI.io | `TWITTER_API_KEY` | $10/1M credits | KOL mentions, viral tweets |
| RapidAPI TG | `RAPIDAPI_KEY` | $15/mo | Telegram group checker |
| OpenRouter LLM | `OPENROUTER_API_KEY` | ~$0.03/1M input | Token risk analysis (Gemini Flash Lite) |
| SolSniffer | `SOLSNIFFER_API_KEY` | Paid plan | Cross-validation (5000/mo cap, gray-zone only) |
| Helius | `HELIUS_API_KEY` | Plan | RPC + WS (Meteora DBC) |
| Jupiter | `JUPITER_API_KEY` | Free tier | Sell simulation (PRE_SCAN). Price API deprecated → Birdeye primary |

### Telegram Bot
| Setting | Purpose |
|---------|---------|
| `TELEGRAM_BOT_TOKEN` | Aiogram 3.x bot token for alerts + commands |
| `TELEGRAM_ADMIN_ID` | Admin user ID for restricted commands |

### Infrastructure Settings
| Setting | Default | Purpose |
|---------|---------|---------|
| `GMGN_PROXY_URL` | `""` | SOCKS5/HTTP rotating residential proxy for GMGN anti-block |
| `ENRICHMENT_WORKERS` | `3` | Number of parallel enrichment worker tasks |
| `DASHBOARD_ENABLED` | `true` | Enable/disable dashboard API |
| `DASHBOARD_PORT` | `8080` | Dashboard HTTP port |
| `DASHBOARD_ADMIN_PASSWORD` | — | bcrypt-hashed admin password for JWT auth |
| `DASHBOARD_JWT_SECRET` | — | JWT signing secret (256-bit min) |

### Free (no keys needed)
GMGN (SOCKS5 proxy + circuit breaker), DexScreener, PumpPortal WS, RugCheck, GoPlus, Raydium, Metaplex, Jito, pump.fun, Solana Tracker

### Disabled
- Bubblemaps (`enable_bubblemaps=False`) — $200+/mo, non-critical

### Removed (not needed)
- ~~Solscan~~ — data covered by Birdeye/GMGN
- ~~Dune~~ — historical analytics, not for real-time

## Repository & Production
- **Git**: https://github.com/Mobiss11/mec-radar
- **Production server**: Hetzner CCX23 (4 vCPU, 16GB RAM, 160GB disk, $28.99/mo)
  - IP: `178.156.247.90`
  - OS: Ubuntu 24.04 LTS
  - Path: `/opt/mec-radar`
  - Service: `systemctl start|stop|restart mec-radar`
  - Dashboard: `http://178.156.247.90:8080`
  - Logs: `journalctl -u mec-radar -f`
  - Python 3.12 + PostgreSQL 16 + Redis 7 + Node 22

## Команды
```bash
# Dev (starts worker + dashboard on port 8080)
.venv/bin/python -m src.main

# Production
systemctl restart mec-radar
journalctl -u mec-radar -f

# Тесты
.venv/bin/python -m pytest tests/ -v

# Миграции
poetry run alembic upgrade head

# Deploy update
git push origin main
ssh root@178.156.247.90 "cd /opt/mec-radar && git pull && .venv/bin/pip install -r requirements.txt && systemctl restart mec-radar"

# Линтинг
ruff check . --fix
```
## AI Rules

### Язык и стиль
- Отвечай на русском
- Код и комментарии в коде — на английском
- Не объясняй очевидное, будь конкретен
- Предлагай решения, а не спрашивай разрешение
- Если нужно больше контекста — прочитай файл, не спрашивай

### Backend (Python)
- Async everywhere — никаких sync вызовов в async контексте
- Type hints обязательны для всех функций
- Pydantic v2 для схем (BaseModel, model_validator, не validator)
- snake_case для Python, camelCase для TypeScript
- Обработка ошибок: конкретные exceptions, не голый try/except
- Логирование: structlog/loguru, не print()
- Импорты: stdlib → third-party → local, группы через пустую строку
- Не используй deprecated API (SQLAlchemy 1.x, Pydantic v1)
- FastAPI: Depends() для DI, отдельные роутеры по доменам
- SQLAlchemy: async сессии, Mapped[] аннотации, select() не query()

### Frontend (React + TypeScript)
- TypeScript strict mode, NEVER используй any
- shadcn/ui как база компонентов, кастомизация через className + variants
- cn() для условных Tailwind классов
- Named exports only (не default export кроме pages)
- Mobile-first всегда (Telegram Mini Apps = мобилка)
- Подробные правила дизайна: @.claude/rules/frontend.md

### Frontend Aesthetics (ОБЯЗАТЕЛЬНО)
- NEVER используй Inter, Roboto, Arial, system defaults — выбирай уникальные шрифты
- NEVER делай фиолетовые градиенты на белом фоне (AI-slop)
- ALWAYS создавай атмосферные фоны с глубиной вместо однотонных
- ALWAYS используй экстремальные контрасты жирности шрифтов (200 vs 800)
- Доминантный цвет с резкими акцентами — не равномерное распределение
- Skeleton loading states, не спиннеры
- Каждый UI должен удивлять и быть дизайнерского уровня

### Git
- Conventional commits: feat: fix: refactor: docs: chore:
- Одна фича = один коммит
- НЕ коммить: .env, секреты, логи, node_modules, __pycache__
- git push — ТОЛЬКО после ревью (заблокирован в permissions)

### Правила по категориям
- Безопасность: @.claude/rules/security.md
- Фронтенд и дизайн: @.claude/rules/frontend.md
- API стандарты: @.claude/rules/api-conventions.md
- Тесты: @.claude/rules/testing.md

### MCP серверы
- context7: пиши "use context7" для актуальных доков фреймворков
- Serena: навигация по символам — не читай файлы целиком
- Figma: извлекай дизайн-токены из макетов
- shadcn: проверяй актуальные пропсы компонентов
- Beads: beads_ready в начале сессии, создавай задачи для багов

### Архитектура
- Не ломай существующие паттерны — сначала пойми, потом меняй
- Alembic для миграций, не ALTER TABLE вручную
- Docker Compose для всех сервисов
- Новый файл > изменение большого существующего (SRP)

### Workflow
- На сложных задачах: /plan для создания плана
- Перед коммитом: /review
- Security audit: /sec-scan
- Проверка UI: /ui-check (Playwright скриншот + анализ)
- После /clear: /catchup для восстановления контекста
