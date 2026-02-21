from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class SmartWallet(Base):
    """Tracked smart wallets with proven history."""

    __tablename__ = "smart_wallets"

    id: Mapped[int] = mapped_column(primary_key=True)
    address: Mapped[str] = mapped_column(String(64), unique=True)
    chain: Mapped[str] = mapped_column(String(10), default="sol")
    category: Mapped[str | None] = mapped_column(String(50))
    label: Mapped[str | None] = mapped_column(String(255))
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric)
    avg_profit_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    total_trades: Mapped[int | None] = mapped_column(Integer)
    total_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class WalletActivity(Base):
    """Individual trades by smart wallets."""

    __tablename__ = "wallet_activity"

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("smart_wallets.id"))
    token_id: Mapped[int | None] = mapped_column(ForeignKey("tokens.id"))
    token_address: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str | None] = mapped_column(String(10))
    amount_sol: Mapped[Decimal | None] = mapped_column(Numeric)
    amount_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    amount_token: Mapped[Decimal | None] = mapped_column(Numeric)
    tx_hash: Mapped[str | None] = mapped_column(String(128))
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_wallet_activity_time", "timestamp"),
        Index("idx_wallet_activity_wallet", "wallet_id"),
    )


class WalletCluster(Base):
    """Groups of wallets identified as belonging to the same entity."""

    __tablename__ = "wallet_clusters"

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[str] = mapped_column(String(64))
    wallet_address: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric)
    method: Mapped[str] = mapped_column(String(30))  # "coordinated_trade", "holder_overlap"
    detected_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_wallet_clusters_cluster", "cluster_id"),
        Index("idx_wallet_clusters_wallet", "wallet_address"),
    )
