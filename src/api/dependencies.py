"""FastAPI dependency injection — auth, DB session, registry."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import (
    COOKIE_NAME,
    create_access_token,
    decode_token,
    set_auth_cookie,
    should_refresh_token,
)
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

    Sliding window: if token is older than REFRESH_THRESHOLD_MINUTES,
    automatically issues a fresh token in the response cookie.

    Returns the decoded JWT payload.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    payload = decode_token(token)

    # Sliding window refresh — silently extend session
    if should_refresh_token(payload):
        username = payload.get("sub", "")
        new_token, _new_payload = create_access_token(username)
        # Stash new token on request state; middleware will set cookie
        request.state.refresh_token = new_token

    return payload
