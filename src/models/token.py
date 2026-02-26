from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class Token(Base):
    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    address: Mapped[str] = mapped_column(String(64))
    chain: Mapped[str] = mapped_column(String(10), default="sol")
    name: Mapped[str | None] = mapped_column(String(255))
    symbol: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    source: Mapped[str | None] = mapped_column(String(50))

    # PumpPortal launch data
    creator_address: Mapped[str | None] = mapped_column(String(64))
    initial_buy_sol: Mapped[Decimal | None] = mapped_column(Numeric)
    initial_mcap_sol: Mapped[Decimal | None] = mapped_column(Numeric)
    bonding_curve_key: Mapped[str | None] = mapped_column(String(64))
    v_sol_in_bonding_curve: Mapped[Decimal | None] = mapped_column(Numeric)
    v_tokens_in_bonding_curve: Mapped[Decimal | None] = mapped_column(Numeric)

    # Meteora DBC data
    dbc_pool_address: Mapped[str | None] = mapped_column(String(64))
    dbc_quote_reserve: Mapped[Decimal | None] = mapped_column(Numeric)
    dbc_launchpad: Mapped[str | None] = mapped_column(String(50))
    dbc_is_migrated: Mapped[bool | None] = mapped_column(Boolean)
    dbc_migration_timestamp: Mapped[datetime | None] = mapped_column(DateTime)
    dbc_damm_tvl: Mapped[Decimal | None] = mapped_column(Numeric)
    dbc_damm_volume: Mapped[Decimal | None] = mapped_column(Numeric)

    # Token metadata (from Birdeye)
    image_url: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(String(2000))
    website: Mapped[str | None] = mapped_column(String(500))
    twitter: Mapped[str | None] = mapped_column(String(500))
    telegram: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        UniqueConstraint("address", "chain", name="uq_token_address_chain"),
        Index("idx_tokens_creator", "creator_address"),
        Index("idx_tokens_source", "source"),
    )


class TokenSnapshot(Base):
    """Periodic metrics snapshot for active tokens."""

    __tablename__ = "token_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    price: Mapped[Decimal | None] = mapped_column(Numeric)
    market_cap: Mapped[Decimal | None] = mapped_column(Numeric)
    liquidity_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    volume_5m: Mapped[Decimal | None] = mapped_column(Numeric)
    volume_1h: Mapped[Decimal | None] = mapped_column(Numeric)
    volume_24h: Mapped[Decimal | None] = mapped_column(Numeric)
    holders_count: Mapped[int | None] = mapped_column(Integer)
    top10_holders_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    dev_holds_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    smart_wallets_count: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[int | None] = mapped_column(Integer)
    score_v3: Mapped[int | None] = mapped_column(Integer)
    stage: Mapped[str | None] = mapped_column(String(20))

    # DexScreener cross-validation
    dex_price: Mapped[Decimal | None] = mapped_column(Numeric)
    dex_liquidity_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    dex_volume_5m: Mapped[Decimal | None] = mapped_column(Numeric)
    dex_volume_1h: Mapped[Decimal | None] = mapped_column(Numeric)
    dex_volume_24h: Mapped[Decimal | None] = mapped_column(Numeric)
    dex_fdv: Mapped[Decimal | None] = mapped_column(Numeric)

    # Trade counts (from Birdeye or DexScreener)
    buys_5m: Mapped[int | None] = mapped_column(Integer)
    sells_5m: Mapped[int | None] = mapped_column(Integer)
    buys_1h: Mapped[int | None] = mapped_column(Integer)
    sells_1h: Mapped[int | None] = mapped_column(Integer)
    buys_24h: Mapped[int | None] = mapped_column(Integer)
    sells_24h: Mapped[int | None] = mapped_column(Integer)

    # Volatility and LP metrics
    volatility_5m: Mapped[Decimal | None] = mapped_column(Numeric)
    volatility_1h: Mapped[Decimal | None] = mapped_column(Numeric)
    lp_removed_pct: Mapped[Decimal | None] = mapped_column(Numeric)

    # Jupiter cross-validation
    jupiter_price: Mapped[Decimal | None] = mapped_column(Numeric)

    # Phase 15: Vybe holder PnL
    holders_in_profit_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    vybe_top_holder_pct: Mapped[Decimal | None] = mapped_column(Numeric)

    # Phase 15: Twitter social signals
    twitter_mentions: Mapped[int | None] = mapped_column(Integer)
    twitter_kol_mentions: Mapped[int | None] = mapped_column(Integer)
    twitter_max_likes: Mapped[int | None] = mapped_column(Integer)

    # Phase 16: Holder growth, website, telegram, LLM
    holder_growth_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    has_website: Mapped[bool | None] = mapped_column(Boolean)
    domain_age_days: Mapped[int | None] = mapped_column(Integer)
    tg_member_count: Mapped[int | None] = mapped_column(Integer)
    llm_risk_score: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("idx_snapshots_token_time", "token_id", "timestamp"),
        Index("idx_snapshots_timestamp", "timestamp"),
        Index("idx_snapshots_token_stage", "token_id", "stage"),
    )


class TokenSecurity(Base):
    """Security analysis data from gmgn.ai."""

    __tablename__ = "token_security"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"), unique=True)
    is_open_source: Mapped[bool | None]
    is_proxy: Mapped[bool | None]
    is_mintable: Mapped[bool | None]
    lp_burned: Mapped[bool | None]
    lp_locked: Mapped[bool | None]
    lp_lock_duration_days: Mapped[int | None] = mapped_column(Integer)
    contract_renounced: Mapped[bool | None]
    top10_holders_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    dev_holds_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    dev_token_balance: Mapped[Decimal | None] = mapped_column(Numeric)
    is_honeypot: Mapped[bool | None]
    buy_tax: Mapped[Decimal | None] = mapped_column(Numeric)
    sell_tax: Mapped[Decimal | None] = mapped_column(Numeric)
    raw_data: Mapped[dict | None] = mapped_column(JSON)
    rugcheck_score: Mapped[int | None] = mapped_column(Integer)
    rugcheck_score_max: Mapped[int | None] = mapped_column(Integer)  # Phase 53: monotonic max, never decreases
    rugcheck_risks: Mapped[str | None] = mapped_column(String(2000))
    # Phase 12 fields
    bundled_buy_detected: Mapped[bool | None] = mapped_column(Boolean)
    lp_burned_pct_raydium: Mapped[Decimal | None] = mapped_column(Numeric)
    goplus_score: Mapped[str | None] = mapped_column(String(2000))
    checked_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TokenTopHolder(Base):
    """Point-in-time snapshot of a token's top holders."""

    __tablename__ = "token_top_holders"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("token_snapshots.id", ondelete="CASCADE"))
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"))
    rank: Mapped[int] = mapped_column(Integer)
    address: Mapped[str] = mapped_column(String(64))
    balance: Mapped[Decimal | None] = mapped_column(Numeric)
    percentage: Mapped[Decimal | None] = mapped_column(Numeric)
    pnl: Mapped[Decimal | None] = mapped_column(Numeric)

    __table_args__ = (
        Index("idx_top_holders_snapshot", "snapshot_id"),
        Index("idx_top_holders_token", "token_id"),
    )


class TokenOHLCV(Base):
    """OHLCV price candles from Birdeye."""

    __tablename__ = "token_ohlcv"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    interval: Mapped[str] = mapped_column(String(10))  # "1m", "5m", "15m", "1H"
    open: Mapped[Decimal | None] = mapped_column(Numeric)
    high: Mapped[Decimal | None] = mapped_column(Numeric)
    low: Mapped[Decimal | None] = mapped_column(Numeric)
    close: Mapped[Decimal | None] = mapped_column(Numeric)
    volume: Mapped[Decimal | None] = mapped_column(Numeric)

    __table_args__ = (
        Index("idx_ohlcv_token_time", "token_id", "timestamp"),
        UniqueConstraint(
            "token_id", "timestamp", "interval",
            name="uq_ohlcv_token_time_interval",
        ),
    )


class TokenTrade(Base):
    """Individual trade events from Birdeye or PumpPortal."""

    __tablename__ = "token_trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"))
    source: Mapped[str] = mapped_column(String(20))  # "birdeye", "pumpportal"
    tx_hash: Mapped[str | None] = mapped_column(String(128))
    side: Mapped[str] = mapped_column(String(4))  # "buy" or "sell"
    price_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    amount_token: Mapped[Decimal | None] = mapped_column(Numeric)
    amount_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    amount_sol: Mapped[Decimal | None] = mapped_column(Numeric)
    wallet_address: Mapped[str | None] = mapped_column(String(64))
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_trades_token_time", "token_id", "timestamp"),
        Index("idx_trades_wallet", "wallet_address"),
    )


class TokenOutcome(Base):
    """Tracks peak performance for learning which patterns predict success."""

    __tablename__ = "token_outcomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"), unique=True)
    initial_mcap: Mapped[Decimal | None] = mapped_column(Numeric)
    peak_mcap: Mapped[Decimal | None] = mapped_column(Numeric)
    peak_price: Mapped[Decimal | None] = mapped_column(Numeric)
    peak_multiplier: Mapped[Decimal | None] = mapped_column(Numeric)
    time_to_peak_sec: Mapped[int | None] = mapped_column(Integer)
    final_mcap: Mapped[Decimal | None] = mapped_column(Numeric)
    final_multiplier: Mapped[Decimal | None] = mapped_column(Numeric)
    peak_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("token_snapshots.id", ondelete="SET NULL"))
    is_rug: Mapped[bool | None] = mapped_column(Boolean)
    outcome_stage: Mapped[str | None] = mapped_column(String(20))
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CreatorProfile(Base):
    """Profiling data for token creators — launch history and success rate."""

    __tablename__ = "creator_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    address: Mapped[str] = mapped_column(String(64), unique=True)
    total_launches: Mapped[int] = mapped_column(Integer, default=0)
    rugged_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_peak_multiplier: Mapped[Decimal | None] = mapped_column(Numeric)
    avg_time_to_peak_sec: Mapped[int | None] = mapped_column(Integer)
    last_launch_at: Mapped[datetime | None] = mapped_column(DateTime)
    risk_score: Mapped[int | None] = mapped_column(Integer)  # 0-100, higher = riskier
    is_first_launch: Mapped[bool | None] = mapped_column(Boolean)
    funded_by: Mapped[str | None] = mapped_column(String(64))
    funding_risk_score: Mapped[int | None] = mapped_column(Integer)
    pumpfun_dead_tokens: Mapped[int | None] = mapped_column(Integer)  # Phase 12
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
