"""Per-note review tracking for the Organize job.

The Organize job only runs from the nightly cron or the `second-brain
organize` CLI — there is no webapp UI for it. The single piece of state
we still persist is the per-note `last_reviewed_at`, used by the
candidate selector to skip notes that haven't changed since the LLM
last looked at them.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def mark_note_reviewed(
    conn: sqlite3.Connection, path: str, *, when: datetime | None = None
) -> None:
    """Record that a note has been reviewed by the LLM. Per-note reviews
    drive the default candidate scope: a note is re-reviewed only when
    its mtime exceeds its last_reviewed_at."""
    iso = (when or datetime.now(timezone.utc)).isoformat()
    conn.execute(
        "INSERT INTO note_reviews (path, last_reviewed_at) VALUES (?, ?) "
        "ON CONFLICT(path) DO UPDATE SET last_reviewed_at = excluded.last_reviewed_at",
        (path, iso),
    )


def get_note_review_map(conn: sqlite3.Connection) -> dict[str, float]:
    """Path → last_reviewed_at as a unix timestamp. Used by the candidate
    selector to decide which notes are due for review."""
    rows = conn.execute("SELECT path, last_reviewed_at FROM note_reviews").fetchall()
    out: dict[str, float] = {}
    for r in rows:
        dt = _parse_dt(r["last_reviewed_at"])
        if dt is not None:
            out[r["path"]] = dt.timestamp()
    return out


__all__ = [
    "get_note_review_map",
    "mark_note_reviewed",
]
