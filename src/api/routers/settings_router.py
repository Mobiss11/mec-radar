"""Settings endpoints — view and update runtime configuration."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from config.settings import settings
from src.api.auth import verify_csrf_token
from src.api.dependencies import get_current_user

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

# Whitelist of fields that can be mutated at runtime
MUTABLE_FIELDS: set[str] = {
    "paper_trading_enabled",
    "paper_sol_per_trade",
    "paper_max_positions",
    "paper_take_profit_x",
    "paper_stop_loss_pct",
    "paper_timeout_hours",
    "signal_decay_enabled",
    "signal_strong_buy_ttl_hours",
    "signal_buy_ttl_hours",
    "signal_watch_ttl_hours",
    "enable_birdeye",
    "enable_gmgn",
    "enable_pumpportal",
    "enable_dexscreener",
    "enable_meteora_dbc",
    "enable_grpc_streaming",
    "enable_solsniffer",
    "enable_twitter",
    "enable_telegram_checker",
    "enable_llm_analysis",
}


class SettingsUpdate(BaseModel):
    """Partial settings update — only whitelisted fields."""

    paper_trading_enabled: bool | None = None
    paper_sol_per_trade: float | None = Field(None, ge=0.01, le=100.0)
    paper_max_positions: int | None = Field(None, ge=1, le=50)
    paper_take_profit_x: float | None = Field(None, ge=1.5, le=50.0)
    paper_stop_loss_pct: float | None = Field(None, le=-5.0, ge=-90.0)
    paper_timeout_hours: int | None = Field(None, ge=1, le=72)
    signal_decay_enabled: bool | None = None
    signal_strong_buy_ttl_hours: int | None = Field(None, ge=1, le=48)
    signal_buy_ttl_hours: int | None = Field(None, ge=1, le=72)
    signal_watch_ttl_hours: int | None = Field(None, ge=1, le=168)
    enable_birdeye: bool | None = None
    enable_gmgn: bool | None = None
    enable_pumpportal: bool | None = None
    enable_dexscreener: bool | None = None
    enable_meteora_dbc: bool | None = None
    enable_grpc_streaming: bool | None = None
    enable_solsniffer: bool | None = None
    enable_twitter: bool | None = None
    enable_telegram_checker: bool | None = None
    enable_llm_analysis: bool | None = None


@router.get("")
async def get_settings(_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Return current runtime settings (mutable fields only)."""
    result: dict[str, Any] = {}
    for field_name in MUTABLE_FIELDS:
        result[field_name] = getattr(settings, field_name, None)
    return result


@router.patch("")
async def update_settings(
    request: Request,
    body: SettingsUpdate,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Update runtime settings (requires CSRF token)."""
    # CSRF validation
    csrf_token = request.headers.get("X-CSRF-Token", "")
    if not verify_csrf_token(csrf_token, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token",
        )

    updated: list[str] = []
    updates = body.model_dump(exclude_none=True)

    for key, value in updates.items():
        if key not in MUTABLE_FIELDS:
            continue
        if hasattr(settings, key):
            setattr(settings, key, value)
            updated.append(key)

    return {"updated": updated}


@router.get("/api-status")
async def api_status(_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Show which API services are configured (never expose actual keys)."""
    services = [
        {"name": "Birdeye", "configured": bool(settings.birdeye_api_key), "enabled": settings.enable_birdeye},
        {"name": "Chainstack gRPC", "configured": bool(settings.chainstack_grpc_endpoint), "enabled": settings.enable_grpc_streaming},
        {"name": "Helius", "configured": bool(settings.helius_api_key), "enabled": settings.enable_helius_analysis},
        {"name": "Jupiter", "configured": bool(settings.jupiter_api_key), "enabled": settings.enable_jupiter},
        {"name": "Vybe Network", "configured": bool(settings.vybe_api_key), "enabled": settings.enable_vybe},
        {"name": "TwitterAPI.io", "configured": bool(settings.twitter_api_key), "enabled": settings.enable_twitter},
        {"name": "RapidAPI TG", "configured": bool(settings.rapidapi_key), "enabled": settings.enable_telegram_checker},
        {"name": "OpenRouter LLM", "configured": bool(settings.openrouter_api_key), "enabled": settings.enable_llm_analysis},
        {"name": "SolSniffer", "configured": bool(settings.solsniffer_api_key), "enabled": settings.enable_solsniffer},
        {"name": "Bubblemaps", "configured": bool(settings.bubblemaps_api_key), "enabled": settings.enable_bubblemaps},
        {"name": "Telegram Bot", "configured": bool(settings.telegram_bot_token), "enabled": bool(settings.telegram_bot_token)},
    ]
    return {"services": services}
