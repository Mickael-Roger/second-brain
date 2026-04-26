"""SQLite persistence for the News & Events feature.

Three tables: news_articles (one row per fetched item), news_events
(LLM-grouped event bubble), news_fetch_runs (observability for fetch +
cluster jobs). All datetimes are stored as ISO-8601 UTC text — same
convention as the rest of the codebase.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from ulid import ULID

FetchRunKind = Literal["fetch", "cluster"]
FetchRunStatus = Literal["running", "ok", "error"]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class StoredArticle:
    id: str
    source: str
    external_id: str
    feed_id: str | None
    feed_title: str | None
    url: str | None
    title: str
    description: str | None
    author: str | None
    published_at: str
    fetched_at: str
    event_id: str | None


@dataclass(slots=True)
class StoredEvent:
    id: str
    title: str
    summary: str | None
    occurred_on: str
    article_count: int
    created_at: str
    updated_at: str


@dataclass(slots=True)
class StoredFetchRun:
    id: int
    kind: FetchRunKind
    source: str | None
    started_at: str
    finished_at: str | None
    status: FetchRunStatus
    fetched: int
    inserted: int
    clustered: int
    error: str | None


# ── Articles ───────────────────────────────────────────────────────


# News articles age out fast — once a story is older than this we don't
# show it in the UI any more (the period selector only goes to 30d), so
# it's just dead weight in the DB and noise for the cluster prompt.
RETENTION_DAYS = 30


def purge_old_articles(conn: sqlite3.Connection, *, days: int = RETENTION_DAYS) -> int:
    """Delete articles older than `days` days and drop any events whose
    last article just got pruned. Returns the number of articles deleted.

    Called at the start of every fetch pass so the DB stays bounded
    without needing a separate housekeeping job."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).isoformat()
    cur = conn.execute(
        "DELETE FROM news_articles WHERE published_at < ?", (cutoff,)
    )
    deleted_articles = cur.rowcount
    # Orphaned events: every article was just deleted.
    conn.execute(
        "DELETE FROM news_events WHERE id NOT IN ("
        "  SELECT DISTINCT event_id FROM news_articles "
        "  WHERE event_id IS NOT NULL"
        ")"
    )
    # Refresh article_count on events that lost some but not all articles
    # — the bubble UI sizes off this number, so a stale count would
    # mis-render the bubble vs. the actual hover list.
    conn.execute(
        "UPDATE news_events SET article_count = ("
        "  SELECT COUNT(*) FROM news_articles "
        "  WHERE news_articles.event_id = news_events.id"
        ")"
    )
    return deleted_articles


def insert_article(
    conn: sqlite3.Connection,
    *,
    source: str,
    external_id: str,
    feed_id: str | None,
    feed_title: str | None,
    url: str | None,
    title: str,
    description: str | None,
    author: str | None,
    published_at: str,
) -> bool:
    """Insert a new article. Returns True if a row was inserted, False on
    duplicate (the article was already stored on a previous fetch)."""
    article_id = f"{source}:{external_id}"
    cur = conn.execute(
        "INSERT OR IGNORE INTO news_articles "
        "(id, source, external_id, feed_id, feed_title, url, title, "
        " description, author, published_at, fetched_at, event_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        (
            article_id,
            source,
            external_id,
            feed_id,
            feed_title,
            url,
            title,
            description,
            author,
            published_at,
            _utcnow_iso(),
        ),
    )
    return cur.rowcount > 0


def list_unclustered_articles(
    conn: sqlite3.Connection, *, since_iso: str
) -> list[StoredArticle]:
    rows = conn.execute(
        "SELECT * FROM news_articles "
        "WHERE event_id IS NULL AND published_at >= ? "
        "ORDER BY published_at DESC",
        (since_iso,),
    ).fetchall()
    return [_row_to_article(r) for r in rows]


def assign_article_to_event(
    conn: sqlite3.Connection, article_id: str, event_id: str
) -> None:
    conn.execute(
        "UPDATE news_articles SET event_id = ? WHERE id = ?",
        (event_id, article_id),
    )


def get_event_articles(conn: sqlite3.Connection, event_id: str) -> list[StoredArticle]:
    rows = conn.execute(
        "SELECT * FROM news_articles WHERE event_id = ? ORDER BY published_at DESC",
        (event_id,),
    ).fetchall()
    return [_row_to_article(r) for r in rows]


# ── Events ─────────────────────────────────────────────────────────


def upsert_event(
    conn: sqlite3.Connection,
    *,
    title: str,
    summary: str | None,
    occurred_on: str,
    article_ids: list[str],
) -> str:
    """Create a new event row and link the given articles to it.

    Returns the event id. Updates `article_count` to the number of
    articles linked. Articles already attached to a different event are
    left alone (clusters are not merged here — that would risk losing
    user-visible groupings on incremental re-runs)."""
    event_id = str(ULID())
    now = _utcnow_iso()
    conn.execute(
        "INSERT INTO news_events "
        "(id, title, summary, occurred_on, article_count, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 0, ?, ?)",
        (event_id, title, summary, occurred_on, now, now),
    )
    linked = 0
    for aid in article_ids:
        cur = conn.execute(
            "UPDATE news_articles SET event_id = ? "
            "WHERE id = ? AND event_id IS NULL",
            (event_id, aid),
        )
        linked += cur.rowcount
    conn.execute(
        "UPDATE news_events SET article_count = ?, updated_at = ? WHERE id = ?",
        (linked, now, event_id),
    )
    return event_id


# Hot-topics threshold: an "event" only shows up in the dashboard if at
# least this many articles cover it. Singletons stay in the DB so we
# don't re-prompt the LLM about them on every cluster pass, but they're
# noise on a hot-topics view.
MIN_EVENT_ARTICLES = 2


def list_events(
    conn: sqlite3.Connection, *, from_iso: str, to_iso: str
) -> list[StoredEvent]:
    """Events whose `occurred_on` falls in the [from, to] range AND
    that have at least `MIN_EVENT_ARTICLES` articles. Both bounds are
    inclusive ISO dates (YYYY-MM-DD) — the period selector in the UI
    maps to these directly."""
    rows = conn.execute(
        "SELECT * FROM news_events "
        "WHERE occurred_on >= ? AND occurred_on <= ? "
        "  AND article_count >= ? "
        "ORDER BY article_count DESC, occurred_on DESC",
        (from_iso, to_iso, MIN_EVENT_ARTICLES),
    ).fetchall()
    return [_row_to_event(r) for r in rows]


def get_event(conn: sqlite3.Connection, event_id: str) -> StoredEvent | None:
    row = conn.execute(
        "SELECT * FROM news_events WHERE id = ?", (event_id,)
    ).fetchone()
    return _row_to_event(row) if row is not None else None


# ── Fetch runs ─────────────────────────────────────────────────────


def create_fetch_run(
    conn: sqlite3.Connection, *, kind: FetchRunKind, source: str | None = None
) -> int:
    cur = conn.execute(
        "INSERT INTO news_fetch_runs (kind, source, started_at, status) "
        "VALUES (?, ?, ?, 'running')",
        (kind, source, _utcnow_iso()),
    )
    return int(cur.lastrowid or 0)


def set_fetch_run_status(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: FetchRunStatus,
    error: str | None = None,
) -> None:
    conn.execute(
        "UPDATE news_fetch_runs SET status = ?, error = ? WHERE id = ?",
        (status, error, run_id),
    )


def finish_fetch_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: FetchRunStatus = "ok",
    fetched: int = 0,
    inserted: int = 0,
    clustered: int = 0,
    error: str | None = None,
) -> None:
    conn.execute(
        "UPDATE news_fetch_runs SET status = ?, finished_at = ?, "
        "fetched = ?, inserted = ?, clustered = ?, error = ? "
        "WHERE id = ?",
        (status, _utcnow_iso(), fetched, inserted, clustered, error, run_id),
    )


def list_recent_runs(
    conn: sqlite3.Connection, *, limit: int = 20
) -> list[StoredFetchRun]:
    rows = conn.execute(
        "SELECT * FROM news_fetch_runs ORDER BY started_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_run(r) for r in rows]


# ── Row mapping ────────────────────────────────────────────────────


def _row_to_article(row: sqlite3.Row) -> StoredArticle:
    return StoredArticle(
        id=row["id"],
        source=row["source"],
        external_id=row["external_id"],
        feed_id=row["feed_id"],
        feed_title=row["feed_title"],
        url=row["url"],
        title=row["title"],
        description=row["description"],
        author=row["author"],
        published_at=row["published_at"],
        fetched_at=row["fetched_at"],
        event_id=row["event_id"],
    )


def _row_to_event(row: sqlite3.Row) -> StoredEvent:
    return StoredEvent(
        id=row["id"],
        title=row["title"],
        summary=row["summary"],
        occurred_on=row["occurred_on"],
        article_count=int(row["article_count"] or 0),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_run(row: sqlite3.Row) -> StoredFetchRun:
    return StoredFetchRun(
        id=int(row["id"]),
        kind=row["kind"],
        source=row["source"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        status=row["status"],
        fetched=int(row["fetched"] or 0),
        inserted=int(row["inserted"] or 0),
        clustered=int(row["clustered"] or 0),
        error=row["error"],
    )
