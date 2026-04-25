"""LLM-driven Organize pass.

Walks recently-modified notes plus everything under Inbox/, asks the LLM
to propose improvements per note, aggregates the proposals into a markdown
report. Step 7 ships the dry-run only — proposals are reported, no writes
happen. Step 9 will add apply mode.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.db.connection import open_connection
from app.llm import Message, TextBlock, complete
from app.vault import vault_root

log = logging.getLogger(__name__)

MAX_NOTES_PER_RUN = 50
MAX_VAULT_PATHS_IN_PROMPT = 200
MAX_NOTE_CHARS = 12_000

_LAST_RUN_KEY = "last_run_at"
_MODULE = "organize"


@dataclass(frozen=True, slots=True)
class NoteProposal:
    path: str
    body: str        # markdown returned by the LLM


@dataclass(frozen=True, slots=True)
class OrganizeResult:
    started_at: datetime
    finished_at: datetime
    mode: str
    processed: int
    skipped: list[tuple[str, str]]   # (path, reason)
    proposals: list[NoteProposal]
    report: str


# ── candidate selection ──────────────────────────────────────────────


def _last_run_at(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute(
        "SELECT value FROM module_state WHERE module_id = ? AND key = ?",
        (_MODULE, _LAST_RUN_KEY),
    ).fetchone()
    if row is None or not row["value"]:
        return None
    return datetime.fromisoformat(row["value"])


def _record_run(conn: sqlite3.Connection, when: datetime) -> None:
    iso = when.isoformat()
    conn.execute(
        "INSERT INTO module_state (module_id, key, value, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(module_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (_MODULE, _LAST_RUN_KEY, iso, iso),
    )


def _select_candidates(conn: sqlite3.Connection) -> tuple[list[Path], datetime | None]:
    root = vault_root()
    last_run = _last_run_at(conn)
    cutoff = last_run.timestamp() if last_run else 0.0

    in_inbox: list[Path] = []
    modified: list[Path] = []
    for p in root.rglob("*.md"):
        rel_parts = p.relative_to(root).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        rel = p.relative_to(root).as_posix()
        if rel.startswith("Inbox/"):
            in_inbox.append(p)
        elif p.stat().st_mtime > cutoff:
            modified.append(p)

    # Sort: Inbox first (oldest mtime first to drain backlog), then modified.
    in_inbox.sort(key=lambda x: x.stat().st_mtime)
    modified.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    candidates = (in_inbox + modified)[:MAX_NOTES_PER_RUN]
    return candidates, last_run


# ── per-note LLM call ────────────────────────────────────────────────


_SYSTEM_PROMPT = """\
You are reviewing one note from the user's Obsidian vault. Propose targeted
improvements without rewriting it. Use INDEX.md (provided below) as the map
of where notes belong.

Return CONCISE markdown using exactly this structure:

**Move to:** `<vault-relative path>` or `(stay)`
**Tags:** [tag1, tag2] or `(no change)`
**Wikilinks to add:**
  - `[[Target Note]]` — short reason
**Refactor notes:** <one short paragraph or `(none)`>

If everything is already in order, return only:
**OK, no changes proposed.**

Keep responses tight. No preamble, no closing remarks.
"""


def _read_index() -> str:
    s = get_settings()
    if s.obsidian.vault_path is None:
        return ""
    idx = vault_root() / s.obsidian.index_file
    if not idx.is_file():
        return ""
    return idx.read_text(encoding="utf-8")


def _vault_paths_sample() -> list[str]:
    root = vault_root()
    paths: list[str] = []
    for p in root.rglob("*.md"):
        rel_parts = p.relative_to(root).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        paths.append(p.relative_to(root).as_posix())
        if len(paths) >= MAX_VAULT_PATHS_IN_PROMPT:
            break
    return paths


def _build_user_prompt(note_path: str, note_content: str, index: str, paths: list[str]) -> str:
    truncated = note_content
    if len(truncated) > MAX_NOTE_CHARS:
        truncated = truncated[:MAX_NOTE_CHARS] + "\n\n[…content truncated for review]"
    parts = [
        f"## Note path\n{note_path}",
        f"## INDEX.md (vault map)\n```\n{index.strip() or '(empty)'}\n```",
        "## Vault paths (sample)\n" + "\n".join(f"- {p}" for p in paths),
        f"## Note content\n```markdown\n{truncated}\n```",
    ]
    return "\n\n".join(parts)


async def _propose(note_path: str, note_content: str, index: str, paths: list[str]) -> str:
    user = _build_user_prompt(note_path, note_content, index, paths)
    msgs = [Message(role="user", content=[TextBlock(text=user)])]
    return (await complete(_SYSTEM_PROMPT, msgs)).strip()


# ── orchestration ────────────────────────────────────────────────────


async def run_organize() -> OrganizeResult:
    settings = get_settings()
    started = datetime.now(timezone.utc)

    conn = open_connection()
    try:
        candidates, last_run = _select_candidates(conn)
    finally:
        conn.close()

    proposals: list[NoteProposal] = []
    skipped: list[tuple[str, str]] = []
    if candidates:
        index = _read_index()
        paths = _vault_paths_sample()
        for p in candidates:
            rel = p.relative_to(vault_root()).as_posix()
            try:
                content = p.read_text(encoding="utf-8")
            except OSError as exc:
                skipped.append((rel, f"read error: {exc}"))
                continue
            try:
                body = await _propose(rel, content, index, paths)
            except Exception as exc:
                log.exception("organize: proposal failed for %s", rel)
                skipped.append((rel, f"LLM error: {exc}"))
                continue
            proposals.append(NoteProposal(path=rel, body=body))

    finished = datetime.now(timezone.utc)
    conn = open_connection()
    try:
        _record_run(conn, finished)
    finally:
        conn.close()

    report = _format_report(
        started, finished, settings.organize.mode, last_run, proposals, skipped, len(candidates)
    )
    return OrganizeResult(
        started_at=started,
        finished_at=finished,
        mode=settings.organize.mode,
        processed=len(candidates),
        skipped=skipped,
        proposals=proposals,
        report=report,
    )


def _format_report(
    started: datetime,
    finished: datetime,
    mode: str,
    last_run: datetime | None,
    proposals: list[NoteProposal],
    skipped: list[tuple[str, str]],
    total: int,
) -> str:
    lines = [
        f"# Organize report — {started.date().isoformat()}",
        "",
        f"Mode: **{mode}**",
        f"Started: {started.isoformat()}",
        f"Finished: {finished.isoformat()} ({(finished - started).total_seconds():.1f}s)",
        f"Last run: {last_run.isoformat() if last_run else '(first run)'}",
        f"Notes considered: {total}",
        f"Proposals: {len(proposals)}",
        f"Skipped: {len(skipped)}",
        "",
    ]
    if proposals:
        lines.append("## Proposals")
        lines.append("")
        for p in proposals:
            lines.append(f"### {p.path}")
            lines.append("")
            lines.append(p.body or "(empty)")
            lines.append("")
    if skipped:
        lines.append("## Skipped")
        lines.append("")
        for path, reason in skipped:
            lines.append(f"- `{path}` — {reason}")
        lines.append("")
    return "\n".join(lines)
