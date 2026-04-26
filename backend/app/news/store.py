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
    feed_group: str | None
    url: str | None
    title: str
    description: str | None
    author: str | None
    published_at: str
    fetched_at: str
    event_id: str | None
    tags: list[str] | None
    tags_extracted_at: str | None
    is_read: bool


@dataclass(slots=True)
class FeedSummary:
    feed_id: str
    feed_title: str
    feed_group: str | None
    total: int
    unread: int


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
    feed_group: str | None,
    url: str | None,
    title: str,
    description: str | None,
    author: str | None,
    published_at: str,
    is_read: bool = False,
) -> bool:
    """Insert a new article OR refresh its `is_read` state.

    Returns True if a brand-new row was inserted, False if the article
    was already known. On a duplicate we still update `is_read` so
    reading an article in FreshRSS propagates here on the next fetch.

    Other metadata (title, description, tags, …) is intentionally NOT
    overwritten on a re-fetch: rewriting the row would invalidate the
    tagger's idempotency assumptions."""
    article_id = f"{source}:{external_id}"
    is_read_int = 1 if is_read else 0
    cur = conn.execute(
        "INSERT OR IGNORE INTO news_articles "
        "(id, source, external_id, feed_id, feed_title, feed_group, url, "
        " title, description, author, published_at, fetched_at, event_id, "
        " is_read) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
        (
            article_id,
            source,
            external_id,
            feed_id,
            feed_title,
            feed_group,
            url,
            title,
            description,
            author,
            published_at,
            _utcnow_iso(),
            is_read_int,
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


# ── News tab queries (per-feed listing) ────────────────────────────


def list_feeds_with_counts(
    conn: sqlite3.Connection, *, from_iso: str, to_iso: str
) -> list[FeedSummary]:
    """For each feed, return total + unread article counts in the
    [from, to] window. Used to populate the News tab's feed sidebar.

    Same date-prefix trick as the trend queries: published_at is a
    full datetime, comparison boundaries are bare YYYY-MM-DD."""
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
    unread_only: bool = False,
    limit: int = 500,
) -> list[StoredArticle]:
    """Articles in the window, optionally filtered to one feed and/or
    unread items. Newest-first."""
    sql = (
        "SELECT * FROM news_articles "
        "WHERE substr(published_at, 1, 10) >= ? "
        "  AND substr(published_at, 1, 10) <= ?"
    )
    params: list = [from_iso, to_iso]
    if feed_id:
        sql += " AND feed_id = ?"
        params.append(feed_id)
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


# ── Tag extraction ────────────────────────────────────────────────


def list_pending_tag_articles(
    conn: sqlite3.Connection, *, limit: int = 50
) -> list[StoredArticle]:
    """Newest-first batch of articles whose tags haven't been extracted
    yet. Walks the partial index `idx_news_articles_pending_tags`."""
    rows = conn.execute(
        "SELECT * FROM news_articles "
        "WHERE tags_extracted_at IS NULL "
        "ORDER BY published_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_article(r) for r in rows]


def set_article_tags(
    conn: sqlite3.Connection,
    article_id: str,
    *,
    tags: list[str],
) -> None:
    """Persist the tagger's result for one article. Always sets
    `tags_extracted_at` so the article isn't re-prompted, even if the
    LLM returned an empty array — that's still information ('this
    article doesn't trigger any topic')."""
    import json as _json
    conn.execute(
        "UPDATE news_articles SET tags_json = ?, tags_extracted_at = ? "
        "WHERE id = ?",
        (_json.dumps(tags), _utcnow_iso(), article_id),
    )


def aggregate_tags(
    conn: sqlite3.Connection,
    *,
    from_iso: str,
    to_iso: str,
    min_count: int = 2,
) -> list[tuple[str, int]]:
    """Hot-topics aggregation: tag → number of distinct articles in the
    [from, to] window. Tags appearing on fewer than `min_count`
    articles are dropped (they're not 'trends', they're singletons).

    `from_iso` / `to_iso` are inclusive `YYYY-MM-DD` boundaries. We
    extract the date prefix from `published_at` (which is a full
    ISO datetime) for the comparison — comparing a full datetime
    against a bare date string lexicographically would silently
    exclude every article published ON the upper-bound day (because
    "2026-04-26T15:30:00..." sorts greater than "2026-04-26").

    Case-insensitive grouping so 'GPT-5' and 'gpt-5' aren't treated as
    different tags."""
    rows = conn.execute(
        "SELECT MIN(je.value) AS tag, COUNT(DISTINCT a.id) AS n "
        "FROM news_articles a, json_each(a.tags_json) je "
        "WHERE a.tags_extracted_at IS NOT NULL "
        "  AND substr(a.published_at, 1, 10) >= ? "
        "  AND substr(a.published_at, 1, 10) <= ? "
        "GROUP BY LOWER(je.value) "
        "HAVING n >= ? "
        "ORDER BY n DESC, tag ASC",
        (from_iso, to_iso, min_count),
    ).fetchall()
    return [(r["tag"], int(r["n"])) for r in rows]


def list_articles_with_tag(
    conn: sqlite3.Connection,
    tag: str,
    *,
    from_iso: str,
    to_iso: str,
) -> list[StoredArticle]:
    """All articles in the window whose tag list contains `tag`
    (case-insensitive, exact match — no substring or stemming).

    Same date-prefix trick as `aggregate_tags` so today's articles
    aren't dropped by the upper-bound comparison."""
    rows = conn.execute(
        "SELECT a.* FROM news_articles a, json_each(a.tags_json) je "
        "WHERE LOWER(je.value) = LOWER(?) "
        "  AND substr(a.published_at, 1, 10) >= ? "
        "  AND substr(a.published_at, 1, 10) <= ? "
        "ORDER BY a.published_at DESC",
        (tag, from_iso, to_iso),
    ).fetchall()
    return [_row_to_article(r) for r in rows]


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
    import json as _json

    keys = row.keys()
    raw_tags = row["tags_json"] if "tags_json" in keys else None
    tags: list[str] | None = None
    if raw_tags:
        try:
            parsed = _json.loads(raw_tags)
            if isinstance(parsed, list):
                tags = [str(t) for t in parsed]
        except (TypeError, ValueError):
            tags = None
    return StoredArticle(
        id=row["id"],
        source=row["source"],
        external_id=row["external_id"],
        feed_id=row["feed_id"],
        feed_title=row["feed_title"],
        feed_group=row["feed_group"] if "feed_group" in keys else None,
        url=row["url"],
        title=row["title"],
        description=row["description"],
        author=row["author"],
        published_at=row["published_at"],
        fetched_at=row["fetched_at"],
        event_id=row["event_id"],
        tags=tags,
        tags_extracted_at=(
            row["tags_extracted_at"] if "tags_extracted_at" in keys else None
        ),
        is_read=bool(row["is_read"]) if "is_read" in keys else False,
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
