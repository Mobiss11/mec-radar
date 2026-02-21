"""Dashboard server — runs uvicorn inside the existing asyncio event loop."""

from __future__ import annotations

import uvicorn
from loguru import logger

from config.settings import settings


async def run_dashboard_server() -> None:
    """Start uvicorn serving the FastAPI dashboard.

    Designed to run as an asyncio task alongside the parser worker.
    Uses ``uvicorn.Server.serve()`` which is fully async.
    """
    from src.api.app import create_app

    app = create_app()
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=settings.dashboard_port,
        log_level="warning",
        loop="none",  # use the existing event loop
    )
    server = uvicorn.Server(config)
    logger.info(f"Dashboard API starting on http://0.0.0.0:{settings.dashboard_port}")
    await server.serve()
