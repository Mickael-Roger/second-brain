"""Login/logout/me endpoints."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.auth import (
    SESSION_COOKIE_NAME,
    create_session,
    delete_session,
    optional_current_user,
    verify_password,
)
from app.config import get_settings
from app.db.connection import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class MeResponse(BaseModel):
    username: str


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    conn: sqlite3.Connection = Depends(get_db),
) -> MeResponse:
    settings = get_settings()
    if payload.username != settings.auth.username or not verify_password(
        payload.password, settings.auth.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    signed, expires = create_session(
        conn,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    secure = request.url.scheme == "https"
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=signed,
        httponly=True,
        secure=secure,
        samesite="lax",
        expires=expires,
        path="/",
    )
    return MeResponse(username=settings.auth.username)


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, bool]:
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie:
        delete_session(conn, cookie)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: str | None = Depends(optional_current_user)) -> MeResponse:
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return MeResponse(username=user)
