"""Metrics endpoints — pipeline stats, connections, circuit breakers."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from src.api.dependencies import get_current_user
from src.api.metrics_registry import registry
from src.parsers.sol_price import get_sol_price

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])


@router.get("/overview")
async def metrics_overview(_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """High-level system overview."""
    summary: dict[str, Any] = {}
    if registry.pipeline_metrics:
        summary = registry.pipeline_metrics.get_summary()

    queue_size = 0
    if registry.enrichment_queue:
        queue_size = await registry.enrichment_queue.qsize()

    alerts_sent = 0
    if registry.alert_dispatcher:
        alerts_sent = getattr(registry.alert_dispatcher, "_total_sent", 0)

    return {
        "uptime_sec": summary.get("uptime_sec", 0),
        "total_enrichments": summary.get("total_enrichments", 0),
        "enrichments_per_min": summary.get("enrichments_per_min", 0.0),
        "total_pruned": summary.get("total_pruned", 0),
        "queue_size": queue_size,
        "sol_price_usd": get_sol_price(),
        "alerts_sent": alerts_sent,
    }


@router.get("/connections")
async def metrics_connections(_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Connection status for all data sources."""

    def _ws_status(client: Any) -> dict[str, Any]:
        if client is None:
            return {"state": "disabled", "message_count": 0}
        return {
            "state": getattr(client, "state", "unknown"),
            "message_count": getattr(client, "message_count", 0),
        }

    grpc_info = _ws_status(registry.grpc_client)
    if registry.grpc_client:
        grpc_info["token_count"] = getattr(registry.grpc_client, "_token_count", 0)

    # Redis check
    redis_ok = False
    try:
        if registry.redis:
            await registry.redis.ping()
            redis_ok = True
    except Exception:
        pass

    # DB check
    db_ok = False
    try:
        from sqlalchemy import text
        from src.db.database import engine

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    return {
        "grpc": grpc_info,
        "pumpportal": _ws_status(registry.pumpportal),
        "meteora_dbc": _ws_status(registry.meteora_ws),
        "redis": {"connected": redis_ok},
        "postgres": {"connected": db_ok},
    }


@router.get("/pipeline")
async def metrics_pipeline(_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Per-stage enrichment metrics."""
    if not registry.pipeline_metrics:
        return {"stages": {}}
    summary = registry.pipeline_metrics.get_summary()
    return {"stages": summary.get("stages", {})}


@router.get("/gmgn")
async def metrics_gmgn(_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """GMGN circuit breaker status."""
    if not registry.gmgn:
        return {"enabled": False}

    import time

    client = registry.gmgn
    open_until = getattr(client, "_circuit_open_until", 0.0)
    now = time.monotonic()
    return {
        "enabled": True,
        "circuit_open": now < open_until,
        "remaining_cooldown_sec": max(0, round(open_until - now, 1)),
        "trip_count": getattr(client, "_circuit_trip_count", 0),
    }


@router.get("/solsniffer")
async def metrics_solsniffer(_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """SolSniffer monthly usage."""
    from datetime import datetime

    from config.settings import settings

    month_key = datetime.now().strftime("%Y-%m")
    monthly_calls = 0

    if registry.redis:
        try:
            val = await registry.redis.get(f"solsniffer:monthly_calls:{month_key}")
            monthly_calls = int(val) if val else 0
        except Exception:
            pass

    cap = settings.solsniffer_monthly_cap
    return {
        "monthly_calls": monthly_calls,
        "monthly_cap": cap,
        "remaining": max(0, cap - monthly_calls),
        "month": month_key,
    }
