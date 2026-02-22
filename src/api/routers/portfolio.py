"""Portfolio endpoints — paper & real trading summary, positions, PnL history."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_session
from src.api.metrics_registry import registry
from src.models.trade import Position, Trade

router = APIRouter(prefix="/api/v1/portfolio", tags=["portfolio"])


def _is_paper_filter(mode: str):
    """Build SQLAlchemy filter for is_paper based on mode (paper/real/all)."""
    if mode == "paper":
        return Position.is_paper == 1
    elif mode == "real":
        return Position.is_paper == 0
    # mode == "all" → no filter
    return True


def _is_paper_filter_trade(mode: str):
    """Build SQLAlchemy filter for Trade.is_paper based on mode."""
    if mode == "paper":
        return Trade.is_paper == 1
    elif mode == "real":
        return Trade.is_paper == 0
    return True


@router.get("/summary")
async def portfolio_summary(
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
    mode: str = Query("paper", pattern="^(paper|real|all)$"),
) -> dict[str, Any]:
    """Portfolio summary — paper, real, or combined."""
    # Use trader's built-in summary for single-mode fast path
    if mode == "paper" and registry.paper_trader:
        summary = await registry.paper_trader.get_portfolio_summary(session)
        summary["mode"] = "paper"
        summary["real_trading_enabled"] = registry.real_trader is not None
        return summary
    if mode == "real" and registry.real_trader:
        summary = await registry.real_trader.get_portfolio_summary(session)
        summary["mode"] = "real"
        summary["real_trading_enabled"] = True
        return summary

    # Fallback / "all" mode: query directly
    paper_filter = _is_paper_filter(mode)
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
        ).where(paper_filter)
    )
    row = result.one()

    wins = row.wins or 0
    losses = row.losses or 0
    total_trades = wins + losses

    result_dict: dict[str, Any] = {
        "mode": mode,
        "open_count": row.open_count or 0,
        "closed_count": row.closed_count or 0,
        "total_invested_sol": float(row.total_invested or 0),
        "total_pnl_usd": float(row.total_pnl_usd or 0),
        "win_rate": round(wins / total_trades * 100, 1) if total_trades > 0 else 0.0,
        "wins": wins,
        "losses": losses,
        "real_trading_enabled": registry.real_trader is not None,
    }

    # Attach real trader wallet / circuit breaker info when available
    if registry.real_trader and mode in ("real", "all"):
        try:
            result_dict["wallet_balance"] = await registry.real_trader._wallet.get_sol_balance()
            result_dict["circuit_breaker_tripped"] = registry.real_trader._circuit.is_tripped
            result_dict["total_failures"] = registry.real_trader._circuit.total_failures
        except Exception:
            result_dict["wallet_balance"] = None
            result_dict["circuit_breaker_tripped"] = None

    return result_dict


@router.get("/positions")
async def list_positions(
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
    mode: str = Query("paper", pattern="^(paper|real|all)$"),
    pos_status: str = Query("open", alias="status", max_length=20),
    cursor: int | None = Query(None, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """List trading positions — paper, real, or all."""
    query = (
        select(Position)
        .where(_is_paper_filter(mode), Position.status == pos_status)
    )

    if cursor:
        query = query.where(Position.id < cursor)

    order = desc(Position.opened_at) if pos_status == "open" else desc(Position.closed_at)
    query = query.order_by(order).limit(limit + 1)

    result = await session.execute(query)
    positions = result.scalars().all()

    has_more = len(positions) > limit
    page = positions[:limit]

    # Batch-load tx_hash from Trade for real positions (Position has no tx_hash column)
    tx_hash_map: dict[int, str | None] = {}
    real_token_ids = [p.token_id for p in page if not p.is_paper]
    if real_token_ids:
        # Get first tx_hash per token (buy side) for real trades
        tx_result = await session.execute(
            select(Trade.token_id, Trade.tx_hash)
            .where(Trade.token_id.in_(real_token_ids), Trade.is_paper == 0, Trade.side == "buy")
            .order_by(Trade.executed_at)
        )
        for row in tx_result.all():
            if row.token_id not in tx_hash_map:
                tx_hash_map[row.token_id] = row.tx_hash

    items = []
    for p in page:
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
            "is_paper": bool(p.is_paper),
            "tx_hash": tx_hash_map.get(p.token_id) if not p.is_paper else None,
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

    # Related trades — filter by same is_paper as the position
    trade_result = await session.execute(
        select(Trade)
        .where(Trade.token_id == pos.token_id, Trade.is_paper == pos.is_paper)
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
            "fee_sol": float(t.fee_sol) if t.fee_sol else None,
            "tx_hash": t.tx_hash,
            "is_paper": bool(t.is_paper),
            "executed_at": t.executed_at.isoformat() if t.executed_at else None,
        })

    return {"position": pos_dict, "trades": trade_items}


@router.get("/pnl-history")
async def pnl_history(
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
    mode: str = Query("paper", pattern="^(paper|real|all)$"),
    days: int = Query(30, ge=1, le=365),
) -> dict[str, Any]:
    """Daily cumulative PnL for closed positions — paper, real, or combined."""
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)

    result = await session.execute(
        select(
            func.date(Position.closed_at).label("date"),
            func.sum(Position.pnl_usd).label("daily_pnl"),
        )
        .where(
            _is_paper_filter(mode),
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
