"""Copy Trading endpoints — wallet management, stats, and trade history.

Phase 55: Copy trading dashboard — manage tracked wallets and monitor
copy-trade performance. Backend stores wallet list in settings/DB,
gRPC monitors wallet transactions, CopyTrader executes via Jupiter.
"""

from __future__ import annotations

import html
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import verify_csrf_token
from src.api.dependencies import get_current_user, get_session
from src.models.trade import Position, Trade

router = APIRouter(prefix="/api/v1/copy-trading", tags=["copy-trading"])

# Solana address regex (base58, 32-44 chars)
_SOL_ADDR_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AddWalletRequest(BaseModel):
    """Add a wallet to the tracked list."""
    address: str = Field(min_length=32, max_length=44, pattern=r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
    label: str = Field(default="", max_length=64)
    multiplier: float = Field(default=1.0, ge=0.01, le=100.0)
    max_sol_per_trade: float = Field(default=0.05, ge=0.001, le=10.0)
    enabled: bool = True


class UpdateWalletRequest(BaseModel):
    """Update wallet settings."""
    label: str | None = Field(default=None, max_length=64)
    multiplier: float | None = Field(default=None, ge=0.01, le=100.0)
    max_sol_per_trade: float | None = Field(default=None, ge=0.001, le=10.0)
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# In-memory wallet store (persisted via settings API later)
# In production, this would be a DB table. For MVP, we use a module-level dict
# that the worker process can also access via import.
# ---------------------------------------------------------------------------

_tracked_wallets: dict[str, dict[str, Any]] = {}
# Format: { "7xK...abc": { "label": "Whale1", "multiplier": 1.0,
#            "max_sol_per_trade": 0.05, "enabled": True, "added_at": "..." } }


def get_tracked_wallets() -> dict[str, dict[str, Any]]:
    """Accessor for worker process to read tracked wallets."""
    return _tracked_wallets


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/summary")
async def copy_trading_summary(
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Copy trading overview — tracked wallets, open positions, PnL stats."""

    # Count positions opened by copy trading (source = 'copy_trade')
    open_q = select(
        func.count().label("open_count"),
        func.coalesce(func.sum(Position.amount_sol_invested), 0).label("total_invested"),
    ).where(
        Position.status == "open",
        Position.source == "copy_trade",
    )
    open_result = await session.execute(open_q)
    open_row = open_result.one()

    # Closed stats
    closed_q = select(
        func.count().label("closed_count"),
        func.coalesce(func.sum(Position.pnl_usd), 0).label("total_pnl_usd"),
        func.count(case((Position.pnl_pct > 0, 1))).label("wins"),
        func.count(case((Position.pnl_pct <= 0, 1))).label("losses"),
    ).where(
        Position.status == "closed",
        Position.source == "copy_trade",
    )
    closed_result = await session.execute(closed_q)
    closed_row = closed_result.one()

    wins = closed_row.wins or 0
    losses = closed_row.losses or 0
    total_closed = wins + losses
    win_rate = round((wins / total_closed * 100) if total_closed > 0 else 0, 1)

    # Wallet stats
    active_wallets = sum(1 for w in _tracked_wallets.values() if w.get("enabled"))
    total_wallets = len(_tracked_wallets)

    return {
        "active_wallets": active_wallets,
        "total_wallets": total_wallets,
        "open_positions": open_row.open_count or 0,
        "total_invested_sol": float(open_row.total_invested or 0),
        "closed_count": closed_row.closed_count or 0,
        "total_pnl_usd": float(closed_row.total_pnl_usd or 0),
        "win_rate": win_rate,
        "wins": wins,
        "losses": losses,
        "copy_trading_enabled": bool(_tracked_wallets),
    }


@router.get("/wallets")
async def list_wallets(
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """List all tracked wallets with their configuration."""
    items = []
    for addr, config in _tracked_wallets.items():
        items.append({
            "address": addr,
            "label": config.get("label", ""),
            "multiplier": config.get("multiplier", 1.0),
            "max_sol_per_trade": config.get("max_sol_per_trade", 0.05),
            "enabled": config.get("enabled", True),
            "added_at": config.get("added_at", ""),
        })
    return {"items": items, "total": len(items)}


@router.post("/wallets")
async def add_wallet(
    body: AddWalletRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Add a wallet to the tracked list. Requires CSRF token."""
    csrf_token = request.headers.get("X-CSRF-Token", "")
    if not verify_csrf_token(csrf_token, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token",
        )

    addr = body.address.strip()
    if not _SOL_ADDR_RE.match(addr):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid Solana address format",
        )

    if addr in _tracked_wallets:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Wallet already tracked",
        )

    from datetime import datetime, timezone

    _tracked_wallets[addr] = {
        "label": html.escape(body.label.strip()) if body.label else "",
        "multiplier": body.multiplier,
        "max_sol_per_trade": body.max_sol_per_trade,
        "enabled": body.enabled,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }

    return {"ok": True, "address": addr, "total_wallets": len(_tracked_wallets)}


@router.patch("/wallets/{address}")
async def update_wallet(
    address: str,
    body: UpdateWalletRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Update wallet settings (label, multiplier, enabled). Requires CSRF."""
    csrf_token = request.headers.get("X-CSRF-Token", "")
    if not verify_csrf_token(csrf_token, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token",
        )

    if address not in _tracked_wallets:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not tracked",
        )

    wallet = _tracked_wallets[address]
    if body.label is not None:
        wallet["label"] = html.escape(body.label.strip())
    if body.multiplier is not None:
        wallet["multiplier"] = body.multiplier
    if body.max_sol_per_trade is not None:
        wallet["max_sol_per_trade"] = body.max_sol_per_trade
    if body.enabled is not None:
        wallet["enabled"] = body.enabled

    return {"ok": True, "address": address, "wallet": wallet}


@router.delete("/wallets/{address}")
async def remove_wallet(
    address: str,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Remove a wallet from tracking. Requires CSRF."""
    csrf_token = request.headers.get("X-CSRF-Token", "")
    if not verify_csrf_token(csrf_token, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token",
        )

    if address not in _tracked_wallets:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not tracked",
        )

    del _tracked_wallets[address]
    return {"ok": True, "address": address, "total_wallets": len(_tracked_wallets)}


@router.get("/positions")
async def list_copy_positions(
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
    status_filter: str = Query("all", pattern="^(all|open|closed)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """List positions opened by copy trading."""

    query = select(Position).where(Position.source == "copy_trade")

    if status_filter != "all":
        query = query.where(Position.status == status_filter)

    # Count total
    count_q = select(func.count()).select_from(query.subquery())
    total = (await session.execute(count_q)).scalar() or 0

    # Paginate
    offset = (page - 1) * limit
    query = query.order_by(desc(Position.id)).offset(offset).limit(limit)
    result = await session.execute(query)
    positions = result.scalars().all()

    items = []
    for p in positions:
        items.append({
            "id": p.id,
            "token_address": p.token_address,
            "symbol": p.symbol,
            "source": p.source,
            "entry_price": float(p.entry_price) if p.entry_price else None,
            "current_price": float(p.current_price) if p.current_price else None,
            "amount_sol_invested": float(p.amount_sol_invested) if p.amount_sol_invested else None,
            "pnl_pct": float(p.pnl_pct) if p.pnl_pct else None,
            "pnl_usd": float(p.pnl_usd) if p.pnl_usd else None,
            "status": p.status,
            "close_reason": p.close_reason,
            "is_paper": bool(p.is_paper),
            "tx_hash": p.tx_hash if hasattr(p, "tx_hash") else None,
            "opened_at": p.opened_at.isoformat() if p.opened_at else None,
            "closed_at": p.closed_at.isoformat() if p.closed_at else None,
        })

    total_pages = (total + limit - 1) // limit if total > 0 else 1

    return {
        "items": items,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "has_more": page < total_pages,
    }
