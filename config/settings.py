from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Database
    database_url: str = "postgresql+asyncpg://trader:changeme@localhost:5432/memcoin_trader"
    redis_url: str = "redis://localhost:6380/0"

    # Helius (Solana RPC + WebSocket)
    helius_api_key: str = ""
    helius_rpc_url: str = ""
    helius_ws_url: str = ""

    # Telegram bot
    telegram_bot_token: str = ""
    telegram_admin_id: int = 0

    # Trading (live trading kill-switch)
    trading_enabled: bool = False

    # gmgn.ai
    gmgn_parse_interval_sec: int = 1  # Ultra-aggressive 1s polling for fastest discovery
    gmgn_max_rps: float = 5.0  # Raised for 5s polling with proxy pool
    gmgn_proxy_url: str = ""  # SOCKS5 or HTTP proxy for GMGN (rotating residential)
    gmgn_proxy_pool: str = ""  # Comma-separated proxy URLs for round-robin rotation

    # DexScreener
    dexscreener_max_rps: float = 4.0

    # Birdeye Data Services API (paid — $39/mo)
    birdeye_api_key: str = ""
    birdeye_max_rps: float = 15.0  # Phase 50: Starter plan = 15 RPS

    # Solana RPC (fallback if helius_rpc_url is empty)
    solana_rpc_url: str = ""

    # Enrichment workers (parallel consumers)
    enrichment_workers: int = 5  # Number of parallel enrichment workers (was 3)

    # Feature flags — token discovery
    enable_gmgn: bool = True
    enable_pumpportal: bool = True
    enable_dexscreener: bool = True
    enable_meteora_dbc: bool = False  # Believe/LetsBonk/Boop tokens via Helius WS (disabled)
    enable_birdeye: bool = True  # PRIMARY data source

    # Jupiter (free tier — 1 RPS)
    jupiter_api_key: str = ""

    # Feature flags — enrichment APIs (all free, no keys)
    enable_rugcheck: bool = True
    enable_jupiter: bool = True
    enable_helius_analysis: bool = True  # requires helius_api_key
    enable_goplus: bool = True
    enable_pumpfun_history: bool = True
    enable_raydium_lp: bool = True
    enable_jito_detection: bool = True
    enable_metaplex_check: bool = True
    enable_rugcheck_insiders: bool = True
    enable_solana_tracker: bool = True
    enable_jupiter_verify: bool = True

    # Feature flags — deep detection (all free)
    enable_fee_payer_clustering: bool = True
    enable_convergence_analysis: bool = True
    enable_metadata_scoring: bool = True
    enable_wash_trading_detection: bool = True
    enable_rugcheck_risk_parsing: bool = True
    enable_lp_monitor: bool = True
    enable_rug_guard: bool = True  # Phase 45: real-time LP removal via gRPC
    enable_cross_token_whales: bool = True
    enable_website_checker: bool = True  # no API key needed

    # LP monitoring thresholds
    lp_removal_warning_pct: float = 20.0
    lp_removal_critical_pct: float = 50.0

    # Cross-token whale correlation
    cross_whale_lookback_hours: int = 2
    cross_whale_min_shared_wallets: int = 3

    # Creator trace
    default_creator_risk: int = 25

    # PRE_SCAN Birdeye fast check — reject microcap junk before expensive INITIAL
    prescan_min_mcap_usd: float = 5000.0     # Hard reject if MCap < $5K
    prescan_min_liquidity_usd: float = 100.0  # Hard reject if liquidity < $100

    # Phase 29: API credit optimization — tunable Helius/Vybe parameters
    convergence_max_buyers: int = 10  # was 15 — each buyer = 2 Helius RPC calls
    funding_trace_max_hops: int = 2   # was 3 — each hop = 6 Helius RPC calls
    wallet_age_max_wallets: int = 7   # was 10 — each wallet = 1 Helius RPC call
    vybe_max_holders_pnl: int = 3     # was 5 — each holder = 1 Vybe API call
    vybe_prescan_risk_gate: int = 5   # was 10 — skip Vybe if prescan_risk_boost >= this

    # Signal decay
    signal_decay_enabled: bool = True
    signal_decay_interval_sec: int = 300
    signal_strong_buy_ttl_hours: int = 4
    signal_buy_ttl_hours: int = 6
    signal_watch_ttl_hours: int = 12

    # Paper trading
    paper_trading_enabled: bool = True  # ENABLED for testing
    paper_sol_per_trade: float = 0.5
    paper_max_positions: int = 20
    paper_take_profit_x: float = 1.5  # Phase 35: was 2.0, reduced to capture gains before instant rugs
    paper_stop_loss_pct: float = -50.0
    paper_timeout_hours: int = 8
    paper_trailing_activation_x: float = 1.3  # Phase 31C: trailing stop activates at 1.3x (was hardcoded 1.5x)
    paper_trailing_drawdown_pct: float = 15.0  # Phase 31C: close on 15% drop from max (was 20%)
    paper_stagnation_timeout_min: float = 25.0  # Phase 31B: close stagnating positions after 25 min
    paper_stagnation_max_pnl_pct: float = 15.0  # Phase 31B: only close if PnL < 15%
    liquidity_grace_period_sec: int = 90  # Phase 36: grace period for zero-liq fresh positions (DexScreener lag)

    # Real trading (DISABLED by default — requires wallet_private_key)
    real_trading_enabled: bool = False
    wallet_private_key: str = ""  # Base58 secret key — NEVER LOG THIS
    real_sol_per_trade: float = 0.05  # ~$4.15 at SOL=$83
    real_max_positions: int = 5
    real_take_profit_x: float = 1.5  # Phase 42: aligned with paper (was 2.0)
    real_stop_loss_pct: float = -50.0
    real_timeout_hours: int = 8
    real_trailing_activation_x: float = 1.3  # Phase 31C: trailing stop activates at 1.3x
    real_trailing_drawdown_pct: float = 15.0  # Phase 31C: close on 15% drop from max
    real_stagnation_timeout_min: float = 25.0  # Phase 31B: close stagnating positions after 25 min
    real_stagnation_max_pnl_pct: float = 15.0  # Phase 31B: only close if PnL < 15%
    real_slippage_bps: int = 500  # 5%
    real_min_liquidity_usd: float = 5000.0
    real_priority_fee_lamports: int = 100000  # 0.0001 SOL
    real_max_sol_exposure: float = 0.8  # Max total SOL in open positions (2 × 0.375 strong_buy)
    real_circuit_breaker_threshold: int = 3  # Pause after N consecutive failures
    real_circuit_breaker_cooldown_sec: int = 1800  # 30 min cooldown

    # SolSniffer (paid plan — higher cap)
    enable_solsniffer: bool = True
    solsniffer_api_key: str = ""
    solsniffer_monthly_cap: int = 5000  # Paid plan

    # Early watch alerts (prescan T+5s notifications — high volume, disable to reduce spam)
    enable_early_watch_alerts: bool = False

    # Bubblemaps (disabled until API key obtained)
    enable_bubblemaps: bool = False
    bubblemaps_api_key: str = ""

    # Chainstack gRPC (Yellowstone Geyser)
    chainstack_grpc_endpoint: str = ""
    chainstack_grpc_token: str = ""
    enable_grpc_streaming: bool = True  # gRPC = sub-second discovery, PumpPortal is fallback

    # Vybe Network — DISABLED: holder PnL now computed from GMGN data (free)
    vybe_api_key: str = ""
    enable_vybe: bool = False

    # TwitterAPI.io (paid — $10/1M credits)
    twitter_api_key: str = ""
    enable_twitter: bool = True

    # Telegram group checker (RapidAPI)
    rapidapi_key: str = ""
    enable_telegram_checker: bool = True

    # LLM analysis via OpenRouter
    openrouter_api_key: str = ""
    enable_llm_analysis: bool = True
    llm_model: str = "google/gemini-2.5-flash-lite"

    # Dashboard
    dashboard_enabled: bool = True
    dashboard_port: int = 8080
    dashboard_admin_user: str = "admin"
    dashboard_admin_password: str = ""
    dashboard_jwt_secret: str = ""


settings = Settings()
