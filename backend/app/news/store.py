"""SQLite persistence for the News feature.

Two tables: news_articles (one row per fetched item) and
news_fetch_runs (observability for fetch jobs). All datetimes are
stored as ISO-8601 UTC text — same convention as the rest of the
codebase. The article body is stored on disk (see `summaries.py`),
not in `news_articles`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

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
    feed_group: str | None
    url: str | None
    title: str
    author: str | None
    published_at: str
    fetched_at: str
    is_read: bool
    image_url: str | None


@dataclass(slots=True)
class FeedSummary:
    feed_id: str
    feed_title: str
    feed_group: str | None
    total: int
    unread: int


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
    error: str | None


# ── Articles ───────────────────────────────────────────────────────


# News articles age out fast — once a story is older than this we
# don't show it any more, so it's just dead weight in the DB.
RETENTION_DAYS = 30


def purge_old_articles(conn: sqlite3.Connection, *, days: int = RETENTION_DAYS) -> int:
    """Delete articles older than `days`. Returns the number of
    articles deleted. The caller is responsible for also removing the
    corresponding summary files on disk; we return the deleted ids
    so they can do that.

    NB: this signature changed from earlier — it used to return just
    a count. Callers should switch to `purge_old_articles_with_ids`
    if they need both."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).isoformat()
    cur = conn.execute(
        "DELETE FROM news_articles WHERE published_at < ?", (cutoff,)
    )
    return cur.rowcount


def purge_old_articles_with_ids(
    conn: sqlite3.Connection, *, days: int = RETENTION_DAYS
) -> list[str]:
    """Same as purge_old_articles but returns the ids that were
    deleted, so the caller can clean up summary files on disk."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).isoformat()
    rows = conn.execute(
        "SELECT id FROM news_articles WHERE published_at < ?", (cutoff,)
    ).fetchall()
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    conn.execute(
        "DELETE FROM news_articles WHERE published_at < ?", (cutoff,)
    )
    return ids


def insert_article(
    conn: sqlite3.Connection,
    *,
    source: str,
    external_id: str,
    feed_id: str | None,
    feed_title: str | None,
    feed_group: str | None,
    url: str | None,
    title: str,
    author: str | None,
    published_at: str,
    is_read: bool = False,
    image_url: str | None = None,
) -> bool:
    """Insert a new article OR refresh its `is_read` state.

    Returns True if a brand-new row was inserted, False if the article
    was already known. On a duplicate we still update `is_read` so
    reading an article in FreshRSS propagates here on the next fetch.

    Other metadata stays immutable on re-fetch."""
    article_id = f"{source}:{external_id}"
    is_read_int = 1 if is_read else 0
    cur = conn.execute(
        "INSERT OR IGNORE INTO news_articles "
        "(id, source, external_id, feed_id, feed_title, feed_group, url, "
        " title, author, published_at, fetched_at, "
        " is_read, image_url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            article_id,
            source,
            external_id,
            feed_id,
            feed_title,
            feed_group,
            url,
            title,
            author,
            published_at,
            _utcnow_iso(),
            is_read_int,
            image_url,
        ),
    )
    if cur.rowcount > 0:
        return True
    # Existing row — sync is_read so FreshRSS reads propagate here.
    conn.execute(
        "UPDATE news_articles SET is_read = ? WHERE id = ?",
        (is_read_int, article_id),
    )
    return False


def mark_article_read(
    conn: sqlite3.Connection, article_id: str, *, is_read: bool = True
) -> bool:
    """Flip the local read-state for one article. Returns True if a
    row was updated."""
    cur = conn.execute(
        "UPDATE news_articles SET is_read = ? WHERE id = ?",
        (1 if is_read else 0, article_id),
    )
    return cur.rowcount > 0


# ── News tab queries (per-feed listing) ────────────────────────────


def list_feeds_with_counts(
    conn: sqlite3.Connection, *, from_iso: str, to_iso: str
) -> list[FeedSummary]:
    """For each feed, return total + unread article counts in the
    [from, to] window. Date-prefix comparison on published_at so
    today's articles aren't dropped by lexicographic mismatch."""
    rows = conn.execute(
        "SELECT feed_id, "
        "       COALESCE(MAX(feed_title), '(unknown feed)') AS feed_title, "
        "       MAX(feed_group) AS feed_group, "
        "       COUNT(*) AS total, "
        "       SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS unread "
        "FROM news_articles "
        "WHERE substr(published_at, 1, 10) >= ? "
        "  AND substr(published_at, 1, 10) <= ? "
        "GROUP BY feed_id "
        "ORDER BY feed_title COLLATE NOCASE ASC",
        (from_iso, to_iso),
    ).fetchall()
    return [
        FeedSummary(
            feed_id=str(r["feed_id"] or ""),
            feed_title=r["feed_title"],
            feed_group=r["feed_group"],
            total=int(r["total"] or 0),
            unread=int(r["unread"] or 0),
        )
        for r in rows
    ]


def list_articles(
    conn: sqlite3.Connection,
    *,
    from_iso: str,
    to_iso: str,
    feed_id: str | None = None,
    feed_group: str | None = None,
    unread_only: bool = False,
    limit: int = 500,
) -> list[StoredArticle]:
    """Articles in the window, optionally filtered to one feed, one
    category (feed_group), and/or unread items. Newest-first."""
    sql = (
        "SELECT * FROM news_articles "
        "WHERE substr(published_at, 1, 10) >= ? "
        "  AND substr(published_at, 1, 10) <= ?"
    )
    params: list = [from_iso, to_iso]
    if feed_id:
        sql += " AND feed_id = ?"
        params.append(feed_id)
    if feed_group:
        sql += " AND feed_group = ?"
        params.append(feed_group)
    if unread_only:
        sql += " AND is_read = 0"
    sql += " ORDER BY published_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_article(r) for r in rows]


def get_article(conn: sqlite3.Connection, article_id: str) -> StoredArticle | None:
    row = conn.execute(
        "SELECT * FROM news_articles WHERE id = ?", (article_id,)
    ).fetchone()
    return _row_to_article(row) if row is not None else None


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
    keys = row.keys()
    return StoredArticle(
        id=row["id"],
        source=row["source"],
        external_id=row["external_id"],
        feed_id=row["feed_id"],
        feed_title=row["feed_title"],
        feed_group=row["feed_group"] if "feed_group" in keys else None,
        url=row["url"],
        title=row["title"],
        author=row["author"],
        published_at=row["published_at"],
        fetched_at=row["fetched_at"],
        is_read=bool(row["is_read"]) if "is_read" in keys else False,
        image_url=row["image_url"] if "image_url" in keys else None,
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
        error=row["error"],
    )
