"""SQLite connection management.

We use stdlib `sqlite3`. One connection per request (FastAPI dependency).
No ORM, no session, no identity map — call sites issue SQL directly.

`isolation_level=None` puts the driver in autocommit mode: implicit
transactions are disabled, and code that needs atomicity issues
explicit `BEGIN` / `COMMIT` (see `db.migrations`).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from app.config import get_settings


def _db_path() -> Path:
    return get_settings().app.data_dir / "second-brain.db"


def open_connection() -> sqlite3.Connection:
    settings = get_settings()
    settings.app.data_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        _db_path(),
        isolation_level=None,           # autocommit; we BEGIN explicitly when needed
        check_same_thread=False,        # FastAPI may dispatch sync handlers across threads
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: yield a fresh connection scoped to the request."""
    conn = open_connection()
    try:
        yield conn
    finally:
        conn.close()
