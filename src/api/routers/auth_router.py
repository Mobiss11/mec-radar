"""Auth endpoints — login, logout, CSRF, me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.api.auth import (
    authenticate_admin,
    clear_auth_cookie,
    create_access_token,
    generate_csrf_token,
    set_auth_cookie,
)
from src.api.dependencies import get_current_user

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Login-specific rate limiter
limiter = Limiter(key_func=get_remote_address)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class LoginResponse(BaseModel):
    ok: bool = True
    username: str = ""
    csrf_token: str = ""


class MeResponse(BaseModel):
    username: str


class CsrfResponse(BaseModel):
    csrf_token: str


@router.post("/login", response_model=LoginResponse)
async def login(request: Request, body: LoginRequest, response: Response) -> LoginResponse:
    """Authenticate admin and set JWT cookie."""
    if not authenticate_admin(body.username, body.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    token, payload = create_access_token(body.username)
    set_auth_cookie(response, token)
    csrf = generate_csrf_token(payload)
    return LoginResponse(username=body.username, csrf_token=csrf)


@router.post("/logout", response_model=LoginResponse)
async def logout(
    response: Response,
    _user: dict = Depends(get_current_user),
) -> LoginResponse:
    """Clear auth cookie."""
    clear_auth_cookie(response)
    return LoginResponse()


@router.get("/me", response_model=MeResponse)
async def me(user: dict = Depends(get_current_user)) -> MeResponse:
    """Return current user info."""
    return MeResponse(username=user["sub"])


@router.get("/csrf", response_model=CsrfResponse)
async def get_csrf(user: dict = Depends(get_current_user)) -> CsrfResponse:
    """Generate a CSRF token tied to the current JWT."""
    return CsrfResponse(csrf_token=generate_csrf_token(user))
