"""JWT authentication — single admin user with httpOnly cookie."""

from __future__ import annotations

import hmac
import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

import bcrypt
from fastapi import HTTPException, Response, status
from jose import JWTError, jwt

from config.settings import settings

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours
REFRESH_THRESHOLD_MINUTES = 720  # Refresh when >12h elapsed (sliding window)
COOKIE_NAME = "access_token"


def _get_secret() -> str:
    """Return JWT secret, auto-generating one if not configured."""
    if settings.dashboard_jwt_secret:
        return settings.dashboard_jwt_secret
    # Deterministic fallback from admin password (not ideal, but works)
    return sha256(f"jwt-{settings.dashboard_admin_password}".encode()).hexdigest()


def hash_password(password: str) -> str:
    """Hash a password with bcrypt (12 rounds)."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain password against a bcrypt hash."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(username: str) -> tuple[str, dict[str, Any]]:
    """Create a signed JWT.  Returns (encoded_token, payload)."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": username,
        "jti": secrets.token_hex(16),
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, _get_secret(), algorithm=ALGORITHM), payload


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT.  Raises HTTPException on failure."""
    try:
        payload = jwt.decode(token, _get_secret(), algorithms=[ALGORITHM])
        if not payload.get("sub"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return payload
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def should_refresh_token(payload: dict[str, Any]) -> bool:
    """Return True if token was issued more than REFRESH_THRESHOLD_MINUTES ago."""
    iat = payload.get("iat")
    if iat is None:
        return False
    if isinstance(iat, int | float):
        issued = datetime.fromtimestamp(iat, tz=UTC)
    else:
        issued = iat
    elapsed = (datetime.now(UTC) - issued).total_seconds() / 60
    return elapsed > REFRESH_THRESHOLD_MINUTES


def generate_csrf_token(jwt_payload: dict[str, Any]) -> str:
    """Derive a CSRF token from the JWT's jti via HMAC."""
    jti = jwt_payload.get("jti", "")
    return hmac.new(
        _get_secret().encode(), f"csrf-{jti}".encode(), sha256
    ).hexdigest()[:32]


def verify_csrf_token(csrf_token: str, jwt_payload: dict[str, Any]) -> bool:
    """Verify a CSRF token matches the JWT."""
    expected = generate_csrf_token(jwt_payload)
    return hmac.compare_digest(csrf_token, expected)


def set_auth_cookie(response: Response, token: str) -> None:
    """Set JWT as httpOnly Secure SameSite=Lax cookie."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=False,  # Set to True in production behind HTTPS
        samesite="lax",
        path="/",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


def clear_auth_cookie(response: Response) -> None:
    """Remove the auth cookie."""
    response.delete_cookie(key=COOKIE_NAME, path="/")


def authenticate_admin(username: str, password: str) -> bool:
    """Check admin credentials against settings."""
    expected_user = settings.dashboard_admin_user
    expected_pass = settings.dashboard_admin_password
    if not expected_pass:
        return False
    return username == expected_user and password == expected_pass
