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
    is_starred: bool
    feed_favicon: str | None  # joined from news_feeds
    labels: list[str]         # joined from news_article_labels


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
    is_starred: bool = False,
) -> bool:
    """Insert (or refresh is_read/is_starred on duplicate). Returns True
    on first sight, False when the article was already known."""
    article_id = f"{source}:{external_id}"
    is_read_int = 1 if is_read else 0
    is_starred_int = 1 if is_starred else 0
    cur = conn.execute(
        "INSERT OR IGNORE INTO news_articles "
        "(id, source, external_id, feed_id, feed_title, feed_group, "
        " title, published_at, is_read, is_starred) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            is_starred_int,
        ),
    )
    if cur.rowcount > 0:
        return True
    conn.execute(
        "UPDATE news_articles SET is_read = ?, is_starred = ? WHERE id = ?",
        (is_read_int, is_starred_int, article_id),
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


def mark_article_starred(
    conn: sqlite3.Connection, article_id: str, *, is_starred: bool = True
) -> bool:
    cur = conn.execute(
        "UPDATE news_articles SET is_starred = ? WHERE id = ?",
        (1 if is_starred else 0, article_id),
    )
    return cur.rowcount > 0


# ── Labels ─────────────────────────────────────────────────────────


def add_article_label(
    conn: sqlite3.Connection, article_id: str, label: str
) -> None:
    """Attach ``label`` to ``article_id``. Idempotent. Also ensures the
    label exists in the ``news_labels`` autocomplete index."""
    conn.execute(
        "INSERT OR IGNORE INTO news_article_labels (article_id, label) "
        "VALUES (?, ?)",
        (article_id, label),
    )
    conn.execute(
        "INSERT OR IGNORE INTO news_labels (name, created_at) VALUES (?, ?)",
        (label, _utcnow_iso()),
    )


def remove_article_label(
    conn: sqlite3.Connection, article_id: str, label: str
) -> None:
    conn.execute(
        "DELETE FROM news_article_labels WHERE article_id = ? AND label = ?",
        (article_id, label),
    )


def replace_article_labels(
    conn: sqlite3.Connection, article_id: str, labels: list[str]
) -> None:
    """Diff-replace the label set for ``article_id``. Used by the
    fetch path to keep local labels aligned with FreshRSS on every
    pass."""
    current = {
        r["label"]
        for r in conn.execute(
            "SELECT label FROM news_article_labels WHERE article_id = ?",
            (article_id,),
        )
    }
    target = set(labels)
    for lbl in current - target:
        conn.execute(
            "DELETE FROM news_article_labels WHERE article_id = ? AND label = ?",
            (article_id, lbl),
        )
    for lbl in target - current:
        conn.execute(
            "INSERT OR IGNORE INTO news_article_labels (article_id, label) "
            "VALUES (?, ?)",
            (article_id, lbl),
        )
        conn.execute(
            "INSERT OR IGNORE INTO news_labels (name, created_at) VALUES (?, ?)",
            (lbl, _utcnow_iso()),
        )


def list_labels(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM news_labels ORDER BY name COLLATE NOCASE ASC"
    ).fetchall()
    return [r["name"] for r in rows]


def remember_label(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO news_labels (name, created_at) VALUES (?, ?)",
        (name, _utcnow_iso()),
    )


def forget_label_everywhere(conn: sqlite3.Connection, name: str) -> int:
    """Detach ``name`` from every article and drop it from the autocomplete
    index. Returns the number of articles that had it. Used when the user
    deletes a label from the manage UI."""
    cur = conn.execute(
        "DELETE FROM news_article_labels WHERE label = ?", (name,)
    )
    affected = cur.rowcount
    conn.execute("DELETE FROM news_labels WHERE name = ?", (name,))
    return affected


def reconcile_read_state(
    conn: sqlite3.Connection,
    *,
    source: str,
    unread_external_ids: set[str],
) -> tuple[int, int]:
    """Align local is_read with FreshRSS using its full unread set as
    ground truth. Catches read-state changes the ranged walk misses —
    in particular, toggles on articles older than the 30-day window.

    Returns (newly_read, newly_unread):
      - newly_read: local is_read=0 but external_id NOT in FreshRSS unread → flipped to 1
      - newly_unread: local is_read=1 but external_id IS in FreshRSS unread → flipped to 0

    Uses a temp table so the IN-list is unbounded (avoids SQLite's
    ~32k parameter cap on busy aggregators)."""
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _unread_ids (external_id TEXT PRIMARY KEY)"
    )
    try:
        conn.execute("DELETE FROM _unread_ids")
        if unread_external_ids:
            conn.execute("BEGIN")
            try:
                conn.executemany(
                    "INSERT OR IGNORE INTO _unread_ids (external_id) VALUES (?)",
                    [(eid,) for eid in unread_external_ids],
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        cur1 = conn.execute(
            "UPDATE news_articles SET is_read = 1 "
            "WHERE source = ? AND is_read = 0 "
            "AND external_id NOT IN (SELECT external_id FROM _unread_ids)",
            (source,),
        )
        newly_read = cur1.rowcount
        cur2 = conn.execute(
            "UPDATE news_articles SET is_read = 0 "
            "WHERE source = ? AND is_read = 1 "
            "AND external_id IN (SELECT external_id FROM _unread_ids)",
            (source,),
        )
        newly_unread = cur2.rowcount
    finally:
        conn.execute("DROP TABLE IF EXISTS _unread_ids")
    return newly_read, newly_unread


def reconcile_starred_state(
    conn: sqlite3.Connection,
    *,
    source: str,
    starred_external_ids: set[str],
) -> tuple[int, int]:
    """Mirror of :func:`reconcile_read_state` for the starred flag.
    Returns (newly_starred, newly_unstarred)."""
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _starred_ids (external_id TEXT PRIMARY KEY)"
    )
    try:
        conn.execute("DELETE FROM _starred_ids")
        if starred_external_ids:
            conn.execute("BEGIN")
            try:
                conn.executemany(
                    "INSERT OR IGNORE INTO _starred_ids (external_id) VALUES (?)",
                    [(eid,) for eid in starred_external_ids],
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        cur1 = conn.execute(
            "UPDATE news_articles SET is_starred = 1 "
            "WHERE source = ? AND is_starred = 0 "
            "AND external_id IN (SELECT external_id FROM _starred_ids)",
            (source,),
        )
        newly_starred = cur1.rowcount
        cur2 = conn.execute(
            "UPDATE news_articles SET is_starred = 0 "
            "WHERE source = ? AND is_starred = 1 "
            "AND external_id NOT IN (SELECT external_id FROM _starred_ids)",
            (source,),
        )
        newly_unstarred = cur2.rowcount
    finally:
        conn.execute("DROP TABLE IF EXISTS _starred_ids")
    return newly_starred, newly_unstarred


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


# Unified visibility predicate used by every list query. An article
# is visible when either:
#   - it falls inside the [from, to] date window (the recency rule), OR
#   - it's still unread (the "never lose track of unread" rule).
# Read articles older than the window are deliberately hidden.
_VISIBILITY_WHERE = (
    "((substr(a.published_at, 1, 10) >= ? AND "
    "  substr(a.published_at, 1, 10) <= ?) "
    " OR a.is_read = 0)"
)


def list_feeds_with_counts(
    conn: sqlite3.Connection, *, from_iso: str, to_iso: str
) -> list[FeedSummary]:
    """For each feed, return total + unread article counts (window OR
    unread), plus the feed's favicon."""
    rows = conn.execute(
        "SELECT a.feed_id, "
        "       COALESCE(MAX(a.feed_title), '(unknown feed)') AS feed_title, "
        "       MAX(a.feed_group) AS feed_group, "
        "       MAX(f.favicon_data_uri) AS favicon, "
        "       COUNT(*) AS total, "
        "       SUM(CASE WHEN a.is_read = 0 THEN 1 ELSE 0 END) AS unread "
        "FROM news_articles a LEFT JOIN news_feeds f ON f.id = a.feed_id "
        f"WHERE {_VISIBILITY_WHERE} "
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
    label: str | None = None,
    starred_only: bool = False,
    unread_only: bool = False,
    limit: int = 500,
) -> list[StoredArticle]:
    sql = _ARTICLE_SELECT + f" WHERE {_VISIBILITY_WHERE}"
    params: list = [from_iso, to_iso]
    if feed_id:
        sql += " AND a.feed_id = ?"
        params.append(feed_id)
    if feed_group:
        sql += " AND a.feed_group = ?"
        params.append(feed_group)
    if label:
        sql += (
            " AND EXISTS (SELECT 1 FROM news_article_labels al "
            "WHERE al.article_id = a.id AND al.label = ?)"
        )
        params.append(label)
    if starred_only:
        sql += " AND a.is_starred = 1"
    if unread_only:
        sql += " AND a.is_read = 0"
    sql += " ORDER BY a.published_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    arts = [_row_to_article(r) for r in rows]
    _attach_labels(conn, arts)
    return arts


def get_article(conn: sqlite3.Connection, article_id: str) -> StoredArticle | None:
    row = conn.execute(
        _ARTICLE_SELECT + " WHERE a.id = ?", (article_id,)
    ).fetchone()
    if row is None:
        return None
    art = _row_to_article(row)
    _attach_labels(conn, [art])
    return art


def get_article_labels(
    conn: sqlite3.Connection, article_id: str
) -> list[str]:
    rows = conn.execute(
        "SELECT label FROM news_article_labels WHERE article_id = ? "
        "ORDER BY label COLLATE NOCASE ASC",
        (article_id,),
    ).fetchall()
    return [r["label"] for r in rows]


def _attach_labels(
    conn: sqlite3.Connection, articles: list[StoredArticle]
) -> None:
    """Backfill ``labels`` on a batch of articles in one SELECT.
    Avoids the per-row N+1 you'd get from looping :func:`get_article_labels`."""
    if not articles:
        return
    ids = [a.id for a in articles]
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT article_id, label FROM news_article_labels "
        f"WHERE article_id IN ({placeholders}) "
        f"ORDER BY label COLLATE NOCASE ASC",
        ids,
    ).fetchall()
    by_id: dict[str, list[str]] = {}
    for r in rows:
        by_id.setdefault(r["article_id"], []).append(r["label"])
    for a in articles:
        a.labels = by_id.get(a.id, [])


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


def list_categories(conn: sqlite3.Connection) -> list[str]:
    """Distinct non-null category names across news_feeds, sorted
    case-insensitively. The single source of truth for "categories
    that currently exist" — derived from feed metadata since the
    GReader API has no notion of a free-floating empty category."""
    rows = conn.execute(
        "SELECT DISTINCT feed_group FROM news_feeds "
        "WHERE feed_group IS NOT NULL AND feed_group != '' "
        "ORDER BY feed_group COLLATE NOCASE ASC"
    ).fetchall()
    return [r["feed_group"] for r in rows]


def list_id_mappings(
    conn: sqlite3.Connection, source: str
) -> list[tuple[str, str]]:
    """(article_id, external_id) for every row of ``source``. Used by
    the one-shot decimal→hex re-encode at first start under the
    GReader client."""
    rows = conn.execute(
        "SELECT id, external_id FROM news_articles WHERE source = ?",
        (source,),
    ).fetchall()
    return [(r["id"], r["external_id"]) for r in rows]


def rewrite_article_id(
    conn: sqlite3.Connection, old_id: str, new_id: str, new_external_id: str
) -> None:
    """Atomically rewrite an article's id + external_id. Used by the
    re-encode migration. Skips if ``old_id`` no longer exists (defensive
    against partial runs)."""
    conn.execute(
        "UPDATE news_articles SET id = ?, external_id = ? WHERE id = ?",
        (new_id, new_external_id, old_id),
    )
    # news_article_labels FK is ON DELETE CASCADE but not ON UPDATE
    # CASCADE — rewire by hand.
    conn.execute(
        "UPDATE news_article_labels SET article_id = ? WHERE article_id = ?",
        (new_id, old_id),
    )


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
        is_starred=bool(row["is_starred"]) if "is_starred" in keys else False,
        feed_favicon=row["feed_favicon"] if "feed_favicon" in keys else None,
        labels=[],  # filled in by _attach_labels
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
