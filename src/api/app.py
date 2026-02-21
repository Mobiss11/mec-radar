"""FastAPI application factory for the monitoring dashboard."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.cors import CORSMiddleware

from src.api.middleware import SecurityHeadersMiddleware

# Rate limiter (shared instance)
limiter = Limiter(key_func=get_remote_address)

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title="Memecoin Dashboard API",
        version="0.1.0",
        docs_url="/api/docs" if os.getenv("DASHBOARD_DEBUG") else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if os.getenv("DASHBOARD_DEBUG") else None,
    )

    # Rate limiting
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Security headers
    app.add_middleware(SecurityHeadersMiddleware)

    # CORS — only needed for dev (Vite on :5173 → API on :8080)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    # Import and include routers
    from src.api.routers.analytics import router as analytics_router
    from src.api.routers.auth_router import router as auth_router
    from src.api.routers.health import router as health_router
    from src.api.routers.metrics import router as metrics_router
    from src.api.routers.portfolio import router as portfolio_router
    from src.api.routers.settings_router import router as settings_router
    from src.api.routers.signals import router as signals_router
    from src.api.routers.tokens import router as tokens_router

    app.include_router(auth_router)
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(tokens_router)
    app.include_router(signals_router)
    app.include_router(portfolio_router)
    app.include_router(settings_router)
    app.include_router(analytics_router)

    # Serve frontend (production build)
    if FRONTEND_DIST.exists():
        # Static assets (JS, CSS, images)
        app.mount(
            "/assets",
            StaticFiles(directory=str(FRONTEND_DIST / "assets")),
            name="static-assets",
        )

        # SPA catch-all: any non-API path → index.html
        index_html = FRONTEND_DIST / "index.html"

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str) -> FileResponse:
            # Serve actual files from dist if they exist (favicon, etc.)
            file = FRONTEND_DIST / full_path
            if full_path and file.exists() and file.is_file():
                return FileResponse(file)
            return FileResponse(index_html)

    return app
