from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class Trade(Base):
    """Executed trade (real or paper)."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"))
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"))
    token_address: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(10))
    amount_sol: Mapped[Decimal | None] = mapped_column(Numeric)
    amount_token: Mapped[Decimal | None] = mapped_column(Numeric)
    price: Mapped[Decimal | None] = mapped_column(Numeric)
    slippage_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    fee_sol: Mapped[Decimal | None] = mapped_column(Numeric)
    tx_hash: Mapped[str | None] = mapped_column(String(128))
    is_paper: Mapped[int] = mapped_column(Integer, default=1)
    # Phase 57: Copy trading source tracking
    source: Mapped[str | None] = mapped_column(String(50))  # "signal" | "copy_trade"
    copied_from_wallet: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(20))
    executed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (Index("idx_trades_time", "executed_at"),)


class Position(Base):
    """Aggregated position."""

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"))
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"))
    token_address: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str | None] = mapped_column(String(32))
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric)
    current_price: Mapped[Decimal | None] = mapped_column(Numeric)
    amount_token: Mapped[Decimal | None] = mapped_column(Numeric)
    amount_sol_invested: Mapped[Decimal | None] = mapped_column(Numeric)
    pnl_pct: Mapped[Decimal] = mapped_column(Numeric, default=0)
    pnl_usd: Mapped[Decimal] = mapped_column(Numeric, default=0)
    max_price: Mapped[Decimal | None] = mapped_column(Numeric)
    status: Mapped[str] = mapped_column(String(20), default="open")
    close_reason: Mapped[str | None] = mapped_column(String(30))
    is_paper: Mapped[int] = mapped_column(Integer, default=1)
    # Phase 57: Copy trading source tracking
    source: Mapped[str | None] = mapped_column(String(50))  # "signal" | "copy_trade"
    copied_from_wallet: Mapped[str | None] = mapped_column(String(64))
    opened_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        Index("idx_positions_status", "status"),
        Index("idx_positions_token_status", "token_id", "status"),
        Index(
            "uq_positions_open_paper",
            "token_id", "is_paper", "source",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )
