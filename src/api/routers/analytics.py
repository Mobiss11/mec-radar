"""Analytics endpoints — aggregation queries for dashboards."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_session
from src.models.signal import Signal
from src.models.token import Token, TokenSnapshot
from src.models.trade import Position

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@router.get("/score-distribution")
async def score_distribution(
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Score distribution histogram (v2 + v3) from latest snapshots."""
    # Latest snapshot per token
    latest = (
        select(func.max(TokenSnapshot.id).label("max_id"))
        .group_by(TokenSnapshot.token_id)
        .subquery()
    )
    snap_query = (
        select(TokenSnapshot.score, TokenSnapshot.score_v3)
        .join(latest, TokenSnapshot.id == latest.c.max_id)
        .where(TokenSnapshot.score.isnot(None))
    )
    result = await session.execute(snap_query)
    rows = result.all()

    # Bucket into ranges
    buckets = ["0-14", "15-29", "30-44", "45-59", "60-74", "75-89", "90-100"]
    v2_counts = {b: 0 for b in buckets}
    v3_counts = {b: 0 for b in buckets}

    for score_v2, score_v3 in rows:
        if score_v2 is not None:
            idx = min(int(score_v2) // 15, 6)
            v2_counts[buckets[idx]] += 1
        if score_v3 is not None:
            idx = min(int(score_v3) // 15, 6)
            v3_counts[buckets[idx]] += 1

    return {
        "v2": [{"bucket": b, "count": c} for b, c in v2_counts.items()],
        "v3": [{"bucket": b, "count": c} for b, c in v3_counts.items()],
    }


@router.get("/signals-by-status")
async def signals_by_status(
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
    hours: int = Query(24, ge=1, le=168),
) -> dict[str, int]:
    """Signal count by status in the last N hours."""
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=hours)

    result = await session.execute(
        select(Signal.status, func.count())
        .where(Signal.created_at >= cutoff)
        .group_by(Signal.status)
    )
    return {status: count for status, count in result.all()}


@router.get("/discovery-by-source")
async def discovery_by_source(
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
    hours: int = Query(24, ge=1, le=168),
) -> dict[str, int]:
    """Token discovery count by source in the last N hours."""
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=hours)

    result = await session.execute(
        select(Token.source, func.count())
        .where(Token.created_at >= cutoff)
        .group_by(Token.source)
    )
    return {source or "unknown": count for source, count in result.all()}


@router.get("/close-reasons")
async def close_reasons(
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
) -> dict[str, int]:
    """Paper position close reason breakdown."""
    result = await session.execute(
        select(Position.close_reason, func.count())
        .where(
            Position.is_paper == 1,
            Position.status == "closed",
            Position.close_reason.isnot(None),
        )
        .group_by(Position.close_reason)
    )
    return {reason: count for reason, count in result.all()}


@router.get("/top-performers")
async def top_performers(
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Top tokens by peak multiplier from outcome tracking."""
    from src.models.token import TokenOutcome

    result = await session.execute(
        select(
            Token.address,
            Token.symbol,
            Token.name,
            TokenOutcome.multiplier,
            TokenOutcome.peak_mcap,
            TokenOutcome.is_rug,
        )
        .join(TokenOutcome, Token.id == TokenOutcome.token_id)
        .where(TokenOutcome.multiplier.isnot(None), TokenOutcome.multiplier > 1)
        .order_by(desc(TokenOutcome.multiplier))
        .limit(limit)
    )
    rows = result.all()

    items = []
    for address, symbol, name, multiplier, peak_mcap, is_rug in rows:
        items.append({
            "address": address,
            "symbol": symbol,
            "name": name,
            "peak_multiplier": float(multiplier) if multiplier else None,
            "peak_mcap": float(peak_mcap) if peak_mcap else None,
            "is_rug": is_rug,
        })

    return {"items": items}
