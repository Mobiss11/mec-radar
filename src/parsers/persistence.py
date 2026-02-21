"""Data persistence layer — maps parser Pydantic models to SQLAlchemy models."""

from datetime import datetime
from decimal import Decimal

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.signal import Signal
from src.models.token import (
    Token,
    TokenOHLCV,
    TokenOutcome,
    TokenSecurity,
    TokenSnapshot,
    TokenTopHolder,
    TokenTrade,
)
from src.models.wallet import SmartWallet, WalletActivity
from src.parsers.birdeye.models import (
    BirdeyeOHLCVItem,
    BirdeyeTokenOverview,
    BirdeyeTokenSecurity,
    BirdeyeTradeItem,
)
from src.parsers.dexscreener.models import DexScreenerPair
from src.parsers.gmgn.models import (
    GmgnSecurityInfo,
    GmgnSmartWallet,
    GmgnTokenInfo,
    GmgnTopHolder,
)
from src.parsers.meteora.models import MeteoraVirtualPool
from src.parsers.pumpportal.models import PumpPortalNewToken, PumpPortalTrade
from src.parsers.goplus.models import GoPlusReport
from src.parsers.rugcheck.models import RugcheckReport


def _sanitize(val: str | None) -> str | None:
    """Strip null bytes and control chars that PostgreSQL rejects."""
    if val is None:
        return None
    return val.replace("\x00", "").strip() or None


async def upsert_token(
    session: AsyncSession,
    *,
    address: str,
    chain: str = "sol",
    name: str | None = None,
    symbol: str | None = None,
    source: str,
    created_at: datetime | None = None,
) -> Token:
    """Insert or update a token, returning the DB record."""
    name = _sanitize(name)
    symbol = _sanitize(symbol)
    stmt = (
        pg_insert(Token)
        .values(
            address=address,
            chain=chain,
            name=name,
            symbol=symbol,
            source=source,
            created_at=created_at,
        )
        .on_conflict_do_update(
            constraint="uq_token_address_chain",
            set_={
                "name": name,
                "symbol": symbol,
            },
        )
        .returning(Token)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.scalar_one()


async def upsert_token_from_pumpportal(
    session: AsyncSession, event: PumpPortalNewToken
) -> Token:
    """Insert or update a token from PumpPortal, preserving launch data."""
    name = _sanitize(event.name)
    symbol = _sanitize(event.symbol)
    stmt = (
        pg_insert(Token)
        .values(
            address=event.mint,
            chain="sol",
            name=name,
            symbol=symbol,
            source="pumpportal",
            creator_address=event.traderPublicKey,
            initial_buy_sol=event.initialBuy,
            initial_mcap_sol=event.marketCapSol,
            bonding_curve_key=event.bondingCurveKey,
            v_sol_in_bonding_curve=event.vSolInBondingCurve,
            v_tokens_in_bonding_curve=event.vTokensInBondingCurve,
        )
        .on_conflict_do_update(
            constraint="uq_token_address_chain",
            set_={
                "name": name,
                "symbol": symbol,
                # Preserve launch data — only fill if previously NULL
                "creator_address": func.coalesce(
                    Token.creator_address, event.traderPublicKey
                ),
                "initial_buy_sol": func.coalesce(
                    Token.initial_buy_sol, event.initialBuy
                ),
                "initial_mcap_sol": func.coalesce(
                    Token.initial_mcap_sol, event.marketCapSol
                ),
                "bonding_curve_key": func.coalesce(
                    Token.bonding_curve_key, event.bondingCurveKey
                ),
                "v_sol_in_bonding_curve": func.coalesce(
                    Token.v_sol_in_bonding_curve, event.vSolInBondingCurve
                ),
                "v_tokens_in_bonding_curve": func.coalesce(
                    Token.v_tokens_in_bonding_curve, event.vTokensInBondingCurve
                ),
            },
        )
        .returning(Token)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.scalar_one()


async def upsert_token_from_meteora_dbc(
    session: AsyncSession, pool: MeteoraVirtualPool
) -> Token:
    """Insert or update a token from Meteora DBC, preserving DBC-specific data."""
    stmt = (
        pg_insert(Token)
        .values(
            address=pool.base_mint,
            chain="sol",
            source="meteora_dbc",
            creator_address=pool.creator,
            bonding_curve_key=pool.pool_address,
            dbc_pool_address=pool.pool_address,
            dbc_quote_reserve=pool.quote_reserve,
            dbc_launchpad=pool.launchpad,
            dbc_is_migrated=pool.is_migrated,
        )
        .on_conflict_do_update(
            constraint="uq_token_address_chain",
            set_={
                # Preserve existing data — only fill if previously NULL
                "creator_address": func.coalesce(Token.creator_address, pool.creator),
                "bonding_curve_key": func.coalesce(
                    Token.bonding_curve_key, pool.pool_address
                ),
                "dbc_pool_address": func.coalesce(
                    Token.dbc_pool_address, pool.pool_address
                ),
                "dbc_quote_reserve": pool.quote_reserve,
                "dbc_launchpad": func.coalesce(Token.dbc_launchpad, pool.launchpad),
                "dbc_is_migrated": pool.is_migrated,
            },
        )
        .returning(Token)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.scalar_one()


async def save_token_snapshot(
    session: AsyncSession,
    token_id: int,
    data: GmgnTokenInfo | None,
    *,
    stage: str | None = None,
    dex_data: DexScreenerPair | None = None,
    birdeye_data: BirdeyeTokenOverview | None = None,
    top10_pct: Decimal | None = None,
    smart_wallets_count: int | None = None,
    jupiter_price: Decimal | None = None,
    # Phase 15: Vybe + Twitter
    holders_in_profit_pct: Decimal | None = None,
    vybe_top_holder_pct: Decimal | None = None,
    twitter_mentions: int | None = None,
    twitter_kol_mentions: int | None = None,
    twitter_max_likes: int | None = None,
    # Phase 16: Enrichment
    holder_growth_pct: Decimal | None = None,
    has_website: bool | None = None,
    domain_age_days: int | None = None,
    tg_member_count: int | None = None,
    llm_risk_score: int | None = None,
) -> TokenSnapshot:
    """Save a point-in-time metrics snapshot for a token.

    Data priority: Birdeye (primary) > GMGN (secondary) > DexScreener (cross-validation).
    """
    # Primary fields — Birdeye first, then GMGN fallback
    price = None
    market_cap = None
    liquidity = None
    volume_5m = None
    volume_1h = None
    volume_24h = None
    holders = None
    buys_5m = None
    sells_5m = None
    buys_1h = None
    sells_1h = None
    buys_24h = None
    sells_24h = None

    if birdeye_data:
        price = birdeye_data.price
        market_cap = birdeye_data.marketCap
        liquidity = birdeye_data.liquidity
        volume_5m = birdeye_data.v5mUSD
        volume_1h = birdeye_data.v1hUSD
        volume_24h = birdeye_data.v24hUSD
        holders = birdeye_data.holder
        buys_5m = birdeye_data.buy5m
        sells_5m = birdeye_data.sell5m
        buys_1h = birdeye_data.buy1h
        sells_1h = birdeye_data.sell1h
        buys_24h = birdeye_data.buy24h
        sells_24h = birdeye_data.sell24h

    if data:
        price = price or data.effective_price
        market_cap = market_cap or data.market_cap
        liquidity = liquidity or data.liquidity
        volume_5m = volume_5m or data.effective_volume_5m
        volume_1h = volume_1h or data.effective_volume_1h
        volume_24h = volume_24h or data.effective_volume_24h
        holders = holders or data.holder_count

    # DexScreener buy/sell fallback
    if dex_data and dex_data.txns:
        txns = dex_data.txns
        if buys_5m is None and txns.m5:
            buys_5m = txns.m5.buys
            sells_5m = txns.m5.sells
        if buys_1h is None and txns.h1:
            buys_1h = txns.h1.buys
            sells_1h = txns.h1.sells
        if buys_24h is None and txns.h24:
            buys_24h = txns.h24.buys
            sells_24h = txns.h24.sells

    snapshot = TokenSnapshot(
        token_id=token_id,
        stage=stage,
        price=price,
        market_cap=market_cap,
        liquidity_usd=liquidity,
        volume_5m=volume_5m,
        volume_1h=volume_1h,
        volume_24h=volume_24h,
        holders_count=holders,
        top10_holders_pct=top10_pct,
        buys_5m=buys_5m,
        sells_5m=sells_5m,
        buys_1h=buys_1h,
        sells_1h=sells_1h,
        buys_24h=buys_24h,
        sells_24h=sells_24h,
        # DexScreener cross-validation
        dex_price=(
            Decimal(dex_data.priceUsd)
            if dex_data and dex_data.priceUsd
            else None
        ),
        dex_liquidity_usd=(
            dex_data.liquidity.usd
            if dex_data and dex_data.liquidity
            else None
        ),
        dex_volume_5m=(
            dex_data.volume.m5 if dex_data and dex_data.volume else None
        ),
        dex_volume_1h=(
            dex_data.volume.h1 if dex_data and dex_data.volume else None
        ),
        dex_volume_24h=(
            dex_data.volume.h24 if dex_data and dex_data.volume else None
        ),
        dex_fdv=dex_data.fdv if dex_data else None,
        smart_wallets_count=smart_wallets_count,
        jupiter_price=jupiter_price,
        # Phase 15: Vybe + Twitter
        holders_in_profit_pct=holders_in_profit_pct,
        vybe_top_holder_pct=vybe_top_holder_pct,
        twitter_mentions=twitter_mentions,
        twitter_kol_mentions=twitter_kol_mentions,
        twitter_max_likes=twitter_max_likes,
        # Phase 16: Enrichment
        holder_growth_pct=holder_growth_pct,
        has_website=has_website,
        domain_age_days=domain_age_days,
        tg_member_count=tg_member_count,
        llm_risk_score=llm_risk_score,
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def save_token_security(
    session: AsyncSession,
    token_id: int,
    data: GmgnSecurityInfo,
) -> TokenSecurity:
    """Save or update security analysis for a token."""
    stmt = (
        pg_insert(TokenSecurity)
        .values(
            token_id=token_id,
            is_open_source=data.is_open_source,
            is_proxy=data.is_proxy,
            is_mintable=data.is_mintable,
            lp_burned=data.lp_burned,
            lp_locked=data.lp_locked,
            lp_lock_duration_days=data.lp_lock_duration_days,
            contract_renounced=data.contract_renounced,
            top10_holders_pct=data.top10_holder_rate,
            dev_holds_pct=None,
            dev_token_balance=data.dev_token_burn_amount,
            is_honeypot=data.is_honeypot,
            buy_tax=data.buy_tax,
            sell_tax=data.sell_tax,
            raw_data=data.model_dump(mode="json"),
        )
        .on_conflict_do_update(
            index_elements=["token_id"],
            set_={
                "is_open_source": data.is_open_source,
                "is_mintable": data.is_mintable,
                "lp_burned": data.lp_burned,
                "lp_locked": data.lp_locked,
                "lp_lock_duration_days": data.lp_lock_duration_days,
                "contract_renounced": data.contract_renounced,
                "top10_holders_pct": data.top10_holder_rate,
                "is_honeypot": data.is_honeypot,
                "buy_tax": data.buy_tax,
                "sell_tax": data.sell_tax,
                "raw_data": data.model_dump(mode="json"),
                "checked_at": func.now(),
            },
        )
        .returning(TokenSecurity)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.scalar_one()


async def save_token_security_from_birdeye(
    session: AsyncSession,
    token_id: int,
    data: BirdeyeTokenSecurity,
) -> TokenSecurity:
    """Save or update security analysis from Birdeye Data Services."""
    stmt = (
        pg_insert(TokenSecurity)
        .values(
            token_id=token_id,
            is_open_source=None,
            is_proxy=None,
            is_mintable=data.is_mintable,
            lp_burned=None,
            lp_locked=data.lockInfo is not None,
            contract_renounced=None,
            top10_holders_pct=data.top10HolderPercent,
            dev_holds_pct=data.ownerPercentage,
            is_honeypot=data.nonTransferable,
            buy_tax=None,
            sell_tax=None,
            raw_data=data.model_dump(mode="json"),
        )
        .on_conflict_do_update(
            index_elements=["token_id"],
            set_={
                "is_mintable": data.is_mintable,
                "lp_locked": data.lockInfo is not None,
                "top10_holders_pct": data.top10HolderPercent,
                "dev_holds_pct": data.ownerPercentage,
                "is_honeypot": data.nonTransferable,
                "raw_data": data.model_dump(mode="json"),
                "checked_at": func.now(),
            },
        )
        .returning(TokenSecurity)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.scalar_one()


async def save_rugcheck_report(
    session: AsyncSession,
    token_id: int,
    report: RugcheckReport,
) -> None:
    """Save Rugcheck.xyz report data into TokenSecurity (rugcheck_score, rugcheck_risks).

    Updates existing TokenSecurity record; creates one if it doesn't exist.
    """
    risk_names = ", ".join(r.name for r in report.risks) if report.risks else None

    stmt = (
        pg_insert(TokenSecurity)
        .values(
            token_id=token_id,
            rugcheck_score=report.score,
            rugcheck_risks=risk_names,
        )
        .on_conflict_do_update(
            index_elements=["token_id"],
            set_={
                "rugcheck_score": report.score,
                "rugcheck_risks": risk_names,
                "checked_at": func.now(),
            },
        )
    )
    await session.execute(stmt)
    await session.flush()


async def save_goplus_report(
    session: AsyncSession,
    token_id: int,
    report: GoPlusReport,
) -> None:
    """Save GoPlus security report into TokenSecurity (goplus_score field).

    Also cross-validates honeypot: if GoPlus says honeypot, overrides existing flag.
    """
    import json

    goplus_json = json.dumps({
        "is_honeypot": report.is_honeypot,
        "is_mintable": report.is_mintable,
        "buy_tax": report.buy_tax,
        "sell_tax": report.sell_tax,
        "is_open_source": report.is_open_source,
        "is_proxy": report.is_proxy,
        "transfer_pausable": report.transfer_pausable,
        "trading_cooldown": report.trading_cooldown,
        "is_airdrop_scam": report.is_airdrop_scam,
    })

    update_set: dict = {
        "goplus_score": goplus_json,
        "checked_at": func.now(),
    }
    # Cross-validate: GoPlus honeypot overrides if True
    if report.is_honeypot is True:
        update_set["is_honeypot"] = True

    stmt = (
        pg_insert(TokenSecurity)
        .values(
            token_id=token_id,
            goplus_score=goplus_json,
            is_honeypot=report.is_honeypot or False,
        )
        .on_conflict_do_update(
            index_elements=["token_id"],
            set_=update_set,
        )
    )
    await session.execute(stmt)
    await session.flush()


async def update_security_phase12(
    session: AsyncSession,
    token_id: int,
    *,
    bundled_buy_detected: bool | None = None,
    lp_burned_pct_raydium: "Decimal | None" = None,
) -> None:
    """Update TokenSecurity with Phase 12 fields (bundled buy, Raydium LP)."""
    update_set: dict = {"checked_at": func.now()}
    if bundled_buy_detected is not None:
        update_set["bundled_buy_detected"] = bundled_buy_detected
    if lp_burned_pct_raydium is not None:
        update_set["lp_burned_pct_raydium"] = lp_burned_pct_raydium

    stmt = (
        pg_insert(TokenSecurity)
        .values(
            token_id=token_id,
            bundled_buy_detected=bundled_buy_detected,
            lp_burned_pct_raydium=lp_burned_pct_raydium,
        )
        .on_conflict_do_update(
            index_elements=["token_id"],
            set_=update_set,
        )
    )
    await session.execute(stmt)
    await session.flush()


async def update_creator_pumpfun(
    session: AsyncSession,
    creator_address: str,
    pumpfun_dead_tokens: int,
) -> None:
    """Update CreatorProfile with pump.fun dead token count."""
    from src.models.token import CreatorProfile

    stmt = select(CreatorProfile).where(CreatorProfile.address == creator_address)
    result = await session.execute(stmt)
    profile = result.scalar_one_or_none()
    if profile:
        profile.pumpfun_dead_tokens = pumpfun_dead_tokens
        await session.flush()


async def upsert_smart_wallet(
    session: AsyncSession, data: GmgnSmartWallet
) -> SmartWallet:
    """Insert or update a smart wallet."""
    stmt = (
        pg_insert(SmartWallet)
        .values(
            address=data.address,
            category=data.category,
            win_rate=data.win_rate,
            avg_profit_pct=data.avg_profit_pct,
            total_trades=data.total_trades,
            total_pnl_usd=data.total_profit,
        )
        .on_conflict_do_update(
            index_elements=["address"],
            set_={
                "category": data.category,
                "win_rate": data.win_rate,
                "avg_profit_pct": data.avg_profit_pct,
                "total_trades": data.total_trades,
                "total_pnl_usd": data.total_profit,
                "updated_at": func.now(),
            },
        )
        .returning(SmartWallet)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.scalar_one()


async def save_wallet_activity(
    session: AsyncSession,
    wallet_id: int,
    trade: PumpPortalTrade,
    token_id: int | None = None,
) -> WalletActivity:
    """Save a wallet trade event."""
    activity = WalletActivity(
        wallet_id=wallet_id,
        token_id=token_id,
        token_address=trade.mint,
        action=trade.txType,
        amount_sol=trade.solAmount,
        amount_token=trade.tokenAmount,
        tx_hash=trade.signature,
    )
    session.add(activity)
    await session.flush()
    return activity


async def save_top_holders(
    session: AsyncSession,
    snapshot_id: int,
    token_id: int,
    holders: list[GmgnTopHolder],
) -> list[TokenTopHolder]:
    """Save top-10 holders linked to a snapshot."""
    results: list[TokenTopHolder] = []
    for rank, holder in enumerate(holders[:10], start=1):
        record = TokenTopHolder(
            snapshot_id=snapshot_id,
            token_id=token_id,
            rank=rank,
            address=holder.address,
            balance=holder.balance,
            percentage=holder.percentage,
            pnl=holder.pnl,
        )
        session.add(record)
        results.append(record)
    await session.flush()
    return results


async def upsert_token_outcome(
    session: AsyncSession,
    token_id: int,
    snapshot: TokenSnapshot,
    *,
    is_initial: bool = False,
    is_final: bool = False,
    stage_name: str | None = None,
) -> TokenOutcome:
    """Create or update outcome tracking with peak/final metrics.

    Rug detection happens at any stage (HOUR_4+): if drawdown > 90% from peak.
    Final assessment (final_mcap, final_multiplier) only at is_final=True.
    """
    stmt = select(TokenOutcome).where(TokenOutcome.token_id == token_id)
    result = await session.execute(stmt)
    outcome = result.scalar_one_or_none()

    mcap = snapshot.market_cap or snapshot.dex_fdv
    price = snapshot.price or snapshot.dex_price

    if outcome is None:
        outcome = TokenOutcome(
            token_id=token_id,
            initial_mcap=mcap,
            peak_mcap=mcap,
            peak_price=price,
            peak_multiplier=Decimal("1.0") if mcap else None,
            time_to_peak_sec=0,
            peak_snapshot_id=snapshot.id,
        )
        session.add(outcome)
    else:
        # Backfill initial_mcap if it was NULL at creation
        if outcome.initial_mcap is None and mcap is not None:
            outcome.initial_mcap = mcap

        # Update peak if current mcap exceeds previous
        if mcap is not None and (outcome.peak_mcap is None or mcap > outcome.peak_mcap):
            outcome.peak_mcap = mcap
            outcome.peak_price = price
            outcome.peak_snapshot_id = snapshot.id
            if outcome.initial_mcap and outcome.initial_mcap > 0:
                outcome.peak_multiplier = mcap / outcome.initial_mcap

        # Early rug detection: check drawdown at every stage (not just final)
        if mcap is not None and outcome.peak_mcap and outcome.peak_mcap > 0:
            drawdown = (outcome.peak_mcap - mcap) / outcome.peak_mcap
            if drawdown > Decimal("0.9") and outcome.is_rug is not True:
                outcome.is_rug = True
                outcome.outcome_stage = stage_name

        if is_final and mcap is not None:
            outcome.final_mcap = mcap
            if outcome.initial_mcap and outcome.initial_mcap > 0:
                outcome.final_multiplier = mcap / outcome.initial_mcap
            # Final stage: also check rug if not already detected
            if outcome.is_rug is not True and outcome.peak_mcap and outcome.peak_mcap > 0:
                drawdown = (outcome.peak_mcap - mcap) / outcome.peak_mcap
                outcome.is_rug = drawdown > Decimal("0.9")
            if outcome.outcome_stage is None:
                outcome.outcome_stage = stage_name

    outcome.updated_at = datetime.now()
    await session.flush()

    # Update linked Signal records with outcome data
    await _update_signal_outcomes(session, token_id, outcome)

    return outcome


async def _update_signal_outcomes(
    session: AsyncSession,
    token_id: int,
    outcome: TokenOutcome,
) -> None:
    """Update all signals for this token with outcome data from TokenOutcome."""
    stmt = select(Signal).where(Signal.token_id == token_id)
    result = await session.execute(stmt)
    signals = result.scalars().all()

    for sig in signals:
        if outcome.peak_mcap and sig.token_mcap_at_signal and sig.token_mcap_at_signal > 0:
            sig.peak_multiplier_after = outcome.peak_mcap / sig.token_mcap_at_signal
            roi = (outcome.peak_mcap - sig.token_mcap_at_signal) / sig.token_mcap_at_signal * 100
            sig.peak_roi_pct = roi
        sig.is_rug_after = outcome.is_rug
        sig.outcome_updated_at = func.now()

    await session.flush()


async def get_prev_snapshot(
    session: AsyncSession, token_id: int, current_snapshot_id: int
) -> TokenSnapshot | None:
    """Get the most recent snapshot before the current one for price momentum."""
    stmt = (
        select(TokenSnapshot)
        .where(
            TokenSnapshot.token_id == token_id,
            TokenSnapshot.id < current_snapshot_id,
        )
        .order_by(TokenSnapshot.id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_latest_snapshot(
    session: AsyncSession, token_id: int
) -> TokenSnapshot | None:
    """Get the most recent snapshot for a token (before saving a new one)."""
    stmt = (
        select(TokenSnapshot)
        .where(TokenSnapshot.token_id == token_id)
        .order_by(TokenSnapshot.id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_token_by_address(
    session: AsyncSession, address: str, chain: str = "sol"
) -> Token | None:
    """Find a token by its on-chain address."""
    stmt = select(Token).where(Token.address == address, Token.chain == chain)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_token_security_by_token_id(
    session: AsyncSession, token_id: int
) -> TokenSecurity | None:
    """Fetch the security record for a token."""
    stmt = select(TokenSecurity).where(TokenSecurity.token_id == token_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def save_ohlcv(
    session: AsyncSession,
    token_id: int,
    items: list[BirdeyeOHLCVItem],
    interval: str = "5m",
) -> int:
    """Save OHLCV candles (upsert to avoid duplicates). Returns count saved."""
    if not items:
        return 0
    count = 0
    for item in items:
        ts = datetime.utcfromtimestamp(item.unixTime)
        stmt = (
            pg_insert(TokenOHLCV)
            .values(
                token_id=token_id,
                timestamp=ts,
                interval=interval,
                open=item.o,
                high=item.h,
                low=item.l,
                close=item.c,
                volume=item.v,
            )
            .on_conflict_do_update(
                constraint="uq_ohlcv_token_time_interval",
                set_={
                    "open": item.o,
                    "high": item.h,
                    "low": item.l,
                    "close": item.c,
                    "volume": item.v,
                },
            )
        )
        await session.execute(stmt)
        count += 1
    await session.flush()
    return count


async def save_birdeye_trades(
    session: AsyncSession,
    token_id: int,
    items: list[BirdeyeTradeItem],
) -> int:
    """Save individual trades from Birdeye. Returns count saved."""
    if not items:
        return 0
    count = 0
    for item in items:
        ts = (
            datetime.utcfromtimestamp(item.blockUnixTime)
            if item.blockUnixTime
            else None
        )
        # Extract USD amount from 'to' field (bought token)
        amount_usd = None
        amount_token = None
        if item.to and isinstance(item.to, dict):
            amount_usd = item.to.get("nearestPrice")
            amount_token = item.to.get("uiAmount")

        trade = TokenTrade(
            token_id=token_id,
            source="birdeye",
            tx_hash=item.txHash,
            side=item.side or "buy",
            price_usd=item.price,
            amount_token=Decimal(str(amount_token)) if amount_token else None,
            amount_usd=Decimal(str(amount_usd)) if amount_usd else None,
            wallet_address=item.owner,
            timestamp=ts,
        )
        session.add(trade)
        count += 1
    await session.flush()
    return count


async def save_pumpportal_trade(
    session: AsyncSession,
    token_id: int,
    trade: PumpPortalTrade,
) -> TokenTrade:
    """Save a PumpPortal trade event."""
    record = TokenTrade(
        token_id=token_id,
        source="pumpportal",
        tx_hash=trade.signature,
        side=trade.txType,
        amount_token=trade.tokenAmount,
        amount_sol=trade.solAmount,
        wallet_address=trade.traderPublicKey,
    )
    session.add(record)
    await session.flush()
    return record


async def get_holder_velocity(
    session: AsyncSession,
    token_id: int,
) -> float | None:
    """Compute holder velocity (holders/min) from last two snapshots.

    Returns None if fewer than 2 snapshots exist.
    """
    stmt = (
        select(TokenSnapshot.holders_count, TokenSnapshot.timestamp)
        .where(
            TokenSnapshot.token_id == token_id,
            TokenSnapshot.holders_count.isnot(None),
        )
        .order_by(TokenSnapshot.timestamp.desc())
        .limit(2)
    )
    result = await session.execute(stmt)
    rows = result.all()
    if len(rows) < 2:
        return None

    latest_holders, latest_ts = rows[0]
    prev_holders, prev_ts = rows[1]

    delta_holders = latest_holders - prev_holders
    delta_minutes = (latest_ts - prev_ts).total_seconds() / 60.0
    if delta_minutes <= 0:
        return None
    return delta_holders / delta_minutes


async def cleanup_old_data(
    session: AsyncSession,
    *,
    snapshot_retention_days: int = 7,
    trade_retention_days: int = 7,
    ohlcv_retention_days: int = 14,
) -> dict[str, int]:
    """Delete old snapshots, trades, and OHLCV data beyond retention period.

    Keeps recent data for active analysis while preventing unbounded DB growth.
    Returns count of deleted rows per table.
    """
    from datetime import timedelta

    from sqlalchemy import delete

    now = datetime.utcnow()  # noqa: DTZ003 — DB uses naive UTC
    cutoff_snapshot = now - timedelta(days=snapshot_retention_days)
    cutoff_trade = now - timedelta(days=trade_retention_days)
    cutoff_ohlcv = now - timedelta(days=ohlcv_retention_days)

    # Delete old snapshots
    snap_result = await session.execute(
        delete(TokenSnapshot).where(TokenSnapshot.timestamp < cutoff_snapshot)
    )

    # Delete old trades
    trade_result = await session.execute(
        delete(TokenTrade).where(TokenTrade.timestamp < cutoff_trade)
    )

    # Delete old OHLCV candles
    ohlcv_result = await session.execute(
        delete(TokenOHLCV).where(TokenOHLCV.timestamp < cutoff_ohlcv)
    )

    deleted = {
        "snapshots": snap_result.rowcount,
        "trades": trade_result.rowcount,
        "ohlcv": ohlcv_result.rowcount,
    }

    total = sum(deleted.values())
    if total > 0:
        logger.info(f"[CLEANUP] Deleted {total} old rows: {deleted}")

    return deleted
