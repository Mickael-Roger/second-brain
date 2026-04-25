"""Cookie-backed session storage.

The session id is stored client-side in an HttpOnly cookie. We persist a row
in the `sessions` table holding the SHA-256 of the cookie value (so even DB
exposure doesn't grant login). The cookie value itself is `itsdangerous`-signed
with the configured `session_secret` to prevent forgery without DB access.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import get_settings

SESSION_COOKIE_NAME = "sb_session"
_MAX_AGE_HEADROOM_SEC = 60


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        secret_key=get_settings().auth.session_secret,
        salt="second-brain.session",
    )


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def create_session(
    conn: sqlite3.Connection,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> tuple[str, datetime]:
    """Create a session, persist it, and return (signed_cookie_value, expires_at)."""
    settings = get_settings()
    raw = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw)
    now = _utcnow()
    expires = now + timedelta(days=settings.auth.session_lifetime_days)
    conn.execute(
        "INSERT INTO sessions (id, created_at, expires_at, user_agent, ip) "
        "VALUES (?, ?, ?, ?, ?)",
        (token_hash, now.isoformat(), expires.isoformat(), user_agent, ip),
    )
    signed = _serializer().dumps(raw)
    return signed, expires


def get_session_user(conn: sqlite3.Connection, signed_token: str) -> str | None:
    """Validate a signed cookie value and return the username, or None."""
    settings = get_settings()
    max_age = settings.auth.session_lifetime_days * 86400 + _MAX_AGE_HEADROOM_SEC
    try:
        raw = _serializer().loads(signed_token, max_age=max_age)
    except BadSignature:
        return None

    row = conn.execute(
        "SELECT id, expires_at FROM sessions WHERE id = ?",
        (_hash_token(raw),),
    ).fetchone()
    if row is None:
        return None
    if _parse_dt(row["expires_at"]) < _utcnow():
        conn.execute("DELETE FROM sessions WHERE id = ?", (row["id"],))
        return None
    return settings.auth.username


def delete_session(conn: sqlite3.Connection, signed_token: str) -> None:
    try:
        raw = _serializer().loads(signed_token, max_age=10**9)
    except BadSignature:
        return
    conn.execute("DELETE FROM sessions WHERE id = ?", (_hash_token(raw),))


def purge_expired(conn: sqlite3.Connection) -> int:
    """Delete expired session rows. Returns count deleted."""
    cur = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_utcnow().isoformat(),))
    return cur.rowcount
