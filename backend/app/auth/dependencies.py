"""FastAPI dependencies for authentication."""

from __future__ import annotations

import sqlite3

from fastapi import Cookie, Depends, HTTPException, status

from app.db.connection import get_db

from .sessions import SESSION_COOKIE_NAME, get_session_user


def optional_current_user(
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    conn: sqlite3.Connection = Depends(get_db),
) -> str | None:
    if not session_cookie:
        return None
    return get_session_user(conn, session_cookie)


def current_user(user: str | None = Depends(optional_current_user)) -> str:
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user
