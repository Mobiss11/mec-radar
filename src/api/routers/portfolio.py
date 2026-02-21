"""Portfolio endpoints — paper trading summary, positions, PnL history."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_session
from src.api.metrics_registry import registry
from src.models.trade import Position, Trade

router = APIRouter(prefix="/api/v1/portfolio", tags=["portfolio"])


@router.get("/summary")
async def portfolio_summary(
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Paper trading portfolio summary."""
    if registry.paper_trader:
        return await registry.paper_trader.get_portfolio_summary(session)

    # Fallback: query directly
    result = await session.execute(
        select(
            func.count().filter(Position.status == "open").label("open_count"),
            func.count().filter(Position.status == "closed").label("closed_count"),
            func.sum(Position.amount_sol_invested).label("total_invested"),
            func.sum(
                case((Position.status == "closed", Position.pnl_usd), else_=0)
            ).label("total_pnl_usd"),
            func.count().filter(
                (Position.status == "closed") & (Position.pnl_pct > 0)
            ).label("wins"),
            func.count().filter(
                (Position.status == "closed") & (Position.pnl_pct <= 0)
            ).label("losses"),
        ).where(Position.is_paper == 1)
    )
    row = result.one()

    wins = row.wins or 0
    losses = row.losses or 0
    total_trades = wins + losses

    return {
        "open_count": row.open_count or 0,
        "closed_count": row.closed_count or 0,
        "total_invested_sol": float(row.total_invested or 0),
        "total_pnl_usd": float(row.total_pnl_usd or 0),
        "win_rate": round(wins / total_trades * 100, 1) if total_trades > 0 else 0.0,
        "wins": wins,
        "losses": losses,
    }


@router.get("/positions")
async def list_positions(
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
    pos_status: str = Query("open", alias="status", max_length=20),
    cursor: int | None = Query(None, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """List paper trading positions."""
    query = (
        select(Position)
        .where(Position.is_paper == 1, Position.status == pos_status)
    )

    if cursor:
        query = query.where(Position.id < cursor)

    order = desc(Position.opened_at) if pos_status == "open" else desc(Position.closed_at)
    query = query.order_by(order).limit(limit + 1)

    result = await session.execute(query)
    positions = result.scalars().all()

    has_more = len(positions) > limit
    items = []
    for p in positions[:limit]:
        items.append({
            "id": p.id,
            "token_address": p.token_address,
            "symbol": p.symbol,
            "entry_price": float(p.entry_price) if p.entry_price else None,
            "current_price": float(p.current_price) if p.current_price else None,
            "amount_sol_invested": float(p.amount_sol_invested) if p.amount_sol_invested else None,
            "pnl_pct": float(p.pnl_pct) if p.pnl_pct else None,
            "pnl_usd": float(p.pnl_usd) if p.pnl_usd else None,
            "max_price": float(p.max_price) if p.max_price else None,
            "status": p.status,
            "close_reason": p.close_reason,
            "opened_at": p.opened_at.isoformat() if p.opened_at else None,
            "closed_at": p.closed_at.isoformat() if p.closed_at else None,
        })

    next_cursor = items[-1]["id"] if items and has_more else None
    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}


@router.get("/positions/{position_id}")
async def position_detail(
    position_id: int,
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Position detail with trades."""
    result = await session.execute(
        select(Position).where(Position.id == position_id)
    )
    pos = result.scalar_one_or_none()
    if not pos:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Position not found")

    # Related trades
    trade_result = await session.execute(
        select(Trade)
        .where(Trade.token_id == pos.token_id, Trade.is_paper == 1)
        .order_by(Trade.executed_at)
    )
    trades = trade_result.scalars().all()

    pos_dict = {
        "id": pos.id,
        "token_address": pos.token_address,
        "symbol": pos.symbol,
        "entry_price": float(pos.entry_price) if pos.entry_price else None,
        "current_price": float(pos.current_price) if pos.current_price else None,
        "amount_token": float(pos.amount_token) if pos.amount_token else None,
        "amount_sol_invested": float(pos.amount_sol_invested) if pos.amount_sol_invested else None,
        "pnl_pct": float(pos.pnl_pct) if pos.pnl_pct else None,
        "pnl_usd": float(pos.pnl_usd) if pos.pnl_usd else None,
        "max_price": float(pos.max_price) if pos.max_price else None,
        "status": pos.status,
        "close_reason": pos.close_reason,
        "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
        "closed_at": pos.closed_at.isoformat() if pos.closed_at else None,
    }

    trade_items = []
    for t in trades:
        trade_items.append({
            "id": t.id,
            "side": t.side,
            "amount_sol": float(t.amount_sol) if t.amount_sol else None,
            "price": float(t.price) if t.price else None,
            "slippage_pct": float(t.slippage_pct) if t.slippage_pct else None,
            "executed_at": t.executed_at.isoformat() if t.executed_at else None,
        })

    return {"position": pos_dict, "trades": trade_items}


@router.get("/pnl-history")
async def pnl_history(
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
    days: int = Query(30, ge=1, le=365),
) -> dict[str, Any]:
    """Daily cumulative PnL for closed paper positions."""
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)

    result = await session.execute(
        select(
            func.date(Position.closed_at).label("date"),
            func.sum(Position.pnl_usd).label("daily_pnl"),
        )
        .where(
            Position.is_paper == 1,
            Position.status == "closed",
            Position.closed_at >= cutoff,
        )
        .group_by(func.date(Position.closed_at))
        .order_by(func.date(Position.closed_at))
    )
    rows = result.all()

    cumulative = 0.0
    items = []
    for row in rows:
        daily = float(row.daily_pnl or 0)
        cumulative += daily
        items.append({
            "date": str(row.date),
            "daily_pnl_usd": round(daily, 2),
            "cumulative_pnl_usd": round(cumulative, 2),
        })

    return {"items": items}
