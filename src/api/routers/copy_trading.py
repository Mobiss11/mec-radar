"""Copy Trading endpoints — wallet management, stats, settings, and trade history.

Phase 55: Copy trading dashboard — manage tracked wallets and monitor
copy-trade performance. Backend stores wallet list in settings/DB,
gRPC monitors wallet transactions, CopyTrader executes via Jupiter.

Phase 56: Paper/Real mode toggles, GMGN leaderboard wallet presets,
per-wallet gmgn_rank/winrate/pnl metadata.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
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
    # GMGN metadata (optional, populated from leaderboard)
    gmgn_rank: int | None = Field(default=None, ge=1, le=1000)
    winrate_7d: float | None = Field(default=None, ge=0, le=100)
    pnl_7d_usd: float | None = Field(default=None)
    twitter: str | None = Field(default=None, max_length=64)


class UpdateWalletRequest(BaseModel):
    """Update wallet settings."""
    label: str | None = Field(default=None, max_length=64)
    multiplier: float | None = Field(default=None, ge=0.01, le=100.0)
    max_sol_per_trade: float | None = Field(default=None, ge=0.001, le=10.0)
    enabled: bool | None = None


class UpdateSettingsRequest(BaseModel):
    """Update copy trading mode settings."""
    paper_mode: bool | None = None
    real_mode: bool | None = None


# ---------------------------------------------------------------------------
# In-memory wallet store + settings
# ---------------------------------------------------------------------------

_tracked_wallets: dict[str, dict[str, Any]] = {}
# Format: { "7xK...abc": { "label": "Whale1", "multiplier": 1.0,
#            "max_sol_per_trade": 0.05, "enabled": True, "added_at": "...",
#            "gmgn_rank": 4, "winrate_7d": 93.7, "pnl_7d_usd": 57902, "twitter": "" } }

_copy_settings: dict[str, Any] = {
    "paper_mode": True,   # Paper trading ON by default
    "real_mode": False,    # Real trading OFF by default (safety)
}

# ---------------------------------------------------------------------------
# GMGN Leaderboard Presets (7D top traders, 2026-02-26)
# Criteria: WR >= 70%, PnL >= $20K/7d, tracked < 1000, loss50+ <= 5
# ---------------------------------------------------------------------------
_GMGN_PRESETS: list[dict[str, Any]] = [
    {"address": "A3WySdFfsNLNyRQABzfV5wAo1Y9fo2Kgrmuug7fTfBxL", "label": "GMGN#4 WR93.7%", "gmgn_rank": 4, "winrate_7d": 93.7, "pnl_7d_usd": 57902, "twitter": ""},
    {"address": "Dzp1SrZ474xwGp6ZEP6cNKo39u9zeXe1YAuTkyZyv3t4", "label": "GMGN#33 WR98.5%", "gmgn_rank": 33, "winrate_7d": 98.5, "pnl_7d_usd": 29655, "twitter": ""},
    {"address": "FSYojWVXvrXNkFfvCAptVhpnWHuJoNzQNu7QSgsecCEz", "label": "GMGN#38 WR98.6%", "gmgn_rank": 38, "winrate_7d": 98.6, "pnl_7d_usd": 27834, "twitter": ""},
    {"address": "HiSo5kykqDPs3EG14Fk9QY4B5RvkuEs8oJTiqPX3EDAn", "label": "GMGN#46 WR90.3%", "gmgn_rank": 46, "winrate_7d": 90.3, "pnl_7d_usd": 24862, "twitter": ""},
    {"address": "9g7QpJvPvMULB3n6tQMjbzbNoDDhMKpVoJJDvoECXViG", "label": "GMGN#32 WR84.5%", "gmgn_rank": 32, "winrate_7d": 84.5, "pnl_7d_usd": 30690, "twitter": ""},
    {"address": "FqmXjEGnLx38pd9ZxDJcEuNNZNngrvXYNPsFw8XvgTeb", "label": "GMGN#12 WR81%", "gmgn_rank": 12, "winrate_7d": 81.0, "pnl_7d_usd": 46580, "twitter": ""},
    {"address": "843VNwYH83tgpBfrZxkxQQWfQ8CRdsLBmzhcT4JGjHBs", "label": "Alan Sousa", "gmgn_rank": 8, "winrate_7d": 75.5, "pnl_7d_usd": 50529, "twitter": "allaanll"},
    {"address": "8oEdL8WBRpE3C63FeqZ7hwSH8fjh715ZvkgmMLhDneGm", "label": "GMGN#6 WR75.6%", "gmgn_rank": 6, "winrate_7d": 75.6, "pnl_7d_usd": 52488, "twitter": ""},
    {"address": "7kGAXsa7n1qN2FuNoJAGmzebmN9KqqLAHcwj7gvoekKk", "label": "GMGN#54 WR81.5%", "gmgn_rank": 54, "winrate_7d": 81.5, "pnl_7d_usd": 19863, "twitter": ""},
    {"address": "2oUG1MwkQc6yoURyvDxzCyDn2aGvoJLGjzZSKsK3ULbQ", "label": "GMGN#34 WR78.3%", "gmgn_rank": 34, "winrate_7d": 78.3, "pnl_7d_usd": 28778, "twitter": ""},
    {"address": "GUgMNi2tJcjYpDSFs2VZQpjryeWRG4Xgmi1JPDEoe1TH", "label": "GMGN#39 WR78.1%", "gmgn_rank": 39, "winrate_7d": 78.1, "pnl_7d_usd": 26973, "twitter": ""},
    {"address": "8ghYW6ftL5kUemfsoA9X37rz3ZnvyMSZRAx1kt1CxpoS", "label": "GMGN#22 WR71.9%", "gmgn_rank": 22, "winrate_7d": 71.9, "pnl_7d_usd": 36538, "twitter": ""},
    {"address": "5ATd36Tdq6zrtHBokC5moRs9Y5EFSNaZQ12fzRmTeNWd", "label": "GMGN#41 WR73.8%", "gmgn_rank": 41, "winrate_7d": 73.8, "pnl_7d_usd": 25704, "twitter": ""},
    {"address": "FFEjC9MHhpQViBPrD2iU6LmV2hEigyhLJaL7MZUZzyD4", "label": "GMGN#57 WR70.2%", "gmgn_rank": 57, "winrate_7d": 70.2, "pnl_7d_usd": 20143, "twitter": ""},
]


def _load_presets() -> None:
    """Load GMGN leaderboard presets into tracked wallets on startup."""
    for preset in _GMGN_PRESETS:
        addr = preset["address"]
        if addr not in _tracked_wallets:
            _tracked_wallets[addr] = {
                "label": preset["label"],
                "multiplier": 1.0,
                "max_sol_per_trade": 0.05,
                "enabled": True,
                "added_at": datetime.now(timezone.utc).isoformat(),
                "gmgn_rank": preset.get("gmgn_rank"),
                "winrate_7d": preset.get("winrate_7d"),
                "pnl_7d_usd": preset.get("pnl_7d_usd"),
                "twitter": preset.get("twitter", ""),
            }


# Auto-load presets on module import
_load_presets()


def get_tracked_wallets() -> dict[str, dict[str, Any]]:
    """Accessor for worker process to read tracked wallets."""
    return _tracked_wallets


def get_copy_settings() -> dict[str, Any]:
    """Accessor for worker process to read copy trading settings."""
    return _copy_settings


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
        "paper_mode": _copy_settings["paper_mode"],
        "real_mode": _copy_settings["real_mode"],
    }


@router.get("/settings")
async def get_settings(
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Get copy trading mode settings."""
    return {
        "paper_mode": _copy_settings["paper_mode"],
        "real_mode": _copy_settings["real_mode"],
    }


@router.patch("/settings")
async def update_settings(
    body: UpdateSettingsRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Update copy trading mode settings (paper/real toggles). Requires CSRF."""
    csrf_token = request.headers.get("X-CSRF-Token", "")
    if not verify_csrf_token(csrf_token, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token",
        )

    if body.paper_mode is not None:
        _copy_settings["paper_mode"] = body.paper_mode
    if body.real_mode is not None:
        _copy_settings["real_mode"] = body.real_mode

    return {
        "ok": True,
        "paper_mode": _copy_settings["paper_mode"],
        "real_mode": _copy_settings["real_mode"],
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
            "gmgn_rank": config.get("gmgn_rank"),
            "winrate_7d": config.get("winrate_7d"),
            "pnl_7d_usd": config.get("pnl_7d_usd"),
            "twitter": config.get("twitter", ""),
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

    _tracked_wallets[addr] = {
        "label": html.escape(body.label.strip()) if body.label else "",
        "multiplier": body.multiplier,
        "max_sol_per_trade": body.max_sol_per_trade,
        "enabled": body.enabled,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "gmgn_rank": body.gmgn_rank,
        "winrate_7d": body.winrate_7d,
        "pnl_7d_usd": body.pnl_7d_usd,
        "twitter": body.twitter or "",
    }

    return {"ok": True, "address": addr, "total_wallets": len(_tracked_wallets)}


@router.post("/wallets/bulk")
async def add_wallets_bulk(
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Bulk-add wallets from GMGN leaderboard data. Requires CSRF."""
    csrf_token = request.headers.get("X-CSRF-Token", "")
    if not verify_csrf_token(csrf_token, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token",
        )

    body = await request.json()
    wallets_data: list[dict[str, Any]] = body.get("wallets", [])
    if not wallets_data or len(wallets_data) > 50:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide 1-50 wallets",
        )

    added = 0
    skipped = 0
    for w in wallets_data:
        addr = str(w.get("address", "")).strip()
        if not _SOL_ADDR_RE.match(addr):
            skipped += 1
            continue
        if addr in _tracked_wallets:
            skipped += 1
            continue

        _tracked_wallets[addr] = {
            "label": html.escape(str(w.get("label", ""))[:64]),
            "multiplier": min(max(float(w.get("multiplier", 1.0)), 0.01), 100.0),
            "max_sol_per_trade": min(max(float(w.get("max_sol_per_trade", 0.05)), 0.001), 10.0),
            "enabled": bool(w.get("enabled", True)),
            "added_at": datetime.now(timezone.utc).isoformat(),
            "gmgn_rank": w.get("gmgn_rank"),
            "winrate_7d": w.get("winrate_7d"),
            "pnl_7d_usd": w.get("pnl_7d_usd"),
            "twitter": str(w.get("twitter", ""))[:64],
        }
        added += 1

    return {
        "ok": True,
        "added": added,
        "skipped": skipped,
        "total_wallets": len(_tracked_wallets),
    }


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
        # Resolve wallet label from tracked wallets
        wallet_label = ""
        if p.copied_from_wallet:
            w_cfg = _tracked_wallets.get(p.copied_from_wallet)
            if w_cfg:
                wallet_label = w_cfg.get("label", "")

        items.append({
            "id": p.id,
            "token_address": p.token_address,
            "symbol": p.symbol,
            "source": p.source,
            "copied_from_wallet": p.copied_from_wallet,
            "wallet_label": wallet_label,
            "entry_price": float(p.entry_price) if p.entry_price else None,
            "current_price": float(p.current_price) if p.current_price else None,
            "amount_sol_invested": float(p.amount_sol_invested) if p.amount_sol_invested else None,
            "pnl_pct": float(p.pnl_pct) if p.pnl_pct else None,
            "pnl_usd": float(p.pnl_usd) if p.pnl_usd else None,
            "status": p.status,
            "close_reason": p.close_reason,
            "is_paper": bool(p.is_paper),
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


@router.get("/stats/by-wallet")
async def stats_by_wallet(
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Per-wallet copy trading P&L stats — how each tracked wallet performs for us."""

    # Aggregate closed positions by copied_from_wallet
    q = (
        select(
            Position.copied_from_wallet,
            func.count().label("total_trades"),
            func.count(case((Position.pnl_pct > 0, 1))).label("wins"),
            func.count(case((Position.pnl_pct <= 0, 1))).label("losses"),
            func.coalesce(func.sum(Position.pnl_usd), 0).label("total_pnl_usd"),
            func.coalesce(func.avg(Position.pnl_pct), 0).label("avg_pnl_pct"),
        )
        .where(
            Position.source == "copy_trade",
            Position.status == "closed",
            Position.copied_from_wallet.isnot(None),
        )
        .group_by(Position.copied_from_wallet)
    )
    result = await session.execute(q)
    rows = result.all()

    # Also count open positions per wallet
    open_q = (
        select(
            Position.copied_from_wallet,
            func.count().label("open_count"),
        )
        .where(
            Position.source == "copy_trade",
            Position.status == "open",
            Position.copied_from_wallet.isnot(None),
        )
        .group_by(Position.copied_from_wallet)
    )
    open_result = await session.execute(open_q)
    open_map = {r.copied_from_wallet: r.open_count for r in open_result.all()}

    items = []
    for row in rows:
        wallet_addr = row.copied_from_wallet
        w_cfg = _tracked_wallets.get(wallet_addr, {})
        total = row.total_trades or 0
        wins = row.wins or 0
        wr = round((wins / total * 100) if total > 0 else 0, 1)

        items.append({
            "address": wallet_addr,
            "label": w_cfg.get("label", (wallet_addr or "")[:12]),
            "gmgn_rank": w_cfg.get("gmgn_rank"),
            "winrate_7d": w_cfg.get("winrate_7d"),
            "total_trades": total,
            "wins": wins,
            "losses": row.losses or 0,
            "win_rate": wr,
            "total_pnl_usd": float(row.total_pnl_usd),
            "avg_pnl_pct": round(float(row.avg_pnl_pct), 1),
            "open_positions": open_map.get(wallet_addr, 0),
        })

    # Sort by total P&L descending
    items.sort(key=lambda x: x["total_pnl_usd"], reverse=True)

    return {"items": items, "total": len(items)}
