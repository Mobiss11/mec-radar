# Architecture Overview

## System Purpose
Async Python platform for detecting and scoring Solana memecoins.
Integrates 20+ data sources, runs an 11-stage enrichment pipeline,
generates trading signals, and paper-trades automatically.

## Core Data Flow
```
Token Discovery        Enrichment Pipeline        Signal Generation
┌──────────────┐      ┌──────────────────┐       ┌──────────────┐
│ Chainstack   │─┐    │ PRE_SCAN (+5s)   │──X──→ │ Reject scams │
│  gRPC stream │ │    │ INITIAL (+25s)   │──→──→ │ Score 0-100  │
│ PumpPortal WS│ ├──→ │ MIN_2..MIN_30    │──→──→ │ 54 rules     │
│ Meteora DBC  │ │    │ HOUR_1..HOUR_24  │       │ strong_buy   │
│ GMGN polling │ │    └──────────────────┘       │ buy/watch    │
│ DexScreener  │─┘           │                    └──────┬───────┘
└──────────────┘             ▼                           │
                      ┌──────────────┐           ┌───────▼───────┐
                      │ PostgreSQL   │           │ Paper Trader  │
                      │ Redis cache  │           │ TG Alerts     │
                      └──────────────┘           └───────────────┘
```

## Key Components

### Token Discovery (< 1s latency)
- **Chainstack gRPC** ($49/mo) — Yellowstone Geyser stream, pump.fun txs, sub-second discovery (PRIMARY)
- **PumpPortal WS** (free) — pump.fun new tokens, trades, migrations (FALLBACK when gRPC unavailable)
- **Meteora DBC WS** (Helius WS) — logsSubscribe for Believe/LetsBonk/Boop bonding curve pools
- **GMGN polling** (free) — new_pairs + trending (60s interval)
- **DexScreener** (free) — boosted tokens discovery

### Enrichment Pipeline (11 stages)
Each token passes through stages at scheduled intervals:

| Stage | Offset | What Happens |
|-------|--------|--------------|
| PRE_SCAN | +5s | Mint check, Jupiter sell sim, instant reject. **early_watch** alert for clean tokens |
| INITIAL | +25s | Full data: Birdeye, GMGN, GoPlus, Rugcheck, holders, Twitter, Vybe, TG check, LLM |
| MIN_2 | +2m | Quick price check (Birdeye + DexScreener in parallel) |
| MIN_5 | +5m | Holder shift, OHLCV, smart money. Prune score < 20 |
| MIN_10 | +10m | Price trajectory (DexScreener) |
| MIN_15 | +15m | Deep GMGN check, holders, smart money. Prune score < 25 |
| MIN_30 | +30m | Security re-check |
| HOUR_1 | +1h | Full holder behavior analysis |
| HOUR_2 | +2h | Cross-validation (DexScreener) |
| HOUR_4 | +4h | Deep check with security |
| HOUR_8 | +8h | Trajectory tracking |
| HOUR_24 | +24h | Final outcome assessment |

Non-INITIAL stages run API calls in parallel via `asyncio.gather()` for speed.
All gather calls are wrapped with `asyncio.wait_for()` timeouts: 45s for INITIAL, 30s for non-INITIAL stages.

**Parallel enrichment workers** (default 3, configurable via `ENRICHMENT_WORKERS`):
Multiple workers consume from the Redis-backed priority queue concurrently.
Redis sorted set `zrem` ensures atomic task pop — no duplicate processing.

### Scoring (0-100 pts, v2 + v3 A/B)
Two scoring models run in parallel with divergence logging:
- **v2** (`scoring.py`) — balanced across all signals
- **v3** (`scoring_v3.py`) — momentum-weighted, harsher penalties

Divergence alerts: warning at delta >= 15, info at delta >= 8.

Hard disqualifiers: honeypot → 0, jupiter_banned → 0.
Data completeness gate: < 3 core metrics → capped at 40.

### Signal Generation (54 rules)
Rules fire independently, summing bullish (+) and bearish (-) weights:
- **net >= 8** → strong_buy
- **net >= 5** → buy
- **net >= 2** → watch
- **else** → avoid

Signals generated at INITIAL and MIN_5 stages. Signal decay TTL (based on `updated_at`, not `created_at` — re-confirmed signals reset their TTL):
- strong_buy → buy after 4h
- buy → watch after 6h
- watch → expired after 12h

### Paper Trading
- Volume-weighted entry: strong_buy = 1.5x base SOL, buy = 1.0x
- Real-time price updates via Birdeye multi-price (15s interval), Jupiter fallback for missing tokens
- SOL/USD price feed via Birdeye (primary, 60s cache), Jupiter fallback
- Trailing stop: after 2x, close if 30% drawdown from max
- Early stop: -20% in first 30 minutes
- Take profit: 3x, Stop loss: -50%, Timeout: 8h
- Slippage estimation: haircut if exit > 2% of liquidity (both enrichment + price loop paths)
- Telegram alerts on open/close via AlertDispatcher

### Telegram Alerts
- **early_watch**: fast notification at T+5s for clean tokens passing PRE_SCAN
- Signal alerts: strong_buy/buy/watch with score, reasons, price
- Paper trade alerts: position open/close with P&L
- Portfolio report: hourly summary with win rate
- Cooldown deduplication: 300s per (token_address, action) — watch doesn't block buy

## External Services (All Keys Present)

### Paid Services (API keys in .env)
| Service | Cost | Purpose | Rate Limit |
|---------|------|---------|------------|
| **Birdeye** | $39/mo | PRIMARY data: liquidity, volume, holders, security, OHLCV, trades. Retry with backoff (timeout, 429, 5xx) | 10 RPS |
| **Chainstack gRPC** | $49/mo | Sub-second token discovery via Yellowstone Geyser | N/A (stream) |
| **Vybe Network** | Dev plan (500K/mo) | Top holders, wallet PnL, holder profit %. Retry with backoff (2 retries, 1s/3s) | 8 RPS |
| **TwitterAPI.io** | $10/1M credits | KOL mentions (50K+ followers), viral tweets. Retry with backoff (2 retries, 1s/3s) | 1 RPS |
| **RapidAPI TG** | $15/mo | Telegram group member count, activity check | 0.9 RPS |
| **OpenRouter LLM** | ~$0.03/1M input | Token risk analysis via Gemini 2.5 Flash Lite | 2 RPS |
| **SolSniffer** | Paid plan | Cross-validation for gray-zone tokens (score 30-60). API key configured | 0.1 RPS |
| **Jupiter** | Free tier | Sell simulation (PRE_SCAN honeypot check). Price API deprecated — Birdeye primary | 1 RPS |

### Free Services (no API keys needed)
| Service | Purpose | Rate Limit |
|---------|---------|------------|
| **GMGN** | Secondary data: top holders with PnL (SOCKS5 proxy + circuit breaker) | 1.5 RPS |
| **DexScreener** | Cross-validation: trade buy/sell counts | 4 RPS |
| **PumpPortal WS** | pump.fun token discovery (fallback to gRPC) | N/A |
| **Meteora DBC WS** | Believe/LetsBonk/Boop pool events via Helius | N/A |
| **RugCheck** | Honeypot detection, risk parsing, insider network | 2 RPS |
| **GoPlus** | Security: honeypot, dangerous functions | 0.5 RPS |
| **Raydium** | LP burn/lock detection | 5 RPS |
| **Helius** | Enhanced RPC, transaction parsing, DAS `getAsset` API for metadata | varies |
| **pump.fun** | Creator history (dead tokens count) | 1 RPS |
| **Solana Tracker** | Additional token risk data | 1 RPS |
| **Metaplex** | Metadata mutability, homoglyph detection | N/A |
| **Jito** | MEV bundle detection | N/A |

### Disabled / Not Needed
| Service | Status | Reason |
|---------|--------|--------|
| **Bubblemaps** | DISABLED (`enable_bubblemaps=False`) | $200+/mo, non-critical. Scoring impact: -8 to +3 pts |
| ~~Solscan~~ | REMOVED | Not needed, data covered by Birdeye/GMGN |
| ~~Dune~~ | REMOVED | Historical analytics only, not needed for real-time |

## Data Source Priority
1. **Birdeye** (primary, paid) — liquidity, volume, holders, security, OHLCV
2. **GMGN** (secondary, free) — top holders with PnL, token info fallback
3. **DexScreener** (cross-validation, free) — trade counts, liquidity check
4. **Vybe** (unique data, paid) — holder PnL distribution
5. **Twitter** (social signals, paid) — KOL detection, viral tweets
6. **GoPlus/Rugcheck** (security, free) — honeypot, risks
7. **Jupiter** (free tier, API key) — sell simulation only. Price API deprecated, Birdeye is primary

## Database
- **PostgreSQL** — async via SQLAlchemy 2.0 (Mapped[] annotations)
- **Redis** — enrichment queue persistence, smart money cache, SolSniffer monthly counter, rate limiting
- **Alembic** — schema migrations
- **Data cleanup** — periodic loop (every 6h): snapshots 7d, trades 7d, OHLCV 14d retention

## GMGN Anti-Block Strategy
- **Rotating residential SOCKS5 proxy** via `GMGN_PROXY_URL` (new IP per request)
- **TLS fingerprint rotation** via `tls_client` (chrome/safari/firefox identifiers, rotated every 50 requests)
- **Circuit breaker** with exponential cooldown: 60s → 120s → 240s → ... → 600s max on repeated 403s
- Resets to base cooldown on any successful request

## Dashboard (Web UI)
- **Backend**: FastAPI running as asyncio task in same process as worker (shared event loop)
- **Frontend**: React 18 + TypeScript + Vite + shadcn/ui + TailwindCSS + Recharts
- **Auth**: JWT in httpOnly cookie (30min), bcrypt password from `DASHBOARD_ADMIN_PASSWORD`
- **Access**: `http://178.156.247.90:8080` (prod) / `http://localhost:8080` (dev)
- FastAPI serves both API (`/api/v1/`) and static frontend (`/`) on one port
- MetricsRegistry singleton provides direct access to runtime objects (clients, queues, paper trader)

## Tech Stack
- Python 3.12, asyncio, httpx, websockets, grpcio
- FastAPI + uvicorn (dashboard API, same-process)
- React 18 + TypeScript + Vite + shadcn/ui (dashboard UI)
- SQLAlchemy 2.0 async, Alembic
- Pydantic v2 for data validation
- loguru for logging
- aiogram 3.x for Telegram bot
- tls_client for GMGN (TLS fingerprint bypass)

## Change Log
- **Phase 21** (2026-02-20): Deep audit fixes — R18/R24 signal bug fixes, Jito bundle rewrite (Helius batch API), Helius DAS `getAsset`, scoring v3 volume acceleration guard, signal decay uses `updated_at`, retry logic for Vybe/Twitter, gRPC channel leak fix, gather timeouts (45s/30s), bare except removal, paper trader race condition fix (partial unique index + IntegrityError handling), alert logging upgrade
- **Phase 22** (2026-02-20): Signal decay Core UPDATE fix (`updated_at=func.now()`), SolSniffer gray-zone filter (~50-70% savings), Telegram AdminFilter, FK CASCADE migration, HTML escape
- **Phase 23** (2026-02-20): SharedRateLimiter (Birdeye 10 RPS across 3 workers), signal dedup, prescan Redis persistence, GMGN gradual recovery, SolSniffer atomic cap, gRPC timeouts, scoring v2/v3 alignment
- **Phase 24** (2026-02-20): Signal atomic upsert (pg ON CONFLICT), alert dedup (address,action), entry slippage, DB indexes, GMGN timeout, worker loop resilience
- **Phase 25** (2026-02-21): Stage starvation fix (queue FIFO within priority), MintInfo dict→object deserialization, signal calibration (rugcheck -4→-2, llm -3→-1, mutable -2→-1), portfolio is_paper type fix, web dashboard (FastAPI + React)
- **Phase 26** (2026-02-21): Production deployment — GitHub repo (Mobiss11/mec-radar), Hetzner CCX23 server (178.156.247.90, Ubuntu 24.04, 4vCPU/16GB), systemd service, PostgreSQL data migration, firewall (UFW)
