"""Database-backed identity, RBAC, ownership, and Origin dependencies."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from app.config import Settings, get_settings
from app.db.connections import create_runtime_engine
from app.db.models import Conversation, Dashboard, Session, User

from .sessions import hash_session_token


@lru_cache
def runtime_engine() -> Engine:
    return create_runtime_engine(get_settings())


def database_session() -> Iterator[OrmSession]:
    with OrmSession(runtime_engine()) as database:
        yield database


Database = Annotated[OrmSession, Depends(database_session)]


def require_trusted_origin(request: Request, settings: Settings = Depends(get_settings)) -> None:
    origins = request.headers.getlist("origin")
    if len(origins) != 1 or origins[0] != settings.frontend_origin.rstrip("/"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Untrusted origin")


def current_user(
    request: Request,
    database: Database,
    settings: Settings = Depends(get_settings),
) -> User:
    token = request.cookies.get(settings.session_cookie_name)
    if token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    now = datetime.now(UTC)
    record = database.execute(
        select(Session, User)
        .join(User, User.id == Session.user_id)
        .where(
            Session.token_hash == hash_session_token(token),
            Session.revoked_at.is_(None),
            Session.expires_at > now,
            User.is_active.is_(True),
        )
    ).one_or_none()
    if record is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return record[1]
CurrentUser = Annotated[User, Depends(current_user)]


def require_builder(user: CurrentUser) -> User:
    if user.role != "builder":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={
            "code": "authorization_denied_viewer",
            "message": "Viewer không được tạo hoặc chỉnh sửa dashboard bằng Agent.",
        })
    return user


Builder = Annotated[User, Depends(require_builder)]


def require_owned_dashboard(dashboard_id: uuid.UUID, user: Builder, database: Database) -> Dashboard:
    dashboard = database.scalar(
        select(Dashboard).where(Dashboard.id == dashboard_id, Dashboard.owner_user_id == user.id)
    )
    if dashboard is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard not found")
    return dashboard


def require_owned_conversation(
    conversation_id: uuid.UUID, user: Builder, database: Database
) -> Conversation:
    conversation = database.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.owner_user_id == user.id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation
