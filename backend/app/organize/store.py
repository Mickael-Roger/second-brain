"""Persistence layer for organize runs + per-note proposals.

A run is created the moment an organize job starts (cron or on-demand).
Each Proposal the LLM produces is inserted as it arrives; the run is
finalized at the end. The webapp's Organize view polls these tables to
show progress and lets the user discard or apply pending proposals.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from ulid import ULID

RunStatus = Literal["running", "completed", "applied", "discarded", "failed"]
ProposalState = Literal["pending", "applied", "discarded", "failed"]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass(slots=True)
class StoredProposal:
    run_id: str
    path: str
    move_to: str | None
    tags: list[str] | None
    wikilinks: list[dict[str, str]]
    refactor: str | None
    notes: str | None
    parse_error: str | None
    raw_response: str
    state: ProposalState
    apply_error: str | None
    apply_ops: list[str]
    created_at: datetime


@dataclass(slots=True)
class StoredRun:
    id: str
    started_at: datetime
    finished_at: datetime | None
    mode: str
    status: RunStatus
    notes_total: int
    summary: str | None
    error: str | None
    proposals: list[StoredProposal] = field(default_factory=list)


# ── Runs ────────────────────────────────────────────────────────────


def create_run(conn: sqlite3.Connection, *, mode: str) -> str:
    run_id = str(ULID())
    conn.execute(
        "INSERT INTO organize_runs (id, started_at, mode, status) VALUES (?, ?, ?, 'running')",
        (run_id, _utcnow_iso(), mode),
    )
    return run_id


def set_run_status(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: RunStatus,
    error: str | None = None,
) -> None:
    conn.execute(
        "UPDATE organize_runs SET status = ?, error = ? WHERE id = ?",
        (status, error, run_id),
    )


def finish_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: RunStatus = "completed",
    notes_total: int = 0,
    summary: str | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        "UPDATE organize_runs SET status = ?, finished_at = ?, "
        "notes_total = ?, summary = ?, error = ? WHERE id = ?",
        (status, _utcnow_iso(), notes_total, summary, error, run_id),
    )


# ── Proposals ───────────────────────────────────────────────────────


def insert_proposal(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    path: str,
    move_to: str | None,
    tags: list[str] | None,
    wikilinks: list[dict[str, str]],
    refactor: str | None,
    notes: str | None,
    parse_error: str | None,
    raw_response: str,
) -> None:
    # Always start as pending. apply_pending() flips to applied/failed.
    # If the LLM had a parse error, mark discarded immediately — no point
    # showing the user something we can't act on.
    state: ProposalState = "discarded" if parse_error else "pending"
    conn.execute(
        "INSERT INTO organize_proposals "
        "(run_id, path, move_to, tags_json, wikilinks_json, refactor, "
        " notes, parse_error, raw_response, state, apply_error, "
        " apply_ops, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '[]', ?)",
        (
            run_id,
            path,
            move_to,
            json.dumps(tags) if tags is not None else None,
            json.dumps(wikilinks) if wikilinks else None,
            refactor,
            notes,
            parse_error,
            raw_response,
            state,
            _utcnow_iso(),
        ),
    )


def set_proposal_state(
    conn: sqlite3.Connection,
    run_id: str,
    path: str,
    *,
    state: ProposalState,
    apply_error: str | None = None,
    apply_ops: list[str] | None = None,
) -> None:
    conn.execute(
        "UPDATE organize_proposals SET state = ?, apply_error = ?, apply_ops = ? "
        "WHERE run_id = ? AND path = ?",
        (
            state,
            apply_error,
            json.dumps(apply_ops or []),
            run_id,
            path,
        ),
    )


def discard_proposal(conn: sqlite3.Connection, run_id: str, path: str) -> bool:
    """Mark a proposal as discarded. Returns True if it was actually pending."""
    cur = conn.execute(
        "UPDATE organize_proposals SET state = 'discarded' "
        "WHERE run_id = ? AND path = ? AND state = 'pending'",
        (run_id, path),
    )
    return cur.rowcount > 0


# ── Reads ───────────────────────────────────────────────────────────


def _row_to_proposal(row: sqlite3.Row) -> StoredProposal:
    return StoredProposal(
        run_id=row["run_id"],
        path=row["path"],
        move_to=row["move_to"],
        tags=json.loads(row["tags_json"]) if row["tags_json"] else None,
        wikilinks=json.loads(row["wikilinks_json"]) if row["wikilinks_json"] else [],
        refactor=row["refactor"],
        notes=row["notes"],
        parse_error=row["parse_error"],
        raw_response=row["raw_response"] or "",
        state=row["state"],
        apply_error=row["apply_error"],
        apply_ops=json.loads(row["apply_ops"]) if row["apply_ops"] else [],
        created_at=_parse_dt(row["created_at"]) or datetime.now(timezone.utc),
    )


def _row_to_run(row: sqlite3.Row) -> StoredRun:
    started = _parse_dt(row["started_at"])
    assert started is not None
    return StoredRun(
        id=row["id"],
        started_at=started,
        finished_at=_parse_dt(row["finished_at"]),
        mode=row["mode"],
        status=row["status"],
        notes_total=row["notes_total"] or 0,
        summary=row["summary"],
        error=row["error"],
    )


def get_run(
    conn: sqlite3.Connection, run_id: str, *, include_proposals: bool = True
) -> StoredRun | None:
    row = conn.execute("SELECT * FROM organize_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    run = _row_to_run(row)
    if include_proposals:
        prop_rows = conn.execute(
            "SELECT * FROM organize_proposals WHERE run_id = ? ORDER BY created_at",
            (run_id,),
        ).fetchall()
        run.proposals = [_row_to_proposal(r) for r in prop_rows]
    return run


def get_current_run(conn: sqlite3.Connection) -> StoredRun | None:
    """Most-recent run that's still running OR has any pending proposals."""
    row = conn.execute(
        "SELECT * FROM organize_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return get_run(conn, row["id"])


def list_runs(conn: sqlite3.Connection, *, limit: int = 20) -> list[StoredRun]:
    rows = conn.execute(
        "SELECT * FROM organize_runs ORDER BY started_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_run(r) for r in rows]


def proposal_summary(run: StoredRun) -> dict[str, int]:
    """Count proposals per state — used in the run summary."""
    out: dict[str, int] = {
        "pending": 0,
        "applied": 0,
        "discarded": 0,
        "failed": 0,
    }
    for p in run.proposals:
        out[p.state] = out.get(p.state, 0) + 1
    return out


def fetch_pending_proposals(conn: sqlite3.Connection, run_id: str) -> list[StoredProposal]:
    rows = conn.execute(
        "SELECT * FROM organize_proposals WHERE run_id = ? AND state = 'pending' "
        "ORDER BY created_at",
        (run_id,),
    ).fetchall()
    return [_row_to_proposal(r) for r in rows]


# Re-export for callers that want a stable typing surface.
def mark_note_reviewed(
    conn: sqlite3.Connection, path: str, *, when: datetime | None = None
) -> None:
    """Record that a note has been reviewed by the LLM. Called once per
    Proposal generation (regardless of whether it gets applied). Per-note
    reviews drive the default scope: a note is re-reviewed only when its
    mtime exceeds its last_reviewed_at."""
    iso = (when or datetime.now(timezone.utc)).isoformat()
    conn.execute(
        "INSERT INTO note_reviews (path, last_reviewed_at) VALUES (?, ?) "
        "ON CONFLICT(path) DO UPDATE SET last_reviewed_at = excluded.last_reviewed_at",
        (path, iso),
    )


def reconcile_dangling_runs(conn: sqlite3.Connection) -> int:
    """Mark every still-'running' run as 'failed' with a clear error.

    Called at app startup. A run only stays 'running' while the
    corresponding asyncio task is alive — if the container restarted, the
    task is gone and nothing will ever flip the row out of 'running'. The
    UI would otherwise spin forever waiting for status to change."""
    cur = conn.execute(
        "UPDATE organize_runs "
        "SET status = 'failed', "
        "    finished_at = COALESCE(finished_at, ?), "
        "    error = COALESCE(error, 'interrupted by app restart') "
        "WHERE status = 'running'",
        (_utcnow_iso(),),
    )
    return cur.rowcount


def discard_run(conn: sqlite3.Connection, run_id: str) -> bool:
    """User-driven: mark the run discarded. Pending proposals are also
    flipped to 'discarded' so the run is fully closed out."""
    cur = conn.execute(
        "UPDATE organize_runs SET status = 'discarded', "
        "finished_at = COALESCE(finished_at, ?) WHERE id = ?",
        (_utcnow_iso(), run_id),
    )
    if cur.rowcount == 0:
        return False
    conn.execute(
        "UPDATE organize_proposals SET state = 'discarded' "
        "WHERE run_id = ? AND state = 'pending'",
        (run_id,),
    )
    return True


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
    "ProposalState",
    "RunStatus",
    "StoredProposal",
    "StoredRun",
    "create_run",
    "discard_proposal",
    "discard_run",
    "fetch_pending_proposals",
    "finish_run",
    "get_current_run",
    "get_note_review_map",
    "get_run",
    "insert_proposal",
    "list_runs",
    "mark_note_reviewed",
    "proposal_summary",
    "reconcile_dangling_runs",
    "set_proposal_state",
    "set_run_status",
]
