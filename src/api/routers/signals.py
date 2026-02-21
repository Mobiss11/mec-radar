"""Signal endpoints — list, detail."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_session
from src.models.signal import Signal
from src.models.token import Token

router = APIRouter(prefix="/api/v1/signals", tags=["signals"])


def _signal_to_dict(sig: Signal) -> dict[str, Any]:
    d: dict[str, Any] = {}
    for c in sig.__table__.columns:
        val = getattr(sig, c.name)
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        elif hasattr(val, "__float__"):
            val = float(val)
        d[c.name] = val
    return d


@router.get("")
async def list_signals(
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
    status_filter: str = Query(
        "strong_buy,buy,watch",
        alias="status",
        max_length=100,
        description="Comma-separated statuses",
    ),
    cursor: int | None = Query(None, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """List signals with status filter and cursor pagination."""
    statuses = [s.strip() for s in status_filter.split(",") if s.strip()]

    query = (
        select(Signal, Token.symbol, Token.name, Token.image_url)
        .join(Token, Signal.token_id == Token.id)
        .where(Signal.status.in_(statuses))
    )

    if cursor:
        query = query.where(Signal.id < cursor)

    query = query.order_by(desc(Signal.id)).limit(limit + 1)

    result = await session.execute(query)
    rows = result.all()

    has_more = len(rows) > limit
    items = []
    for row in rows[:limit]:
        sig, symbol, name, image_url = row
        d = _signal_to_dict(sig)
        d["token_symbol"] = symbol
        d["token_name"] = name
        d["token_image_url"] = image_url
        items.append(d)

    next_cursor = items[-1]["id"] if items and has_more else None

    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}


@router.get("/{signal_id}")
async def signal_detail(
    signal_id: int,
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Signal detail with token info."""
    result = await session.execute(
        select(Signal).where(Signal.id == signal_id)
    )
    sig = result.scalar_one_or_none()
    if not sig:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found")

    # Token info
    token_result = await session.execute(
        select(Token).where(Token.id == sig.token_id)
    )
    token = token_result.scalar_one_or_none()

    sig_dict = _signal_to_dict(sig)
    token_dict = None
    if token:
        token_dict = {}
        for c in token.__table__.columns:
            val = getattr(token, c.name)
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            elif hasattr(val, "__float__"):
                val = float(val)
            token_dict[c.name] = val

    return {"signal": sig_dict, "token": token_dict}
