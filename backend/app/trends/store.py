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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class TrendRecord:
    id: str
    name: str
    description: str
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
    return TrendRecord(
        id=str(row["id"]),
        name=str(row["name"]),
        description=str(row["description"]),
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
        "SELECT * FROM trends WHERE pruned_at IS NULL "
        "ORDER BY weight DESC, last_reinforced_at DESC"
    ).fetchall()
    return [_row_to_trend(r) for r in rows]


def get_trend(conn: sqlite3.Connection, trend_id: str) -> TrendRecord | None:
    row = conn.execute(
        "SELECT * FROM trends WHERE id = ?", (trend_id,)
    ).fetchone()
    return _row_to_trend(row) if row else None


def get_trend_by_name(
    conn: sqlite3.Connection, name: str, *, include_pruned: bool = False
) -> TrendRecord | None:
    if include_pruned:
        row = conn.execute(
            "SELECT * FROM trends WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
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
    weight: float,
    example_title: str | None = None,
) -> TrendRecord:
    now = _utcnow_iso()
    trend_id = uuid.uuid4().hex[:12]
    examples = [example_title] if example_title else []
    conn.execute(
        "INSERT INTO trends "
        "(id, name, description, weight, created_at, last_reinforced_at, "
        " reinforcement_count, examples_json, pruned_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, ?, NULL)",
        (
            trend_id, name, description, weight, now, now,
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
    row = conn.execute(
        "SELECT examples_json FROM trends WHERE id = ?", (trend_id,)
    ).fetchone()
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


def update_weight(
    conn: sqlite3.Connection, trend_id: str, new_weight: float
) -> None:
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
) -> bool:
    """Rename a trend and/or refine its description. At least one of
    new_name/new_description must be non-empty. Returns True if the row
    was updated."""
    sets: list[str] = []
    params: list[object] = []
    if new_name and new_name.strip():
        sets.append("name = ?")
        params.append(new_name.strip())
    if new_description and new_description.strip():
        sets.append("description = ?")
        params.append(new_description.strip())
    if not sets:
        return False
    params.append(trend_id)
    cur = conn.execute(
        f"UPDATE trends SET {', '.join(sets)} WHERE id = ?", params
    )
    return cur.rowcount > 0


def prune_trend(conn: sqlite3.Connection, trend_id: str) -> None:
    conn.execute(
        "UPDATE trends SET pruned_at = ?, weight = 0 "
        "WHERE id = ? AND pruned_at IS NULL",
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
    conn: sqlite3.Connection, *, since_iso: str | None = None, limit: int = 500,
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
            "SELECT * FROM trend_snapshots "
            "ORDER BY recorded_at DESC, id DESC LIMIT ?",
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
    summary: str          # title + plain-text body, capped


def pending_articles(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    since_iso: str | None = None,
) -> list[PendingArticle]:
    """Articles with `trends_processed_at IS NULL`, oldest first
    (so we process in publication order). When `since_iso` is set,
    only articles published on/after that ISO timestamp are returned —
    this is how we cap the backlog cost on first enable (anything
    older than the configured window is silently skipped)."""
    if since_iso:
        rows = conn.execute(
            "SELECT id, title FROM news_articles "
            "WHERE trends_processed_at IS NULL AND published_at >= ? "
            "ORDER BY published_at ASC LIMIT ?",
            (since_iso, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, title FROM news_articles "
            "WHERE trends_processed_at IS NULL "
            "ORDER BY published_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        PendingArticle(article_id=str(r["id"]), title=str(r["title"]), summary="")
        for r in rows
    ]


def mark_article_processed(
    conn: sqlite3.Connection, article_id: str
) -> None:
    conn.execute(
        "UPDATE news_articles SET trends_processed_at = ? WHERE id = ?",
        (_utcnow_iso(), article_id),
    )
