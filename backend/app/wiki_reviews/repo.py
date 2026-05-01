"""SQLite layer for the wiki review feature.

Two tables:
  - `wiki_reviews`     latest state per note (last rating, next due, excluded).
  - `wiki_review_log`  append-only history (one row per click).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Allowed rating tokens. Stored verbatim in the DB.
#   uninteresting → exclude the note from future reviews
#   soon          → +1 day
#   roughly       → +7 days
#   perfect       → +30 days
RATINGS: tuple[str, ...] = ("uninteresting", "soon", "roughly", "perfect")

_INTERVAL_DAYS: dict[str, int] = {
    "soon": 1,
    "roughly": 7,
    "perfect": 30,
}


@dataclass(slots=True)
class ReviewState:
    path: str
    last_reviewed_at: str
    last_rating: str
    next_due_at: str
    excluded: bool
    review_count: int


@dataclass(slots=True)
class ReviewStatus:
    has_reviewed_today: bool
    reviewed_today_count: int
    excluded_count: int
    total_in_state: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _start_of_today_utc_iso() -> str:
    now = _utcnow()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def review_state(conn: sqlite3.Connection, path: str) -> ReviewState | None:
    row = conn.execute(
        "SELECT path, last_reviewed_at, last_rating, next_due_at, "
        "       excluded, review_count "
        "FROM wiki_reviews WHERE path = ?",
        (path,),
    ).fetchone()
    return _row_to_state(row) if row is not None else None


def all_states(conn: sqlite3.Connection) -> list[ReviewState]:
    rows = conn.execute(
        "SELECT path, last_reviewed_at, last_rating, next_due_at, "
        "       excluded, review_count FROM wiki_reviews"
    ).fetchall()
    return [_row_to_state(r) for r in rows]


def excluded_paths(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT path FROM wiki_reviews WHERE excluded = 1"
    ).fetchall()
    return {r["path"] for r in rows}


def record_rating(
    conn: sqlite3.Connection,
    path: str,
    rating: str,
) -> ReviewState:
    """Persist a rating: append to the log AND upsert the per-note state."""
    if rating not in RATINGS:
        raise ValueError(f"unknown rating {rating!r}; expected one of {RATINGS}")

    now = _utcnow()
    now_iso = _iso(now)
    excluded = 1 if rating == "uninteresting" else 0
    if rating == "uninteresting":
        # The note is excluded — `next_due_at` is irrelevant for selection,
        # but we set it far in the future so it doesn't bubble up if the
        # user later clears the excluded flag manually.
        next_due_iso = _iso(now + timedelta(days=3650))
    else:
        next_due_iso = _iso(now + timedelta(days=_INTERVAL_DAYS[rating]))

    log_review(conn, path, rating, when=now_iso)

    conn.execute(
        "INSERT INTO wiki_reviews "
        "  (path, last_reviewed_at, last_rating, next_due_at, "
        "   excluded, review_count) "
        "VALUES (?, ?, ?, ?, ?, 1) "
        "ON CONFLICT(path) DO UPDATE SET "
        "  last_reviewed_at = excluded.last_reviewed_at, "
        "  last_rating      = excluded.last_rating, "
        "  next_due_at      = excluded.next_due_at, "
        "  excluded         = excluded.excluded, "
        "  review_count     = wiki_reviews.review_count + 1",
        (path, now_iso, rating, next_due_iso, excluded),
    )

    state = review_state(conn, path)
    assert state is not None  # we just upserted it
    return state


def log_review(
    conn: sqlite3.Connection,
    path: str,
    rating: str,
    *,
    when: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO wiki_review_log (path, reviewed_at, rating) VALUES (?, ?, ?)",
        (path, when or _iso(_utcnow()), rating),
    )


def review_status(conn: sqlite3.Connection) -> ReviewStatus:
    """High-level counters for the header badge.

    `has_reviewed_today` is the trigger for the "do a review now" dot in
    the wiki header — true iff the log has at least one entry since UTC
    midnight.
    """
    today_iso = _start_of_today_utc_iso()
    today_count = int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM wiki_review_log WHERE reviewed_at >= ?",
            (today_iso,),
        ).fetchone()["c"]
        or 0
    )
    excluded_count = int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM wiki_reviews WHERE excluded = 1"
        ).fetchone()["c"]
        or 0
    )
    total_in_state = int(
        conn.execute("SELECT COUNT(*) AS c FROM wiki_reviews").fetchone()["c"] or 0
    )
    return ReviewStatus(
        has_reviewed_today=today_count > 0,
        reviewed_today_count=today_count,
        excluded_count=excluded_count,
        total_in_state=total_in_state,
    )


def _row_to_state(row: sqlite3.Row) -> ReviewState:
    return ReviewState(
        path=row["path"],
        last_reviewed_at=row["last_reviewed_at"],
        last_rating=row["last_rating"],
        next_due_at=row["next_due_at"],
        excluded=bool(row["excluded"]),
        review_count=int(row["review_count"] or 0),
    )
