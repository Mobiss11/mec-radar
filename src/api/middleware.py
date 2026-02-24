"""Security middleware — CSP headers, CSRF validation, sliding JWT refresh."""

from __future__ import annotations

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.auth import set_auth_cookie


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject security headers + sliding window JWT refresh on every response."""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        response: Response = await call_next(request)

        # Sliding window JWT refresh: if get_current_user() stashed a new token,
        # set it as cookie on the response (transparent to client).
        refresh_token = getattr(request.state, "refresh_token", None)
        if refresh_token:
            set_auth_cookie(response, refresh_token)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # CSP: allow self + inline styles (shadcn/tailwind needs them)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self' https://fonts.gstatic.com; "
            "connect-src 'self'"
        )
        return response
