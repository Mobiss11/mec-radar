"""Token endpoints — list, detail, snapshots."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_session
from src.models.signal import Signal
from src.models.token import Token, TokenSecurity, TokenSnapshot

router = APIRouter(prefix="/api/v1/tokens", tags=["tokens"])


@router.get("")
async def list_tokens(
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
    cursor: int | None = Query(None, ge=0, description="Last seen token ID"),
    limit: int = Query(20, ge=1, le=100),
    search: str = Query("", max_length=100),
    source: str = Query("", max_length=30),
    min_score: int = Query(0, ge=0, le=100),
    sort: str = Query("score", pattern="^(score|newest|mcap|liquidity)$"),
    enriched_only: bool = Query(False, description="Only show enriched tokens"),
) -> dict[str, Any]:
    """List tokens with cursor pagination, search, filters, and sorting."""
    # Latest snapshot subquery for score + stage
    latest_snap = (
        select(
            TokenSnapshot.token_id,
            func.max(TokenSnapshot.id).label("max_id"),
        )
        .group_by(TokenSnapshot.token_id)
        .subquery()
    )

    query = (
        select(
            Token,
            TokenSnapshot.score,
            TokenSnapshot.score_v3,
            TokenSnapshot.price,
            TokenSnapshot.market_cap,
            TokenSnapshot.liquidity_usd,
            TokenSnapshot.holders_count,
            TokenSnapshot.stage,
        )
        .outerjoin(latest_snap, Token.id == latest_snap.c.token_id)
        .outerjoin(TokenSnapshot, TokenSnapshot.id == latest_snap.c.max_id)
    )

    # Filters
    if search:
        like = f"%{search}%"
        query = query.where(
            (Token.symbol.ilike(like))
            | (Token.name.ilike(like))
            | (Token.address.ilike(like))
        )
    if source:
        query = query.where(Token.source == source)
    if min_score > 0:
        query = query.where(TokenSnapshot.score >= min_score)
    if enriched_only:
        query = query.where(TokenSnapshot.score.isnot(None))

    # Sorting
    sort_map = {
        "score": desc(func.coalesce(TokenSnapshot.score, -1)),
        "newest": desc(Token.id),
        "mcap": desc(func.coalesce(TokenSnapshot.market_cap, -1)),
        "liquidity": desc(func.coalesce(TokenSnapshot.liquidity_usd, -1)),
    }
    order_col = sort_map.get(sort, sort_map["score"])

    # Offset-based pagination for non-newest sorts (cursor only works with id order)
    if sort == "newest" and cursor:
        query = query.where(Token.id < cursor)

    query = query.order_by(order_col, desc(Token.id)).limit(limit + 1)

    # For non-newest sorts, use offset via cursor as page number
    if sort != "newest" and cursor:
        query = query.offset(cursor)

    result = await session.execute(query)
    rows = result.all()

    has_more = len(rows) > limit
    items = []
    for row in rows[:limit]:
        token = row[0]
        items.append({
            "id": token.id,
            "address": token.address,
            "name": token.name,
            "symbol": token.symbol,
            "source": token.source,
            "score": row[1],
            "score_v3": row[2],
            "price": float(row[3]) if row[3] else None,
            "market_cap": float(row[4]) if row[4] else None,
            "liquidity_usd": float(row[5]) if row[5] else None,
            "holders_count": row[6],
            "stage": row[7],
            "created_at": token.created_at.isoformat() if token.created_at else None,
            "image_url": token.image_url,
        })

    # Cursor depends on sort type
    if sort == "newest":
        next_cursor = items[-1]["id"] if items and has_more else None
    else:
        # Offset-based: next cursor = current offset + limit
        next_cursor = ((cursor or 0) + limit) if has_more else None

    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}


@router.get("/{address}")
async def token_detail(
    address: str,
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Full token detail: token + latest snapshot + security + active signal."""
    # Token
    result = await session.execute(
        select(Token).where(Token.address == address)
    )
    token = result.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")

    # Latest snapshot
    snap_result = await session.execute(
        select(TokenSnapshot)
        .where(TokenSnapshot.token_id == token.id)
        .order_by(desc(TokenSnapshot.id))
        .limit(1)
    )
    snapshot = snap_result.scalar_one_or_none()

    # Security
    sec_result = await session.execute(
        select(TokenSecurity).where(TokenSecurity.token_id == token.id)
    )
    security = sec_result.scalar_one_or_none()

    # Active signal
    sig_result = await session.execute(
        select(Signal)
        .where(
            Signal.token_id == token.id,
            Signal.status.in_(("strong_buy", "buy", "watch")),
        )
        .order_by(desc(Signal.updated_at))
        .limit(1)
    )
    signal = sig_result.scalar_one_or_none()

    def _model_to_dict(obj: Any) -> dict[str, Any] | None:
        if obj is None:
            return None
        d = {}
        for c in obj.__table__.columns:
            val = getattr(obj, c.name)
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            elif hasattr(val, "__float__"):
                val = float(val)
            d[c.name] = val
        return d

    return {
        "token": _model_to_dict(token),
        "latest_snapshot": _model_to_dict(snapshot),
        "security": _model_to_dict(security),
        "active_signal": _model_to_dict(signal),
    }


@router.get("/{address}/snapshots")
async def token_snapshots(
    address: str,
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """Historical snapshots for a token."""
    result = await session.execute(
        select(Token.id).where(Token.address == address)
    )
    token_id = result.scalar_one_or_none()
    if not token_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")

    snap_result = await session.execute(
        select(TokenSnapshot)
        .where(TokenSnapshot.token_id == token_id)
        .order_by(desc(TokenSnapshot.id))
        .limit(limit)
    )
    snapshots = snap_result.scalars().all()

    items = []
    for s in snapshots:
        d = {}
        for c in s.__table__.columns:
            val = getattr(s, c.name)
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            elif hasattr(val, "__float__"):
                val = float(val)
            d[c.name] = val
        items.append(d)

    return {"items": items}
