"""SQLite persistence for the News feature (slim).

Only indexed metadata lives here — the full article record (summary,
url, author, image, …) sits at <data_dir>/news/<safe_id>.json. See
`articles.py` for the disk format.

Three tables:
  - news_articles: per-article indexed columns (slim).
  - news_feeds: per-feed metadata (title, group, favicon).
  - news_fetch_runs: observability for fetch jobs.
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
    """Indexed article row — what's in SQLite. Full body / url / image
    / etc. live in the JSON file (load via `articles.read_article`)."""

    id: str
    source: str
    external_id: str
    feed_id: str | None
    feed_title: str | None
    feed_group: str | None
    title: str
    published_at: str
    is_read: bool
    feed_favicon: str | None  # joined from news_feeds


@dataclass(slots=True)
class FeedSummary:
    feed_id: str
    feed_title: str
    feed_group: str | None
    favicon: str | None
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


# News articles age out after this — but only READ articles. Unread
# stays forever (the user explicitly wants to keep all unread items
# regardless of age).
RETENTION_DAYS = 30


def purge_old_articles(conn: sqlite3.Connection, *, days: int = RETENTION_DAYS) -> int:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).isoformat()
    cur = conn.execute(
        "DELETE FROM news_articles "
        "WHERE published_at < ? AND is_read = 1",
        (cutoff,),
    )
    return cur.rowcount


def purge_old_articles_with_ids(
    conn: sqlite3.Connection, *, days: int = RETENTION_DAYS
) -> list[str]:
    """Delete READ articles older than `days` and return their ids
    so the caller can also remove the corresponding JSON files on
    disk. Unread articles are preserved indefinitely."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).isoformat()
    rows = conn.execute(
        "SELECT id FROM news_articles "
        "WHERE published_at < ? AND is_read = 1",
        (cutoff,),
    ).fetchall()
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    conn.execute(
        "DELETE FROM news_articles "
        "WHERE published_at < ? AND is_read = 1",
        (cutoff,),
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
    title: str,
    published_at: str,
    is_read: bool = False,
) -> bool:
    """Insert (or refresh is_read on duplicate). Returns True on first
    sight, False when the article was already known."""
    article_id = f"{source}:{external_id}"
    is_read_int = 1 if is_read else 0
    cur = conn.execute(
        "INSERT OR IGNORE INTO news_articles "
        "(id, source, external_id, feed_id, feed_title, feed_group, "
        " title, published_at, is_read) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            article_id,
            source,
            external_id,
            feed_id,
            feed_title,
            feed_group,
            title,
            published_at,
            is_read_int,
        ),
    )
    if cur.rowcount > 0:
        return True
    conn.execute(
        "UPDATE news_articles SET is_read = ? WHERE id = ?",
        (is_read_int, article_id),
    )
    return False


def mark_article_read(
    conn: sqlite3.Connection, article_id: str, *, is_read: bool = True
) -> bool:
    cur = conn.execute(
        "UPDATE news_articles SET is_read = ? WHERE id = ?",
        (1 if is_read else 0, article_id),
    )
    return cur.rowcount > 0


def upsert_feed(
    conn: sqlite3.Connection,
    *,
    feed_id: str,
    title: str | None,
    feed_group: str | None,
    site_url: str | None,
    favicon_data_uri: str | None,
) -> None:
    """Refresh per-feed metadata. Called once per feed at the start
    of every fetch pass."""
    conn.execute(
        "INSERT INTO news_feeds "
        "(id, title, feed_group, site_url, favicon_data_uri, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "  title = excluded.title, "
        "  feed_group = excluded.feed_group, "
        "  site_url = excluded.site_url, "
        "  favicon_data_uri = excluded.favicon_data_uri, "
        "  updated_at = excluded.updated_at",
        (
            feed_id,
            title,
            feed_group,
            site_url,
            favicon_data_uri,
            _utcnow_iso(),
        ),
    )


# ── Reads ──────────────────────────────────────────────────────────


_ARTICLE_SELECT = (
    "SELECT a.*, f.favicon_data_uri AS feed_favicon "
    "FROM news_articles a LEFT JOIN news_feeds f ON f.id = a.feed_id"
)


def list_feeds_with_counts(
    conn: sqlite3.Connection, *, from_iso: str, to_iso: str
) -> list[FeedSummary]:
    """For each feed, return total + unread article counts in the
    [from, to] window, plus the feed's favicon."""
    rows = conn.execute(
        "SELECT a.feed_id, "
        "       COALESCE(MAX(a.feed_title), '(unknown feed)') AS feed_title, "
        "       MAX(a.feed_group) AS feed_group, "
        "       MAX(f.favicon_data_uri) AS favicon, "
        "       COUNT(*) AS total, "
        "       SUM(CASE WHEN a.is_read = 0 THEN 1 ELSE 0 END) AS unread "
        "FROM news_articles a LEFT JOIN news_feeds f ON f.id = a.feed_id "
        "WHERE substr(a.published_at, 1, 10) >= ? "
        "  AND substr(a.published_at, 1, 10) <= ? "
        "GROUP BY a.feed_id "
        "ORDER BY feed_title COLLATE NOCASE ASC",
        (from_iso, to_iso),
    ).fetchall()
    return [
        FeedSummary(
            feed_id=str(r["feed_id"] or ""),
            feed_title=r["feed_title"],
            feed_group=r["feed_group"],
            favicon=r["favicon"],
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
    sql = (
        _ARTICLE_SELECT
        + " WHERE substr(a.published_at, 1, 10) >= ?"
          " AND substr(a.published_at, 1, 10) <= ?"
    )
    params: list = [from_iso, to_iso]
    if feed_id:
        sql += " AND a.feed_id = ?"
        params.append(feed_id)
    if feed_group:
        sql += " AND a.feed_group = ?"
        params.append(feed_group)
    if unread_only:
        sql += " AND a.is_read = 0"
    sql += " ORDER BY a.published_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_article(r) for r in rows]


def get_article(conn: sqlite3.Connection, article_id: str) -> StoredArticle | None:
    row = conn.execute(
        _ARTICLE_SELECT + " WHERE a.id = ?", (article_id,)
    ).fetchone()
    return _row_to_article(row) if row is not None else None


def existing_external_ids(
    conn: sqlite3.Connection, source: str
) -> set[str]:
    """All external_ids stored for `source`. Used by the unread
    completeness pass to find ids we haven't fetched yet."""
    rows = conn.execute(
        "SELECT external_id FROM news_articles WHERE source = ?",
        (source,),
    ).fetchall()
    return {str(r["external_id"]) for r in rows}


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
        title=row["title"],
        published_at=row["published_at"],
        is_read=bool(row["is_read"]) if "is_read" in keys else False,
        feed_favicon=row["feed_favicon"] if "feed_favicon" in keys else None,
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
