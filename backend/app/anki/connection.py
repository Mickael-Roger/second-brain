"""Connection management for the Anki collection SQLite file.

The Anki collection lives in its own SQLite at
`<data_dir>/anki/collection.anki2`, so we can upload it to AnkiWeb
byte-for-byte. Keep it isolated from `second-brain.db`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from app.config import get_settings


def anki_dir() -> Path:
    return get_settings().app.data_dir / "anki"


def anki_db_path() -> Path:
    return anki_dir() / "collection.anki2"


def open_anki() -> sqlite3.Connection:
    """Open the local Anki collection.

    Caller is responsible for `bootstrap_collection()` having been run
    at least once (the FastAPI lifespan does this when anki.enabled).
    """
    anki_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        anki_db_path(),
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_anki_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency."""
    conn = open_anki()
    try:
        yield conn
    finally:
        conn.close()
