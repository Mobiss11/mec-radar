"""FastAPI dependency injection — auth, DB session, registry."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Cookie, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import COOKIE_NAME, decode_token
from src.api.metrics_registry import MetricsRegistry, registry
from src.db.database import async_session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async DB session (auto-closes)."""
    async with async_session_factory() as session:
        yield session


def get_registry() -> MetricsRegistry:
    """Return the global metrics registry."""
    return registry


async def get_current_user(request: Request) -> dict:
    """Extract and validate JWT from httpOnly cookie.

    Returns the decoded JWT payload.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return decode_token(token)
