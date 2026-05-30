"""SQLite persistence for the Trends feature.

Three concerns:

  - `trends`           current state (active + soft-deleted).
  - `trend_snapshots`  audit-log + time-series. One row per LLM
                        evolution, with the full state-of-the-world
                        in `weights_json`.
  - `news_articles.trends_processed_at` marker used by the worker to
    pick up only fresh articles.

All functions take an explicit connection — the caller owns the
lifecycle (per request, per worker tick, …).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class TrendRecord:
    id: str
    name: str
    description: str
    category: str
    weight: float
    created_at: str
    last_reinforced_at: str
    reinforcement_count: int
    examples: list[str]
    pruned_at: str | None


def _row_to_trend(row: sqlite3.Row) -> TrendRecord:
    examples_raw = row["examples_json"] or "[]"
    try:
        examples = json.loads(examples_raw)
        if not isinstance(examples, list):
            examples = []
    except json.JSONDecodeError:
        examples = []
    # ``category`` was added in migration 0017 with a default of
    # 'Other' — sqlite3.Row.keys() doesn't help us here so we read
    # defensively by index existence.
    try:
        category = str(row["category"])
    except IndexError:  # pragma: no cover — pre-migration safety
        category = "Other"
    return TrendRecord(
        id=str(row["id"]),
        name=str(row["name"]),
        description=str(row["description"]),
        category=category,
        weight=float(row["weight"]),
        created_at=str(row["created_at"]),
        last_reinforced_at=str(row["last_reinforced_at"]),
        reinforcement_count=int(row["reinforcement_count"]),
        examples=[str(x) for x in examples],
        pruned_at=row["pruned_at"],
    )


# ── Trends CRUD ─────────────────────────────────────────────────────


def list_active_trends(conn: sqlite3.Connection) -> list[TrendRecord]:
    rows = conn.execute(
        "SELECT * FROM trends WHERE pruned_at IS NULL ORDER BY weight DESC, last_reinforced_at DESC"
    ).fetchall()
    return [_row_to_trend(r) for r in rows]


def get_trend(conn: sqlite3.Connection, trend_id: str) -> TrendRecord | None:
    row = conn.execute("SELECT * FROM trends WHERE id = ?", (trend_id,)).fetchone()
    return _row_to_trend(row) if row else None


def get_trend_by_name(
    conn: sqlite3.Connection, name: str, *, include_pruned: bool = False
) -> TrendRecord | None:
    if include_pruned:
        row = conn.execute("SELECT * FROM trends WHERE name = ? LIMIT 1", (name,)).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM trends WHERE name = ? AND pruned_at IS NULL LIMIT 1",
            (name,),
        ).fetchone()
    return _row_to_trend(row) if row else None


def create_trend(
    conn: sqlite3.Connection,
    *,
    name: str,
    description: str,
    category: str,
    weight: float,
    example_title: str | None = None,
) -> TrendRecord:
    now = _utcnow_iso()
    trend_id = uuid.uuid4().hex[:12]
    examples = [example_title] if example_title else []
    conn.execute(
        "INSERT INTO trends "
        "(id, name, description, category, weight, created_at, "
        " last_reinforced_at, reinforcement_count, examples_json, pruned_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, NULL)",
        (
            trend_id,
            name,
            description,
            category,
            weight,
            now,
            now,
            json.dumps(examples, ensure_ascii=False),
        ),
    )
    row = conn.execute("SELECT * FROM trends WHERE id = ?", (trend_id,)).fetchone()
    return _row_to_trend(row)


def reinforce_trend(
    conn: sqlite3.Connection,
    *,
    trend_id: str,
    new_weight: float,
    example_title: str | None = None,
    examples_cap: int = 3,
) -> None:
    """Bump the weight, the reinforcement count, and prepend the
    example (kept capped at `examples_cap`). The actual weight value
    is computed by the engine; the store just persists it."""
    row = conn.execute("SELECT examples_json FROM trends WHERE id = ?", (trend_id,)).fetchone()
    if row is None:
        return
    try:
        examples = json.loads(row["examples_json"] or "[]")
        if not isinstance(examples, list):
            examples = []
    except json.JSONDecodeError:
        examples = []
    if example_title:
        examples = [example_title] + [e for e in examples if e != example_title]
        examples = examples[:examples_cap]
    conn.execute(
        "UPDATE trends SET weight = ?, last_reinforced_at = ?, "
        "reinforcement_count = reinforcement_count + 1, "
        "examples_json = ? "
        "WHERE id = ?",
        (
            new_weight,
            _utcnow_iso(),
            json.dumps(examples, ensure_ascii=False),
            trend_id,
        ),
    )


def update_weight(conn: sqlite3.Connection, trend_id: str, new_weight: float) -> None:
    """Pure weight update — used by the decay pass."""
    conn.execute(
        "UPDATE trends SET weight = ? WHERE id = ?",
        (new_weight, trend_id),
    )


def rename_trend(
    conn: sqlite3.Connection,
    *,
    trend_id: str,
    new_name: str | None,
    new_description: str | None,
    new_category: str | None = None,
) -> bool:
    """Rename a trend and/or refine its description and/or move it to
    a different category. At least one of new_name/new_description/
    new_category must be non-empty. Returns True if the row was
    updated."""
    sets: list[str] = []
    params: list[object] = []
    if new_name and new_name.strip():
        sets.append("name = ?")
        params.append(new_name.strip())
    if new_description and new_description.strip():
        sets.append("description = ?")
        params.append(new_description.strip())
    if new_category and new_category.strip():
        sets.append("category = ?")
        params.append(new_category.strip())
    if not sets:
        return False
    params.append(trend_id)
    cur = conn.execute(f"UPDATE trends SET {', '.join(sets)} WHERE id = ?", params)
    return cur.rowcount > 0


def prune_trend(conn: sqlite3.Connection, trend_id: str) -> None:
    conn.execute(
        "UPDATE trends SET pruned_at = ?, weight = 0 WHERE id = ? AND pruned_at IS NULL",
        (_utcnow_iso(), trend_id),
    )


# ── Snapshots (time series) ────────────────────────────────────────


def insert_snapshot(
    conn: sqlite3.Connection,
    *,
    trigger_article_id: str | None,
    trigger_action: str,
    trigger_trend_id: str | None,
    weights: dict[str, float],
) -> int:
    """Persist a snapshot of all active trends' weights after an
    evolution. `weights` is `{trend_id: weight}` for every active
    trend (including the one just touched). Returns the snapshot id."""
    cur = conn.execute(
        "INSERT INTO trend_snapshots "
        "(recorded_at, trigger_article_id, trigger_action, "
        " trigger_trend_id, weights_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            _utcnow_iso(),
            trigger_article_id,
            trigger_action,
            trigger_trend_id,
            json.dumps(weights, ensure_ascii=False),
        ),
    )
    return int(cur.lastrowid or 0)


@dataclass(slots=True)
class StoredSnapshot:
    id: int
    recorded_at: str
    trigger_article_id: str | None
    trigger_action: str
    trigger_trend_id: str | None
    weights: dict[str, float]


def list_snapshots_since(
    conn: sqlite3.Connection,
    *,
    since_iso: str | None = None,
    limit: int = 500,
) -> list[StoredSnapshot]:
    """Recent snapshots, oldest first (UI replays the evolution
    chronologically). When `since_iso` is None, returns the latest
    `limit` entries reversed."""
    if since_iso:
        rows = conn.execute(
            "SELECT * FROM trend_snapshots WHERE recorded_at >= ? "
            "ORDER BY recorded_at ASC, id ASC LIMIT ?",
            (since_iso, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM trend_snapshots ORDER BY recorded_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        rows = list(reversed(rows))
    out: list[StoredSnapshot] = []
    for r in rows:
        try:
            weights = json.loads(r["weights_json"] or "{}")
            if not isinstance(weights, dict):
                weights = {}
        except json.JSONDecodeError:
            weights = {}
        out.append(
            StoredSnapshot(
                id=int(r["id"]),
                recorded_at=str(r["recorded_at"]),
                trigger_article_id=r["trigger_article_id"],
                trigger_action=str(r["trigger_action"]),
                trigger_trend_id=r["trigger_trend_id"],
                weights={str(k): float(v) for k, v in weights.items()},
            )
        )
    return out


# ── News article markers ────────────────────────────────────────────


@dataclass(slots=True)
class PendingArticle:
    """Just the bits the trends assigner needs."""

    article_id: str
    title: str
    summary: str  # title + plain-text body, capped
    published_at: str
    content_hash: str


def pending_articles(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    since_iso: str | None = None,
    until_iso: str | None = None,
) -> list[PendingArticle]:
    """Articles whose current content version still needs trend extraction.

    The rolling window bounds backlog cost permanently; `until_iso` lets the
    worker wait for external enrichment (news-synthesis) before processing.
    """
    sql = (
        "SELECT id, title, published_at, content_hash FROM news_articles "
        "WHERE content_hash IS NOT NULL "
        "AND (trends_content_hash IS NULL OR trends_content_hash IS NOT content_hash)"
    )
    params: list[object] = []
    if since_iso:
        sql += " AND published_at >= ?"
        params.append(since_iso)
    if until_iso:
        sql += " AND published_at <= ?"
        params.append(until_iso)
    sql += " ORDER BY published_at ASC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [
        PendingArticle(
            article_id=str(r["id"]),
            title=str(r["title"]),
            summary="",
            published_at=str(r["published_at"]),
            content_hash=str(r["content_hash"]),
        )
        for r in rows
    ]


def mark_article_processed(
    conn: sqlite3.Connection, article_id: str, content_hash: str | None = None
) -> None:
    if content_hash is None:
        row = conn.execute(
            "SELECT content_hash FROM news_articles WHERE id = ?", (article_id,)
        ).fetchone()
        content_hash = str(row["content_hash"]) if row and row["content_hash"] else None
    conn.execute(
        "UPDATE news_articles SET trends_processed_at = ?, trends_content_hash = ? WHERE id = ?",
        (_utcnow_iso(), content_hash, article_id),
    )


# ── Moment clusters / mentions ─────────────────────────────────────


@dataclass(slots=True)
class TrendClusterRecord:
    id: str
    title: str
    description: str
    category: str
    created_at: str
    first_seen_at: str
    last_seen_at: str
    status: str


@dataclass(slots=True)
class TrendMentionRecord:
    id: int
    cluster_id: str
    article_id: str
    article_content_hash: str
    subject_title: str
    subject_description: str
    article_type: str
    intensity: float
    confidence: float
    evidence: str
    created_at: str
    article_title: str
    feed_title: str | None
    feed_group: str | None
    published_at: str


def _row_to_cluster(row: sqlite3.Row) -> TrendClusterRecord:
    return TrendClusterRecord(
        id=str(row["id"]),
        title=str(row["title"]),
        description=str(row["description"]),
        category=str(row["category"]),
        created_at=str(row["created_at"]),
        first_seen_at=str(row["first_seen_at"]),
        last_seen_at=str(row["last_seen_at"]),
        status=str(row["status"]),
    )


def list_recent_clusters(
    conn: sqlite3.Connection, *, since_iso: str, limit: int = 80
) -> list[TrendClusterRecord]:
    rows = conn.execute(
        "SELECT * FROM trend_clusters "
        "WHERE status = 'active' AND last_seen_at >= ? "
        "ORDER BY last_seen_at DESC LIMIT ?",
        (since_iso, limit),
    ).fetchall()
    return [_row_to_cluster(r) for r in rows]


def create_cluster(
    conn: sqlite3.Connection,
    *,
    title: str,
    description: str,
    category: str,
    seen_at: str,
) -> TrendClusterRecord:
    now = _utcnow_iso()
    cluster_id = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO trend_clusters "
        "(id, title, description, category, created_at, first_seen_at, last_seen_at, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'active')",
        (cluster_id, title, description, category, now, seen_at, seen_at),
    )
    row = conn.execute("SELECT * FROM trend_clusters WHERE id = ?", (cluster_id,)).fetchone()
    return _row_to_cluster(row)


def touch_cluster(
    conn: sqlite3.Connection,
    cluster_id: str,
    *,
    seen_at: str,
    description: str | None = None,
    category: str | None = None,
) -> None:
    sets = ["last_seen_at = CASE WHEN ? > last_seen_at THEN ? ELSE last_seen_at END"]
    params: list[object] = [seen_at, seen_at]
    if description:
        sets.append("description = ?")
        params.append(description)
    if category:
        sets.append("category = ?")
        params.append(category)
    params.append(cluster_id)
    conn.execute(f"UPDATE trend_clusters SET {', '.join(sets)} WHERE id = ?", params)


def replace_article_mentions(
    conn: sqlite3.Connection,
    *,
    article_id: str,
    content_hash: str,
) -> None:
    conn.execute(
        "DELETE FROM trend_mentions WHERE article_id = ? AND article_content_hash IS NOT ?",
        (article_id, content_hash),
    )
    conn.execute(
        "DELETE FROM trend_mentions WHERE article_id = ? AND article_content_hash = ?",
        (article_id, content_hash),
    )


def insert_mention(
    conn: sqlite3.Connection,
    *,
    cluster_id: str,
    article_id: str,
    content_hash: str,
    subject_title: str,
    subject_description: str,
    article_type: str,
    intensity: float,
    confidence: float,
    evidence: str,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO trend_mentions "
        "(cluster_id, article_id, article_content_hash, subject_title, "
        " subject_description, article_type, intensity, confidence, evidence, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            cluster_id,
            article_id,
            content_hash,
            subject_title,
            subject_description,
            article_type,
            max(0.0, min(1.0, intensity)),
            max(0.0, min(1.0, confidence)),
            evidence,
            _utcnow_iso(),
        ),
    )


def list_mentions_since(conn: sqlite3.Connection, *, since_iso: str) -> list[TrendMentionRecord]:
    rows = conn.execute(
        "SELECT m.*, a.title AS article_title, a.feed_title, a.feed_group, a.published_at "
        "FROM trend_mentions m JOIN news_articles a ON a.id = m.article_id "
        "WHERE a.published_at >= ? "
        "ORDER BY a.published_at DESC, m.id DESC",
        (since_iso,),
    ).fetchall()
    return [
        TrendMentionRecord(
            id=int(r["id"]),
            cluster_id=str(r["cluster_id"]),
            article_id=str(r["article_id"]),
            article_content_hash=str(r["article_content_hash"]),
            subject_title=str(r["subject_title"]),
            subject_description=str(r["subject_description"]),
            article_type=str(r["article_type"]),
            intensity=float(r["intensity"]),
            confidence=float(r["confidence"]),
            evidence=str(r["evidence"] or ""),
            created_at=str(r["created_at"]),
            article_title=str(r["article_title"]),
            feed_title=r["feed_title"],
            feed_group=r["feed_group"],
            published_at=str(r["published_at"]),
        )
        for r in rows
    ]
