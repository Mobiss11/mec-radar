"""Health check — no auth required."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from src.api.metrics_registry import registry
from src.db.database import engine

router = APIRouter(prefix="/api/v1", tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_sec: int
    db_ok: bool
    redis_ok: bool


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check DB and Redis connectivity."""
    # DB check
    db_ok = False
    try:
        from sqlalchemy import text

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    # Redis check
    redis_ok = False
    try:
        if registry.redis:
            await registry.redis.ping()
            redis_ok = True
    except Exception:
        pass

    # Uptime
    uptime = 0
    if registry.pipeline_metrics:
        summary = registry.pipeline_metrics.get_summary()
        uptime = summary.get("uptime_sec", 0)

    return HealthResponse(
        status="ok" if db_ok and redis_ok else "degraded",
        version="0.1.0",
        uptime_sec=uptime,
        db_ok=db_ok,
        redis_ok=redis_ok,
    )
