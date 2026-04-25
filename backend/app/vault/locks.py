"""Edit-session locks for the wiki editor.

Single-user app, but multiple browser tabs (or WhatsApp-driven writes in
Phase 3) could conflict. While the wiki editor is open on a note, we hold
a per-path lock with a TTL. Saves are accepted only if the caller holds the
matching token. The lock auto-expires so a closed browser doesn't strand it.

Token storage matches the session pattern: only a SHA-256 of the token is
in SQLite — even DB exposure doesn't grant write access.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

LOCK_TTL_MIN = 30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class LockGrant:
    path: str
    token: str
    expires_at: datetime


class LockConflict(RuntimeError):
    """Another session holds the lock on this path."""


class LockInvalid(RuntimeError):
    """The supplied token doesn't match (or no lock is held)."""


def _purge_expired(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM edit_locks WHERE expires_at < ?", (_utcnow().isoformat(),))


def acquire(conn: sqlite3.Connection, path: str) -> LockGrant:
    """Take a fresh lock on `path`. Raises LockConflict if held by someone else."""
    _purge_expired(conn)
    row = conn.execute(
        "SELECT token_hash, expires_at FROM edit_locks WHERE path = ?", (path,)
    ).fetchone()
    if row is not None:
        raise LockConflict(f"another session is editing {path}")

    token = secrets.token_urlsafe(24)
    now = _utcnow()
    expires = now + timedelta(minutes=LOCK_TTL_MIN)
    conn.execute(
        "INSERT INTO edit_locks (path, token_hash, created_at, expires_at) "
        "VALUES (?, ?, ?, ?)",
        (path, _hash(token), now.isoformat(), expires.isoformat()),
    )
    return LockGrant(path=path, token=token, expires_at=expires)


def release(conn: sqlite3.Connection, path: str, token: str) -> None:
    """Release the lock if the token matches. Silently no-ops if no lock is held."""
    row = conn.execute(
        "SELECT token_hash FROM edit_locks WHERE path = ?", (path,)
    ).fetchone()
    if row is None:
        return
    if row["token_hash"] != _hash(token):
        raise LockInvalid("token does not match the lock on this path")
    conn.execute("DELETE FROM edit_locks WHERE path = ?", (path,))


def verify(conn: sqlite3.Connection, path: str, token: str) -> None:
    """Raise LockInvalid unless the caller holds the lock for `path`."""
    _purge_expired(conn)
    row = conn.execute(
        "SELECT token_hash FROM edit_locks WHERE path = ?", (path,)
    ).fetchone()
    if row is None:
        raise LockInvalid(f"no lock is held on {path} — call /api/vault/edit/lock first")
    if row["token_hash"] != _hash(token):
        raise LockInvalid("token does not match the lock on this path")
