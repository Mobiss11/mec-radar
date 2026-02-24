"""Main parser worker — orchestrates all data collection.

Runs parallel async tasks:
1. PumpPortal WebSocket — always-on listener for pump.fun tokens and migrations
2. Meteora DBC WebSocket — Solana logsSubscribe for DBC pool events
3. Polling loop — periodic REST calls to gmgn.ai for new_pairs + trending
4. Enrichment worker — 11-stage priority queue (30s to 24h) with pruning
5. Smart money tracker — background cache refresh from GMGN
6. Stats reporter — periodic logging of system health

Phase 14D: INITIAL stage uses asyncio.gather() for parallel API calls:
- Batch 1 (~11 parallel): Birdeye, GMGN holders, security, metadata, Jupiter price,
  Rugcheck, GoPlus, Raydium, RugCheck insiders, Solana Tracker, Jupiter Verify
- Batch 2 (~7 parallel): smart money, convergence, wallet ages, creator analysis
  (profiling + risk + funding + repeat + pumpfun + bundled), fee payer, Jito, Metaplex
- Result: INITIAL latency cut from ~20-30s sequential to ~8-12s parallel
"""

import asyncio
import time as _time_mod
from datetime import datetime
from decimal import Decimal

from loguru import logger

# Phase 33/34: Copycat symbol tracking — Redis-backed with in-memory cache.
# Tracks (symbol, rug_count) to detect serial scam deployments.
# Updated by _paper_price_loop and _real_price_loop when liquidity_removed fires.
# Read by _enrich_token before evaluate_signals() to penalize repeat scam symbols.
# Phase 34: Migrated from in-memory dict to Redis hash for persistence across restarts.
_RUGGED_SYMBOLS: dict[str, tuple[float, int]] = {}  # symbol_upper -> (monotonic_ts, rug_count)
_RUGGED_SYMBOLS_TTL = 7200  # 2 hours (base TTL)
_RUGGED_SYMBOLS_REDIS_KEY = "antiscam:rugged_symbols"  # Redis hash: symbol -> "count:ts_unix"
# Phase 44: Track which token_ids already fed into _RUGGED_SYMBOLS via outcome detection.
# Prevents inflating rug_count on re-enrichment of the same token.
_OUTCOME_RUG_TRACKED: set[int] = set()


def _get_copycat_ttl(count: int) -> int:
    """Phase 35/44: Count-dependent TTL — serial scam symbols tracked longer.

    Phase 44 fix: 马到成功 serial scammer (44 tokens, 7 rugs) bypassed copycat
    because 2h TTL expired between batches (gap ~8h between 03:55→11:52 UTC).
    Increased TTLs: count>=3 → 12h, >=7 → 24h, >=10 → 48h, >=50 → 72h.
    """
    if count >= 50:
        return 259200  # 72 hours
    if count >= 10:
        return 172800  # 48 hours
    if count >= 7:
        return 86400  # 24 hours
    if count >= 3:
        return 43200  # 12 hours
    return _RUGGED_SYMBOLS_TTL  # 2 hours

from config.settings import settings
from src.db.database import async_session_factory
from src.db.redis import close_redis, get_redis
from src.parsers.birdeye.client import BirdeyeApiError, BirdeyeClient
from src.parsers.birdeye.models import BirdeyeTokenOverview
from src.parsers.dexscreener.client import DexScreenerClient
from src.parsers.enrichment_queue import PersistentEnrichmentQueue
from src.parsers.enrichment_types import (
    NEXT_STAGE,
    STAGE_SCHEDULE,
    EnrichmentPriority,
    EnrichmentStage,
    EnrichmentTask,
)
from src.parsers.gmgn.client import GmgnClient
from src.parsers.gmgn.exceptions import GmgnError
from src.parsers.gmgn.models import GmgnTopHolder
from src.parsers.meteora.client import MeteoraClient
from src.parsers.meteora.constants import KNOWN_LAUNCHPADS
from src.parsers.meteora.models import MeteoraMigration, MeteoraNewPool, MeteoraVirtualPool
from src.parsers.meteora.ws_client import MeteoraDBCClient
from src.parsers.persistence import (
    get_token_by_address,
    get_token_security_by_token_id,
    save_birdeye_trades,
    save_ohlcv,
    save_pumpportal_trade,
    save_token_security,
    save_token_security_from_birdeye,
    save_token_snapshot,
    save_top_holders,
    upsert_smart_wallet,
    upsert_token,
    upsert_token_from_meteora_dbc,
    upsert_token_from_pumpportal,
    upsert_token_outcome,
)
from src.parsers.pumpportal.models import (
    PumpPortalMigration,
    PumpPortalNewToken,
    PumpPortalTrade,
)
from src.parsers.pumpportal.ws_client import PumpPortalClient
from src.parsers.alerts import AlertDispatcher, TokenAlert
from src.parsers.health_alerting import HealthAlerter
from src.parsers.metrics import metrics as pipeline_metrics
from src.parsers.rate_limiter import RateLimiter
from src.parsers.scoring import compute_score
from src.parsers.scoring_v3 import compute_score_v3
from src.parsers.signals import evaluate_signals
from src.parsers.smart_money import SmartMoneyTracker
from src.parsers.lp_monitor import check_lp_removal, get_lp_removal_pct
from src.parsers.cross_token_whales import detect_cross_token_coordination
from src.parsers.creator_trace import assess_creator_risk
from src.parsers.rugcheck.client import RugcheckClient
from src.parsers.jupiter.client import JupiterClient
from src.parsers.helius.client import HeliusClient
from src.parsers.persistence import (
    get_latest_snapshot,
    save_goplus_report,
    save_rugcheck_report,
    update_creator_pumpfun,
    update_security_phase12,
)
from src.parsers.concentration_rate import compute_concentration_rate
from src.parsers.mint_parser import MintInfo, parse_mint_account
from src.parsers.goplus.client import GoPlusClient
from src.parsers.pumpfun.client import PumpfunClient
from src.parsers.raydium.client import RaydiumClient
from src.parsers.bundled_buy_detector import detect_bundled_buys
from src.parsers.creator_repeat import check_creator_recent_launches
from src.parsers.holder_pnl import analyse_holder_pnl
from src.parsers.launchpad_reputation import compute_launchpad_reputation, get_launchpad_score_impact
from src.parsers.price_momentum import compute_price_momentum
from src.parsers.price_validator import validate_price_consistency
from src.parsers.volume_profile import analyse_volume_profile
# Phase 14B: Additional free API integrations
from src.parsers.jito_bundle import detect_jito_bundle
from src.parsers.metaplex_checker import check_metaplex_metadata
from src.parsers.rugcheck_insiders import get_insider_network
from src.parsers.solana_tracker import get_token_risk
from src.parsers.jupiter_verify import check_jupiter_verify
# Phase 13: Deep detection modules
from src.parsers.fee_payer_cluster import cluster_by_fee_payer
from src.parsers.convergence_analyzer import analyze_convergence
from src.parsers.metadata_scorer import score_metadata
from src.parsers.rugcheck_risk_parser import parse_rugcheck_risks
from src.parsers.wallet_cluster import detect_coordinated_traders
from src.parsers.wallet_age import check_wallet_ages
from src.parsers.lp_events import detect_lp_events_onchain
from src.parsers.rug_guard import RugGuard
# Phase 15: Paid API integrations
from src.parsers.chainstack.grpc_client import ChainstackGrpcClient
# VybeClient import removed — holder PnL now computed from GMGN data
# VybeTokenHoldersPnL model still used as data container (imported locally)
from src.parsers.twitter.client import TwitterClient
from src.parsers.website_checker import check_website, WebsiteCheckResult
from src.parsers.telegram_checker.client import TelegramCheckerClient, TelegramCheckResult
from src.parsers.llm_analyzer.client import LLMAnalyzerClient, LLMAnalysisResult


async def _load_rugged_symbols_from_redis() -> None:
    """Load rugged symbols from Redis + DB on startup. Survives process restarts.

    Phase 44: Added DB fallback — loads rugged symbols from token_outcomes
    for the last 72h. This catches serial scammers like 马到成功 (44 tokens,
    7 rugs) whose Redis TTL expired during overnight gaps.
    """
    now_mono = _time_mod.monotonic()
    now_unix = _time_mod.time()

    # 1. Load from Redis (fast, in-memory)
    redis_loaded = 0
    try:
        redis = await get_redis()
        raw = await redis.hgetall(_RUGGED_SYMBOLS_REDIS_KEY)
        for sym, val in raw.items():
            # Format: "count:unix_timestamp"
            parts = val.split(":")
            if len(parts) != 2:
                continue
            count = int(parts[0])
            ts_unix = float(parts[1])
            age_seconds = now_unix - ts_unix
            _ttl = _get_copycat_ttl(count)
            if age_seconds < _ttl:
                # Convert unix time to monotonic-equivalent
                mono_ts = now_mono - age_seconds
                _RUGGED_SYMBOLS[sym.upper()] = (mono_ts, count)
                redis_loaded += 1
            else:
                # Expired — clean up from Redis
                await redis.hdel(_RUGGED_SYMBOLS_REDIS_KEY, sym)
        if redis_loaded:
            logger.info(f"[COPYCAT] Loaded {redis_loaded} rugged symbols from Redis")
    except Exception as e:
        logger.warning(f"[COPYCAT] Failed to load from Redis: {e}")

    # 2. DB fallback: load rugged symbols from token_outcomes (last 72h)
    # This catches symbols missed by Redis TTL expiry or process restarts.
    db_loaded = 0
    try:
        async with async_session_factory() as session:
            from sqlalchemy import text as sa_text

            rows = await session.execute(
                sa_text(
                    "SELECT UPPER(t.symbol) AS sym, COUNT(*) AS cnt "
                    "FROM token_outcomes o "
                    "JOIN tokens t ON t.id = o.token_id "
                    "WHERE o.is_rug = true "
                    "AND o.updated_at >= NOW() - INTERVAL '72 hours' "
                    "AND t.symbol IS NOT NULL AND t.symbol != '' "
                    "GROUP BY UPPER(t.symbol) "
                    "HAVING COUNT(*) >= 2"
                )
            )
            for row in rows:
                sym = row.sym
                cnt = row.cnt
                existing = _RUGGED_SYMBOLS.get(sym)
                if existing is None or existing[1] < cnt:
                    # DB has more rugs than Redis — update
                    _RUGGED_SYMBOLS[sym] = (now_mono, cnt)
                    db_loaded += 1
                    # Persist to Redis too
                    try:
                        redis = await get_redis()
                        await redis.hset(
                            _RUGGED_SYMBOLS_REDIS_KEY,
                            sym,
                            f"{cnt}:{now_unix:.0f}",
                        )
                    except Exception:
                        pass
        if db_loaded:
            logger.info(
                f"[COPYCAT] DB fallback: loaded {db_loaded} additional "
                f"rugged symbols (total: {len(_RUGGED_SYMBOLS)})"
            )
    except Exception as e:
        logger.warning(f"[COPYCAT] DB fallback failed: {e}")


async def _track_rugged_symbol(symbol: str) -> None:
    """Record a rugged symbol in both memory and Redis.

    Increments rug_count for repeat scammers (CASH×9, ELSTONKS×8 etc).
    """
    sym = symbol.upper()
    now_mono = _time_mod.monotonic()
    now_unix = _time_mod.time()

    # Update in-memory cache
    prev = _RUGGED_SYMBOLS.get(sym)
    count = (prev[1] + 1) if prev else 1
    _RUGGED_SYMBOLS[sym] = (now_mono, count)

    if not prev:
        logger.info(f"[COPYCAT] Tracking rugged symbol: {symbol} (count={count})")
    else:
        logger.info(f"[COPYCAT] Repeat rug: {symbol} (count={count})")

    # Persist to Redis
    try:
        redis = await get_redis()
        await redis.hset(_RUGGED_SYMBOLS_REDIS_KEY, sym, f"{count}:{now_unix:.0f}")
    except Exception as e:
        logger.warning(f"[COPYCAT] Redis persist failed: {e}")


def _check_copycat(symbol: str) -> tuple[bool, int]:
    """Check if a symbol matches a recently rugged token.

    Returns (is_copycat, rug_count).
    """
    if not symbol:
        return False, 0
    sym = symbol.upper()
    entry = _RUGGED_SYMBOLS.get(sym)
    if entry is None:
        return False, 0
    ts, count = entry
    _ttl = _get_copycat_ttl(count)
    if (_time_mod.monotonic() - ts) >= _ttl:
        return False, 0
    return True, count


async def run_parser() -> None:
    """Entry point — starts all parsers and enrichment worker."""
    gmgn_rate_limiter = RateLimiter(settings.gmgn_max_rps)
    # Build proxy pool from comma-separated env + legacy single proxy
    _proxy_pool = [p.strip() for p in settings.gmgn_proxy_pool.split(",") if p.strip()] if settings.gmgn_proxy_pool else []
    gmgn = GmgnClient(
        rate_limiter=gmgn_rate_limiter,
        proxy_url=settings.gmgn_proxy_url,
        proxy_pool=_proxy_pool,
    )
    if _proxy_pool:
        logger.info(f"GMGN proxy pool: {len(_proxy_pool)} proxies (round-robin)")
    elif settings.gmgn_proxy_url:
        logger.info("GMGN single proxy configured")
    pumpportal = PumpPortalClient()
    dexscreener = DexScreenerClient(max_rps=settings.dexscreener_max_rps)

    # Birdeye client (primary data source when enabled)
    birdeye: BirdeyeClient | None = None
    if settings.enable_birdeye and settings.birdeye_api_key:
        from src.parsers.rate_limiter import SharedRateLimiter

        birdeye_limiter = SharedRateLimiter.get_or_create(
            "birdeye", settings.birdeye_max_rps,
        )
        birdeye = BirdeyeClient(
            api_key=settings.birdeye_api_key,
            rate_limiter=birdeye_limiter,
        )
        logger.info(
            f"Birdeye Data Services enabled (shared rate limiter: "
            f"{settings.birdeye_max_rps} RPS global)"
        )

    # Phase 11: Rugcheck client (free, no key)
    rugcheck: RugcheckClient | None = None
    if settings.enable_rugcheck:
        rugcheck = RugcheckClient()
        logger.info("Rugcheck.xyz client enabled")

    # Phase 11: Jupiter API (free tier: 1 RPS, requires API key)
    jupiter: JupiterClient | None = None
    if settings.enable_jupiter:
        jupiter = JupiterClient(api_key=settings.jupiter_api_key, max_rps=1.0)
        logger.info("Jupiter API enabled (free tier, 1 RPS)")

    # Phase 12: GoPlus security client (free, 30 req/min)
    goplus: GoPlusClient | None = None
    if settings.enable_goplus:
        goplus = GoPlusClient(max_rps=0.5)
        logger.info("GoPlus Security API enabled")

    # Phase 12: Pump.fun creator history (free, no key)
    pumpfun: PumpfunClient | None = None
    if settings.enable_pumpfun_history:
        pumpfun = PumpfunClient(max_rps=2.0)
        logger.info("Pump.fun Creator History enabled")

    # Phase 12: Raydium LP verification (free, no key)
    raydium: RaydiumClient | None = None
    if settings.enable_raydium_lp:
        raydium = RaydiumClient(max_rps=5.0)
        logger.info("Raydium LP verification enabled")

    # Phase 11: Helius enhanced analysis client
    helius: HeliusClient | None = None
    if settings.enable_helius_analysis and settings.helius_api_key:
        helius = HeliusClient(
            api_key=settings.helius_api_key,
            rpc_url=settings.helius_rpc_url,
        )
        logger.info("Helius enhanced analysis enabled")

    # Phase 15: Chainstack gRPC client
    grpc_client: ChainstackGrpcClient | None = None
    if settings.enable_grpc_streaming and settings.chainstack_grpc_endpoint:
        grpc_client = ChainstackGrpcClient(
            endpoint=settings.chainstack_grpc_endpoint,
            token=settings.chainstack_grpc_token,
        )
        logger.info("Chainstack gRPC streaming enabled (sub-second latency)")

    # Phase 15: Vybe Network — DISABLED, holder PnL now computed from GMGN data
    # Kept import for VybeTokenHoldersPnL model (used as data container)
    vybe = None  # noqa: F841

    # Phase 15: TwitterAPI.io client
    twitter: TwitterClient | None = None
    if settings.enable_twitter and settings.twitter_api_key:
        twitter = TwitterClient(api_key=settings.twitter_api_key)
        logger.info("TwitterAPI.io enabled (social signals)")

    # Phase 16: Telegram group checker
    tg_checker: TelegramCheckerClient | None = None
    if settings.enable_telegram_checker and settings.rapidapi_key:
        tg_checker = TelegramCheckerClient(rapidapi_key=settings.rapidapi_key)
        logger.info("Telegram group checker enabled (RapidAPI)")

    # Phase 16: LLM analyzer
    llm_analyzer: LLMAnalyzerClient | None = None
    if settings.enable_llm_analysis and settings.openrouter_api_key:
        llm_analyzer = LLMAnalyzerClient(
            api_key=settings.openrouter_api_key,
            model=settings.llm_model,
        )
        logger.info(f"LLM analyzer enabled ({settings.llm_model})")

    # Phase 13: Bubblemaps decentralization analysis
    bubblemaps: "BubblemapsClient | None" = None
    if settings.enable_bubblemaps and settings.bubblemaps_api_key:
        from src.parsers.bubblemaps.client import BubblemapsClient
        bubblemaps = BubblemapsClient(api_key=settings.bubblemaps_api_key)
        logger.info("Bubblemaps enabled (holder clustering)")

    # Phase 13: SolSniffer cross-validation
    solsniffer: "SolSnifferClient | None" = None
    if settings.enable_solsniffer and settings.solsniffer_api_key:
        from src.parsers.solsniffer.client import SolSnifferClient
        solsniffer = SolSnifferClient(
            api_key=settings.solsniffer_api_key,
            max_rps=0.1,
        )
        logger.info(f"SolSniffer enabled (cap={settings.solsniffer_monthly_cap}/mo)")

    # Redis for queue persistence + smart money cache
    redis = None
    try:
        redis = await get_redis()
        await redis.ping()
        logger.info("Redis connected — persistent queue + smart money cache enabled")
    except Exception as e:
        logger.warning(f"Redis unavailable ({e}), using in-memory fallback")
        redis = None

    # Pass Redis to SolSniffer for persistent monthly counter
    if solsniffer and redis:
        solsniffer._redis = redis

    # Persistent enrichment queue (Redis-backed with in-memory fallback)
    enrichment_queue = PersistentEnrichmentQueue(redis=redis, maxsize=5000)
    recovered = await enrichment_queue.restore_from_redis()
    if recovered:
        logger.info(f"Restored {recovered} enrichment tasks from Redis")
        purged = await enrichment_queue.purge_stale()
        if purged:
            logger.info(f"Purged {purged} stale tasks, {recovered - purged} remaining")
        # Re-score remaining tasks with current formula (handles format changes)
        await enrichment_queue.migrate_scores()

    # Real-time alert dispatcher
    alert_dispatcher = AlertDispatcher(
        telegram_bot_token=settings.telegram_bot_token,
        telegram_admin_id=settings.telegram_admin_id,
        redis=redis,
        cooldown_sec=300,
    )

    # Health alerter (monitors pipeline metrics)
    health_alerter = HealthAlerter(pipeline_metrics)

    # Smart money tracker
    smart_money: SmartMoneyTracker | None = None
    if redis and settings.enable_gmgn:
        smart_money = SmartMoneyTracker(redis=redis, gmgn=gmgn)
        # Initial cache load
        try:
            count = await smart_money.refresh_wallets()
            logger.info(f"Smart money tracker ready ({count} wallets)")
        except Exception as e:
            logger.warning(f"Smart money initial load failed: {e}")
        # Also persist wallets to DB
        await _sync_smart_wallets_to_db(gmgn)

    # Dedup set for migrations — prevents re-enqueue of already-processed tokens
    _seen_migrations: set[str] = set()

    # PumpPortal event handlers
    async def on_new_token(token: PumpPortalNewToken) -> None:
        try:
            async with async_session_factory() as session:
                await upsert_token_from_pumpportal(session, token)
                await session.commit()
            now = asyncio.get_event_loop().time()
            task = EnrichmentTask(
                priority=EnrichmentPriority.NORMAL,
                scheduled_at=now + 5,  # Phase 12: PRE_SCAN at +5s
                address=token.mint,
                stage=EnrichmentStage.PRE_SCAN,
                fetch_security=True,
                discovery_time=now,
            )
            await enrichment_queue.put(task)
            logger.info(
                f"[PP] New token: {token.symbol or '???'} ({token.mint[:12]}...)"
            )
        except Exception as e:
            logger.error(f"[PP] Error saving token {token.mint[:12]}: {e}")

    async def on_migration(migration: PumpPortalMigration) -> None:
        if migration.mint in _seen_migrations:
            return
        _seen_migrations.add(migration.mint)
        logger.info(f"[PP] Migration to Raydium: {migration.mint[:12]}...")
        try:
            async with async_session_factory() as session:
                await upsert_token(
                    session,
                    address=migration.mint,
                    source="pumpportal_migration",
                )
                await session.commit()
            now = asyncio.get_event_loop().time()
            task = EnrichmentTask(
                priority=EnrichmentPriority.MIGRATION,
                scheduled_at=now,  # Immediate — already on Raydium
                address=migration.mint,
                stage=EnrichmentStage.INITIAL,
                fetch_security=True,
                is_migration=True,
                discovery_time=now,
            )
            await enrichment_queue.put(task)
        except Exception as e:
            logger.error(f"[PP] Error saving migration {migration.mint[:12]}: {e}")

    async def on_trade(trade: PumpPortalTrade) -> None:
        try:
            async with async_session_factory() as session:
                token = await get_token_by_address(session, trade.mint)
                if token:
                    await save_pumpportal_trade(session, token.id, trade)
                    await session.commit()
        except Exception as e:
            logger.debug(f"[PP] Trade save error: {e}")

    pumpportal.on_new_token = on_new_token
    pumpportal.on_migration = on_migration
    pumpportal.on_trade = on_trade

    # Phase 15: gRPC uses same handlers as PumpPortal (same models)
    if grpc_client:
        grpc_client.on_new_token = on_new_token
        grpc_client.on_migration = on_migration
        grpc_client.on_trade = on_trade

    # Meteora DBC setup
    rpc_url = settings.helius_rpc_url or settings.solana_rpc_url
    meteora_ws: MeteoraDBCClient | None = None
    meteora_rest: MeteoraClient | None = None

    if settings.enable_meteora_dbc and settings.helius_ws_url and rpc_url:
        meteora_ws = MeteoraDBCClient(ws_url=settings.helius_ws_url)
        meteora_rest = MeteoraClient(solana_rpc_url=rpc_url)

        async def on_dbc_new_pool(event: MeteoraNewPool) -> None:
            try:
                assert meteora_rest is not None
                tx_data = await meteora_rest.get_transaction(event.signature)
                if not tx_data:
                    logger.debug(f"[MDBC] No tx data for {event.signature[:16]}")
                    return

                pool_address, base_mint, creator, pool_config = _extract_dbc_accounts(tx_data)
                if not base_mint:
                    logger.debug(f"[MDBC] Could not extract base_mint from {event.signature[:16]}")
                    return

                pool_data = None
                if pool_address:
                    pool_data = await meteora_rest.get_virtual_pool(pool_address)

                launchpad = KNOWN_LAUNCHPADS.get(pool_config) if pool_config else None

                if not pool_data:
                    pool_data = MeteoraVirtualPool(
                        pool_address=pool_address or "",
                        creator=creator or "",
                        base_mint=base_mint,
                        quote_mint="",
                        base_reserve=0,
                        quote_reserve=0,
                        is_migrated=False,
                    )
                pool_data.launchpad = launchpad
                async with async_session_factory() as session:
                    await upsert_token_from_meteora_dbc(session, pool_data)
                    await session.commit()

                now = asyncio.get_event_loop().time()
                task = EnrichmentTask(
                    priority=EnrichmentPriority.NORMAL,
                    scheduled_at=now + 30,
                    address=base_mint,
                    stage=EnrichmentStage.PRE_SCAN,
                    fetch_security=True,
                    discovery_time=now,
                )
                await enrichment_queue.put(task)
                progress_str = ""
                if pool_data.bonding_curve_progress_pct is not None:
                    progress_str = f" curve={float(pool_data.bonding_curve_progress_pct):.1f}%"
                logger.info(
                    f"[MDBC] New DBC pool: {base_mint[:12]}... "
                    f"launchpad={launchpad or 'unknown'}{progress_str}"
                )
            except Exception as e:
                logger.error(f"[MDBC] Error handling new pool: {e}")

        async def on_dbc_migration(event: MeteoraMigration) -> None:
            try:
                assert meteora_rest is not None
                # Skip if already processed (gRPC/WS can re-deliver)
                if event.signature in _seen_migrations:
                    return
                _seen_migrations.add(event.signature)
                tx_data = await meteora_rest.get_transaction(event.signature)
                if not tx_data:
                    return

                _, base_mint, _, _ = _extract_dbc_accounts(tx_data)
                if not base_mint:
                    return
                if base_mint in _seen_migrations:
                    return
                _seen_migrations.add(base_mint)

                async with async_session_factory() as session:
                    await upsert_token(
                        session,
                        address=base_mint,
                        source="meteora_dbc_migration",
                    )
                    token = await get_token_by_address(session, base_mint)
                    if token:
                        token.dbc_is_migrated = True
                        token.dbc_migration_timestamp = datetime.now()
                        # Fetch DAMM v2 pool data post-migration
                        if token.dbc_pool_address:
                            damm = await meteora_rest.get_damm_pool(token.dbc_pool_address)
                            if damm:
                                token.dbc_damm_tvl = damm.pool_tvl
                                token.dbc_damm_volume = damm.trading_volume
                                logger.info(
                                    f"[MDBC] DAMM v2 pool TVL=${damm.pool_tvl or 0:,.0f} "
                                    f"vol=${damm.trading_volume or 0:,.0f}"
                                )
                    await session.commit()

                now = asyncio.get_event_loop().time()
                task = EnrichmentTask(
                    priority=EnrichmentPriority.MIGRATION,
                    scheduled_at=now,
                    address=base_mint,
                    stage=EnrichmentStage.PRE_SCAN,
                    fetch_security=True,
                    is_migration=True,
                    discovery_time=now,
                )
                await enrichment_queue.put(task)
                logger.info(
                    f"[MDBC] Migration to {event.migration_type}: {base_mint[:12]}..."
                )
            except Exception as e:
                logger.error(f"[MDBC] Error handling migration: {e}")

        meteora_ws.on_new_pool = on_dbc_new_pool
        meteora_ws.on_migration = on_dbc_migration

    # Paper trading engine
    paper_trader = None
    if settings.paper_trading_enabled:
        from src.parsers.paper_trader import PaperTrader

        paper_trader = PaperTrader(
            sol_per_trade=settings.paper_sol_per_trade,
            max_positions=settings.paper_max_positions,
            take_profit_x=settings.paper_take_profit_x,
            stop_loss_pct=settings.paper_stop_loss_pct,
            timeout_hours=settings.paper_timeout_hours,
            trailing_activation_x=settings.paper_trailing_activation_x,
            trailing_drawdown_pct=settings.paper_trailing_drawdown_pct,
            stagnation_timeout_min=settings.paper_stagnation_timeout_min,
            stagnation_max_pnl_pct=settings.paper_stagnation_max_pnl_pct,
            alert_dispatcher=alert_dispatcher,
        )
        logger.info("Paper trading engine enabled")

    # Real trading engine
    real_trader = None
    if settings.trading_enabled and settings.real_trading_enabled and settings.wallet_private_key:
        try:
            from src.trading.real_trader import RealTrader
            from src.trading.wallet import SolanaWallet
            from src.trading.jupiter_swap import JupiterSwapClient
            from src.trading.risk_manager import RiskManager, TradingCircuitBreaker

            rpc_url = settings.helius_rpc_url or settings.solana_rpc_url
            if not rpc_url:
                logger.error("[REAL] No RPC URL configured, real trading disabled")
            else:
                wallet = SolanaWallet(settings.wallet_private_key, rpc_url)
                swap_client = JupiterSwapClient(
                    api_key=settings.jupiter_api_key,
                    rpc_url=rpc_url,
                    keypair=wallet.keypair,
                    default_slippage_bps=settings.real_slippage_bps,
                    priority_fee_lamports=settings.real_priority_fee_lamports,
                )
                risk_mgr = RiskManager(
                    max_sol_per_trade=settings.real_sol_per_trade,
                    max_positions=settings.real_max_positions,
                    max_total_exposure_sol=settings.real_max_sol_exposure,
                    min_liquidity_usd=settings.real_min_liquidity_usd,
                )
                circuit_breaker = TradingCircuitBreaker(
                    threshold=settings.real_circuit_breaker_threshold,
                    cooldown_sec=settings.real_circuit_breaker_cooldown_sec,
                )
                real_trader = RealTrader(
                    wallet=wallet,
                    swap_client=swap_client,
                    risk_manager=risk_mgr,
                    circuit_breaker=circuit_breaker,
                    sol_per_trade=settings.real_sol_per_trade,
                    max_positions=settings.real_max_positions,
                    take_profit_x=settings.real_take_profit_x,
                    stop_loss_pct=settings.real_stop_loss_pct,
                    timeout_hours=settings.real_timeout_hours,
                    trailing_activation_x=settings.real_trailing_activation_x,
                    trailing_drawdown_pct=settings.real_trailing_drawdown_pct,
                    stagnation_timeout_min=settings.real_stagnation_timeout_min,
                    stagnation_max_pnl_pct=settings.real_stagnation_max_pnl_pct,
                    alert_dispatcher=alert_dispatcher,
                )
                logger.info(f"[REAL] Real trading enabled. Wallet: {wallet.pubkey_str}")
        except Exception as e:
            logger.error(f"[REAL] Failed to initialize real trading: {e}")
            real_trader = None

    # Phase 45: RugGuard — real-time LP removal detection via gRPC
    rug_guard: RugGuard | None = None
    if settings.enable_rug_guard and grpc_client and (paper_trader or real_trader):
        rug_guard = RugGuard(
            paper_trader=paper_trader,
            real_trader=real_trader,
            alert_dispatcher=alert_dispatcher,
        )
        grpc_client.on_lp_removal = rug_guard.on_lp_removal
        logger.info("[RUGGUARD] Enabled — real-time LP removal detection via gRPC")

    # Build task list based on feature flags
    tasks: list[asyncio.Task] = []

    # Phase 15: gRPC is primary, PumpPortal is fallback
    if grpc_client:
        tasks.append(asyncio.create_task(grpc_client.connect(), name="grpc_streaming"))
        logger.info("Chainstack gRPC streaming enabled (primary)")

    if settings.enable_pumpportal:
        tasks.append(asyncio.create_task(pumpportal.connect(), name="pumpportal_ws"))
        if grpc_client:
            logger.info("PumpPortal WebSocket enabled (fallback)")
        else:
            logger.info("PumpPortal WebSocket enabled")

    if settings.enable_meteora_dbc and meteora_ws:
        tasks.append(asyncio.create_task(meteora_ws.connect(), name="meteora_dbc_ws"))
        logger.info("Meteora DBC WebSocket enabled")

    if settings.enable_gmgn:
        tasks.append(
            asyncio.create_task(
                _polling_loop(gmgn, dexscreener, enrichment_queue), name="gmgn_polling"
            )
        )
        logger.info("gmgn.ai polling enabled")

    # Smart money refresh loop
    if smart_money:
        tasks.append(
            asyncio.create_task(smart_money.refresh_loop(), name="smart_money_refresh")
        )

    # Phase 34: Load rugged symbols from Redis before enrichment starts
    await _load_rugged_symbols_from_redis()

    # Enrichment workers always run (needs at least one data source)
    if settings.enable_birdeye or settings.enable_gmgn:
        num_workers = max(1, settings.enrichment_workers)
        for worker_idx in range(num_workers):
            tasks.append(
                asyncio.create_task(
                    _enrichment_worker(
                        gmgn, dexscreener, birdeye, enrichment_queue,
                        smart_money, alert_dispatcher, paper_trader,
                        pumpportal if settings.enable_pumpportal else None,
                        real_trader=real_trader,
                        rugcheck=rugcheck,
                        jupiter=jupiter,
                        helius=helius,
                        goplus=goplus,
                        pumpfun=pumpfun,
                        raydium=raydium,
                        vybe=vybe,
                        twitter=twitter,
                        tg_checker=tg_checker,
                        llm_analyzer=llm_analyzer,
                        bubblemaps=bubblemaps,
                        solsniffer=solsniffer,
                    ),
                    name=f"enrichment_{worker_idx}",
                )
            )
        logger.info(f"Enrichment workers started: {num_workers} parallel consumers")

    if not tasks:
        logger.warning("No parsers enabled! Check feature flags in .env")
        return

    # Stats reporter
    tasks.append(
        asyncio.create_task(
            _stats_reporter(pumpportal, meteora_ws, enrichment_queue), name="stats"
        )
    )

    # Health degradation alerter
    tasks.append(
        asyncio.create_task(health_alerter.run_loop(), name="health_alerter")
    )

    # Telegram bot (aiogram polling)
    if settings.telegram_bot_token:
        try:
            from src.bot.bot import run_bot

            tasks.append(
                asyncio.create_task(run_bot(), name="telegram_bot")
            )
            logger.info("Telegram bot task started")
        except ImportError:
            logger.debug("[BOT] aiogram not installed, skipping bot")

    # SOL/USD price feed (used by paper trader for accurate PnL)
    if birdeye or jupiter:
        from src.parsers.sol_price import sol_price_loop
        tasks.append(
            asyncio.create_task(
                sol_price_loop(birdeye_client=birdeye, jupiter_client=jupiter),
                name="sol_price",
            )
        )
        logger.info("SOL/USD price feed enabled (Birdeye primary, Jupiter fallback, 60s)")

    # Paper trading: real-time prices (15s) + sweep stale (5m) + report (1h)
    if paper_trader:
        if birdeye or jupiter:
            tasks.append(
                asyncio.create_task(
                    _paper_price_loop(paper_trader, birdeye=birdeye, jupiter=jupiter, dexscreener=dexscreener),
                    name="paper_price",
                )
            )
            logger.info("Paper trading real-time prices enabled (Birdeye primary, Jupiter fallback, 15s)")
        tasks.append(
            asyncio.create_task(
                _paper_sweep_loop(paper_trader),
                name="paper_sweep",
            )
        )
        tasks.append(
            asyncio.create_task(
                _paper_report_loop(paper_trader, alert_dispatcher),
                name="paper_report",
            )
        )
        logger.info("Paper trading sweep (5m) + report (1h) loops enabled")

    # Real trading: real-time prices (15s) + sweep stale (5m)
    if real_trader:
        if birdeye or jupiter:
            tasks.append(
                asyncio.create_task(
                    _real_price_loop(real_trader, birdeye=birdeye, jupiter=jupiter, dexscreener=dexscreener),
                    name="real_price",
                )
            )
            logger.info("[REAL] Real trading real-time prices enabled (10s)")
        tasks.append(
            asyncio.create_task(
                _real_sweep_loop(real_trader),
                name="real_sweep",
            )
        )
        logger.info("[REAL] Real trading sweep (5m) loop enabled")

    # Phase 45: RugGuard position refresh loop
    if rug_guard:
        tasks.append(
            asyncio.create_task(
                rug_guard.run_refresh_loop(),
                name="rug_guard_refresh",
            )
        )
        logger.info("[RUGGUARD] Position refresh loop enabled (30s)")

    # Signal decay loop
    if settings.signal_decay_enabled:
        tasks.append(
            asyncio.create_task(
                _signal_decay_loop(), name="signal_decay"
            )
        )
        logger.info("Signal decay enabled")

    # Data cleanup loop (prevent unbounded DB growth)
    tasks.append(
        asyncio.create_task(
            _data_cleanup_loop(), name="data_cleanup"
        )
    )
    logger.info("Data cleanup loop enabled (every 6h)")

    # Dashboard API server (same event loop)
    if settings.dashboard_enabled and settings.dashboard_admin_password:
        from src.api.metrics_registry import registry

        registry.pipeline_metrics = pipeline_metrics
        registry.pumpportal = pumpportal
        registry.grpc_client = grpc_client
        registry.meteora_ws = meteora_ws
        registry.gmgn = gmgn
        registry.enrichment_queue = enrichment_queue
        registry.alert_dispatcher = alert_dispatcher
        registry.paper_trader = paper_trader
        registry.real_trader = real_trader
        registry.rug_guard = rug_guard
        registry.solsniffer = solsniffer
        registry.redis = redis

        from src.api.server import run_dashboard_server

        tasks.append(
            asyncio.create_task(
                run_dashboard_server(), name="dashboard_api"
            )
        )
        logger.info(f"Dashboard API on port {settings.dashboard_port}")
    elif settings.dashboard_enabled:
        logger.warning("Dashboard disabled — set DASHBOARD_ADMIN_PASSWORD")

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Parser tasks cancelled")
    finally:
        await pumpportal.stop()
        await gmgn.close()
        await dexscreener.close()
        await alert_dispatcher.close()
        if birdeye:
            await birdeye.close()
        if meteora_ws:
            await meteora_ws.stop()
        if meteora_rest:
            await meteora_rest.close()
        if rugcheck:
            await rugcheck.close()
        if jupiter:
            await jupiter.close()
        if helius:
            await helius.close()
        if goplus:
            await goplus.close()
        if pumpfun:
            await pumpfun.close()
        if raydium:
            await raydium.close()
        if grpc_client:
            await grpc_client.stop()
        if vybe:
            await vybe.close()
        if twitter:
            await twitter.close()
        if tg_checker:
            await tg_checker.close()
        if llm_analyzer:
            await llm_analyzer.close()
        if bubblemaps:
            await bubblemaps.close()
        if solsniffer:
            await solsniffer.close()
        await close_redis()


async def _sync_smart_wallets_to_db(gmgn: GmgnClient) -> None:
    """Fetch smart wallets from GMGN and persist to DB."""
    from src.parsers.smart_money import WALLET_CATEGORIES

    for category in WALLET_CATEGORIES:
        try:
            wallets = await gmgn.get_smart_wallets(category=category, limit=100)
            async with async_session_factory() as session:
                for w in wallets:
                    if w.address:
                        await upsert_smart_wallet(session, w)
                await session.commit()
            logger.info(f"[SMART] Synced {len(wallets)} {category} wallets to DB")
        except GmgnError as e:
            logger.debug(f"[SMART] Failed to sync {category}: {e}")
        except Exception as e:
            logger.error(f"[SMART] DB sync error for {category}: {e}")


async def _polling_loop(
    gmgn: GmgnClient,
    dexscreener: DexScreenerClient,
    enrichment_queue: PersistentEnrichmentQueue,
) -> None:
    """Periodic REST polling of gmgn.ai endpoints."""
    while True:
        try:
            # 1. Fetch new pairs
            new_pairs = await gmgn.get_new_pairs(limit=50)
            new_count = 0
            async with async_session_factory() as session:
                for pair in new_pairs:
                    token_addr = pair.token_address
                    if not token_addr:
                        continue
                    existing = await get_token_by_address(session, token_addr)
                    if not existing:
                        await upsert_token(
                            session,
                            address=token_addr,
                            name=pair.token_name,
                            symbol=pair.token_symbol,
                            source="gmgn_new_pairs",
                        )
                        now = asyncio.get_event_loop().time()
                        task = EnrichmentTask(
                            priority=EnrichmentPriority.MIGRATION,
                            scheduled_at=now + 5,
                            address=token_addr,
                            stage=EnrichmentStage.PRE_SCAN,
                            fetch_security=True,
                            discovery_time=now,
                        )
                        await enrichment_queue.put(task)
                        new_count += 1
                await session.commit()

            logger.info(
                f"[GMGN] Fetched {len(new_pairs)} pairs, {new_count} new"
            )

            # 2. Fetch trending pump tokens
            trending = await gmgn.get_pump_trending(limit=50)
            trending_new = 0
            async with async_session_factory() as session:
                for token in trending:
                    if not token.address:
                        continue
                    existing = await get_token_by_address(session, token.address)
                    if not existing:
                        await upsert_token(
                            session,
                            address=token.address,
                            name=token.name,
                            symbol=token.symbol,
                            source="gmgn_pump_trending",
                        )
                        now = asyncio.get_event_loop().time()
                        task = EnrichmentTask(
                            priority=EnrichmentPriority.MIGRATION,
                            scheduled_at=now + 5,
                            address=token.address,
                            stage=EnrichmentStage.PRE_SCAN,
                            fetch_security=True,
                            discovery_time=now,
                        )
                        await enrichment_queue.put(task)
                        trending_new += 1
                await session.commit()

            logger.info(
                f"[GMGN] Fetched {len(trending)} trending, {trending_new} new"
            )

        except GmgnError as e:
            logger.error(f"[GMGN] Polling error: {e}")
        except Exception as e:
            logger.error(f"[GMGN] Unexpected polling error: {e}")

        # 3. Fetch DexScreener boosted tokens (discovery source)
        if settings.enable_dexscreener:
            try:
                boosted = await dexscreener.get_token_boosts()
                boost_new = 0
                async with async_session_factory() as session:
                    for item in boosted:
                        addr = item.get("tokenAddress") or item.get("address", "")
                        if not addr or item.get("chainId", "solana") != "solana":
                            continue
                        existing = await get_token_by_address(session, addr)
                        if not existing:
                            await upsert_token(
                                session,
                                address=addr,
                                name=item.get("name"),
                                symbol=item.get("symbol"),
                                source="dexscreener_boost",
                            )
                            now = asyncio.get_event_loop().time()
                            task = EnrichmentTask(
                                priority=EnrichmentPriority.NORMAL,
                                scheduled_at=now + 30,
                                address=addr,
                                stage=EnrichmentStage.PRE_SCAN,
                                fetch_security=True,
                                discovery_time=now,
                            )
                            await enrichment_queue.put(task)
                            boost_new += 1
                    await session.commit()
                if boost_new:
                    logger.info(f"[DEX] Fetched {len(boosted)} boosted, {boost_new} new")
            except Exception as e:
                logger.debug(f"[DEX] Boosts fetch error: {e}")

        await asyncio.sleep(settings.gmgn_parse_interval_sec)


async def _enrichment_worker(
    gmgn: GmgnClient,
    dexscreener: DexScreenerClient,
    birdeye: BirdeyeClient | None,
    enrichment_queue: PersistentEnrichmentQueue,
    smart_money: SmartMoneyTracker | None,
    alert_dispatcher: AlertDispatcher | None = None,
    paper_trader: "PaperTrader | None" = None,
    pumpportal: PumpPortalClient | None = None,
    *,
    real_trader: "RealTrader | None" = None,
    rugcheck: RugcheckClient | None = None,
    jupiter: JupiterClient | None = None,
    helius: HeliusClient | None = None,
    goplus: GoPlusClient | None = None,
    pumpfun: PumpfunClient | None = None,
    raydium: RaydiumClient | None = None,
    vybe: "VybeClient | None" = None,  # Disabled — holder PnL from GMGN
    twitter: TwitterClient | None = None,
    tg_checker: TelegramCheckerClient | None = None,
    llm_analyzer: LLMAnalyzerClient | None = None,
    bubblemaps: "BubblemapsClient | None" = None,
    solsniffer: "SolSnifferClient | None" = None,
) -> None:
    """Process enrichment tasks in priority order with scheduled timing.

    11 stages from +30s to +24h. Hybrid Birdeye + gmgn + DexScreener fetching.
    Birdeye is primary (when enabled), GMGN is secondary for unique data.
    Smart money check: compare top holders against cached smart wallet set.
    Migration tokens get immediate processing (priority=0).
    Prunes low-scoring tokens at MIN_5 and MIN_15.
    """
    import time as _time

    while True:
        try:
            task = await enrichment_queue.get()

            # _try_redis_get already filters by scheduled_at <= now + 2s,
            # so tasks returned here are ready or nearly ready.
            now = asyncio.get_event_loop().time()
            delay = task.scheduled_at - now
            if delay > 0:
                # Small wait for near-ready tasks (< 2s)
                await asyncio.sleep(delay)

            # Staleness check: discard tasks that are too old to be useful
            # PRE_SCAN: 5 min, INITIAL: 15 min, later stages: 3x their offset
            _STALENESS_LIMITS = {
                EnrichmentStage.PRE_SCAN: 300,   # 5 min
                EnrichmentStage.INITIAL: 900,     # 15 min
            }
            max_age = _STALENESS_LIMITS.get(
                task.stage,
                STAGE_SCHEDULE[task.stage].offset_sec * 3,
            )
            age = now - task.scheduled_at
            if age > max_age:
                logger.info(
                    f"[ENRICH] Discarding stale {task.address[:12]} at "
                    f"{task.stage.name} (age={age:.0f}s > max={max_age}s)"
                )
                pipeline_metrics.record_prune()
                await enrichment_queue.task_done()
                continue

            # Pruning check
            config = STAGE_SCHEDULE[task.stage]
            if (
                config.prune_below_score is not None
                and task.last_score is not None
                and task.last_score < config.prune_below_score
            ):
                logger.info(
                    f"[ENRICH] Pruning {task.address[:12]} at {task.stage.name} "
                    f"(score={task.last_score} < {config.prune_below_score})"
                )
                pipeline_metrics.record_prune()
                await enrichment_queue.task_done()
                continue

            score = task.last_score
            t_start = _time.monotonic()
            try:
                # Phase 12: PRE_SCAN runs lightweight checks instead of full enrichment
                if task.stage == EnrichmentStage.PRE_SCAN:
                    rpc_url = settings.helius_rpc_url or settings.solana_rpc_url
                    prescan_result = await _run_prescan(
                        task, rpc_url=rpc_url, jupiter=jupiter,
                        goplus=goplus, birdeye=birdeye,
                    )
                    if prescan_result is None:
                        # Rejected — don't schedule INITIAL
                        pipeline_metrics.record_prune()
                        await enrichment_queue.task_done()
                        continue
                    # Passed — carry risk boost to INITIAL
                    task = prescan_result

                    # Fast pre-watch alert: notify user immediately for clean tokens
                    if alert_dispatcher and settings.enable_early_watch_alerts and prescan_result.prescan_risk_boost == 0:
                        try:
                            await alert_dispatcher.dispatch(
                                TokenAlert(
                                    token_address=task.address,
                                    symbol=None,
                                    score=0,
                                    action="early_watch",
                                    reasons={"prescan_passed": "Clean token detected at T+5s"},
                                    price=None,
                                    market_cap=None,
                                    liquidity=None,
                                    source="prescan",
                                )
                            )
                        except Exception as e:
                            logger.debug(f"[PRESCAN] Alert dispatch failed: {e}")
                else:
                    score = await _enrich_token(
                        gmgn, dexscreener, birdeye, smart_money, task,
                        alert_dispatcher, paper_trader,
                        rugcheck=rugcheck, jupiter=jupiter, helius=helius,
                        goplus=goplus, pumpfun=pumpfun, raydium=raydium,
                        vybe=vybe, twitter=twitter,
                        tg_checker=tg_checker, llm_analyzer=llm_analyzer,
                        bubblemaps=bubblemaps, solsniffer=solsniffer,
                        real_trader=real_trader,
                    )
            except Exception as e:
                logger.opt(exception=True).error(
                    f"[ENRICH] Error processing {task.address[:12]} "
                    f"stage={task.stage.name}: {type(e).__name__}: {e}"
                )
            finally:
                # Latency metric (coverage is recorded inside _enrich_token)
                latency_ms = (_time.monotonic() - t_start) * 1000
                # Update latency on the stage's existing metrics entry
                pipeline_metrics.record_latency(task.stage.name, latency_ms)
                await enrichment_queue.task_done()

            # Schedule next re-enrichment stage
            await _schedule_next_stage(enrichment_queue, task, last_score=score)

            # Subscribe to token trades after INITIAL enrichment (pump.fun tokens)
            if (
                task.stage == EnrichmentStage.INITIAL
                and score is not None
                and score >= 35
                and pumpportal is not None
            ):
                try:
                    await pumpportal.subscribe_tokens_live([task.address])
                    logger.debug(f"[ENRICH] Subscribed to trades for {task.address[:12]}")
                except Exception as e:
                    logger.debug(f"[ENRICH] Token subscribe failed: {e}")
        except Exception as _worker_err:
            logger.error(f"[ENRICH] Worker loop error (recovering): {_worker_err}")
            await asyncio.sleep(5.0)


async def _run_prescan(
    task: EnrichmentTask,
    *,
    rpc_url: str = "",
    jupiter: JupiterClient | None = None,
    goplus: GoPlusClient | None = None,
    birdeye: BirdeyeClient | None = None,
) -> EnrichmentTask | None:
    """Phase 30: Two-phase PRE_SCAN — fast Birdeye filter + scam checks.

    Phase 1: Birdeye overview only (~0.3s) — reject 90%+ microcap junk.
    Phase 2: mint + jupiter + goplus (~1-2s) — only for tokens that pass phase 1.
    Returns modified task with prescan_risk_boost if passed,
    or None if hard-rejected (don't schedule INITIAL).
    """
    mint = task.address
    risk_boost = 0
    reject_reasons: list[str] = []

    # --- Phase 1: Birdeye fast filter (reject ~92% of junk in <0.5s) ---
    birdeye_overview = None
    if birdeye:
        try:
            birdeye_overview = await asyncio.wait_for(
                birdeye.get_token_overview(mint), timeout=5.0,
            )
        except Exception as e:
            logger.debug(f"[PRE_SCAN] Birdeye overview failed for {mint[:12]}: {e}")

    if birdeye_overview is not None:
        mcap = float(birdeye_overview.marketCap or 0)
        liq = float(birdeye_overview.liquidity or 0)
        if mcap > 0 and mcap < settings.prescan_min_mcap_usd:
            reject_reasons.append(f"low_mcap(${mcap:,.0f}<${settings.prescan_min_mcap_usd:,.0f})")
        if liq < settings.prescan_min_liquidity_usd:
            reject_reasons.append(f"low_liq(${liq:,.0f}<${settings.prescan_min_liquidity_usd:,.0f})")
        if reject_reasons:
            logger.info(f"[PRE_SCAN] {mint[:12]} REJECTED: {', '.join(reject_reasons)}")
            return None

    # --- Phase 2: Scam checks (only for tokens that passed phase 1) ---
    mint_info: MintInfo | None = None
    sell_sim = None
    goplus_report = None

    coros: list = []
    coro_labels: list[str] = []
    if rpc_url:
        coros.append(parse_mint_account(rpc_url, mint))
        coro_labels.append("mint")
    if jupiter:
        coros.append(jupiter.simulate_sell(mint))
        coro_labels.append("jupiter")
    if goplus:
        coros.append(goplus.get_token_security(mint))
        coro_labels.append("goplus")

    if coros:
        results = await asyncio.wait_for(
            asyncio.gather(*coros, return_exceptions=True),
            timeout=10.0,
        )
        for i, label in enumerate(coro_labels):
            r = results[i]
            if label == "mint":
                if isinstance(r, MintInfo):
                    mint_info = r
                else:
                    logger.debug(f"[PRE_SCAN] Mint parse error for {mint[:12]}: {r}")
            elif label == "jupiter":
                if not isinstance(r, Exception):
                    sell_sim = r
            elif label == "goplus":
                if not isinstance(r, Exception) and r is not None:
                    goplus_report = r

    # --- Hard reject: scam conditions ---

    if mint_info and mint_info.parse_error is None:
        # Both mint + freeze authority active = very likely scam
        if mint_info.mint_authority_active and mint_info.freeze_authority_active:
            reject_reasons.append("mint_authority+freeze_authority")

        # Token2022 with permanentDelegate or nonTransferable = guaranteed scam
        if "PERMANENT_DELEGATE" in mint_info.dangerous_extensions:
            reject_reasons.append("permanentDelegate")
        if "NON_TRANSFERABLE" in mint_info.dangerous_extensions:
            reject_reasons.append("nonTransferable")

    # Jupiter sell sim + mint authority = honeypot
    # Skip if API itself is unavailable (401, 5xx, timeout) — not a token problem
    if sell_sim and not sell_sim.sellable and sell_sim.error and not sell_sim.api_error:
        if mint_info and mint_info.mint_authority_active:
            reject_reasons.append(f"unsellable+mint_authority ({sell_sim.error})")

    # Phase 29: GoPlus confirmed honeypot = instant reject
    if goplus_report and goplus_report.is_honeypot is True:
        reject_reasons.append("goplus_honeypot")

    if reject_reasons:
        logger.info(
            f"[PRE_SCAN] {mint[:12]} REJECTED: {', '.join(reject_reasons)}"
        )
        return None

    # --- Soft flags (carry to INITIAL scoring) ---

    if mint_info and mint_info.parse_error is None:
        if "TRANSFER_FEE_CONFIG" in mint_info.risky_extensions:
            risk_boost += 10
        if "DEFAULT_ACCOUNT_STATE" in mint_info.risky_extensions:
            risk_boost += 5
        if mint_info.freeze_authority_active:
            risk_boost += 15
        if "TRANSFER_HOOK" in mint_info.dangerous_extensions:
            risk_boost += 15

    if sell_sim and not sell_sim.api_error:
        if sell_sim.price_impact_pct is not None and sell_sim.price_impact_pct > 30:
            risk_boost += 5

    flags = []
    if risk_boost > 0:
        if mint_info:
            flags.extend(mint_info.risky_extensions)
            flags.extend(mint_info.dangerous_extensions)
        if sell_sim and sell_sim.price_impact_pct and sell_sim.price_impact_pct > 30:
            flags.append(f"high_impact_{sell_sim.price_impact_pct:.0f}%")

    mcap_str = ""
    if birdeye_overview is not None:
        mcap_str = f", mcap=${float(birdeye_overview.marketCap or 0):,.0f}, liq=${float(birdeye_overview.liquidity or 0):,.0f}"
    logger.info(
        f"[PRE_SCAN] {mint[:12]} PASSED "
        f"(risk_boost={risk_boost}, flags={flags or 'none'}{mcap_str})"
    )
    # Return task with prescan_risk_boost + mint_info/sell_sim for INITIAL to use
    return EnrichmentTask(
        priority=task.priority,
        scheduled_at=task.scheduled_at,
        address=task.address,
        stage=EnrichmentStage.PRE_SCAN,
        fetch_security=task.fetch_security,
        is_migration=task.is_migration,
        discovery_time=task.discovery_time,
        last_score=task.last_score,
        prescan_risk_boost=risk_boost,
        prescan_mint_info=mint_info,
        prescan_sell_sim=sell_sim,
    )


async def _enrich_token(
    gmgn: GmgnClient,
    dexscreener: DexScreenerClient,
    birdeye: BirdeyeClient | None,
    smart_money: SmartMoneyTracker | None,
    task: EnrichmentTask,
    alert_dispatcher: AlertDispatcher | None = None,
    paper_trader: "PaperTrader | None" = None,
    *,
    rugcheck: RugcheckClient | None = None,
    jupiter: JupiterClient | None = None,
    helius: HeliusClient | None = None,
    goplus: GoPlusClient | None = None,
    pumpfun: PumpfunClient | None = None,
    raydium: RaydiumClient | None = None,
    vybe: "VybeClient | None" = None,  # Disabled — holder PnL from GMGN
    twitter: TwitterClient | None = None,
    tg_checker: TelegramCheckerClient | None = None,
    llm_analyzer: LLMAnalyzerClient | None = None,
    bubblemaps: "BubblemapsClient | None" = None,
    solsniffer: "SolSnifferClient | None" = None,
    real_trader: "RealTrader | None" = None,
) -> int | None:
    """Fetch data per stage config, save snapshot, compute score. Returns score."""
    config = STAGE_SCHEDULE[task.stage]

    async with async_session_factory() as session:
        token = await get_token_by_address(session, task.address)
        if not token:
            logger.debug(f"[ENRICH] Token {task.address[:12]} not in DB, skipping")
            return None

        enriched = False
        info = None
        birdeye_overview: BirdeyeTokenOverview | None = None
        holders: list[GmgnTopHolder] = []
        dex_pair = None
        top10_pct: Decimal | None = None
        smart_count: int | None = None
        security_data = None

        # Phase 15 defaults (set in INITIAL Batch 2, used in scoring for all stages)
        holders_in_profit_pct_val: float | None = None
        vybe_top_holder_pct_val: float | None = None
        twitter_mentions_val: int | None = None
        twitter_kol_mentions_val: int | None = None
        twitter_max_likes_val: int | None = None
        twitter_viral_val = False

        # Phase 16 defaults (set in INITIAL Batch 2, used in scoring for all stages)
        has_website_val: bool | None = None
        domain_age_days_val: int | None = None
        tg_member_count_val: int | None = None
        llm_risk_score_val: int | None = None
        holder_growth_pct_val: float | None = None

        # Creator analysis defaults (set in INITIAL Batch 2 after gather)
        pumpfun_dead_tokens_val: int | None = None

        # ============================================================
        # INITIAL stage: parallel API calls via asyncio.gather()
        # Other stages: sequential (fewer calls, different configs)
        # ============================================================
        _is_initial = task.stage == EnrichmentStage.INITIAL

        if _is_initial and config.fetch_gmgn_info:
            # --- BATCH 1: All independent external API calls in parallel ---
            async def _fetch_birdeye_overview() -> BirdeyeTokenOverview | None:
                if not birdeye:
                    return None
                try:
                    return await birdeye.get_token_overview(task.address)
                except BirdeyeApiError as e:
                    logger.debug(f"[ENRICH] Birdeye unavailable for {task.address[:12]}: {e}")
                    pipeline_metrics.record_api_error(task.stage.name, "birdeye")
                    return None

            async def _fetch_gmgn_info():
                try:
                    return await gmgn.get_token_info(task.address)
                except GmgnError as e:
                    logger.debug(f"[ENRICH] GMGN info unavailable for {task.address[:12]}: {e}")
                    pipeline_metrics.record_api_error(task.stage.name, "gmgn")
                    return None

            async def _fetch_security_birdeye():
                if not birdeye:
                    return None
                try:
                    be_sec = await birdeye.get_token_security(task.address)
                    await save_token_security_from_birdeye(session, token.id, be_sec)
                    return "birdeye_ok"
                except BirdeyeApiError:
                    pipeline_metrics.record_api_error(task.stage.name, "birdeye")
                    return None

            async def _fetch_security_gmgn():
                try:
                    sec = await gmgn.get_token_security(task.address)
                    await save_token_security(session, token.id, sec)
                    return "gmgn_ok"
                except GmgnError as e:
                    logger.debug(f"[ENRICH] Security unavailable for {task.address[:12]}: {e}")
                    pipeline_metrics.record_api_error(task.stage.name, "gmgn")
                    return None

            async def _fetch_top_holders() -> list[GmgnTopHolder]:
                try:
                    h = await gmgn.get_top_holders(task.address, limit=10)
                    return h or []
                except Exception as e:
                    logger.debug(f"[ENRICH] Top holders unavailable for {task.address[:12]}: {e}")
                    return []

            async def _fetch_metadata():
                if not birdeye or token.image_url:
                    return None
                try:
                    return await birdeye.get_token_metadata(task.address)
                except BirdeyeApiError as e:
                    logger.debug(f"[ENRICH] Metadata unavailable for {task.address[:12]}: {e}")
                    return None

            async def _fetch_jupiter_price() -> tuple[Decimal | None, str]:
                if not jupiter:
                    return None, "medium"
                try:
                    jup_price = await jupiter.get_price(task.address)
                    if jup_price and jup_price.price is not None:
                        conf = "medium"
                        if jup_price.extra_info:
                            conf = jup_price.extra_info.confidence_level
                        return jup_price.price, conf
                except Exception as e:
                    logger.debug(f"[ENRICH] Jupiter price failed: {e}")
                return None, "medium"

            async def _fetch_rugcheck():
                if not rugcheck:
                    return None
                try:
                    report = await rugcheck.get_token_report(task.address)
                    if report is not None:
                        await save_rugcheck_report(session, token.id, report)
                        if report.score >= 50:
                            logger.info(
                                f"[RUGCHECK] {token.symbol or task.address[:12]} "
                                f"score={report.score} (DANGEROUS) "
                                f"risks={[r.name for r in report.risks[:3]]}"
                            )
                    return report
                except Exception as e:
                    logger.debug(f"[ENRICH] Rugcheck failed: {e}")
                    return None

            async def _fetch_goplus():
                if not goplus:
                    return None
                try:
                    report = await goplus.get_token_security(task.address)
                    if report is not None:
                        await save_goplus_report(session, token.id, report)
                        if report.is_honeypot:
                            logger.warning(
                                f"[GOPLUS] {token.symbol or task.address[:12]} "
                                f"honeypot=True sell_tax={report.sell_tax}"
                            )
                    return report
                except Exception as e:
                    logger.debug(f"[ENRICH] GoPlus failed: {e}")
                    return None

            async def _fetch_raydium():
                if not raydium:
                    return None
                try:
                    pool = await raydium.get_pool_info(task.address)
                    if pool is not None:
                        await update_security_phase12(
                            session, token.id,
                            lp_burned_pct_raydium=Decimal(str(pool.burn_percent)),
                        )
                        logger.debug(f"[RAYDIUM] {task.address[:12]} LP burned={pool.burn_percent:.1f}%")
                    return pool
                except Exception as e:
                    logger.debug(f"[ENRICH] Raydium LP failed: {e}")
                    return None

            async def _fetch_rugcheck_insiders():
                if not settings.enable_rugcheck_insiders:
                    return None
                try:
                    return await get_insider_network(task.address)
                except Exception as e:
                    logger.debug(f"[ENRICH] RugCheck insiders failed: {e}")
                    return None

            async def _fetch_solana_tracker():
                if not settings.enable_solana_tracker:
                    return None
                try:
                    return await get_token_risk(task.address)
                except Exception as e:
                    logger.debug(f"[ENRICH] Solana Tracker failed: {e}")
                    return None

            async def _fetch_jupiter_verify():
                if not settings.enable_jupiter_verify:
                    return None
                try:
                    return await check_jupiter_verify(task.address)
                except Exception as e:
                    logger.debug(f"[ENRICH] Jupiter verify failed: {e}")
                    return None

            async def _fetch_bubblemaps():
                if not bubblemaps:
                    return None
                try:
                    return await bubblemaps.get_map_data(task.address)
                except Exception as e:
                    logger.debug(f"[ENRICH] Bubblemaps failed: {e}")
                    return None

            # Fire Batch 1: all independent API calls (SolSniffer deferred to gray-zone check)
            (
                birdeye_overview,
                holders,
                _sec_birdeye_result,
                _metadata_result,
                _jup_price_result,
                _rugcheck_report,
                _goplus_report,
                _raydium_pool,
                _insider_result,
                _st_result,
                _jv_result,
                _bubblemaps_report,
            ) = await asyncio.wait_for(
                asyncio.gather(
                    _fetch_birdeye_overview(),
                    _fetch_top_holders(),
                    _fetch_security_birdeye(),
                    _fetch_metadata(),
                    _fetch_jupiter_price(),
                    _fetch_rugcheck(),
                    _fetch_goplus(),
                    _fetch_raydium(),
                    _fetch_rugcheck_insiders(),
                    _fetch_solana_tracker(),
                    _fetch_jupiter_verify(),
                    _fetch_bubblemaps(),
                    return_exceptions=True,
                ),
                timeout=45.0,
            )

            # Sanitize: replace any leaked exceptions with None
            _batch1_vars = [
                "birdeye_overview", "holders", "_sec_birdeye_result",
                "_metadata_result", "_jup_price_result", "_rugcheck_report",
                "_goplus_report", "_raydium_pool", "_insider_result",
                "_st_result", "_jv_result", "_bubblemaps_report",
            ]
            _batch1_vals = [
                birdeye_overview, holders, _sec_birdeye_result,
                _metadata_result, _jup_price_result, _rugcheck_report,
                _goplus_report, _raydium_pool, _insider_result,
                _st_result, _jv_result, _bubblemaps_report,
            ]
            for _i, _v in enumerate(_batch1_vals):
                if isinstance(_v, BaseException):
                    logger.warning(f"[ENRICH] Batch1 {_batch1_vars[_i]} exception: {_v}")
            if isinstance(birdeye_overview, BaseException):
                birdeye_overview = None
            if isinstance(holders, BaseException):
                holders = None
            if isinstance(_sec_birdeye_result, BaseException):
                _sec_birdeye_result = None
            if isinstance(_metadata_result, BaseException):
                _metadata_result = None
            if isinstance(_jup_price_result, BaseException):
                _jup_price_result = (None, "unavailable")
            if isinstance(_rugcheck_report, BaseException):
                _rugcheck_report = None
            if isinstance(_goplus_report, BaseException):
                _goplus_report = None
            if isinstance(_raydium_pool, BaseException):
                _raydium_pool = None
            if isinstance(_insider_result, BaseException):
                _insider_result = None
            if isinstance(_st_result, BaseException):
                _st_result = None
            if isinstance(_jv_result, BaseException):
                _jv_result = None
            if isinstance(_bubblemaps_report, BaseException):
                _bubblemaps_report = None

            # Process Birdeye overview / GMGN fallback
            if birdeye_overview:
                enriched = True
            else:
                info = await _fetch_gmgn_info()
                if info:
                    enriched = True

            # Process security: if Birdeye succeeded, done; otherwise try GMGN fallback
            if _sec_birdeye_result == "birdeye_ok":
                enriched = True
            else:
                _sec_gmgn_result = await _fetch_security_gmgn()
                if _sec_gmgn_result:
                    enriched = True

            # Process holders
            if holders:
                total_pct = sum(h.percentage or Decimal(0) for h in holders[:10])
                top10_pct = total_pct
                enriched = True

            # Process metadata
            if _metadata_result:
                meta = _metadata_result
                if meta.logoURI:
                    token.image_url = meta.logoURI
                if meta.description:
                    token.description = meta.description[:2000]
                if meta.website:
                    token.website = meta.website
                if meta.twitter:
                    token.twitter = meta.twitter
                if meta.telegram:
                    token.telegram = meta.telegram
                if not token.name and meta.name:
                    token.name = meta.name
                if not token.symbol and meta.symbol:
                    token.symbol = meta.symbol

            # Process Jupiter price
            jupiter_price_val: Decimal | None = _jup_price_result[0]
            jupiter_confidence: str = _jup_price_result[1]

            # Process Rugcheck
            rugcheck_score_val: int | None = None
            if _rugcheck_report is not None:
                rugcheck_score_val = _rugcheck_report.score

            # Process GoPlus
            goplus_is_honeypot_val: bool | None = None
            goplus_critical_flags_val: list[str] | None = None
            if _goplus_report is not None:
                goplus_is_honeypot_val = _goplus_report.is_honeypot
                # Extract critical flags beyond honeypot for R31 signal
                _crit_flags: list[str] = []
                if _goplus_report.owner_can_change_balance:
                    _crit_flags.append("owner_can_change_balance")
                if _goplus_report.can_take_back_ownership:
                    _crit_flags.append("can_take_back_ownership")
                if _goplus_report.transfer_pausable:
                    _crit_flags.append("transfer_pausable")
                if _goplus_report.slippage_modifiable:
                    _crit_flags.append("slippage_modifiable")
                if _goplus_report.is_proxy:
                    _crit_flags.append("is_proxy")
                if _goplus_report.trading_cooldown:
                    _crit_flags.append("trading_cooldown")
                if _goplus_report.is_airdrop_scam:
                    _crit_flags.append("is_airdrop_scam")
                if _crit_flags:
                    goplus_critical_flags_val = _crit_flags

            # Process Raydium
            raydium_lp_burned_val: bool | None = None
            if _raydium_pool is not None:
                raydium_lp_burned_val = _raydium_pool.lp_burned

            # Process RugCheck insiders
            rugcheck_insider_score_impact = 0
            rugcheck_insider_pct_val: float | None = None
            if _insider_result:
                rugcheck_insider_score_impact = _insider_result.score_impact
                rugcheck_insider_pct_val = _insider_result.insider_pct
                if _insider_result.is_high_risk:
                    logger.info(
                        f"[RUGCHECK_INSIDERS] {task.address[:12]} "
                        f"insider_pct={_insider_result.insider_pct:.0f}% "
                        f"({_insider_result.insider_count}/{_insider_result.total_nodes})"
                    )

            # Process Solana Tracker
            solana_tracker_score_impact = 0
            solana_tracker_risk_val: int | None = None
            if _st_result:
                solana_tracker_score_impact = _st_result.score_impact
                solana_tracker_risk_val = _st_result.risk_score
                if _st_result.is_high_risk:
                    logger.info(
                        f"[SOL_TRACKER] {task.address[:12]} "
                        f"risk={_st_result.risk_score} snipers={_st_result.sniper_count}"
                    )

            # Process Jupiter Verify
            jupiter_verify_score_impact = 0
            jupiter_banned = False
            jupiter_strict = False
            if _jv_result:
                jupiter_verify_score_impact = _jv_result.score_impact
                jupiter_banned = _jv_result.is_banned
                jupiter_strict = _jv_result.is_strict
                if jupiter_strict:
                    logger.info(f"[JUP_VERIFY] {task.address[:12]} is STRICT verified")
                elif jupiter_banned:
                    logger.info(f"[JUP_VERIFY] {task.address[:12]} is BANNED")

            # Process Bubblemaps
            bubblemaps_decentralization_val: float | None = None
            if _bubblemaps_report is not None:
                bubblemaps_decentralization_val = _bubblemaps_report.decentralization_score
                if _bubblemaps_report.decentralization_score is not None:
                    if _bubblemaps_report.decentralization_score < 0.3:
                        logger.info(
                            f"[BUBBLEMAPS] {task.address[:12]} "
                            f"decentralization={_bubblemaps_report.decentralization_score:.2f} (LOW)"
                        )

            # SolSniffer: gray-zone only (conserve 5000/mo cap)
            # Skip if: already honeypot, PRE_SCAN high risk, or clearly safe
            solsniffer_score_val: int | None = None
            _need_solsniffer = (
                solsniffer is not None
                and not (goplus_is_honeypot_val is True)
                and not (jupiter_banned is True)
                and task.prescan_risk_boost < 15
                and not (
                    raydium_lp_burned_val is True
                    and rugcheck_score_val is not None
                    and rugcheck_score_val >= 80
                )
            )
            if _need_solsniffer:
                try:
                    _solsniffer_report = await solsniffer.get_token_audit(
                        task.address,
                        monthly_cap=settings.solsniffer_monthly_cap,
                    )
                    if _solsniffer_report is not None:
                        solsniffer_score_val = _solsniffer_report.snifscore
                        if _solsniffer_report.snifscore < 30:
                            logger.info(
                                f"[SOLSNIFFER] {task.address[:12]} "
                                f"score={_solsniffer_report.snifscore} (DANGEROUS)"
                            )
                except Exception as e:
                    logger.debug(f"[ENRICH] SolSniffer failed: {e}")

            # --- Phase 29: Two-phase INITIAL gate ---
            # After Batch 1, skip expensive Batch 2 (Helius deep detection, Vybe,
            # Twitter, LLM) for tokens that are clearly trash.  Saves ~65% Helius
            # credits with zero quality loss — these tokens would score 0-20 anyway.
            _skip_deep = False
            if _is_initial:
                _b1_liq = float(birdeye_overview.liquidity) if (birdeye_overview and birdeye_overview.liquidity) else None
                _b1_holders = birdeye_overview.holder if birdeye_overview else None
                _b1_rugcheck_dangers = (
                    sum(1 for r in _rugcheck_report.risks if r.level == "danger")
                    if (_rugcheck_report and _rugcheck_report.risks) else 0
                )
                _ultra_low = (_b1_liq is not None and _b1_liq < 500
                              and (_b1_holders is not None and _b1_holders < 5))
                _confirmed_hp = goplus_is_honeypot_val is True
                _multi_danger = _b1_rugcheck_dangers >= 4

                if _ultra_low or _confirmed_hp or _multi_danger:
                    _skip_deep = True
                    _skip_reason = (
                        "ultra_low_liq" if _ultra_low
                        else "goplus_honeypot" if _confirmed_hp
                        else f"rugcheck_{_b1_rugcheck_dangers}_dangers"
                    )
                    logger.info(
                        f"[ENRICH] {task.address[:12]} skip deep detection: "
                        f"{_skip_reason} (liq={_b1_liq}, holders={_b1_holders})"
                    )

            # --- BATCH 2: Calls depending on holders / creator_address ---
            async def _check_smart_money() -> tuple[int | None, float, float | None]:
                """Returns (smart_count, smart_quality, smart_money_weighted)."""
                if not (smart_money and holders):
                    return None, 0.5, None
                try:
                    holder_addrs = {h.address for h in holders if h.address}
                    smart_addrs = await smart_money.check_holders_batch(holder_addrs)
                    sc = len(smart_addrs) if smart_addrs else 0
                    sq: float = 0.5
                    smw: float | None = None
                    if sc > 0:
                        sq = await smart_money.get_wallet_quality(smart_addrs)
                        smw = await smart_money.get_weighted_count(smart_addrs)
                        logger.info(
                            f"[SMART] {token.symbol or task.address[:12]} has "
                            f"{sc} smart wallet(s) (quality={sq:.2f})"
                        )
                    return sc, sq, smw
                except Exception as e:
                    logger.debug(f"[ENRICH] Smart money check failed: {e}")
                    return None, 0.5, None

            async def _check_convergence() -> bool:
                if not (helius and holders and settings.enable_convergence_analysis):
                    return False
                try:
                    buyer_addrs = [h.address for h in holders if h.address]
                    conv = await analyze_convergence(
                        helius, task.address, buyer_addrs,
                        creator_address=token.creator_address or "",
                        max_buyers=settings.convergence_max_buyers,
                    )
                    if conv is not None and conv.converging:
                        logger.info(
                            f"[CONVERGENCE] {token.symbol or task.address[:12]} "
                            f"convergence detected: {conv.convergence_pct:.0%} to {conv.main_destination[:12] if conv.main_destination else 'unknown'}"
                        )
                        return True
                except Exception as e:
                    logger.debug(f"[ENRICH] Convergence analysis failed: {e}")
                return False

            async def _check_wallet_ages_batch() -> int:
                if not (helius and holders):
                    return 0
                try:
                    addrs = [h.address for h in holders[:settings.wallet_age_max_wallets] if h.address]
                    if addrs:
                        result = await check_wallet_ages(helius, addrs)
                        if result is not None:
                            if result.is_sybil_suspected:
                                logger.info(
                                    f"[WALLET_AGE] {token.symbol or task.address[:12]} "
                                    f"sybil suspected: {result.pct_under_1h:.0%} under 1h"
                                )
                            return result.score_impact
                except Exception as e:
                    logger.debug(f"[ENRICH] Wallet age check failed: {e}")
                return 0

            async def _run_creator_analysis() -> tuple:
                """Returns (creator_prof, funding_chain_risk, pumpfun_dead_tokens, bundled_buy)."""
                _creator_prof = None
                _funding_risk: int | None = None
                _pf_dead: int | None = None
                _bundled: bool = False

                if not token.creator_address:
                    return _creator_prof, _funding_risk, _pf_dead, _bundled

                # Creator profiling
                try:
                    from src.parsers.creator_profiler import profile_creator
                    _creator_prof = await profile_creator(session, token.creator_address)
                except Exception as e:
                    logger.debug(f"[ENRICH] Creator profiling failed: {e}")

                # Creator risk assessment
                try:
                    cr_risk, _is_first = await assess_creator_risk(
                        session, token.creator_address
                    )
                    if _creator_prof is None:
                        from src.models.token import CreatorProfile as _CP
                        _creator_prof = _CP(
                            address=token.creator_address, risk_score=cr_risk,
                        )
                    elif (_creator_prof.risk_score or 0) < cr_risk:
                        _creator_prof.risk_score = cr_risk
                except Exception as e:
                    logger.debug(f"[ENRICH] Creator trace failed: {e}")

                # Funding trace (Helius, 3-hop)
                if helius:
                    try:
                        from src.parsers.funding_trace import trace_creator_funding
                        funding = await trace_creator_funding(
                            session, helius, token.creator_address,
                            max_hops=settings.funding_trace_max_hops,
                        )
                        if funding:
                            _funding_risk = funding.funding_risk
                            if funding.funding_risk > 30:
                                logger.info(
                                    f"[FUNDING] {token.symbol or task.address[:12]} "
                                    f"creator funded by {funding.funder or 'unknown'} "
                                    f"risk={funding.funding_risk} hops={funding.chain_depth} "
                                    f"({funding.reason})"
                                )
                            if _creator_prof and (_creator_prof.risk_score or 0) < funding.funding_risk:
                                _creator_prof.risk_score = funding.funding_risk
                    except Exception as e:
                        logger.debug(f"[ENRICH] Funding trace failed: {e}")

                # Creator repeat launch check
                try:
                    activity = await check_creator_recent_launches(
                        session, token.creator_address
                    )
                    if activity and activity.is_serial_launcher:
                        logger.info(
                            f"[CREATOR] Serial launcher: {token.creator_address[:12]} "
                            f"({activity.recent_launches} launches in 4h)"
                        )
                        if _creator_prof:
                            _creator_prof.risk_score = max(
                                _creator_prof.risk_score or 0, activity.risk_boost,
                            )
                except Exception as e:
                    logger.debug(f"[ENRICH] Creator repeat check failed: {e}")

                # Pump.fun creator history
                if pumpfun and token.source == "pumpportal":
                    try:
                        pf_history = await pumpfun.get_creator_history(token.creator_address)
                        if pf_history is not None:
                            _pf_dead = pf_history.dead_token_count
                            await update_creator_pumpfun(
                                session, token.creator_address, pf_history.dead_token_count
                            )
                            if pf_history.risk_boost > 0 and _creator_prof:
                                _creator_prof.risk_score = max(
                                    _creator_prof.risk_score or 0, pf_history.risk_boost,
                                )
                            if pf_history.is_serial_scammer:
                                logger.info(
                                    f"[PUMPFUN] Serial scammer: {token.creator_address[:12]} "
                                    f"({pf_history.dead_token_count} dead tokens)"
                                )
                    except Exception as e:
                        logger.debug(f"[ENRICH] Pump.fun history failed: {e}")

                # Bundled buy detection (Helius)
                if helius:
                    try:
                        bundled_result = await detect_bundled_buys(
                            helius, task.address, token.creator_address
                        )
                        if bundled_result is not None and bundled_result.is_bundled:
                            _bundled = True
                            await update_security_phase12(
                                session, token.id, bundled_buy_detected=True,
                            )
                            logger.info(
                                f"[BUNDLED] {token.symbol or task.address[:12]} "
                                f"{bundled_result.funded_by_creator}/{bundled_result.first_block_buyers} "
                                f"first-block buyers funded by creator"
                            )
                    except Exception as e:
                        logger.debug(f"[ENRICH] Bundled buy detection failed: {e}")

                return _creator_prof, _funding_risk, _pf_dead, _bundled

            async def _check_fee_payer_cluster() -> float | None:
                if not (helius and token.creator_address and settings.enable_fee_payer_clustering):
                    return None
                try:
                    fp = await cluster_by_fee_payer(
                        helius, task.address, token.creator_address,
                    )
                    if fp is not None:
                        if fp.sybil_score > 0.5:
                            logger.info(
                                f"[SYBIL] {token.symbol or task.address[:12]} "
                                f"sybil_score={fp.sybil_score:.2f} clusters={fp.cluster_count}"
                            )
                        return fp.sybil_score
                except Exception as e:
                    logger.debug(f"[ENRICH] Fee payer clustering failed: {e}")
                return None

            async def _check_jito_bundle() -> tuple[int, bool]:
                if not (helius and settings.enable_jito_detection):
                    return 0, False
                try:
                    result = await detect_jito_bundle(
                        helius, task.address, creator_address=token.creator_address
                    )
                    if result.jito_bundle_detected:
                        logger.info(
                            f"[JITO] Bundle detected for {task.address[:12]}: "
                            f"{result.sniper_count} snipers, tip={result.tip_amount_sol} SOL"
                        )
                    return result.score_impact, result.jito_bundle_detected
                except Exception as e:
                    logger.debug(f"[ENRICH] Jito detection failed: {e}")
                return 0, False

            async def _check_metaplex() -> tuple[int, bool | None, bool]:
                if not (helius and settings.enable_metaplex_check):
                    return 0, None, False
                try:
                    result = await check_metaplex_metadata(helius, task.address)
                    if result:
                        if result.risk_flags:
                            logger.info(
                                f"[METAPLEX] {task.address[:12]} flags: "
                                f"{', '.join(result.risk_flags)}"
                            )
                        return result.score_impact, result.is_mutable, result.has_homoglyphs
                except Exception as e:
                    logger.debug(f"[ENRICH] Metaplex check failed: {e}")
                return 0, None, False

            # Phase 15: Holder PnL analysis — computed from GMGN holders (no API call)
            # Previously used Vybe Network API, now derived from GMGN data for free
            async def _fetch_vybe_pnl() -> "VybeTokenHoldersPnL | None":
                # Compute from GMGN holders already fetched in Batch 1
                if not holders:
                    return None
                try:
                    from src.parsers.vybe.models import VybeTokenHoldersPnL
                    holders_with_pnl = [h for h in holders if h.pnl is not None]
                    if not holders_with_pnl:
                        return None
                    in_profit = sum(1 for h in holders_with_pnl if h.pnl > 0)
                    in_loss = len(holders_with_pnl) - in_profit
                    pct = in_profit / len(holders_with_pnl) * 100 if holders_with_pnl else 0
                    top_pct = float(holders[0].percentage or 0) if holders else 0
                    result = VybeTokenHoldersPnL(
                        total_holders_checked=len(holders_with_pnl),
                        holders_in_profit=in_profit,
                        holders_in_loss=in_loss,
                        holders_in_profit_pct=pct,
                        avg_pnl_usd=0.0,  # GMGN doesn't provide USD PnL, not used in scoring
                        top_holder_pct=top_pct,
                    )
                    if result.total_holders_checked > 0:
                        logger.info(
                            f"[GMGN-PNL] {token.symbol or task.address[:12]} "
                            f"holders_in_profit={result.holders_in_profit_pct:.0f}% "
                            f"({result.holders_in_profit}/{result.total_holders_checked})"
                        )
                    return result
                except Exception as e:
                    logger.debug(f"[ENRICH] GMGN holder PnL calc failed: {e}")
                    return None

            # Phase 15: Twitter social signals (skip tokens without searchable name)
            async def _fetch_twitter() -> "TwitterSearchResult | None":
                if not twitter:
                    return None
                # Don't waste API credits on tokens without symbol/name
                sym = token.symbol or ""
                nam = token.name or ""
                if len(sym) < 2 and len(nam) < 3:
                    logger.debug(
                        f"[TWITTER] Skipping {task.address[:12]} — no symbol/name"
                    )
                    return None
                try:
                    from src.parsers.twitter.models import TwitterSearchResult
                    result = await twitter.search_token(
                        symbol=token.symbol,
                        name=token.name,
                        mint_address=task.address,
                    )
                    if result.total_tweets > 0:
                        logger.info(
                            f"[TWITTER] {token.symbol or task.address[:12]} "
                            f"tweets={result.total_tweets} kol={result.kol_mentions} "
                            f"engagement={result.total_engagement}"
                        )
                    return result
                except Exception as e:
                    logger.debug(f"[ENRICH] Twitter search failed: {e}")
                    return None

            # Phase 16: Website check
            async def _fetch_website() -> WebsiteCheckResult | None:
                website_url = token.website
                if not website_url or not settings.enable_website_checker:
                    return None
                try:
                    result = await check_website(website_url)
                    if result.is_reachable:
                        logger.info(
                            f"[WEBSITE] {token.symbol or task.address[:12]} reachable, "
                            f"domain_age={result.domain_age_days}d, ssl={result.has_ssl}"
                        )
                    return result
                except Exception as e:
                    logger.debug(f"[ENRICH] Website check failed: {e}")
                    return None

            # Phase 16: Telegram group check
            async def _fetch_telegram() -> TelegramCheckResult | None:
                tg_link = token.telegram
                if not tg_link or not tg_checker:
                    return None
                try:
                    tg_username = tg_link.rstrip("/").split("/")[-1]
                    result = await tg_checker.check_channel(tg_username)
                    if result.exists:
                        logger.info(
                            f"[TELEGRAM] {token.symbol or task.address[:12]} "
                            f"members={result.member_count} msgs={result.recent_messages}"
                        )
                    return result
                except Exception as e:
                    logger.debug(f"[ENRICH] Telegram check failed: {e}")
                    return None

            # Phase 16: LLM analysis
            async def _fetch_llm() -> LLMAnalysisResult | None:
                if not llm_analyzer:
                    return None
                try:
                    result = await llm_analyzer.analyze_token(
                        symbol=token.symbol,
                        name=token.name,
                        description=token.description,
                        website_url=token.website,
                        twitter_handle=token.twitter,
                        creator_token_count=(
                            pumpfun_dead_tokens_val if pumpfun_dead_tokens_val else None
                        ),
                        top10_holder_pct=(
                            float(top10_pct) if top10_pct is not None else None
                        ),
                    )
                    if result.red_flags:
                        logger.info(
                            f"[LLM] {token.symbol or task.address[:12]} risk={result.risk_score} "
                            f"flags={result.red_flags}"
                        )
                    return result
                except Exception as e:
                    logger.debug(f"[ENRICH] LLM analysis failed: {e}")
                    return None

            # Fire Batch 2: holder-dependent + creator-dependent + Helius + Phase 15 calls
            # Phase 29: skip entire Batch 2 for confirmed trash tokens
            if not _skip_deep:
                (
                    _sm_result,
                    convergence_detected_val,
                    wallet_age_impact,
                    _creator_result,
                    fee_payer_sybil_val,
                    _jito_result,
                    _metaplex_result,
                    _vybe_result,
                    _twitter_result,
                    _website_result,
                    _tg_result,
                    _llm_result,
                ) = await asyncio.wait_for(
                    asyncio.gather(
                        _check_smart_money(),
                        _check_convergence(),
                        _check_wallet_ages_batch(),
                        _run_creator_analysis(),
                        _check_fee_payer_cluster(),
                        _check_jito_bundle(),
                        _check_metaplex(),
                        _fetch_vybe_pnl(),
                        _fetch_twitter(),
                        _fetch_website(),
                        _fetch_telegram(),
                        _fetch_llm(),
                        return_exceptions=True,
                    ),
                    timeout=45.0,
                )

                # Sanitize Batch 2: replace leaked exceptions with safe defaults
                _batch2_names = [
                    "_sm_result", "convergence_detected_val", "wallet_age_impact",
                    "_creator_result", "fee_payer_sybil_val", "_jito_result",
                    "_metaplex_result", "_vybe_result", "_twitter_result",
                    "_website_result", "_tg_result", "_llm_result",
                ]
                _batch2_vals = [
                    _sm_result, convergence_detected_val, wallet_age_impact,
                    _creator_result, fee_payer_sybil_val, _jito_result,
                    _metaplex_result, _vybe_result, _twitter_result,
                    _website_result, _tg_result, _llm_result,
                ]
                for _i, _v in enumerate(_batch2_vals):
                    if isinstance(_v, BaseException):
                        logger.warning(f"[ENRICH] Batch2 {_batch2_names[_i]} exception: {_v}")
                if isinstance(_sm_result, BaseException):
                    _sm_result = (0, 0, 0.0)
                if isinstance(convergence_detected_val, BaseException):
                    convergence_detected_val = False
                if isinstance(wallet_age_impact, BaseException):
                    wallet_age_impact = 0
                if isinstance(_creator_result, BaseException):
                    _creator_result = (None, 0, 0, False)
                if isinstance(fee_payer_sybil_val, BaseException):
                    fee_payer_sybil_val = False
                if isinstance(_jito_result, BaseException):
                    _jito_result = (0, False)
                if isinstance(_metaplex_result, BaseException):
                    _metaplex_result = (0, False, False)
                if isinstance(_vybe_result, BaseException):
                    _vybe_result = None
                if isinstance(_twitter_result, BaseException):
                    _twitter_result = None
                if isinstance(_website_result, BaseException):
                    _website_result = None
                if isinstance(_tg_result, BaseException):
                    _tg_result = None
                if isinstance(_llm_result, BaseException):
                    _llm_result = None

                # Unpack Batch 2 results
                smart_count, smart_quality, smart_money_weighted_val = _sm_result
                creator_prof, funding_chain_risk_val, pumpfun_dead_tokens_val, bundled_buy_val = _creator_result
                jito_score_impact, jito_detected = _jito_result
                metaplex_score_impact, metaplex_mutable_val, metaplex_homoglyphs_val = _metaplex_result

                # Phase 15 results
                holders_in_profit_pct_val: float | None = None
                vybe_top_holder_pct_val: float | None = None
                if _vybe_result:
                    holders_in_profit_pct_val = _vybe_result.holders_in_profit_pct
                    vybe_top_holder_pct_val = _vybe_result.top_holder_pct

                twitter_mentions_val: int | None = None
                twitter_kol_mentions_val: int | None = None
                twitter_max_likes_val: int | None = None
                twitter_viral_val = False
                if _twitter_result:
                    twitter_mentions_val = _twitter_result.total_tweets
                    twitter_kol_mentions_val = _twitter_result.kol_mentions
                    twitter_max_likes_val = _twitter_result.max_likes
                    twitter_viral_val = _twitter_result.max_likes >= 1000

                # Phase 16 results
                if _website_result:
                    has_website_val = _website_result.is_reachable
                    domain_age_days_val = _website_result.domain_age_days

                if _tg_result and _tg_result.exists:
                    tg_member_count_val = _tg_result.member_count

                if _llm_result:
                    llm_risk_score_val = _llm_result.risk_score

            else:
                # Phase 29: safe defaults when deep detection is skipped
                smart_count: int | None = None
                smart_quality: float = 0.5
                smart_money_weighted_val: float | None = None
                convergence_detected_val: bool = False
                wallet_age_impact: int = 0
                creator_prof = None
                funding_chain_risk_val: int | None = None
                pumpfun_dead_tokens_val: int | None = None
                bundled_buy_val: bool = False
                fee_payer_sybil_val: bool = False
                jito_score_impact: int = 0
                jito_detected: bool = False
                metaplex_score_impact: int = 0
                metaplex_mutable_val: bool = False
                metaplex_homoglyphs_val: bool = False
                holders_in_profit_pct_val: float | None = None
                vybe_top_holder_pct_val: float | None = None
                twitter_mentions_val: int | None = None
                twitter_kol_mentions_val: int | None = None
                twitter_max_likes_val: int | None = None
                twitter_viral_val: bool = False

            # PRE_SCAN risk boost + sell sim result
            mint_risk_boost_val = task.prescan_risk_boost or 0
            _prescan_sim = task.prescan_sell_sim
            sell_sim_failed_val = bool(
                _prescan_sim and not _prescan_sim.sellable and _prescan_sim.error
            )

            # Smart money effective count
            effective_smart_count = smart_count
            if smart_count and smart_count > 0 and smart_quality < 0.3:
                effective_smart_count = max(1, smart_count - 1)
            elif smart_count and smart_count > 0 and smart_quality >= 0.7:
                effective_smart_count = smart_count + 1

            # Compute holder growth % vs previous snapshot (before saving)
            current_holders = (
                (birdeye_overview.holder if birdeye_overview else None)
                or (info.holder_count if info else None)
            )
            if current_holders and current_holders > 0:
                prev_snap_for_growth = await get_latest_snapshot(session, token.id)
                if (
                    prev_snap_for_growth
                    and prev_snap_for_growth.holders_count
                    and prev_snap_for_growth.holders_count > 0
                ):
                    holder_growth_pct_val = (
                        (current_holders - prev_snap_for_growth.holders_count)
                        / prev_snap_for_growth.holders_count * 100
                    )

            # Save snapshot
            snapshot = None
            if enriched:
                snapshot = await save_token_snapshot(
                    session, token.id, info,
                    stage=task.stage.name,
                    dex_data=dex_pair,
                    birdeye_data=birdeye_overview,
                    top10_pct=top10_pct,
                    smart_wallets_count=effective_smart_count,
                    jupiter_price=jupiter_price_val,
                    # Phase 15: Vybe + Twitter
                    holders_in_profit_pct=(
                        Decimal(str(holders_in_profit_pct_val))
                        if holders_in_profit_pct_val is not None else None
                    ),
                    vybe_top_holder_pct=(
                        Decimal(str(vybe_top_holder_pct_val))
                        if vybe_top_holder_pct_val is not None else None
                    ),
                    twitter_mentions=twitter_mentions_val,
                    twitter_kol_mentions=twitter_kol_mentions_val,
                    twitter_max_likes=twitter_max_likes_val,
                    # Phase 16: Enrichment
                    holder_growth_pct=(
                        Decimal(str(holder_growth_pct_val))
                        if holder_growth_pct_val is not None else None
                    ),
                    has_website=has_website_val,
                    domain_age_days=domain_age_days_val,
                    tg_member_count=tg_member_count_val,
                    llm_risk_score=llm_risk_score_val,
                )

        else:
            # === NON-INITIAL stages: parallelized where possible ===

            # --- Parallel Batch 1: Independent API calls ---
            async def _ni_birdeye_overview() -> object | None:
                if not birdeye:
                    return None
                try:
                    return await birdeye.get_token_overview(task.address)
                except BirdeyeApiError as e:
                    logger.debug(f"[ENRICH] Birdeye unavailable for {task.address[:12]}: {e}")
                    pipeline_metrics.record_api_error(task.stage.name, "birdeye")
                    return None

            async def _ni_holders() -> list | None:
                if not config.fetch_top_holders:
                    return None
                try:
                    return await gmgn.get_top_holders(task.address, limit=10)
                except Exception as e:
                    logger.debug(f"[ENRICH] Top holders unavailable for {task.address[:12]}: {e}")
                    return None

            async def _ni_dexscreener() -> object | None:
                if not (config.fetch_dexscreener and settings.enable_dexscreener):
                    return None
                try:
                    pairs = await dexscreener.get_token_pairs(task.address)
                    if pairs:
                        # Phase 30: Only consider pairs where our token is baseToken
                        # (priceUsd = price of baseToken; if our token is quoteToken,
                        # priceUsd would be price of SOL/USDC — garbage data)
                        valid_pairs = [
                            p for p in pairs
                            if p.baseToken and p.baseToken.address.lower() == task.address.lower()
                        ]
                        if valid_pairs:
                            return max(
                                valid_pairs,
                                key=lambda p: (
                                    p.liquidity.usd if p.liquidity and p.liquidity.usd else Decimal(0)
                                ),
                            )
                        # Fallback: if no pairs have our token as base, use highest-liq pair
                        # (some DEXes may not set baseToken correctly)
                        return max(
                            pairs,
                            key=lambda p: (
                                p.liquidity.usd if p.liquidity and p.liquidity.usd else Decimal(0)
                            ),
                        )
                except Exception as e:
                    logger.debug(f"[ENRICH] DexScreener unavailable for {task.address[:12]}: {e}")
                    pipeline_metrics.record_api_error(task.stage.name, "dexscreener")
                return None

            async def _ni_ohlcv() -> list | None:
                if not (config.fetch_ohlcv and birdeye):
                    return None
                try:
                    return await birdeye.get_ohlcv(task.address, interval="5m")
                except BirdeyeApiError as e:
                    logger.debug(f"[ENRICH] OHLCV unavailable for {task.address[:12]}: {e}")
                    return None

            async def _ni_trades() -> list | None:
                if not (config.fetch_trades and birdeye):
                    return None
                try:
                    return await birdeye.get_trades(task.address, limit=50)
                except BirdeyeApiError as e:
                    logger.debug(f"[ENRICH] Trades unavailable for {task.address[:12]}: {e}")
                    return None

            async def _ni_security() -> None:
                """Fetch security — side-effect only (saves to DB)."""
                if not config.fetch_security:
                    return
                if birdeye:
                    try:
                        be_sec = await birdeye.get_token_security(task.address)
                        await save_token_security_from_birdeye(session, token.id, be_sec)
                        return
                    except BirdeyeApiError:
                        pipeline_metrics.record_api_error(task.stage.name, "birdeye")
                # Fallback to GMGN
                try:
                    security = await gmgn.get_token_security(task.address)
                    await save_token_security(session, token.id, security)
                except GmgnError as e:
                    logger.debug(f"[ENRICH] Security unavailable for {task.address[:12]}: {e}")
                    pipeline_metrics.record_api_error(task.stage.name, "gmgn")

            (
                birdeye_overview,
                holders,
                dex_pair,
                ohlcv_items,
                trade_items,
                _,  # security is side-effect
            ) = await asyncio.wait_for(
                asyncio.gather(
                    _ni_birdeye_overview(),
                    _ni_holders(),
                    _ni_dexscreener(),
                    _ni_ohlcv(),
                    _ni_trades(),
                    _ni_security(),
                    return_exceptions=True,
                ),
                timeout=30.0,
            )

            # Sanitize non-INITIAL gather results
            if isinstance(birdeye_overview, BaseException):
                logger.warning(f"[ENRICH] NI birdeye_overview exception: {birdeye_overview}")
                birdeye_overview = None
            if isinstance(holders, BaseException):
                logger.warning(f"[ENRICH] NI holders exception: {holders}")
                holders = None
            if isinstance(dex_pair, BaseException):
                logger.warning(f"[ENRICH] NI dex_pair exception: {dex_pair}")
                dex_pair = None
            if isinstance(ohlcv_items, BaseException):
                logger.warning(f"[ENRICH] NI ohlcv exception: {ohlcv_items}")
                ohlcv_items = None
            if isinstance(trade_items, BaseException):
                logger.warning(f"[ENRICH] NI trades exception: {trade_items}")
                trade_items = None

            if birdeye_overview or holders or dex_pair or ohlcv_items or trade_items:
                enriched = True

            # GMGN fallback for token info (only if Birdeye failed)
            if config.fetch_gmgn_info and not birdeye_overview:
                try:
                    info = await gmgn.get_token_info(task.address)
                    enriched = True
                except GmgnError as e:
                    logger.debug(f"[ENRICH] GMGN info unavailable for {task.address[:12]}: {e}")
                    pipeline_metrics.record_api_error(task.stage.name, "gmgn")

            # Process holders results
            if holders:
                total_pct = sum(h.percentage or Decimal(0) for h in holders[:10])
                top10_pct = total_pct
                # Compute holder PnL from GMGN data (replaces Vybe for non-INITIAL)
                _h_with_pnl = [h for h in holders if h.pnl is not None]
                if _h_with_pnl:
                    _in_profit = sum(1 for h in _h_with_pnl if h.pnl > 0)
                    holders_in_profit_pct_val = _in_profit / len(_h_with_pnl) * 100
                    vybe_top_holder_pct_val = float(holders[0].percentage or 0)

            # Save OHLCV and trades
            if ohlcv_items:
                saved = await save_ohlcv(session, token.id, ohlcv_items, interval="5m")
                if saved:
                    logger.debug(f"[ENRICH] Saved {saved} OHLCV candles for {task.address[:12]}")
            if trade_items:
                saved = await save_birdeye_trades(session, token.id, trade_items)
                if saved:
                    logger.debug(f"[ENRICH] Saved {saved} trades for {task.address[:12]}")

            # --- Sequential: Smart money (depends on holders) + Jupiter price ---
            smart_quality: float = 0.5
            if config.check_smart_money and smart_money and holders:
                try:
                    holder_addrs = {h.address for h in holders if h.address}
                    smart_addrs = await smart_money.check_holders_batch(holder_addrs)
                    smart_count = len(smart_addrs) if smart_addrs else 0
                    if smart_count > 0:
                        smart_quality = await smart_money.get_wallet_quality(smart_addrs)
                        logger.info(
                            f"[SMART] {token.symbol or task.address[:12]} has "
                            f"{smart_count} smart wallet(s) (quality={smart_quality:.2f})"
                        )
                except Exception as e:
                    logger.debug(f"[ENRICH] Smart money check failed: {e}")

            # Jupiter price — only if Birdeye overview didn't provide price
            jupiter_price_val: Decimal | None = None
            jupiter_confidence = "medium"
            if jupiter and enriched and not (birdeye_overview and birdeye_overview.price):
                try:
                    jup_price = await jupiter.get_price(task.address)
                    if jup_price and jup_price.price is not None:
                        jupiter_price_val = jup_price.price
                        if jup_price.extra_info:
                            jupiter_confidence = jup_price.extra_info.confidence_level
                except Exception as e:
                    logger.debug(f"[ENRICH] Jupiter price failed: {e}")

            # Save snapshot
            effective_smart_count = smart_count
            if smart_count and smart_count > 0 and smart_quality < 0.3:
                effective_smart_count = max(1, smart_count - 1)
            elif smart_count and smart_count > 0 and smart_quality >= 0.7:
                effective_smart_count = smart_count + 1

            # Compute holder growth % vs previous snapshot (non-INITIAL)
            current_holders_ni = (
                (birdeye_overview.holder if birdeye_overview else None)
                or (info.holder_count if info else None)
            )
            if current_holders_ni and current_holders_ni > 0:
                prev_snap_ni = await get_latest_snapshot(session, token.id)
                if (
                    prev_snap_ni
                    and prev_snap_ni.holders_count
                    and prev_snap_ni.holders_count > 0
                ):
                    holder_growth_pct_val = (
                        (current_holders_ni - prev_snap_ni.holders_count)
                        / prev_snap_ni.holders_count * 100
                    )

            snapshot = None
            if enriched:
                snapshot = await save_token_snapshot(
                    session, token.id, info,
                    stage=task.stage.name,
                    dex_data=dex_pair,
                    birdeye_data=birdeye_overview,
                    top10_pct=top10_pct,
                    smart_wallets_count=effective_smart_count,
                    jupiter_price=jupiter_price_val,
                    # Phase 15: pass through (defaults for non-INITIAL)
                    holders_in_profit_pct=(
                        Decimal(str(holders_in_profit_pct_val))
                        if holders_in_profit_pct_val is not None else None
                    ),
                    vybe_top_holder_pct=(
                        Decimal(str(vybe_top_holder_pct_val))
                        if vybe_top_holder_pct_val is not None else None
                    ),
                    twitter_mentions=twitter_mentions_val,
                    twitter_kol_mentions=twitter_kol_mentions_val,
                    twitter_max_likes=twitter_max_likes_val,
                    # Phase 16: Enrichment
                    holder_growth_pct=(
                        Decimal(str(holder_growth_pct_val))
                        if holder_growth_pct_val is not None else None
                    ),
                    has_website=None,
                    domain_age_days=None,
                    tg_member_count=None,
                    llm_risk_score=None,
                )

            # Initialize variables that INITIAL path sets but non-INITIAL needs as defaults
            rugcheck_score_val: int | None = None
            goplus_is_honeypot_val: bool | None = None
            raydium_lp_burned_val: bool | None = None
            rugcheck_insider_score_impact = 0
            rugcheck_insider_pct_val: float | None = None
            solana_tracker_score_impact = 0
            solana_tracker_risk_val: int | None = None
            jupiter_verify_score_impact = 0
            jupiter_banned = False
            jupiter_strict = False
            smart_money_weighted_val: float | None = None
            creator_prof = None
            funding_chain_risk_val: int | None = None
            pumpfun_dead_tokens_val: int | None = None
            bundled_buy_val: bool = False
            goplus_critical_flags_val: list[str] | None = None
            bubblemaps_decentralization_val: float | None = None
            solsniffer_score_val: int | None = None
            convergence_detected_val: bool = False
            wallet_age_impact = 0
            fee_payer_sybil_val: float | None = None
            jito_score_impact = 0
            jito_detected = False
            metaplex_score_impact = 0
            metaplex_mutable_val: bool | None = None
            metaplex_homoglyphs_val = False
            mint_risk_boost_val = task.prescan_risk_boost or 0
            _prescan_sim_ni = task.prescan_sell_sim
            sell_sim_failed_val = bool(
                _prescan_sim_ni and not _prescan_sim_ni.sellable and _prescan_sim_ni.error
            )

        # 11. Save top holders linked to snapshot
        if holders and snapshot:
            await save_top_holders(session, snapshot.id, token.id, holders)

        # 11b. Whale dynamics analysis (requires 2+ snapshots with holders)
        whale_patterns: list = []
        if holders and snapshot and task.stage != EnrichmentStage.INITIAL:
            try:
                from src.parsers.whale_dynamics import analyse_whale_dynamics

                _whale_diff, whale_patterns = await analyse_whale_dynamics(
                    session, token.id
                )
            except Exception as e:
                logger.debug(f"[ENRICH] Whale dynamics failed: {e}")

        # 12. Creator profiling (INITIAL stage only — handled in parallel batch for INITIAL)
        if not _is_initial:
            creator_prof = None

        # 13. Holder velocity
        h_velocity: float | None = None
        if snapshot is not None:
            try:
                from src.parsers.persistence import get_holder_velocity

                h_velocity = await get_holder_velocity(session, token.id)
            except Exception as e:
                logger.debug(f"[ENRICH] Holder velocity failed for token {token.id}: {e}")

        # 13b. OHLCV pattern detection (needs candle data in DB)
        ohlcv_patterns: list = []
        if snapshot is not None and task.stage != EnrichmentStage.INITIAL:
            try:
                from src.parsers.ohlcv_patterns import detect_ohlcv_patterns

                ohlcv_patterns = await detect_ohlcv_patterns(session, token.id)
            except Exception as e:
                logger.debug(f"[ENRICH] OHLCV patterns failed: {e}")

        # 13c. Trade flow analysis (needs trade data in DB)
        trade_flow_impact = 0
        if snapshot is not None and task.stage != EnrichmentStage.INITIAL:
            try:
                from src.parsers.trade_flow import analyse_trade_flow

                trade_flow = await analyse_trade_flow(session, token.id)
                if trade_flow is not None:
                    trade_flow_impact = trade_flow.score_impact
            except Exception as e:
                logger.debug(f"[ENRICH] Trade flow failed: {e}")

        # 13d. LP removal detection (needs 2+ snapshots)
        lp_removed_pct_val: float | None = None
        if snapshot is not None and task.stage != EnrichmentStage.INITIAL:
            try:
                lp_event = await check_lp_removal(session, token.id)
                if lp_event is not None:
                    extra_lp_impact = lp_event.score_impact
                    trade_flow_impact += extra_lp_impact
                # Also get cumulative LP removal for scoring
                lp_pct = await get_lp_removal_pct(session, token.id)
                if lp_pct is not None:
                    lp_removed_pct_val = float(lp_pct)
                    snapshot.lp_removed_pct = lp_pct
            except Exception as e:
                logger.debug(f"[ENRICH] LP monitor failed: {e}")

        # 13e. Cross-token whale correlation (MIN_15+ stages with holders)
        cross_whale_impact = 0
        _cross_whale_stages = {
            EnrichmentStage.MIN_15, EnrichmentStage.HOUR_1,
            EnrichmentStage.HOUR_2,
        }
        if (
            holders
            and snapshot is not None
            and task.stage in _cross_whale_stages
        ):
            try:
                coord = await detect_cross_token_coordination(session, token.id)
                if coord is not None:
                    cross_whale_impact = coord.score_impact
            except Exception as e:
                logger.debug(f"[ENRICH] Cross-token whale check failed: {e}")

        # 13f. Creator funding trace (INITIAL stage — handled in parallel batch)

        # 13g. Volatility metrics (needs OHLCV candles)
        if snapshot is not None and task.stage != EnrichmentStage.INITIAL:
            try:
                from src.parsers.ohlcv_patterns import get_volatility_metrics

                vol_5m, vol_1h = await get_volatility_metrics(session, token.id)
                if vol_5m is not None:
                    snapshot.volatility_5m = Decimal(str(round(vol_5m, 4)))
                if vol_1h is not None:
                    snapshot.volatility_1h = Decimal(str(round(vol_1h, 4)))
            except Exception as e:
                logger.debug(f"[ENRICH] Volatility metrics failed: {e}")

        # === Phase 11 enrichment steps ===

        # P11-1. Rugcheck contract security (MIN_30 re-check only — INITIAL handled in parallel batch)
        if not _is_initial:
            rugcheck_score_val: int | None = None
        if rugcheck and task.stage == EnrichmentStage.MIN_30:
            try:
                report = await rugcheck.get_token_report(task.address)
                if report is not None:
                    rugcheck_score_val = report.score
                    await save_rugcheck_report(session, token.id, report)
                    if report.score >= 50:
                        logger.info(
                            f"[RUGCHECK] {token.symbol or task.address[:12]} "
                            f"score={report.score} (DANGEROUS) "
                            f"risks={[r.name for r in report.risks[:3]]}"
                        )
            except Exception as e:
                logger.debug(f"[ENRICH] Rugcheck failed: {e}")

        # P11-3. Helius honeypot detection (MIN_5 stage)
        if helius and task.stage == EnrichmentStage.MIN_5:
            try:
                from src.parsers.honeypot_detector import detect_honeypot_onchain

                honeypot = await detect_honeypot_onchain(helius, task.address)
                if honeypot and honeypot.is_honeypot:
                    logger.warning(
                        f"[HONEYPOT] {token.symbol or task.address[:12]} "
                        f"confirmed on-chain ({honeypot.failed_ratio:.0%} failed sells)"
                    )
                    trade_flow_impact += honeypot.score_impact
            except Exception as e:
                logger.debug(f"[ENRICH] Honeypot detection failed: {e}")

        # P11-4 + P11-5: Funding trace + creator repeat (INITIAL — handled in parallel batch)

        # P11-6. Concentration rate of change (MIN_15+ stages)
        concentration_impact = 0
        if snapshot is not None and task.stage not in {
            EnrichmentStage.INITIAL, EnrichmentStage.MIN_5,
        }:
            try:
                conc_rate = await compute_concentration_rate(session, token.id)
                if conc_rate is not None:
                    concentration_impact = conc_rate.score_impact
            except Exception as e:
                logger.debug(f"[ENRICH] Concentration rate failed: {e}")

        # P11-7. Volume profile / wash trading detection (MIN_15+ stages)
        volume_profile_impact = 0
        if snapshot is not None and task.stage not in {
            EnrichmentStage.INITIAL, EnrichmentStage.MIN_5,
        }:
            try:
                vol_prof = await analyse_volume_profile(session, token.id)
                if vol_prof is not None:
                    volume_profile_impact = vol_prof.score_impact
                    if vol_prof.wash_trading_score > 50:
                        logger.info(
                            f"[WASH] {token.symbol or task.address[:12]} "
                            f"wash_score={vol_prof.wash_trading_score}"
                        )
            except Exception as e:
                logger.debug(f"[ENRICH] Volume profile failed: {e}")

        # P11-8. Holder PnL analysis (MIN_15+ stages, needs holders with pnl)
        holder_pnl_impact = 0
        _holder_pnl_result = None  # cached for wash trading check (P13-5)
        if holders and snapshot is not None and task.stage not in {
            EnrichmentStage.INITIAL, EnrichmentStage.MIN_5,
        }:
            try:
                _holder_pnl_result = await analyse_holder_pnl(session, token.id)
                if _holder_pnl_result is not None:
                    holder_pnl_impact = _holder_pnl_result.score_impact
            except Exception as e:
                logger.debug(f"[ENRICH] Holder PnL failed: {e}")

        # P11-9. Price momentum analysis (MIN_15+ stages)
        momentum_impact = 0
        if snapshot is not None and task.stage not in {
            EnrichmentStage.INITIAL, EnrichmentStage.MIN_5,
        }:
            try:
                momentum = await compute_price_momentum(session, token.id)
                if momentum is not None:
                    momentum_impact = momentum.score_impact
            except Exception as e:
                logger.debug(f"[ENRICH] Price momentum failed: {e}")

        # P11-10. DBC launchpad dynamic reputation
        dbc_launchpad_score_val: int | None = None
        if token.dbc_launchpad:
            try:
                rep = await compute_launchpad_reputation(session, token.dbc_launchpad)
                dbc_launchpad_score_val = get_launchpad_score_impact(rep)
            except Exception as e:
                logger.debug(f"[ENRICH] Launchpad reputation failed: {e}")

        # P11-11. Smart money weighted count (non-INITIAL only — INITIAL handled in batch)
        if not _is_initial:
            smart_money_weighted_val: float | None = None
            if smart_money and smart_count and smart_count > 0:
                try:
                    holder_addrs = {h.address for h in holders if h.address}
                    smart_addrs = await smart_money.check_holders_batch(holder_addrs)
                    if smart_addrs:
                        smart_money_weighted_val = await smart_money.get_weighted_count(smart_addrs)
                except Exception as e:
                    logger.debug(f"[ENRICH] Smart money weighted failed: {e}")

        # P12-1 through P14B-5: INITIAL stage — handled in parallel batch above
        # P12-5. PRE_SCAN risk boost (all stages)
        if not _is_initial:
            mint_risk_boost_val = task.prescan_risk_boost or 0

        # P13-1. Metadata scoring (sync, no API, INITIAL stage)
        metadata_score_val: int | None = None
        if _is_initial and settings.enable_metadata_scoring:
            try:
                meta_result = score_metadata(
                    description=token.description,
                    website=token.website,
                    twitter=token.twitter,
                    telegram=token.telegram,
                )
                metadata_score_val = meta_result.score
                if meta_result.score <= -3:
                    logger.info(
                        f"[META] {token.symbol or task.address[:12]} "
                        f"score={meta_result.score} (no socials)"
                    )
            except Exception as e:
                logger.debug(f"[ENRICH] Metadata scoring failed: {e}")

        # P13-2. Rugcheck risk parsing (sync, no API, INITIAL + MIN_30)
        rugcheck_danger_count_val: int | None = None
        if not _is_initial:
            goplus_critical_flags_val: list[str] | None = None
        _rugcheck_parse_stages = {EnrichmentStage.INITIAL, EnrichmentStage.MIN_30}
        if (
            task.stage in _rugcheck_parse_stages
            and settings.enable_rugcheck_risk_parsing
        ):
            try:
                # Read rugcheck_risks string from security data
                sec_data = await get_token_security_by_token_id(session, token.id)
                rugcheck_risks_str = getattr(sec_data, "rugcheck_risks", None) if sec_data else None
                if rugcheck_risks_str:
                    risk_analysis = parse_rugcheck_risks(rugcheck_risks_str)
                    rugcheck_danger_count_val = risk_analysis.danger_count
                    if risk_analysis.danger_count >= 3:
                        logger.info(
                            f"[RUGCHECK] {token.symbol or task.address[:12]} "
                            f"{risk_analysis.danger_count} dangers: "
                            f"{[r.name for r in risk_analysis.dangers[:3]]}"
                        )
            except Exception as e:
                logger.debug(f"[ENRICH] Rugcheck risk parsing failed: {e}")

        # P13-3 through P13-4, P13-6: Fee payer / convergence / wallet ages
        # INITIAL stage — handled in parallel batch above

        # P13-5. Wash trading detection (reuse P11-8 holder PnL result)
        wash_trading_val: bool = False
        if settings.enable_wash_trading_detection and _holder_pnl_result is not None:
            if getattr(_holder_pnl_result, "wash_trading_suspected", False):
                wash_trading_val = True
                logger.info(
                    f"[WASH] {token.symbol or task.address[:12]} "
                    f"wash trading suspected via PnL analysis"
                )

        # P13-7. LP events on-chain (Helius, MIN_15+ stages)
        lp_events_impact = 0
        _lp_event_stages = {
            EnrichmentStage.MIN_15, EnrichmentStage.MIN_30,
            EnrichmentStage.HOUR_1, EnrichmentStage.HOUR_2,
        }
        if (
            helius
            and task.stage in _lp_event_stages
        ):
            try:
                lp_ev = await detect_lp_events_onchain(helius, task.address)
                if lp_ev is not None:
                    lp_events_impact = lp_ev.score_impact
                    if lp_ev.total_removes > 0:
                        logger.info(
                            f"[LP_EVENTS] {token.symbol or task.address[:12]} "
                            f"removes={lp_ev.total_removes} adds={lp_ev.total_adds} "
                            f"impact={lp_ev.score_impact}"
                        )
            except Exception as e:
                logger.debug(f"[ENRICH] LP events failed: {e}")

        # P13-8. Wallet cluster detection (DB only, MIN_15+ stages)
        wallet_cluster_impact = 0
        if (
            holders
            and snapshot is not None
            and task.stage not in {EnrichmentStage.INITIAL, EnrichmentStage.MIN_5}
        ):
            try:
                coord_groups = await detect_coordinated_traders(session, token.id)
                if coord_groups:
                    wallet_cluster_impact = -5 * len(coord_groups)
                    logger.info(
                        f"[CLUSTER] {token.symbol or task.address[:12]} "
                        f"{len(coord_groups)} coordinated trader groups found"
                    )
            except Exception as e:
                logger.debug(f"[ENRICH] Wallet cluster detection failed: {e}")

        # P13-9. Price consistency validation
        if snapshot is not None and jupiter_price_val is not None:
            try:
                validate_price_consistency(
                    snapshot_price=snapshot.price,
                    jupiter_price=jupiter_price_val,
                    jupiter_confidence=jupiter_confidence,
                )
            except Exception as e:
                logger.debug(f"[ENRICH] Price consistency validation failed: {e}")

        # Aggregate Phase 11 + Phase 13 extra impacts
        phase11_extra = (
            concentration_impact + volume_profile_impact
            + holder_pnl_impact + momentum_impact
        )
        phase13_extra = (
            wallet_age_impact + lp_events_impact + wallet_cluster_impact
        )
        phase14b_extra = (
            jito_score_impact + metaplex_score_impact
            + rugcheck_insider_score_impact + solana_tracker_score_impact
            + jupiter_verify_score_impact
        )

        # 14. Compute score (v2: with all dynamics)
        score: int | None = None
        whale_impact = sum(p.score_impact for p in whale_patterns) if whale_patterns else 0
        ohlcv_impact = sum(p.score_impact for p in ohlcv_patterns) if ohlcv_patterns else 0
        extra_score_impact = (
            whale_impact + ohlcv_impact + trade_flow_impact
            + cross_whale_impact + phase11_extra + phase13_extra
            + phase14b_extra
        )
        # Bonding curve progress (pump.fun: max ~85 SOL)
        bc_pct: float | None = None
        if token.v_sol_in_bonding_curve is not None:
            bc_pct = min(float(token.v_sol_in_bonding_curve) / 85.0 * 100, 100.0)

        if snapshot is not None:
            security_data = await get_token_security_by_token_id(session, token.id)
            # Read security for rugcheck_score if not yet fetched
            if rugcheck_score_val is None and security_data and security_data.rugcheck_score is not None:
                rugcheck_score_val = security_data.rugcheck_score

            dev_holds_val: float | None = None
            if security_data and security_data.dev_holds_pct is not None:
                dev_holds_val = float(security_data.dev_holds_pct)

            vol_5m_val: float | None = None
            if snapshot.volatility_5m is not None:
                vol_5m_val = float(snapshot.volatility_5m)

            score = compute_score(
                snapshot,
                security_data,
                creator_profile=creator_prof,
                holder_velocity=h_velocity,
                whale_score_impact=extra_score_impact,
                bonding_curve_pct=bc_pct,
                dbc_launchpad=token.dbc_launchpad,
                dbc_launchpad_score=dbc_launchpad_score_val,
                lp_removed_pct=lp_removed_pct_val,
                dev_holds_pct=dev_holds_val,
                volatility_5m=vol_5m_val,
                rugcheck_score=rugcheck_score_val,
                smart_money_weighted=smart_money_weighted_val,
                # Phase 12
                mint_risk_boost=mint_risk_boost_val,
                sell_sim_failed=sell_sim_failed_val,
                bundled_buy_detected=bundled_buy_val,
                pumpfun_dead_tokens=pumpfun_dead_tokens_val,
                goplus_is_honeypot=goplus_is_honeypot_val,
                raydium_lp_burned=raydium_lp_burned_val,
                # Phase 13
                fee_payer_sybil_score=fee_payer_sybil_val,
                funding_chain_risk=funding_chain_risk_val,
                convergence_detected=convergence_detected_val,
                metadata_score=metadata_score_val,
                wash_trading_suspected=wash_trading_val,
                rugcheck_danger_count=rugcheck_danger_count_val,
                # Phase 14B
                jupiter_banned=jupiter_banned,
                # Phase 13 extras
                bubblemaps_decentralization=bubblemaps_decentralization_val,
                solsniffer_score=solsniffer_score_val,
                # Phase 15
                holders_in_profit_pct=holders_in_profit_pct_val,
                twitter_mentions=twitter_mentions_val,
                twitter_kol_mentions=twitter_kol_mentions_val,
                twitter_viral=twitter_viral_val,
                # Phase 16
                holder_growth_pct=holder_growth_pct_val,
                has_website=has_website_val,
                domain_age_days=domain_age_days_val,
                tg_member_count=tg_member_count_val,
                llm_risk_score=llm_risk_score_val,
            )
            if score is not None:
                snapshot.score = score

            # 14b. Compute score v3 (momentum-weighted) for A/B comparison
            score_v3 = compute_score_v3(
                snapshot,
                security_data,
                creator_profile=creator_prof,
                holder_velocity=h_velocity,
                whale_score_impact=extra_score_impact,
                bonding_curve_pct=bc_pct,
                dbc_launchpad=token.dbc_launchpad,
                dbc_launchpad_score=dbc_launchpad_score_val,
                lp_removed_pct=lp_removed_pct_val,
                dev_holds_pct=dev_holds_val,
                volatility_5m=vol_5m_val,
                rugcheck_score=rugcheck_score_val,
                smart_money_weighted=smart_money_weighted_val,
                # Phase 12
                mint_risk_boost=mint_risk_boost_val,
                sell_sim_failed=sell_sim_failed_val,
                bundled_buy_detected=bundled_buy_val,
                pumpfun_dead_tokens=pumpfun_dead_tokens_val,
                goplus_is_honeypot=goplus_is_honeypot_val,
                raydium_lp_burned=raydium_lp_burned_val,
                # Phase 13
                fee_payer_sybil_score=fee_payer_sybil_val,
                funding_chain_risk=funding_chain_risk_val,
                convergence_detected=convergence_detected_val,
                metadata_score=metadata_score_val,
                wash_trading_suspected=wash_trading_val,
                rugcheck_danger_count=rugcheck_danger_count_val,
                # Phase 14B
                jupiter_banned=jupiter_banned,
                # Phase 13 extras
                bubblemaps_decentralization=bubblemaps_decentralization_val,
                solsniffer_score=solsniffer_score_val,
                # Phase 15
                holders_in_profit_pct=holders_in_profit_pct_val,
                twitter_mentions=twitter_mentions_val,
                twitter_kol_mentions=twitter_kol_mentions_val,
                twitter_viral=twitter_viral_val,
                # Phase 16
                holder_growth_pct=holder_growth_pct_val,
                has_website=has_website_val,
                domain_age_days=domain_age_days_val,
                tg_member_count=tg_member_count_val,
                llm_risk_score=llm_risk_score_val,
            )
            if score_v3 is not None:
                snapshot.score_v3 = score_v3

            # 14c. A/B divergence logging between v2 and v3
            if score is not None and score_v3 is not None:
                _divergence = abs(score - score_v3)
                if _divergence >= 15:
                    logger.warning(
                        f"[SCORE_AB] {task.address[:12]} v2={score} v3={score_v3} "
                        f"delta={score - score_v3:+d} stage={task.stage.name}"
                    )
                elif _divergence >= 8:
                    logger.info(
                        f"[SCORE_AB] {task.address[:12]} v2={score} v3={score_v3} "
                        f"delta={score - score_v3:+d} stage={task.stage.name}"
                    )

        # 15. Generate entry signal (INITIAL + MIN_2 for fast entry)
        # Phase 31A: Removed MIN_5 — analysis shows PROFIT tokens enter at T+26s median,
        # while MIN_5 entries (T+300s) have 60% win rate vs 74% for 0-30s.
        # Entering late means worse prices (+83% above discovery vs +24% for profits).
        # MIN_2 still catches fast movers missed at INITIAL (score=0 → score=49 in 2min).
        _signal_stages = {EnrichmentStage.INITIAL, EnrichmentStage.MIN_2}
        if (
            snapshot is not None
            and score is not None
            and score >= 35
            and task.stage in _signal_stages
        ):
            try:
                from src.models.signal import Signal

                security_data_for_signal = await get_token_security_by_token_id(
                    session, token.id
                ) if security_data is None else security_data

                # Get previous snapshot for price momentum (R9)
                prev_snap = None
                if snapshot.id:
                    from src.parsers.persistence import get_prev_snapshot
                    prev_snap = await get_prev_snapshot(session, token.id, snapshot.id)

                # Phase 16: Holder growth velocity
                if prev_snap and prev_snap.holders_count and prev_snap.holders_count > 0:
                    current_holders = snapshot.holders_count or (len(holders) if holders else 0)
                    if current_holders and current_holders > 0:
                        holder_growth_pct_val = (
                            (current_holders - prev_snap.holders_count)
                            / prev_snap.holders_count * 100
                        )

                # Phase 15B: compute token age for velocity rules
                _token_age_min: float | None = None
                if token.first_seen_at:
                    _age_delta = datetime.utcnow() - token.first_seen_at  # noqa: DTZ003 — DB uses naive UTC
                    _token_age_min = _age_delta.total_seconds() / 60.0

                # Phase 33/34: Check copycat symbol in rugged symbols dict
                _copycat_rugged, _copycat_rug_count = _check_copycat(token.symbol or "")
                if _copycat_rugged:
                    logger.info(
                        f"[COPYCAT] {token.symbol} matches rugged symbol "
                        f"(count={_copycat_rug_count})"
                    )

                sig = evaluate_signals(
                    snapshot,
                    security_data_for_signal,
                    creator_profile=creator_prof,
                    holder_velocity=h_velocity,
                    prev_snapshot=prev_snap,
                    rugcheck_score=rugcheck_score_val,
                    dev_holds_pct=dev_holds_val,
                    jupiter_price=float(jupiter_price_val) if jupiter_price_val else None,
                    lp_removed_pct=lp_removed_pct_val,
                    cross_whale_detected=cross_whale_impact < 0,
                    volatility_5m=vol_5m_val,
                    # Phase 12 (mint_info + sell_sim from PRE_SCAN for R23-R24)
                    mint_info=task.prescan_mint_info,
                    sell_sim_result=task.prescan_sell_sim,
                    bundled_buy_detected=bundled_buy_val,
                    pumpfun_dead_tokens=pumpfun_dead_tokens_val,
                    raydium_lp_burned=raydium_lp_burned_val,
                    goplus_is_honeypot=goplus_is_honeypot_val,
                    # Phase 13
                    fee_payer_sybil_score=fee_payer_sybil_val,
                    funding_chain_risk=funding_chain_risk_val,
                    convergence_detected=convergence_detected_val,
                    metadata_score=metadata_score_val,
                    wash_trading_suspected=wash_trading_val,
                    goplus_critical_flags=goplus_critical_flags_val,
                    rugcheck_danger_count=rugcheck_danger_count_val,
                    # Phase 13 extras
                    bubblemaps_decentralization=bubblemaps_decentralization_val,
                    # Phase 14B
                    jito_bundle_detected=jito_detected,
                    metaplex_mutable=metaplex_mutable_val,
                    metaplex_has_homoglyphs=metaplex_homoglyphs_val,
                    rugcheck_insider_pct=rugcheck_insider_pct_val,
                    jupiter_banned=jupiter_banned,
                    jupiter_strict=jupiter_strict,
                    # Phase 15B — bullish velocity
                    token_age_minutes=_token_age_min,
                    # Phase 16 — community/social/LLM
                    tg_member_count=tg_member_count_val,
                    has_website=has_website_val,
                    domain_age_days=domain_age_days_val,
                    llm_risk_score=llm_risk_score_val,
                    holder_growth_pct=holder_growth_pct_val,
                    # Cross-validation
                    solsniffer_score=solsniffer_score_val,
                    # Phase 33/34 — anti-scam v2/v3
                    copycat_rugged=_copycat_rugged,
                    copycat_rug_count=_copycat_rug_count,
                )
                logger.info(
                    f"[SIGNAL] {token.symbol or task.address[:12]} "
                    f"score={score} net={sig.net_score} bull={sig.bullish_score} "
                    f"bear={sig.bearish_score} action={sig.action} "
                    f"rules={[r.name for r in sig.rules_fired]}"
                )
                if sig.action in ("strong_buy", "buy", "watch"):
                    # Atomic upsert: INSERT ... ON CONFLICT DO UPDATE
                    # Uses partial unique index uq_signals_token_status_active
                    # Wrapped in savepoint to prevent failed upsert from killing
                    # the entire enrichment session (InFailedSQLTransactionError cascade)
                    from sqlalchemy.dialects.postgresql import insert as pg_insert
                    from sqlalchemy import func as _sig_func, text as _text

                    _insert_vals = {
                        "token_id": token.id,
                        "token_address": task.address,
                        "score": score,
                        "reasons": sig.reasons,
                        "token_price_at_signal": snapshot.price,
                        "token_mcap_at_signal": snapshot.market_cap,
                        "liquidity_at_signal": snapshot.liquidity_usd,
                        "status": sig.action,
                    }
                    _upsert_stmt = (
                        pg_insert(Signal)
                        .values(**_insert_vals)
                        .on_conflict_do_update(
                            index_elements=["token_id", "status"],
                            index_where=_text("status IN ('strong_buy', 'buy', 'watch')"),
                            set_={
                                "score": score,
                                "reasons": sig.reasons,
                                "token_price_at_signal": snapshot.price,
                                "token_mcap_at_signal": snapshot.market_cap,
                                "liquidity_at_signal": snapshot.liquidity_usd,
                                "updated_at": _sig_func.now(),
                            },
                        )
                        .returning(Signal)
                    )
                    # Use savepoint so DB errors don't kill the whole session
                    signal_record = None
                    try:
                        async with session.begin_nested():
                            _result = await session.execute(_upsert_stmt)
                            signal_record = _result.scalar_one()
                    except Exception as _upsert_err:
                        logger.warning(
                            f"[SIGNAL] Upsert failed for {token.symbol or task.address[:12]}, "
                            f"falling back to simple INSERT: {_upsert_err}"
                        )
                        # Fallback: simple INSERT (may fail on dupe, that's ok)
                        try:
                            async with session.begin_nested():
                                new_sig = Signal(**_insert_vals)
                                session.add(new_sig)
                                await session.flush()
                                signal_record = new_sig
                        except Exception:
                            logger.warning(
                                f"[SIGNAL] Fallback INSERT also failed for "
                                f"{token.symbol or task.address[:12]}"
                            )
                    if signal_record is None:
                        raise RuntimeError("Signal upsert failed, skip position opening")
                    if sig.action in ("strong_buy", "buy"):
                        logger.info(
                            f"[SIGNAL] {sig.action.upper()} {token.symbol or task.address[:12]} "
                            f"score={score} net={sig.net_score} "
                            f"rules={[r.name for r in sig.rules_fired]}"
                        )
                        # Real-time alert dispatch
                        if alert_dispatcher:
                            await alert_dispatcher.dispatch(
                                TokenAlert(
                                    token_address=task.address,
                                    symbol=token.symbol,
                                    score=score,
                                    action=sig.action,
                                    reasons=sig.reasons,
                                    price=float(snapshot.price) if snapshot.price else None,
                                    market_cap=float(snapshot.market_cap) if snapshot.market_cap else None,
                                    liquidity=float(snapshot.liquidity_usd) if snapshot.liquidity_usd else None,
                                    source=token.source,
                                )
                            )
                    # Paper/Real trading: open on INITIAL + MIN_5 (both signal stages)
                    # Paper trading: open position on signal
                    if paper_trader and sig.action in ("strong_buy", "buy"):
                        try:
                            await session.flush()  # get signal_record.id
                            from src.parsers.sol_price import get_sol_price_safe
                            _sol_price = get_sol_price_safe()
                            logger.info(
                                f"[PAPER] Calling on_signal for {token.symbol or task.address[:12]} "
                                f"signal_id={signal_record.id} price={snapshot.price} "
                                f"liq={snapshot.liquidity_usd} sol_price={_sol_price}"
                            )
                            pos = await paper_trader.on_signal(
                                session, signal_record, snapshot.price,
                                symbol=token.symbol,
                                liquidity_usd=float(snapshot.liquidity_usd) if snapshot.liquidity_usd else None,
                                sol_price_usd=_sol_price,
                                lp_removed_pct=lp_removed_pct_val,
                            )
                            if pos is None:
                                logger.warning(
                                    f"[PAPER] on_signal returned None for {token.symbol or task.address[:12]} "
                                    f"(signal status={signal_record.status})"
                                )
                        except Exception as e:
                            logger.opt(exception=True).error(f"[PAPER] Error opening position: {e}")
                    # Real trading: open position on signal
                    # Safety: fetch fresh price from Jupiter before risking real SOL
                    if real_trader and sig.action in ("strong_buy", "buy"):
                        try:
                            await session.flush()
                            from src.parsers.sol_price import get_sol_price_safe
                            _sol_price_r = get_sol_price_safe()

                            # Pre-trade price sanity: get fresh Jupiter quote
                            _fresh_price = None
                            if jupiter:
                                try:
                                    _jp = await jupiter.get_price(task.address)
                                    if _jp and _jp.price is not None:
                                        _fresh_price = _jp.price
                                except Exception:
                                    pass

                            if _fresh_price and snapshot.price and float(snapshot.price) > 0:
                                _price_ratio = float(_fresh_price) / float(snapshot.price)
                                if _price_ratio < 0.3 or _price_ratio > 3.0:
                                    logger.warning(
                                        f"[REAL] Price mismatch for {token.symbol or task.address[:12]}: "
                                        f"snapshot={snapshot.price} jupiter={_fresh_price} "
                                        f"ratio={_price_ratio:.2f} — skipping real trade"
                                    )
                                    # Don't open real trade — price is stale/inconsistent
                                    raise ValueError("Price sanity check failed")

                            logger.info(
                                f"[REAL] Calling on_signal for {token.symbol or task.address[:12]} "
                                f"signal_id={signal_record.id} price={snapshot.price} "
                                f"liq={snapshot.liquidity_usd} sol_price={_sol_price_r} "
                                f"fresh_jup_price={_fresh_price}"
                            )
                            pos = await real_trader.on_signal(
                                session, signal_record, snapshot.price,
                                symbol=token.symbol,
                                liquidity_usd=float(snapshot.liquidity_usd) if snapshot.liquidity_usd else None,
                                sol_price_usd=_sol_price_r,
                                lp_removed_pct=lp_removed_pct_val,
                            )
                            if pos is None:
                                logger.warning(
                                    f"[REAL] on_signal returned None for {token.symbol or task.address[:12]} "
                                    f"(signal status={signal_record.status})"
                                )
                        except Exception as e:
                            logger.opt(exception=True).error(f"[REAL] Error opening position: {e}")
            except Exception as e:
                logger.opt(exception=True).error(f"[SIGNAL] Error generating signal: {e}")

        # 15b. Paper trading: update existing positions with current price
        if paper_trader and snapshot is not None and snapshot.price:
            try:
                from sqlalchemy import select as sa_select
                from src.models.token import TokenOutcome

                is_rug = False
                outcome_stmt = sa_select(TokenOutcome).where(
                    TokenOutcome.token_id == token.id
                )
                outcome_res = await session.execute(outcome_stmt)
                outcome = outcome_res.scalar_one_or_none()
                if outcome and outcome.is_rug is True:
                    is_rug = True
                from src.parsers.sol_price import get_sol_price
                _liq_usd = float(snapshot.liquidity_usd or snapshot.dex_liquidity_usd or 0) or None
                await paper_trader.update_positions(
                    session, token.id, snapshot.price, is_rug,
                    liquidity_usd=_liq_usd,
                    sol_price_usd=get_sol_price(),
                )
            except Exception as e:
                logger.debug(f"[PAPER] Error updating positions: {e}")

        # 15c. Real trading: update existing positions with current price
        if real_trader and snapshot is not None and snapshot.price:
            try:
                from sqlalchemy import select as sa_select
                from src.models.token import TokenOutcome

                is_rug = False
                outcome_stmt = sa_select(TokenOutcome).where(
                    TokenOutcome.token_id == token.id
                )
                outcome_res = await session.execute(outcome_stmt)
                outcome = outcome_res.scalar_one_or_none()
                if outcome and outcome.is_rug is True:
                    is_rug = True
                from src.parsers.sol_price import get_sol_price
                _liq_usd = float(snapshot.liquidity_usd or snapshot.dex_liquidity_usd or 0) or None
                await real_trader.update_positions(
                    session, token.id, snapshot.price, is_rug,
                    liquidity_usd=_liq_usd,
                    sol_price_usd=get_sol_price(),
                )
            except Exception as e:
                logger.warning(f"[REAL] Error updating positions: {e}")

        # 16. Update outcome tracking (every stage from HOUR_4+ for early rug detection)
        _outcome_stages = {
            EnrichmentStage.INITIAL, EnrichmentStage.HOUR_4,
            EnrichmentStage.HOUR_8, EnrichmentStage.HOUR_24,
        }
        if snapshot is not None and task.stage in _outcome_stages:
            is_initial = task.stage == EnrichmentStage.INITIAL
            is_final = task.stage == EnrichmentStage.HOUR_24
            _outcome = await upsert_token_outcome(
                session,
                token.id,
                snapshot,
                is_initial=is_initial,
                is_final=is_final,
                stage_name=task.stage.name,
            )
            # Phase 44: Feed rug detections into copycat memory.
            # Previously only paper/real price loops tracked rugged symbols,
            # meaning 90%+ of rugs went untracked (only bought tokens were tracked).
            # Now ANY token detected as rug by outcome tracking feeds _RUGGED_SYMBOLS.
            # Guard: track once per token_id to avoid inflating count on re-enrichment.
            if _outcome and _outcome.is_rug and token.symbol:
                if token.id not in _OUTCOME_RUG_TRACKED:
                    _OUTCOME_RUG_TRACKED.add(token.id)
                    await _track_rugged_symbol(token.symbol)

        if enriched:
            await session.commit()
            _log_enrichment(task, token, snapshot, score, birdeye_overview is not None, smart_count)

        # Record coverage metrics (inside _enrich_token where data is available)
        pipeline_metrics.record_enrichment(
            task.stage.name,
            0,  # latency recorded separately in _enrichment_worker
            has_price=snapshot is not None and snapshot.price is not None,
            has_mcap=snapshot is not None and snapshot.market_cap is not None,
            has_liquidity=snapshot is not None and snapshot.liquidity_usd is not None,
            has_holders=snapshot is not None and snapshot.holders_count is not None,
            has_security=security_data is not None,
            has_score=score is not None,
        )

        return score


def _log_enrichment(
    task: EnrichmentTask,
    token: "Token",  # noqa: F821
    snapshot: "TokenSnapshot | None",  # noqa: F821
    score: int | None,
    has_birdeye: bool,
    smart_count: int | None = None,
) -> None:
    """Log enrichment result."""
    parts = [f"[ENRICH] {task.stage.name} enriched"]
    parts.append(token.symbol or task.address[:12])
    if task.is_migration:
        parts.append("(migration)")
    if score is not None:
        parts.append(f"score={score}")
    if snapshot and snapshot.market_cap:
        parts.append(f"mcap=${int(snapshot.market_cap):,}")
    if smart_count and smart_count > 0:
        parts.append(f"smart={smart_count}")
    if has_birdeye:
        parts.append("[birdeye]")
    logger.info(" ".join(parts))


async def _schedule_next_stage(
    queue: PersistentEnrichmentQueue,
    completed_task: EnrichmentTask,
    *,
    last_score: int | None = None,
) -> None:
    """Put the next re-enrichment task on the queue, if any."""
    next_stage = NEXT_STAGE.get(completed_task.stage)
    if next_stage is None:
        return

    next_config = STAGE_SCHEDULE[next_stage]
    scheduled_at = completed_task.discovery_time + next_config.offset_sec

    # Don't schedule in the past — run immediately instead
    now = asyncio.get_event_loop().time()
    if scheduled_at < now:
        scheduled_at = now

    next_task = EnrichmentTask(
        priority=completed_task.priority,
        scheduled_at=scheduled_at,
        address=completed_task.address,
        stage=next_stage,
        fetch_security=next_config.fetch_security,
        is_migration=completed_task.is_migration,
        discovery_time=completed_task.discovery_time,
        last_score=last_score,
        prescan_risk_boost=completed_task.prescan_risk_boost,
        prescan_mint_info=completed_task.prescan_mint_info,
        prescan_sell_sim=completed_task.prescan_sell_sim,
    )

    logger.debug(
        f"[QUEUE] Scheduling {completed_task.address[:12]} "
        f"{completed_task.stage.name} -> {next_stage.name} "
        f"scheduled_at={scheduled_at:.0f} (delay={scheduled_at - now:.0f}s)"
    )
    await queue.put(next_task)


def _extract_dbc_accounts(
    tx_data: dict,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Extract (pool_address, base_mint, creator, pool_config) from a DBC tx.

    DBC instruction accounts: [0]=creator, [1]=pool_config, [2]=pool_address.
    base_mint comes from ``initializeMint2`` in inner instructions (most reliable),
    or instruction account [3] for the 16-account variant.
    """
    from src.parsers.meteora.constants import DBC_PROGRAM_ID

    try:
        message = tx_data.get("transaction", {}).get("message", {})
        instructions = message.get("instructions", [])

        pool_address: str | None = None
        pool_config: str | None = None
        creator: str | None = None
        dbc_accounts: list[str] = []
        for ix in instructions:
            if ix.get("programId") != DBC_PROGRAM_ID:
                continue
            dbc_accounts = ix.get("accounts", [])
            creator = dbc_accounts[0] if len(dbc_accounts) > 0 else None
            pool_config = dbc_accounts[1] if len(dbc_accounts) > 1 else None
            pool_address = dbc_accounts[2] if len(dbc_accounts) > 2 else None
            break

        base_mint = _find_mint_in_inner(tx_data)

        if not base_mint and len(dbc_accounts) >= 16:
            base_mint = dbc_accounts[3]

        if not creator:
            acct_keys = message.get("accountKeys", [])
            if acct_keys:
                k = acct_keys[0]
                creator = k.get("pubkey", k) if isinstance(k, dict) else k

        return pool_address, base_mint, creator, pool_config
    except Exception as e:
        logger.debug(f"[MDBC] Failed to parse pool init: {e}")
        return None, None, None, None


def _find_mint_in_inner(tx_data: dict) -> str | None:
    """Find new token mint from initializeMint/initializeMint2 in inner instructions."""
    try:
        meta = tx_data.get("meta", {})
        for ig in meta.get("innerInstructions", []):
            for iix in ig.get("instructions", []):
                parsed = iix.get("parsed")
                if not isinstance(parsed, dict):
                    continue
                if parsed.get("type") in ("initializeMint", "initializeMint2"):
                    mint = parsed.get("info", {}).get("mint")
                    if mint:
                        return mint
    except Exception as e:
        logger.debug(f"[MDBC] Failed to find mint in inner instructions: {e}")
    return None


async def _signal_decay_loop() -> None:
    """Periodically decay stale signals based on TTL."""
    from src.parsers.signal_decay import decay_stale_signals

    interval = settings.signal_decay_interval_sec
    while True:
        await asyncio.sleep(interval)
        try:
            async with async_session_factory() as session:
                decayed = await decay_stale_signals(
                    session,
                    strong_buy_ttl_hours=settings.signal_strong_buy_ttl_hours,
                    buy_ttl_hours=settings.signal_buy_ttl_hours,
                    watch_ttl_hours=settings.signal_watch_ttl_hours,
                )
                if decayed > 0:
                    await session.commit()
        except Exception as e:
            logger.debug(f"[DECAY] Error: {e}")


async def _stats_reporter(
    pumpportal: PumpPortalClient,
    meteora_ws: MeteoraDBCClient | None,
    enrichment_queue: PersistentEnrichmentQueue,
) -> None:
    """Log parser stats every 60 seconds."""
    while True:
        await asyncio.sleep(60)
        qsize = await enrichment_queue.qsize()
        parts = [
            f"PP messages: {pumpportal.message_count}",
            f"PP state: {pumpportal.state.value}",
        ]
        if meteora_ws:
            parts.append(f"MDBC messages: {meteora_ws.message_count}")
            parts.append(f"MDBC state: {meteora_ws.state.value}")
        parts.append(f"Enrichment queue: {qsize}")
        parts.append(pipeline_metrics.format_stats_line())
        logger.info(f"[STATS] {' | '.join(parts)}")


async def _paper_price_loop(
    paper_trader: "PaperTrader",
    birdeye: "BirdeyeClient | None" = None,
    jupiter: "JupiterClient | None" = None,
    dexscreener: "DexScreenerClient | None" = None,
) -> None:
    """Real-time price updates for open paper positions.

    Uses Birdeye multi-price (primary), Jupiter batch (fallback),
    and DexScreener (fallback for pump.fun bonding curve tokens).
    Runs every 15 seconds, fetches prices for all open positions in one batch call,
    and triggers take_profit/stop_loss checks with fresh prices.
    """
    from sqlalchemy import select as sa_select

    from src.db.database import async_session_factory
    from src.models.trade import Position

    await asyncio.sleep(10)  # Phase 35: was 30s — faster first check for new positions

    while True:
        _interval = 15  # Default interval; overridden below for fresh positions
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    sa_select(Position).where(
                        Position.status == "open",
                        Position.is_paper == 1,
                    )
                )
                positions = list(result.scalars().all())

            if not positions:
                await asyncio.sleep(15)
                continue

            # Collect unique token addresses
            addresses = list({p.token_address for p in positions})

            # Primary: Birdeye multi-price WITH liquidity (Phase 36)
            token_prices: dict[int, Decimal] = {}
            live_liq_map: dict[int, float | None] = {}  # Live liquidity from API
            _stale_tokens: set[int] = set()  # token_ids with stale Birdeye price
            _dead_tokens: set[int] = set()  # Phase 30: token_ids with dead price (>10min)
            if birdeye:
                try:
                    import time as _time
                    _now_unix = int(_time.time())
                    _stale_threshold = 300  # 5 min — pump.fun prices freeze after migration
                    _dead_threshold = 600  # 10 min — likely dead token, force close
                    be_prices = await birdeye.get_price_multi(
                        addresses[:100], include_liquidity=True,
                    )
                    for pos in positions:
                        bp = be_prices.get(pos.token_address)
                        if bp and bp.value:
                            # Skip stale prices (pump.fun tokens freeze on Birdeye after migration)
                            if bp.updateUnixTime and (_now_unix - bp.updateUnixTime) > _stale_threshold:
                                _age = _now_unix - bp.updateUnixTime
                                _stale_tokens.add(pos.token_id)
                                if _age > _dead_threshold:
                                    logger.warning(f"[PAPER] Birdeye dead price for {pos.token_address[:12]}... (age={_age}s, marking liq=0)")
                                    # Dead token: force liquidity=0 to trigger close
                                    live_liq_map[pos.token_id] = 0.0
                                    token_prices[pos.token_id] = bp.value  # Use last known price for PnL
                                    _dead_tokens.add(pos.token_id)  # Phase 30
                                else:
                                    logger.warning(f"[PAPER] Birdeye stale price for {pos.token_address[:12]}... (age={_age}s, skipping)")
                                continue
                            token_prices[pos.token_id] = bp.value
                        # Phase 36: Extract liquidity from Birdeye (primary source)
                        if bp and bp.liquidity is not None:
                            live_liq_map[pos.token_id] = float(bp.liquidity)
                except Exception as e:
                    logger.warning(f"[PAPER] Birdeye multi-price failed: {e}")

            # Fallback 1: Jupiter batch for any missing tokens
            missing_addrs = [p.token_address for p in positions if p.token_id not in token_prices]
            if jupiter and missing_addrs:
                try:
                    jp_prices = await jupiter.get_prices_batch(missing_addrs[:100])
                    for pos in positions:
                        if pos.token_id not in token_prices:
                            jp = jp_prices.get(pos.token_address)
                            if jp and jp.price:
                                token_prices[pos.token_id] = jp.price
                except Exception as e:
                    logger.warning(f"[PAPER] Jupiter batch fallback failed: {e}")

            # Fallback 2: DexScreener for pump.fun tokens still missing price
            # Phase 36: Also collects liquidity for positions where Birdeye didn't return it
            # Uses get_token_pairs (per-token) instead of get_tokens_batch because
            # batch endpoint returns only one pair per token (often the dead bonding
            # curve pool with near-zero price). Per-token gives all pairs so we can
            # pick the one with highest liquidity / highest price.
            _addr_to_tid = {p.token_address: p.token_id for p in positions}
            missing_addrs2 = [p.token_address for p in positions if p.token_id not in token_prices]
            # Phase 37: Fetch DexScreener for ALL positions (not just missing).
            # Birdeye multi_price returns bonding-curve liq (near-zero) for migrated tokens.
            # DexScreener gives per-DEX-pair data — much more reliable for liquidity.
            _dex_fetch_addrs = list(dict.fromkeys(missing_addrs2 + list(addresses)))
            if missing_addrs2:
                logger.info(f"[PAPER] {len(missing_addrs2)} tokens missing after Birdeye+Jupiter, trying DexScreener")
            if dexscreener and _dex_fetch_addrs:
                from decimal import Decimal as _Dec
                dex_price_map: dict[str, _Dec] = {}
                for addr in _dex_fetch_addrs[:20]:  # Limit 20 (was 10, covers most open positions)
                    try:
                        pairs = await dexscreener.get_token_pairs(addr)
                        _tid = _addr_to_tid.get(addr)
                        if not pairs:
                            if _tid and _tid not in live_liq_map:
                                live_liq_map[_tid] = None  # Phase 36: No pairs = unknown (indexing lag)
                            continue
                        # Phase 30: Pick price from pair with HIGHEST liquidity
                        # where our token is the baseToken (not quoteToken).
                        # Old logic was broken: `p_usd > best_price or pair_liq > 100`
                        # picked highest price from ANY pair, often returning SOL price
                        # from inverted pairs or frozen bonding curve prices.
                        best_price = _Dec(0)
                        best_price_liq = 0.0
                        max_liq = 0.0
                        for pair in pairs:
                            # Collect liquidity from all pairs (for liq_map)
                            pair_liq = float(pair.liquidity.usd) if pair.liquidity and pair.liquidity.usd else 0
                            max_liq = max(max_liq, pair_liq)
                            # Collect price — MUST verify baseToken is our token
                            if not pair.priceUsd or not pair.baseToken:
                                continue
                            # Phase 30: Skip pairs where our token is quoteToken
                            # (priceUsd would be price of OTHER token, e.g. SOL)
                            if pair.baseToken.address.lower() != addr.lower():
                                continue
                            try:
                                p_usd = _Dec(pair.priceUsd)
                            except Exception:
                                continue
                            # Phase 30: Select price from pair with highest liquidity
                            # (most reliable price discovery)
                            if pair_liq > best_price_liq:
                                best_price = p_usd
                                best_price_liq = pair_liq
                            elif pair_liq == best_price_liq and p_usd > best_price:
                                best_price = p_usd
                        if best_price > 0:
                            dex_price_map[addr] = best_price
                        # Phase 37: DexScreener liq OVERRIDES Birdeye (more reliable for DEX pools)
                        if _tid:
                            live_liq_map[_tid] = max_liq
                    except Exception as e:
                        logger.warning(f"[PAPER] DexScreener pairs failed for {addr[:12]}: {e}")
                for pos in positions:
                    if pos.token_id not in token_prices:
                        dp = dex_price_map.get(pos.token_address)
                        if dp and dp > 0:
                            token_prices[pos.token_id] = dp
                if dex_price_map:
                    logger.info(f"[PAPER] DexScreener fallback got {len(dex_price_map)} prices for {len(missing_addrs2)} missing tokens")

            # DB snapshot fallback: for tokens still missing liquidity data
            # (e.g. DexScreener limit exceeded or API failed)
            _still_missing_liq = [
                p for p in positions
                if p.token_id not in live_liq_map and p.token_id in token_prices
            ]
            if _still_missing_liq:
                try:
                    async with async_session_factory() as _liq_session:
                        for pos in _still_missing_liq:
                            snap = await get_latest_snapshot(_liq_session, pos.token_id)
                            if snap:
                                _liq = float(snap.liquidity_usd or snap.dex_liquidity_usd or 0)
                                live_liq_map[pos.token_id] = _liq
                            else:
                                live_liq_map[pos.token_id] = None  # Phase 36: No snapshot = unknown
                except Exception as e:
                    logger.debug(f"[PAPER] DB liq fallback failed: {e}")

            if not token_prices:
                await asyncio.sleep(15)
                continue

            if token_prices:
                from src.parsers.sol_price import get_sol_price
                _sol_usd = get_sol_price()
                async with async_session_factory() as session:
                    for token_id, price in token_prices.items():
                        await paper_trader.update_positions(
                            session, token_id, price,
                            liquidity_usd=live_liq_map.get(token_id),
                            sol_price_usd=_sol_usd,
                            is_dead_price=token_id in _dead_tokens,  # Phase 30
                        )
                    await session.commit()

                # Phase 36: Compute _now_dt once for copycat tracking + adaptive interval
                from datetime import datetime, UTC
                _now_dt = datetime.now(UTC).replace(tzinfo=None)

                # Phase 33/34: Track rugged symbols for copycat detection (R63).
                # If liquidity < $5K for a position, it was closed as liquidity_removed.
                for pos in positions:
                    _pos_liq = live_liq_map.get(pos.token_id)
                    if _pos_liq is not None and _pos_liq < 5_000 and pos.symbol:
                        # Phase 37: Only track as rug if price also crashed
                        # (not just bad liq data from Birdeye)
                        _pos_price = token_prices.get(pos.token_id)
                        _price_crashed = (
                            _pos_price is not None
                            and pos.entry_price
                            and pos.entry_price > 0
                            and float(_pos_price / pos.entry_price) < 0.5
                        )
                        if not _price_crashed:
                            continue  # Price healthy, liq data unreliable
                        # Phase 36: Skip false-positive rug tracking for fresh positions
                        if _pos_liq == 0.0 and pos.opened_at:
                            if (_now_dt - pos.opened_at).total_seconds() < 90:
                                continue
                        await _track_rugged_symbol(pos.symbol)

            # Phase 35: Adaptive interval — 5s for fresh positions (< 120s old),
            # 15s for mature. Fresh positions have highest rug risk; faster checks
            # catch instant rugs and capture profit before LP removal.
            _has_fresh = any(
                pos.opened_at and (_now_dt - pos.opened_at).total_seconds() < 120
                for pos in positions
            )
            if _has_fresh:
                _interval = 5

        except Exception as e:
            logger.warning(f"[PAPER] Price loop error: {e}")

        await asyncio.sleep(_interval)


async def _paper_sweep_loop(paper_trader: "PaperTrader") -> None:
    """Close positions that exceeded timeout even if token stopped being enriched."""
    from src.db.database import async_session_factory

    await asyncio.sleep(120)  # Wait 2 min before first sweep

    while True:
        try:
            async with async_session_factory() as session:
                closed = await paper_trader.sweep_stale_positions(session)
                if closed > 0:
                    await session.commit()
        except Exception as e:
            logger.debug(f"[PAPER] Sweep loop error: {e}")

        await asyncio.sleep(300)  # Every 5 minutes


async def _paper_report_loop(
    paper_trader: "PaperTrader",
    alert_dispatcher: AlertDispatcher,
) -> None:
    """Send paper trading portfolio report to Telegram every hour."""
    from src.db.database import async_session_factory

    await asyncio.sleep(300)  # Wait 5 min before first report

    while True:
        try:
            async with async_session_factory() as session:
                stats = await paper_trader.get_portfolio_summary(session)
            if stats["open_count"] > 0 or stats["closed_count"] > 0:
                await alert_dispatcher.send_paper_report(stats)
        except Exception as e:
            logger.debug(f"[PAPER] Report loop error: {e}")

        await asyncio.sleep(3600)  # Every hour


async def _real_price_loop(
    real_trader: "RealTrader",
    birdeye: "BirdeyeClient | None" = None,
    jupiter: "JupiterClient | None" = None,
    dexscreener: "DexScreenerClient | None" = None,
) -> None:
    """Real-time price updates for open real positions (10s cycle).

    Same pattern as _paper_price_loop but for is_paper=0 positions.
    Triggers take_profit/stop_loss sell execution via Jupiter swap.
    Faster cycle than paper (10s vs 15s) because real money is at stake.
    """
    from sqlalchemy import select as sa_select

    from src.db.database import async_session_factory
    from src.models.trade import Position

    REAL_PRICE_INTERVAL = 10  # seconds — faster than paper for rug protection
    REAL_PRICE_FAST = 5  # Phase 35: fast interval for fresh positions

    await asyncio.sleep(10)  # Phase 35: was 30s — faster first check

    while True:
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    sa_select(Position).where(
                        Position.status == "open",
                        Position.is_paper == 0,
                    )
                )
                positions = list(result.scalars().all())

            if not positions:
                await asyncio.sleep(REAL_PRICE_INTERVAL)
                continue

            addresses = list({p.token_address for p in positions})

            # Primary: Birdeye multi-price WITH liquidity (Phase 36)
            token_prices: dict[int, Decimal] = {}
            live_liq_map: dict[int, float | None] = {}
            _dead_tokens_r: set[int] = set()  # Phase 30: dead price tokens
            if birdeye:
                try:
                    import time as _time_r
                    _now_unix_r = int(_time_r.time())
                    _stale_thr = 300  # 5 min
                    _dead_thr = 600  # 10 min — force close
                    be_prices = await birdeye.get_price_multi(
                        addresses[:100], include_liquidity=True,
                    )
                    for pos in positions:
                        bp = be_prices.get(pos.token_address)
                        if bp and bp.value:
                            if bp.updateUnixTime and (_now_unix_r - bp.updateUnixTime) > _stale_thr:
                                _age_r = _now_unix_r - bp.updateUnixTime
                                if _age_r > _dead_thr:
                                    logger.warning(f"[REAL] Birdeye dead price for {pos.token_address[:12]}... (age={_age_r}s, marking liq=0)")
                                    live_liq_map[pos.token_id] = 0.0
                                    token_prices[pos.token_id] = bp.value
                                    _dead_tokens_r.add(pos.token_id)  # Phase 30
                                continue
                            token_prices[pos.token_id] = bp.value
                        # Phase 36: Extract liquidity from Birdeye
                        if bp and bp.liquidity is not None:
                            live_liq_map[pos.token_id] = float(bp.liquidity)
                except Exception as e:
                    logger.debug(f"[REAL] Birdeye multi-price failed: {e}")

            # Fallback: Jupiter batch
            missing_addrs = [p.token_address for p in positions if p.token_id not in token_prices]
            if jupiter and missing_addrs:
                try:
                    jp_prices = await jupiter.get_prices_batch(missing_addrs[:100])
                    for pos in positions:
                        if pos.token_id not in token_prices:
                            jp = jp_prices.get(pos.token_address)
                            if jp and jp.price:
                                token_prices[pos.token_id] = jp.price
                except Exception as e:
                    logger.debug(f"[REAL] Jupiter batch fallback failed: {e}")

            # Phase 37: DexScreener liq for ALL positions (overrides Birdeye bonding-curve liq)
            if dexscreener and positions:
                from decimal import Decimal as _Dec_r
                _addr_to_tid_r = {p.token_address: p.token_id for p in positions}
                _real_dex_addrs = list({p.token_address for p in positions})[:10]
                for _r_addr in _real_dex_addrs:
                    try:
                        _r_pairs = await dexscreener.get_token_pairs(_r_addr)
                        _r_tid = _addr_to_tid_r.get(_r_addr)
                        if not _r_pairs:
                            continue
                        _r_max_liq = 0.0
                        _r_best_price = _Dec_r(0)
                        _r_best_liq = 0.0
                        for _r_pair in _r_pairs:
                            _r_pair_liq = float(_r_pair.liquidity.usd) if _r_pair.liquidity and _r_pair.liquidity.usd else 0
                            _r_max_liq = max(_r_max_liq, _r_pair_liq)
                            # Phase 30: Pick price from highest-liq pair where token is baseToken
                            if (
                                _r_tid
                                and _r_tid not in token_prices
                                and _r_pair.priceUsd
                                and _r_pair.baseToken
                                and _r_pair.baseToken.address.lower() == _r_addr.lower()
                            ):
                                try:
                                    _r_p = _Dec_r(_r_pair.priceUsd)
                                    if _r_p > 0 and _r_pair_liq > _r_best_liq:
                                        _r_best_price = _r_p
                                        _r_best_liq = _r_pair_liq
                                except Exception:
                                    pass
                        if _r_tid and _r_tid not in token_prices and _r_best_price > 0:
                            token_prices[_r_tid] = _r_best_price
                        if _r_tid:
                            live_liq_map[_r_tid] = _r_max_liq
                    except Exception as e:
                        logger.debug(f"[REAL] DexScreener pairs failed for {_r_addr[:12]}: {e}")

            # DB snapshot fallback for liquidity (when DexScreener + Birdeye didn't return it)
            _real_missing_liq = [
                p for p in positions
                if p.token_id not in live_liq_map and p.token_id in token_prices
            ]
            if _real_missing_liq:
                try:
                    async with async_session_factory() as _liq_sess:
                        for pos in _real_missing_liq:
                            snap = await get_latest_snapshot(_liq_sess, pos.token_id)
                            if snap:
                                _liq_r = float(snap.liquidity_usd or snap.dex_liquidity_usd or 0)
                                live_liq_map[pos.token_id] = _liq_r
                            else:
                                live_liq_map[pos.token_id] = None  # Phase 36: unknown, not confirmed dead
                except Exception as e:
                    logger.debug(f"[REAL] DB liq fallback failed: {e}")

            if not token_prices:
                await asyncio.sleep(REAL_PRICE_INTERVAL)
                continue

            from src.parsers.sol_price import get_sol_price
            _sol_usd = get_sol_price()
            async with async_session_factory() as session:
                for token_id, price in token_prices.items():
                    await real_trader.update_positions(
                        session, token_id, price,
                        liquidity_usd=live_liq_map.get(token_id),
                        sol_price_usd=_sol_usd,
                        is_dead_price=token_id in _dead_tokens_r,  # Phase 30
                    )
                await session.commit()

            # Phase 33/34: Track rugged symbols for copycat detection (R63).
            for pos in positions:
                _pos_liq = live_liq_map.get(pos.token_id)
                if _pos_liq is not None and _pos_liq < 5_000 and pos.symbol:
                    # Phase 37: Only track as rug if price also crashed
                    _pos_price = token_prices.get(pos.token_id)
                    _price_crashed = (
                        _pos_price is not None
                        and pos.entry_price
                        and pos.entry_price > 0
                        and float(_pos_price / pos.entry_price) < 0.5
                    )
                    if not _price_crashed:
                        continue
                    await _track_rugged_symbol(pos.symbol)

            # Phase 35: Adaptive interval for fresh positions
            from datetime import datetime, UTC
            _now_dt = datetime.now(UTC).replace(tzinfo=None)
            _has_fresh = any(
                pos.opened_at and (_now_dt - pos.opened_at).total_seconds() < 120
                for pos in positions
            )
            _real_interval = REAL_PRICE_FAST if _has_fresh else REAL_PRICE_INTERVAL

        except Exception as e:
            logger.debug(f"[REAL] Price loop error: {e}")
            _real_interval = REAL_PRICE_INTERVAL

        await asyncio.sleep(_real_interval)


async def _real_sweep_loop(real_trader: "RealTrader") -> None:
    """Close real positions that exceeded timeout (executes sell swaps)."""
    from src.db.database import async_session_factory

    await asyncio.sleep(120)

    while True:
        try:
            async with async_session_factory() as session:
                closed = await real_trader.sweep_stale_positions(session)
                if closed > 0:
                    await session.commit()
        except Exception as e:
            logger.debug(f"[REAL] Sweep loop error: {e}")

        await asyncio.sleep(300)


async def _data_cleanup_loop() -> None:
    """Periodically delete old snapshots, trades, OHLCV to prevent unbounded DB growth.

    Runs once per 6 hours. Retention: snapshots/trades 7 days, OHLCV 14 days.
    """
    from src.db.database import async_session_factory
    from src.parsers.persistence import cleanup_old_data

    await asyncio.sleep(600)  # Wait 10 min after startup

    while True:
        try:
            async with async_session_factory() as session:
                deleted = await cleanup_old_data(session)
                if sum(deleted.values()) > 0:
                    await session.commit()
        except Exception as e:
            logger.debug(f"[CLEANUP] Error: {e}")

        await asyncio.sleep(6 * 3600)  # Every 6 hours
