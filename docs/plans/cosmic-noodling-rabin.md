# Dashboard Plan: Мониторинг-дашборд для Solana Memecoin System

## Context

Система обнаружения и paper-trading мемкоинов работает, но управление и мониторинг доступны только через Telegram бот (5 команд) и логи. Нужен полноценный веб-дашборд для:
- Визуального мониторинга всех сервисов (gRPC, WS, Redis, DB)
- Просмотра токенов, сигналов, paper trades
- Аналитики и графиков
- Управления настройками без перезапуска
- Безопасного доступа с JWT авторизацией

## Стек

- **Backend**: FastAPI (запускается как asyncio task внутри worker.py — общий event loop)
- **Frontend**: React 18 + TypeScript strict + Vite + shadcn/ui + TailwindCSS + Recharts
- **Auth**: JWT в httpOnly cookie, bcrypt пароль из .env, CSRF для мутаций
- **Hosting**: FastAPI раздаёт и API (`/api/v1/`) и статику фронта (`/`) — один порт

## Архитектура: Same-Process Sharing

```
┌─────────────────────────────────────────────┐
│           Python asyncio event loop          │
│                                              │
│  ┌──────────────┐    ┌───────────────────┐   │
│  │ Worker Tasks  │    │ FastAPI (uvicorn) │   │
│  │ gRPC, WS,    │    │                   │   │
│  │ Enrichment,  │───>│  MetricsRegistry  │   │
│  │ Paper Trader, │    │  (singleton refs) │   │
│  │ TG Bot, etc. │    │                   │   │
│  └──────────────┘    └───────────────────┘   │
│         │                     │               │
│         ▼                     ▼               │
│  ┌─────────────┐    ┌──────────────┐         │
│  │ PostgreSQL   │    │    Redis     │         │
│  │ (shared)     │    │  (shared)    │         │
│  └─────────────┘    └──────────────┘         │
└─────────────────────────────────────────────┘
```

**MetricsRegistry** — singleton, заполняется в `run_parser()` ссылками на все клиенты. FastAPI читает данные напрямую из Python-объектов (thread-safe в single asyncio loop).

## Фазы реализации

### Phase 1: Backend Foundation
1. Добавить зависимости в `pyproject.toml`: fastapi, uvicorn[standard], python-jose[cryptography], bcrypt, slowapi
2. Создать `src/api/` пакет:
   - `app.py` — FastAPI factory, mount routers, middleware, static files
   - `auth.py` — JWT encode/decode, login, bcrypt verify
   - `dependencies.py` — `get_current_user` (cookie JWT), `get_session`
   - `middleware.py` — CSP headers, rate limiting, CSRF validation
   - `metrics_registry.py` — singleton с ссылками на runtime-объекты
   - `server.py` — `run_dashboard_server()` через `uvicorn.Server.serve()`
3. Добавить в `config/settings.py`: `dashboard_enabled`, `dashboard_port`, `dashboard_admin_password`, `dashboard_jwt_secret`
4. Модифицировать `src/parsers/worker.py`: заполнить registry + spawn dashboard task перед `asyncio.gather()`

### Phase 2: Auth + Health + Metrics API
- `POST /api/v1/auth/login` → JWT cookie (30min, httpOnly, Secure, SameSite=Lax)
- `POST /api/v1/auth/logout` → clear cookie
- `GET /api/v1/auth/me` → `{"username": "admin"}`
- `GET /api/v1/health` (no auth) → DB + Redis ping + uptime
- `GET /api/v1/metrics/overview` → uptime, enrichment rate, queue size, SOL price, alerts sent
- `GET /api/v1/metrics/connections` → gRPC/WS/Redis/DB state + message counts
- `GET /api/v1/metrics/pipeline` → per-stage stats (runs, latency, coverage, errors)

### Phase 3: Tokens & Signals API
- `GET /api/v1/tokens?cursor=&limit=20&search=&source=&min_score=` → cursor pagination
- `GET /api/v1/tokens/{address}` → token + latest snapshot + security + signal
- `GET /api/v1/tokens/{address}/snapshots?limit=50`
- `GET /api/v1/signals?status=strong_buy,buy,watch&limit=20&cursor=`
- `GET /api/v1/signals/{id}` → signal + token + rules fired

### Phase 4: Portfolio API
- `GET /api/v1/portfolio/summary` → delegates to `PaperTrader.get_portfolio_summary()`
- `GET /api/v1/portfolio/positions?status=open&limit=20&cursor=`
- `GET /api/v1/portfolio/positions/{id}` → position + trades
- `GET /api/v1/portfolio/pnl-history?days=30` → daily cumulative PnL

### Phase 5: Settings & Analytics API
- `GET /api/v1/settings` → feature flags, paper trader params, signal thresholds
- `PATCH /api/v1/settings` (+ CSRF) → update in-memory settings singleton
- `GET /api/v1/settings/api-status` → configured/missing per service (never show keys!)
- `GET /api/v1/analytics/score-distribution` → v2/v3 histogram buckets
- `GET /api/v1/analytics/signals-by-status?hours=24`
- `GET /api/v1/analytics/discovery-by-source?hours=24`
- `GET /api/v1/analytics/close-reasons`
- `GET /api/v1/analytics/top-performers?limit=20`

### Phase 6: Frontend Scaffold
1. `frontend/` — Vite + React 18 + TS strict + TailwindCSS
2. Шрифты: Space Grotesk (headings) + JetBrains Mono (data/numbers)
3. Тёмная тема по дефолту с глубиной (градиенты, не flat)
4. shadcn/ui компоненты: button, card, table, tabs, badge, sheet, skeleton, switch, input, select, dialog, tooltip, separator
5. Общие компоненты: status-dot (пульсирующий), stat-card, data-table, address-badge, score-badge, signal-badge, skeleton-card, empty-state
6. Auth: login page, AuthProvider context, protected routes

### Phase 7: Frontend Pages
1. **Overview**: stat cards (uptime, rate, queue, SOL price) + connection cards (gRPC/WS/Redis/DB с StatusDot) + pipeline sparkline
2. **Tokens**: DataTable с поиском + фильтрами, клик → token detail sheet (snapshot + security + score + signals)
3. **Signals**: таблица с tabs по статусу, score-badge, reasons preview
4. **Portfolio**: summary cards (PnL, win rate, open/closed) + positions table + PnL area chart
5. **Settings**: toggles для feature flags, number inputs для params, API status grid
6. **Analytics**: recharts — score histogram, signals pie, discovery by source bars, close reasons donut, top performers table

### Phase 8: Polish
- Skeleton loading на всех страницах
- Polling: overview 5s, tokens/signals 10s, portfolio 15s, analytics 60s
- Анимированные status dots
- Mobile responsive (но приоритет — desktop)
- Error boundaries + empty states

## Безопасность

| Требование | Реализация |
|-----------|-----------|
| JWT storage | httpOnly Secure SameSite=Lax cookie, NOT localStorage |
| Password | bcrypt 12 rounds, пароль из `DASHBOARD_ADMIN_PASSWORD` env |
| CSRF | HMAC от JWT jti, `X-CSRF-Token` header для PATCH/POST/DELETE |
| Rate limit | slowapi: 5/min login, 60/min API |
| Headers | CSP, X-Content-Type-Options, X-Frame-Options: DENY, HSTS |
| SQL injection | SQLAlchemy ORM only, Pydantic validation на все inputs |
| XSS | React auto-escape + CSP |
| Secrets | API status показывает только configured: bool, never keys |
| Error handling | Structured JSON errors, no stack traces |

## Ключевые файлы для модификации

| Файл | Изменение |
|------|----------|
| `pyproject.toml` | +5 backend deps |
| `config/settings.py` | +4 dashboard settings |
| `src/parsers/worker.py` | Populate MetricsRegistry + spawn dashboard task (~10 строк) |

## Новые файлы

**Backend (~22 файла, ~1500 строк Python):**
```
src/api/
  __init__.py, app.py, auth.py, dependencies.py,
  middleware.py, metrics_registry.py, server.py
  routers/: health.py, auth_router.py, tokens.py, signals.py,
            portfolio.py, settings.py, analytics.py, metrics.py
  schemas/: token.py, signal.py, portfolio.py, settings.py, analytics.py
```

**Frontend (~48 файлов, ~4600 строк TypeScript/TSX):**
```
frontend/
  vite.config.ts, tailwind.config.ts, package.json, tsconfig.json
  src/
    App.tsx, main.tsx, globals.css
    types/: token.ts, signal.ts, portfolio.ts, metrics.ts, settings.ts
    lib/: api.ts, cn.ts, format.ts, constants.ts
    hooks/: use-auth.ts, use-api.ts, use-polling.ts, use-debounce.ts
    components/ui/: (13 shadcn primitives)
    components/common/: status-dot, stat-card, data-table, address-badge,
                        score-badge, signal-badge, skeleton-card, empty-state
    components/charts/: sparkline, pipeline-chart, pnl-chart,
                        score-histogram, signal-pie
    layouts/: auth-layout, dashboard-layout, sidebar, header
    pages/: login, overview, tokens, token-detail, signals,
            portfolio, settings, analytics
```

## Verification

1. `poetry install` — зависимости ставятся без ошибок
2. Worker запускается и dashboard доступен на `http://localhost:8080`
3. `GET /api/v1/health` → 200 без авторизации
4. `POST /api/v1/auth/login` → JWT cookie, `GET /api/v1/auth/me` → 200
5. Все endpoints возвращают 401 без cookie
6. PATCH settings без CSRF → 403
7. Login rate limit: 6-й запрос за минуту → 429
8. Frontend: `cd frontend && npm run build` → `dist/` без ошибок
9. Все страницы рендерятся с skeleton → данные
10. `.venv/bin/python -m pytest tests/` — существующие 503 теста проходят
