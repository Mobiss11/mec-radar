# Module Reference

## Models (`src/models/`)

| File | Model | Purpose |
|------|-------|---------|
| `token.py` | `Token` | Base token record (address, symbol, source, creator, metadata) |
| `token.py` | `TokenSnapshot` | Time-series metrics (price, volume, holders, score, score_v3) |
| `token.py` | `TokenSecurity` | Security flags (honeypot, mintable, LP status) |
| `token.py` | `TokenTopHolder` | Individual holder records |
| `token.py` | `TokenOutcome` | Signal performance tracking (peak ROI, is_rug) |
| `token.py` | `TokenOHLCV` | Candle data |
| `token.py` | `CreatorProfile` | Creator wallet analysis cache |
| `signal.py` | `Signal` | Trading signal (score, reasons, action, decay TTL via `updated_at`) |
| `trade.py` | `Trade` | Executed trade (buy/sell, paper/real) |
| `trade.py` | `Position` | Position tracking (entry/current price, PnL, max_price) |
| `wallet.py` | `SmartWallet` | Tracked whale profiles |
| `wallet.py` | `WalletActivity` | Wallet trade history |
| `wallet.py` | `WalletCluster` | Coordinated wallet groups |

## Data Source Clients (`src/parsers/`)

### Token Discovery (Real-time Streams)
| Directory | Client Class | Data Source | Type | Cost |
|-----------|-------------|-------------|------|------|
| `chainstack/` | `ChainstackGrpcClient` | Chainstack gRPC (Yellowstone) | gRPC stream | $49/mo |
| `pumpportal/` | `PumpPortalClient` | PumpPortal WS | WebSocket | Free |
| `meteora/` | `MeteoraDBCClient` | Helius WS (logsSubscribe) | WebSocket | Helius plan |

### Primary Data APIs (Paid)
| Directory | Client Class | Data Source | Rate Limit | Cost |
|-----------|-------------|-------------|------------|------|
| `birdeye/` | `BirdeyeClient` | Birdeye API (retry with backoff) | 10 RPS | $39/mo |
| `vybe/` | `VybeClient` | Vybe Network (retry with backoff) | 8 RPS (500 RPM) | Dev plan |
| `twitter/` | `TwitterClient` | TwitterAPI.io (retry with backoff) | 1 RPS | $10/1M credits |
| `telegram_checker/` | `TelegramCheckerClient` | RapidAPI TG | 0.9 RPS | $15/mo |
| `llm_analyzer/` | `LLMAnalyzerClient` | OpenRouter (Gemini Flash Lite) | 2 RPS | ~$0.03/1M input |
| `solsniffer/` | `SolSnifferClient` | SolSniffer | 0.1 RPS | Paid plan |

### Secondary Data APIs (Free or Free Tier)
| Directory | Client Class | Data Source | Rate Limit |
|-----------|-------------|-------------|------------|
| `gmgn/` | `GmgnClient` | gmgn.ai (TLS bypass + SOCKS5 proxy + circuit breaker) | 1.5 RPS |
| `dexscreener/` | `DexScreenerClient` | DexScreener (retry with backoff) | 4 RPS |
| `jupiter/` | `JupiterClient` | Jupiter API (free tier, API key via `x-api-key` header). Sell sim only, Price API deprecated | 1 RPS |
| `rugcheck/` | `RugcheckClient` | Rugcheck.xyz | 2 RPS |
| `goplus/` | `GoPlusClient` | GoPlus Security | 0.5 RPS |
| `raydium/` | `RaydiumClient` | Raydium API | 5 RPS |
| `helius/` | `HeliusClient` | Helius RPC + DAS `getAsset` API | varies |
| `pumpfun/` | `PumpfunClient` | pump.fun history | 1 RPS |

### Disabled
| Directory | Client Class | Status |
|-----------|-------------|--------|
| `bubblemaps/` | `BubblemapsClient` | DISABLED (`enable_bubblemaps=False`), $200+/mo |

## Analysis Modules (`src/parsers/`)

### Security & Scam Detection
| File | Function | What It Does |
|------|----------|--------------|
| `mint_parser.py` | `parse_mint_account()` | Decode SPL mint authority, freeze authority |
| `bundled_buy_detector.py` | `detect_bundled_buys()` | Coordinated first-block purchases |
| `jito_bundle.py` | `detect_jito_bundle()` | MEV bundle snipe detection (Helius batch `get_parsed_transactions` API) |
| `metaplex_checker.py` | `check_metaplex_metadata()` | Mutable metadata, homoglyphs (Helius DAS `getAsset` API) |
| `convergence_analyzer.py` | `analyze_convergence()` | Token flow convergence (sybil) |
| `fee_payer_cluster.py` | `cluster_by_fee_payer()` | Sybil via shared fee payer |
| `rugcheck_risk_parser.py` | `parse_rugcheck_risks()` | Parse danger-level risk factors |
| `rugcheck_insiders.py` | `get_insider_network()` | Insider holder network |

### Creator Analysis
| File | Function | What It Does |
|------|----------|--------------|
| `creator_trace.py` | `assess_creator_risk()` | Creator wallet funding chain |
| `creator_repeat.py` | `check_creator_recent_launches()` | Repeat launchpad creators |
| `pumpfun/client.py` | `PumpfunClient` | Creator history on pump.fun |

### Holder & Whale Analysis
| File | Function | What It Does |
|------|----------|--------------|
| `smart_money.py` | `SmartMoneyTracker` | Track high-profit wallets (Redis cache) |
| `cross_token_whales.py` | `detect_cross_token_coordination()` | Whales across tokens |
| `holder_pnl.py` | `analyse_holder_pnl()` | Holder P&L distribution |
| `wallet_cluster.py` | `detect_coordinated_traders()` | Group coordinated traders |
| `wallet_age.py` | `check_wallet_ages()` | Wallet creation time |
| `concentration_rate.py` | `compute_concentration_rate()` | Holder concentration velocity |

### Price & Volume
| File | Function | What It Does |
|------|----------|--------------|
| `price_momentum.py` | `compute_price_momentum()` | Price trajectory |
| `price_validator.py` | `validate_price_consistency()` | Cross-source divergence |
| `volume_profile.py` | `analyse_volume_profile()` | Volume distribution |
| `lp_monitor.py` | `check_lp_removal()` | LP removal detection |
| `lp_events.py` | `detect_lp_events_onchain()` | On-chain LP events |
| `sol_price.py` | `get_sol_price()` | SOL/USD price cache (Birdeye primary, Jupiter fallback, 60s) |

### Community & Social
| File | Function | What It Does |
|------|----------|--------------|
| `website_checker.py` | `check_website()` | Domain age, SSL, active check |
| `metadata_scorer.py` | `score_metadata()` | Image/description/social quality |
| `launchpad_reputation.py` | `compute_launchpad_reputation()` | Launchpad trustworthiness (bounded LRU cache, 200 entries max) |

## Scoring & Signals

| File | Function | Purpose |
|------|----------|---------|
| `scoring.py` | `compute_score()` | Token score v2 (0-100, balanced) |
| `scoring_v3.py` | `compute_score_v3()` | Token score v3 (momentum-weighted, stricter) |
| `signals.py` | `evaluate_signals()` | 54 rules → strong_buy/buy/watch/avoid |
| `signal_decay.py` | `decay_stale_signals()` | TTL-based signal downgrade (4h/6h/12h) based on `updated_at` |

## Infrastructure

| File | Class/Function | Purpose |
|------|---------------|---------|
| `worker.py` | `run_parser()` | Main orchestrator, event loop (~3000 lines). 3 parallel enrichment workers (configurable). Gather timeouts: 45s INITIAL, 30s non-INITIAL |
| `persistence.py` | `upsert_token()`, `cleanup_old_data()` | All DB write operations + periodic data cleanup (7d snapshots, 14d OHLCV) |
| `enrichment_queue.py` | `PersistentEnrichmentQueue` | Redis-backed priority queue (multi-consumer safe via atomic `zrem`) |
| `enrichment_types.py` | `EnrichmentStage`, `StageConfig` | 11-stage definitions + config flags |
| `rate_limiter.py` | `RateLimiter` | Token bucket rate limiter |
| `alerts.py` | `AlertDispatcher` | Telegram alerts (early_watch, signals, paper trades) |
| `health_alerting.py` | `HealthAlerter` | Pipeline health monitoring |
| `metrics.py` | `metrics` | System metrics (Prometheus-compatible) |
| `paper_trader.py` | `PaperTrader` | Auto paper trading engine (partial unique index prevents duplicate open positions) |

## Telegram Bot (`src/bot/`)

| File | Function | Purpose |
|------|----------|---------|
| `bot.py` | `run_bot()` | Aiogram 3.x polling loop |
| `handlers.py` | `router` | Commands: /start, /signals, /portfolio, /token, /stats |
| `formatters.py` | `format_*()` | HTML message formatting |

## Dashboard API (`src/api/`)

| File | Class/Function | Purpose |
|------|---------------|---------|
| `app.py` | `create_app()` | FastAPI factory, mount routers, middleware, static files |
| `auth.py` | `create_access_token()` | JWT encode/decode, bcrypt password verify |
| `dependencies.py` | `get_current_user()` | Cookie JWT extraction, session DI |
| `middleware.py` | — | CSP headers, rate limiting (slowapi), CSRF validation |
| `metrics_registry.py` | `MetricsRegistry` | Singleton with refs to runtime objects (clients, queues, traders) |
| `server.py` | `run_dashboard_server()` | uvicorn.Server.serve() as asyncio task |
| `routers/health.py` | — | `GET /api/v1/health` (no auth): DB + Redis + uptime |
| `routers/auth_router.py` | — | Login/logout/me (JWT httpOnly cookie, 30min) |
| `routers/tokens.py` | — | Token list + detail + snapshots (cursor pagination) |
| `routers/signals.py` | — | Signal list + detail with rules fired |
| `routers/portfolio.py` | — | Positions, PnL history, portfolio summary |
| `routers/settings.py` | — | Feature flags + paper trader params (CSRF for PATCH) |
| `routers/analytics.py` | — | Score distribution, signals by status, close reasons |
| `routers/metrics.py` | — | Pipeline stats, connection states, enrichment rate |

## Dashboard UI (`frontend/`)

React 18 + TypeScript strict + Vite + shadcn/ui + TailwindCSS + Recharts.
Built to `frontend/dist/`, served by FastAPI as static files.

| Page | Path | Key Features |
|------|------|-------------|
| Login | `/login` | JWT auth, bcrypt password |
| Overview | `/` | Stat cards, connection status dots, pipeline sparkline |
| Tokens | `/tokens` | DataTable with search/filters, token detail sheet |
| Signals | `/signals` | Tabs by status, score badge, reasons preview |
| Portfolio | `/portfolio` | PnL chart, positions table, summary cards |
| Settings | `/settings` | Feature flag toggles, paper trader params, API status |
| Analytics | `/analytics` | Score histogram, signal pie, discovery by source |

## Scripts (`scripts/`)

| File | Purpose |
|------|---------|
| `compare_scoring_models.py` | A/B comparison of v2 vs v3 scoring with outcome data |
