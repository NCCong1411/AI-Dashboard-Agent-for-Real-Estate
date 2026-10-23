"""Session-backed authentication endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.auth.dependencies import CurrentUser, Database, require_trusted_origin
from app.auth.login_limit import login_limiter
from app.auth.passwords import verify_password
from app.auth.sessions import hash_session_token, new_session_token, session_expiry
from app.config import Settings, get_settings
from app.db.models import Session, User


router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    username: str
    role: str
    scope_profile: str


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="lax",
        path="/",
    )


@router.post(
    "/login",
    response_model=UserResponse,
    dependencies=[Depends(require_trusted_origin)],
)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    database: Database,
    settings: Settings = Depends(get_settings),
) -> User:
    limited = settings.app_env == "production"
    if limited and not login_limiter.begin(payload.username):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts; retry later",
            headers={"Retry-After": "300"},
        )
    successful = False
    try:
        user = database.scalar(
            select(User).where(User.username == payload.username).with_for_update()
        )
        valid = verify_password(user.password_hash if user else None, payload.password)
        if not valid or user is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )

        old_token = request.cookies.get(settings.session_cookie_name)
        if old_token:
            old_session = database.scalar(
                select(Session).where(Session.token_hash == hash_session_token(old_token))
            )
            if old_session is not None and old_session.revoked_at is None:
                old_session.revoked_at = datetime.now(UTC)

        token = new_session_token()
        database.add(
            Session(
                user_id=user.id,
                token_hash=hash_session_token(token),
                expires_at=session_expiry(settings.session_ttl_seconds),
            )
        )
        database.commit()
        successful = True
        _set_session_cookie(response, token, settings)
        return user
    finally:
        if limited:
            login_limiter.end(payload.username, successful=successful)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_trusted_origin)],
)
def logout(
    request: Request,
    response: Response,
    database: Database,
    settings: Settings = Depends(get_settings),
) -> None:
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        session = database.scalar(
            select(Session).where(Session.token_hash == hash_session_token(token))
        )
        if session is not None and session.revoked_at is None:
            session.revoked_at = datetime.now(UTC)
            database.commit()
    response.delete_cookie(
        settings.session_cookie_name,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="lax",
        path="/",
    )


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUser) -> User:
    return user
