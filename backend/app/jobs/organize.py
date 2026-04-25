"""LLM-driven Organize pass.

Walks recently-modified notes plus everything under Inbox/, asks the LLM
to return structured JSON proposals per note, aggregates them into a
markdown report, and (in `apply` mode) writes the changes through the
vault primitives.

The LLM emits JSON so the same path serves both modes:
- dry-run: the JSON renders into a readable markdown report.
- apply:   the JSON drives concrete moves / refactors / tag updates.

Wikilink suggestions are reported as guidance but NOT auto-inserted —
inserting them at the right spot is contextual; the user reviews them and
the LLM re-reads them on subsequent passes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter

from app.config import get_settings
from app.db.connection import open_connection
from app.llm import Message, TextBlock, complete
from app.vault import (
    append_note as _vault_append,  # noqa: F401  (kept for future use)
)
from app.vault import (
    move_note,
    read_note,
    vault_root,
    write_note,
)
from app.vault.guard import GitConflictError
from app.vault.paths import VaultPathError

log = logging.getLogger(__name__)

MAX_NOTES_PER_RUN = 50
MAX_VAULT_PATHS_IN_PROMPT = 200
MAX_NOTE_CHARS = 12_000

_LAST_RUN_KEY = "last_run_at"
_MODULE = "organize"


# ── data ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class WikilinkSuggestion:
    target: str
    context: str = ""


@dataclass(slots=True)
class Proposal:
    """Structured changes for a single note."""

    path: str
    move_to: str | None = None
    tags: list[str] | None = None
    wikilinks: list[WikilinkSuggestion] = field(default_factory=list)
    refactor: str | None = None
    notes: str | None = None  # free-form refactor commentary
    raw_response: str = ""    # the LLM's full text, for debugging
    parse_error: str | None = None

    @property
    def has_changes(self) -> bool:
        return any([self.move_to, self.tags, self.refactor])

    @property
    def is_no_op(self) -> bool:
        return not self.has_changes and not self.wikilinks and not self.notes


@dataclass(slots=True)
class AppliedNote:
    path: str
    operations: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True, slots=True)
class OrganizeResult:
    started_at: datetime
    finished_at: datetime
    mode: str
    processed: int
    skipped: list[tuple[str, str]]
    proposals: list[Proposal]
    applied: list[AppliedNote]
    report: str


# ── candidate selection + run state ──────────────────────────────────


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
        "ON CONFLICT(module_id, key) DO UPDATE SET "
        "value = excluded.value, updated_at = excluded.updated_at",
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

    in_inbox.sort(key=lambda x: x.stat().st_mtime)
    modified.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return (in_inbox + modified)[:MAX_NOTES_PER_RUN], last_run


# ── prompt + parser ──────────────────────────────────────────────────


_SYSTEM_PROMPT = """\
You are reviewing one note from the user's Obsidian vault. Use INDEX.md (the
vault's structural map), USER.md (facts about the user), and PREFERENCES.md
(operating preferences) — all three provided below — as authoritative
context for your proposals.

Return ONE JSON object and nothing else (no preamble, no code fences). The
schema:

{
  "move_to": "<vault-relative .md path>" | null,
  "tags": ["tag1", "tag2"] | null,
  "wikilinks": [{"target": "Note Name", "context": "why"}] | null,
  "refactor": "<the full rewritten note content, including frontmatter>" | null,
  "notes": "<short prose comment, optional>" | null
}

Rules:
- Use null for fields you don't want to change.
- Only propose `move_to` if the note clearly belongs in a different folder
  per INDEX.md.
- Only propose `tags` if the existing frontmatter is missing/wrong.
- Only propose `refactor` for grammar / spelling / clarity / structure
  fixes that materially improve the note. Preserve the user's voice; do
  not invent content. Honor PREFERENCES.md if it constrains style.
- Wikilinks are suggestions for the user to consider; the system does NOT
  auto-insert them.
- If the note is already in good shape, return all-null fields.
"""


def _strip_code_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def parse_proposal(path: str, raw: str) -> Proposal:
    cleaned = _strip_code_fences(raw)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return Proposal(path=path, raw_response=raw, parse_error="no JSON object found")
    try:
        obj = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        return Proposal(path=path, raw_response=raw, parse_error=f"JSON decode: {exc}")

    if not isinstance(obj, dict):
        return Proposal(path=path, raw_response=raw, parse_error="JSON is not an object")

    wikilinks: list[WikilinkSuggestion] = []
    raw_links = obj.get("wikilinks")
    if isinstance(raw_links, list):
        for item in raw_links:
            if isinstance(item, dict) and "target" in item:
                wikilinks.append(
                    WikilinkSuggestion(
                        target=str(item["target"]),
                        context=str(item.get("context", "")),
                    )
                )

    tags = obj.get("tags")
    if not isinstance(tags, list) or not tags:
        tags = None
    else:
        tags = [str(t) for t in tags]

    return Proposal(
        path=path,
        move_to=str(obj["move_to"]) if obj.get("move_to") else None,
        tags=tags,
        wikilinks=wikilinks,
        refactor=str(obj["refactor"]) if obj.get("refactor") else None,
        notes=str(obj["notes"]) if obj.get("notes") else None,
        raw_response=raw,
    )


# ── prompt builders ──────────────────────────────────────────────────


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


def _build_user_prompt(
    note_path: str,
    content: str,
    context_files: list[Any],
    paths: list[str],
) -> str:
    body = content
    if len(body) > MAX_NOTE_CHARS:
        body = body[:MAX_NOTE_CHARS] + "\n\n[…content truncated for review]"

    parts: list[str] = [f"## Note path\n{note_path}"]
    for cf in context_files:
        parts.append(f"## {cf.label}\n```\n{cf.content.strip() or '(empty)'}\n```")
    parts.append("## Vault paths (sample)\n" + "\n".join(f"- {p}" for p in paths))
    parts.append(f"## Note content\n```markdown\n{body}\n```")
    return "\n\n".join(parts)


async def _propose(
    note_path: str,
    content: str,
    context_files: list[Any],
    paths: list[str],
) -> Proposal:
    user = _build_user_prompt(note_path, content, context_files, paths)
    msgs = [Message(role="user", content=[TextBlock(text=user)])]
    raw = await complete(_SYSTEM_PROMPT, msgs)
    return parse_proposal(note_path, raw)


# ── apply ────────────────────────────────────────────────────────────


def _apply_tags(content: str, new_tags: list[str]) -> str:
    """Rewrite a note's frontmatter to set `tags = new_tags`. Preserves
    existing frontmatter for other keys; adds frontmatter if missing."""
    post = frontmatter.loads(content)
    post["tags"] = new_tags
    return frontmatter.dumps(post)


async def _apply_proposal(proposal: Proposal) -> AppliedNote:
    """Best-effort apply. Order: refactor → tags → move. Each step is its
    own commit (vault primitives wrap their own git transaction)."""
    applied = AppliedNote(path=proposal.path)

    current_path = proposal.path

    # 1. Refactor (full content rewrite).
    if proposal.refactor is not None:
        try:
            await write_note(current_path, proposal.refactor)
            applied.operations.append("refactor")
        except (VaultPathError, GitConflictError, OSError) as exc:
            applied.error = f"refactor: {exc}"
            return applied

    # 2. Tags (read current, update frontmatter, write).
    if proposal.tags is not None:
        try:
            current = read_note(current_path).content
            updated = _apply_tags(current, proposal.tags)
            await write_note(current_path, updated)
            applied.operations.append(f"tags={proposal.tags}")
        except (VaultPathError, GitConflictError, OSError, FileNotFoundError) as exc:
            applied.error = f"tags: {exc}"
            return applied

    # 3. Move (last so the previous writes go to the original path first).
    if proposal.move_to and proposal.move_to != current_path:
        try:
            await move_note(current_path, proposal.move_to)
            applied.operations.append(f"move→{proposal.move_to}")
            current_path = proposal.move_to
        except (VaultPathError, FileExistsError, GitConflictError, OSError) as exc:
            applied.error = f"move: {exc}"
            return applied

    return applied


# ── orchestration ────────────────────────────────────────────────────


async def run_organize() -> OrganizeResult:
    settings = get_settings()
    started = datetime.now(timezone.utc)

    conn = open_connection()
    try:
        candidates, last_run = _select_candidates(conn)
    finally:
        conn.close()

    proposals: list[Proposal] = []
    skipped: list[tuple[str, str]] = []
    if candidates:
        from app.vault import read_context_files

        context_files = read_context_files()  # INDEX, USER, PREFERENCES (each optional)
        paths = _vault_paths_sample()
        for p in candidates:
            rel = p.relative_to(vault_root()).as_posix()
            try:
                content = p.read_text(encoding="utf-8")
            except OSError as exc:
                skipped.append((rel, f"read error: {exc}"))
                continue
            try:
                proposals.append(await _propose(rel, content, context_files, paths))
            except Exception as exc:
                log.exception("organize: proposal failed for %s", rel)
                skipped.append((rel, f"LLM error: {exc}"))

    applied: list[AppliedNote] = []
    if settings.organize.mode == "apply":
        for proposal in proposals:
            if proposal.parse_error or proposal.is_no_op:
                continue
            try:
                applied.append(await _apply_proposal(proposal))
            except Exception as exc:
                log.exception("apply failed for %s", proposal.path)
                applied.append(AppliedNote(path=proposal.path, error=f"apply: {exc}"))

    finished = datetime.now(timezone.utc)
    conn = open_connection()
    try:
        _record_run(conn, finished)
    finally:
        conn.close()

    report = _format_report(
        started, finished, settings.organize.mode, last_run, proposals, applied, skipped, len(candidates)
    )
    return OrganizeResult(
        started_at=started,
        finished_at=finished,
        mode=settings.organize.mode,
        processed=len(candidates),
        skipped=skipped,
        proposals=proposals,
        applied=applied,
        report=report,
    )


# ── markdown rendering ───────────────────────────────────────────────


def _render_proposal(p: Proposal) -> str:
    if p.parse_error:
        return (
            f"_(LLM response failed to parse: {p.parse_error}.)_\n\n"
            f"<details><summary>Raw response</summary>\n\n```\n{p.raw_response[:1500]}\n```\n\n</details>"
        )
    if p.is_no_op:
        return "✅ OK, no changes proposed."

    lines: list[str] = []
    if p.move_to:
        lines.append(f"**Move to:** `{p.move_to}`")
    if p.tags is not None:
        lines.append(f"**Tags:** {', '.join(f'`{t}`' for t in p.tags) if p.tags else '(empty)'}")
    if p.wikilinks:
        lines.append("**Wikilinks suggested:**")
        for link in p.wikilinks:
            ctx = f" — {link.context}" if link.context else ""
            lines.append(f"  - `[[{link.target}]]`{ctx}")
    if p.refactor:
        lines.append("**Refactor:** the LLM proposed a rewrite (full new content, see raw).")
    if p.notes:
        lines.append(f"**Notes:** {p.notes}")
    return "\n".join(lines)


def _render_applied(a: AppliedNote) -> str:
    if a.error:
        return f"❌ `{a.path}` — {a.error}"
    if not a.operations:
        return f"`{a.path}` — (no-op)"
    return f"✅ `{a.path}` — {', '.join(a.operations)}"


def _format_report(
    started: datetime,
    finished: datetime,
    mode: str,
    last_run: datetime | None,
    proposals: list[Proposal],
    applied: list[AppliedNote],
    skipped: list[tuple[str, str]],
    total: int,
) -> str:
    lines = [
        f"# Organize report — {started.date().isoformat()}",
        "",
        f"Mode: **{mode}**",
        f"Started:  {started.isoformat()}",
        f"Finished: {finished.isoformat()} ({(finished - started).total_seconds():.1f}s)",
        f"Last run: {last_run.isoformat() if last_run else '(first run)'}",
        f"Notes considered: {total}",
        f"Proposals: {len(proposals)}",
        f"Skipped: {len(skipped)}",
    ]
    if mode == "apply":
        ok = sum(1 for a in applied if not a.error and a.operations)
        lines.append(f"Applied: {ok}")

    lines.append("")
    if proposals:
        lines.append("## Proposals")
        lines.append("")
        for p in proposals:
            lines.append(f"### `{p.path}`")
            lines.append("")
            lines.append(_render_proposal(p))
            lines.append("")
    if mode == "apply" and applied:
        lines.append("## Applied")
        lines.append("")
        for a in applied:
            lines.append(_render_applied(a))
        lines.append("")
    if skipped:
        lines.append("## Skipped")
        lines.append("")
        for path, reason in skipped:
            lines.append(f"- `{path}` — {reason}")
        lines.append("")
    return "\n".join(lines)
