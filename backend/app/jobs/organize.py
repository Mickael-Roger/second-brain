"""LLM-driven Organize pass — agent + tools.

Walks recently-modified vault notes and runs an LLM agent on each
candidate. The agent has the full `vault.*` toolset (read / list /
find / grep / edit_note / move / create_note / append / delete /
replace_in_note / update_frontmatter / create_folder) and acts directly
on the working tree per the policies in ORGANIZE.md (loaded as system
prompt, with INDEX.md / USER.md / PREFERENCES.md appended as authoritative
context).

There is no JSON proposal contract anymore. The agent reads, decides,
and mutates via tools — what changed is observable in the working tree.
The wrapping nightly job in `scheduler.py` runs this whole pass inside
a `batch_session()` (suppresses per-call git IO), then captures the
result via `git diff --stat` and either commits + pushes (apply mode)
or stashes (dry-run) the cumulative working-tree changes.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import get_settings
from app.db.connection import open_connection
from app.llm import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    get_llm_router,
)
from app.vault import vault_root

log = logging.getLogger(__name__)

MAX_NOTES_PER_RUN = 50
MAX_NOTE_CHARS = 12_000

_LAST_RUN_KEY = "last_run_at"
_MODULE = "organize"

# Hard exclusions matching ORGANIZE.md "What you NEVER touch" section.
EXCLUDED_TOP_DIRS = frozenset({
    "Trash",
    "Templates",
    "Excalidraw",
    "files",
    "Tracking",
})
EXCLUDED_PREFIXES = (
    "Raw/Anki/",
    "Raw/Logger/Opencode/",
    "Raw/Review/",
    # Memory is maintained entirely as a side effect of processing other
    # files (facts/decisions/prefs in the existing flat files; People and
    # Events fiches in their sub-folders). Never select Memory files as
    # candidates to process — agents enrich them, but they're not source
    # data themselves. Writes are still allowed by `_writable()`.
    "Memory/",
)
# Files that are never selected as candidates — they're not source data
# the agent processes as input. Some of them are still WRITABLE by the
# agent as a side effect (INDEX.md, TODO.md, Wiki/log.md): see
# `_SCHEMA_PROTECTED_FILES` for the strict no-write subset.
EXCLUDED_FILES = frozenset({
    # User-owned schema layer.
    "USER.md",
    "PREFERENCES.md",
    "AGENTS.md",
    "INGEST.md",
    "ORGANIZE.md",
    "README.md",
    "Cheatsheet.md",
    # Maintained by the agent as a side effect of touching other files.
    "INDEX.md",
    "TODO.md",
    "DONE.md",
    "Wiki/log.md",
    # Sentinel — drained but never moved/deleted itself.
    "Raw/Inbox/Notes.md",
})

# Files the agent must NEVER mutate via tools (a strict subset of
# `EXCLUDED_FILES`). Anything in `EXCLUDED_FILES` but not here can be
# written to as a side effect — INDEX.md gets updated when structure
# drifts, TODO.md when an action item surfaces, Wiki/log.md when a
# non-trivial op happens, etc.
_SCHEMA_PROTECTED_FILES = frozenset({
    "USER.md",
    "PREFERENCES.md",
    "AGENTS.md",
    "INGEST.md",
    "ORGANIZE.md",
    "README.md",
    "Cheatsheet.md",
})


def _is_excluded(rel: str) -> bool:
    """True when a vault-relative .md path is on the hard-exclusion list."""
    if rel in EXCLUDED_FILES:
        return True
    top = rel.split("/", 1)[0]
    if top in EXCLUDED_TOP_DIRS:
        return True
    return any(rel.startswith(prefix) for prefix in EXCLUDED_PREFIXES)


def _is_todays_flat_journal(rel: str) -> bool:
    """True when `rel` is today's flat-path daily note (e.g. Journal/2026-04-28.md).

    The daily note in progress is left alone; archived journals
    (Journal/YYYY/MM/...) and prior days are fair game.
    """
    s = get_settings().obsidian.journal
    today_name = f"{_date.today().isoformat()}.md"
    return rel == f"{s.folder}/{today_name}"


# ── data ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OrganizeResult:
    started_at: datetime
    finished_at: datetime
    mode: str
    candidate_paths: list[str]
    errors: list[tuple[str, str]]  # (path, reason)
    last_run_at: datetime | None = None


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


def _select_candidates(
    conn: sqlite3.Connection,
    *,
    scope: str | None = None,
    since: timedelta | None = None,
) -> tuple[list[Path], datetime | None]:
    """Pick the notes to review.

    Default ("incremental") scope uses per-note `last_reviewed_at`:
        a note is a candidate when it has never been reviewed OR its mtime
        is newer than the last time it was reviewed. Inbox/ notes are
        always candidates regardless.

    Other scopes:
      - "all" / "always_full" → every note in the vault.
      - "since_last_run" → legacy global cutoff.

    `since`, when set, takes precedence and only keeps files modified
    within that window (Inbox included). Used by the `--since 24h` CLI
    flag.
    """
    from app.organize import get_note_review_map

    settings = get_settings()
    requested = (scope or settings.organize.modified_since or "incremental").lower()
    full = requested in ("all", "always_full", "always", "full")
    legacy_global = requested in ("since_last_run", "since-last-run", "global")

    root = vault_root()
    last_run = _last_run_at(conn)
    review_map = get_note_review_map(conn)
    legacy_cutoff = 0.0 if last_run is None else last_run.timestamp()
    since_cutoff = (
        (datetime.now(timezone.utc) - since).timestamp() if since else None
    )

    in_inbox: list[Path] = []
    modified: list[Path] = []
    for p in root.rglob("*.md"):
        rel_parts = p.relative_to(root).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        rel = p.relative_to(root).as_posix()
        if _is_excluded(rel) or _is_todays_flat_journal(rel):
            continue
        if since_cutoff is not None:
            if p.stat().st_mtime > since_cutoff:
                modified.append(p)
            continue
        if rel.startswith("Inbox/"):
            in_inbox.append(p)
            continue
        if full:
            modified.append(p)
            continue
        if legacy_global:
            if p.stat().st_mtime > legacy_cutoff:
                modified.append(p)
            continue
        # Default ("incremental") scope. Cutoff = max of:
        #   - per-note last_reviewed_at (file already seen recently);
        #   - last_run_at, end of the previous apply run.
        # The last_run_at floor breaks the loop where the agent edits
        # file B as a side effect of processing A: B's mtime updates
        # during the run, but mtime ≤ last_run_at, so the next nightly
        # doesn't re-pick B as a candidate. Files modified AFTER the
        # previous run's end (manual edits, fresh captures) still come
        # through normally.
        last_reviewed = review_map.get(rel, 0.0)
        cutoff = max(last_reviewed, legacy_cutoff)
        if p.stat().st_mtime > cutoff:
            modified.append(p)

    in_inbox.sort(key=lambda x: x.stat().st_mtime)
    modified.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return (in_inbox + modified)[:MAX_NOTES_PER_RUN], last_run


# ── system prompt ────────────────────────────────────────────────────


_DEFAULT_SYSTEM_PROMPT = (
    "You are the user's nightly Obsidian vault organiser. Apply the "
    "policies you'll find in ORGANIZE.md (when present) and use INDEX.md "
    "/ USER.md / PREFERENCES.md as authoritative context. Use the "
    "`vault.*` tools (read / list / find / grep / edit_note / move / "
    "create_note / append / delete / replace_in_note / "
    "update_frontmatter / create_folder) to read the surrounding context "
    "and apply mutations directly. There is no JSON output to produce — "
    "your tool calls are the action. When you're done with the file you "
    "were handed (changes made or decided no change is needed), stop "
    "calling tools — your final text turn ends the session."
)


def _load_system_prompt() -> str:
    """Build the system prompt: ORGANIZE.md (when configured) followed
    by the in-vault context files (INDEX.md / USER.md / PREFERENCES.md).
    Falls back to a minimal default when ORGANIZE.md is missing."""
    s = get_settings()
    base = _DEFAULT_SYSTEM_PROMPT
    if s.obsidian.vault_path is not None:
        try:
            path = vault_root() / s.obsidian.organize_prompt_file
        except RuntimeError:
            path = None
        if path is not None and path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                log.warning("could not read organize prompt %s: %s", path, exc)
                text = ""
            if text.startswith("---\n"):
                end = text.find("\n---", 4)
                if end >= 0:
                    text = text[end + 4 :].lstrip("\n")
            body = text.strip()
            if body:
                base = body

    pieces: list[str] = [base]
    try:
        from app.vault import read_context_files

        for cf in read_context_files():
            content = cf.content.strip() or "(empty)"
            pieces.append(f"## {cf.label}\n\n{content}")
    except Exception:
        log.debug("organize: context files not loaded", exc_info=True)
    return "\n\n---\n\n".join(pieces)


# ── tool-call hard-exclusion guard ──────────────────────────────────


# Tools that mutate the vault. Read-only tools (vault.read / list /
# find / grep) are not guarded.
_WRITE_TOOLS = frozenset({
    "vault.edit_note",
    "vault.append",
    "vault.create_note",
    "vault.create_folder",
    "vault.move",
    "vault.replace_in_note",
    "vault.update_frontmatter",
    "vault.delete",
})

# Vault-wide "untouchable" prefixes for write ops — moving INTO or OUT
# of these is also forbidden.
_ABSOLUTE_BLOCKED_TOPS = frozenset({
    "Templates",
    "Excalidraw",
    "files",
    "Tracking",
})
_ABSOLUTE_BLOCKED_PREFIXES = (
    "Raw/Anki/",
    "Raw/Logger/Opencode/",
    "Raw/Review/",
)


def _writable(path: str, op: str) -> str | None:
    """Return None when `op` on `path` is allowed by ORGANIZE.md hard
    rules; otherwise return a human-readable refusal message that we
    surface to the agent so it can self-correct.

    `op` is one of: edit, append, replace, update_fm, delete, create,
    move_src, move_dst, create_folder.
    """
    if not path:
        return f"empty path for {op}"
    if path in _SCHEMA_PROTECTED_FILES:
        return (
            f"`{path}` is on the schema-files exclusion list "
            "(USER/PREFERENCES/AGENTS/INGEST/ORGANIZE/README/Cheatsheet). "
            "The agent never edits these — they're user-owned. "
            "INDEX.md, TODO.md, DONE.md and Wiki/log.md are NOT on this "
            "list and can be edited as side effects."
        )
    top = path.split("/", 1)[0]
    if top in _ABSOLUTE_BLOCKED_TOPS:
        # Sport Santé carve-out: the agent appends sport / fitness /
        # health activity traces verbatim to existing notes under
        # `Tracking/Sport Santé/`. This is the only writable subtree
        # under Tracking/, per ORGANIZE.md's "Tracking is untouched
        # carve-out". `move_dst` and `create` cover routing a Raw
        # input into Tracking/Sport Santé/; append/edit/replace cover
        # adding a trace to an existing dedicated note.
        if path.startswith("Tracking/Sport Santé/") and op in (
            "append", "edit", "replace", "update_fm",
            "create", "create_folder", "move_dst",
        ):
            return None
        return (
            f"`{path}` is under `{top}/`, which ORGANIZE.md marks as "
            "untouchable (read-only / append-only by the user). "
            "The only writable subtree under Tracking/ is "
            "`Tracking/Sport Santé/` — for sport / health activity "
            "traces appended verbatim to the existing dedicated note."
        )
    if any(path.startswith(p) for p in _ABSOLUTE_BLOCKED_PREFIXES):
        return f"`{path}` is in a hard-excluded subtree (Raw/Anki, Raw/Logger/Opencode, Raw/Review)."
    if path.startswith("Trash/"):
        # Trash is "append-only by you, prune-only by the user": new
        # files (move-into / create-into) are fine, but mutations on
        # existing Trash files and moves OUT of Trash are not.
        if op in ("create", "create_folder", "move_dst"):
            return None
        return (
            f"`{path}` is in Trash/. The agent may move FILES INTO "
            "Trash and create new files there, but never edit, "
            "append to, replace within, delete, or move out of Trash."
        )
    if path.startswith("Raw/WebClipper/"):
        # WebClipper clips are input data. The only legitimate action
        # is moving them OUT (to Trash). Editing in place is the
        # "gutting" failure mode we keep observing.
        if op == "move_src":
            return None
        return (
            f"`{path}` is a WebClipper clip. The only valid mutation is "
            "`vault.move` taking it OUT of Raw/WebClipper/ (typically to "
            "Trash/Raw/WebClipper/<filename>.md). Editing or appending "
            "to a clip in place strips the source — that is forbidden "
            "by ORGANIZE.md."
        )
    return None


def _check_tool_call(name: str, args: dict[str, object]) -> str | None:
    """If the tool call would violate a hard rule, return a refusal
    message; otherwise return None."""
    if name not in _WRITE_TOOLS:
        return None  # read-only tools never refused

    if name == "vault.move":
        src = str(args.get("src", "") or "")
        dst = str(args.get("dst", "") or "")
        if (msg := _writable(src, "move_src")):
            return f"vault.move refused on src: {msg}"
        if (msg := _writable(dst, "move_dst")):
            return f"vault.move refused on dst: {msg}"
        return None

    if name == "vault.create_note":
        folder = str(args.get("folder", "") or "")
        title = str(args.get("title", "") or "")
        path = f"{folder}/{title}.md" if folder else f"{title}.md"
        msg = _writable(path, "create")
        return f"vault.create_note refused: {msg}" if msg else None

    if name == "vault.create_folder":
        path = str(args.get("path", "") or "")
        msg = _writable(path, "create_folder")
        return f"vault.create_folder refused: {msg}" if msg else None

    # Default: tools whose write target is `args["path"]`.
    op_map = {
        "vault.edit_note": "edit",
        "vault.append": "append",
        "vault.replace_in_note": "replace",
        "vault.update_frontmatter": "update_fm",
        "vault.delete": "delete",
    }
    op = op_map.get(name, "edit")
    path = str(args.get("path", "") or "")
    msg = _writable(path, op)
    return f"{name} refused: {msg}" if msg else None


# ── agent loop ───────────────────────────────────────────────────────


async def _process_with_agent(
    rel: str,
    content: str,
    system_prompt: str,
    max_rounds: int,
) -> None:
    """Run an agent session for one candidate. The agent has the full
    `vault.*` toolset (filtered to write ops by `_check_tool_call`) and
    acts directly on the working tree. Returns when the agent stops
    calling tools; raises on stream errors or when `max_rounds` is
    exceeded.
    """
    from app.tools.registry import get_registry

    registry = get_registry()
    vault_tools = [t for t in registry.defs() if t.name.startswith("vault.")]

    body = content if len(content) <= MAX_NOTE_CHARS else (
        content[:MAX_NOTE_CHARS] + "\n\n[…content truncated]"
    )
    initial_text = (
        f"Process the vault note at `{rel}`. Read it, look at any "
        "related files / folders you need with the `vault.*` tools, "
        "and apply the appropriate changes via those tools. Apply "
        "ORGANIZE.md policies. When you're done with this file "
        "(changes made or decided not to change), stop calling tools "
        "— your final text turn ends the session.\n\n"
        f"## Initial content of `{rel}`\n\n```markdown\n{body}\n```"
    )

    history: list[Message] = [
        Message(role="user", content=[TextBlock(text=initial_text)])
    ]

    router = get_llm_router()
    provider = router.get(None)

    rounds_left = max_rounds
    while True:
        rounds_left -= 1
        if rounds_left < 0:
            raise RuntimeError(
                f"agent for {rel!r} hit max_rounds={max_rounds} without stopping"
            )

        assistant_message: Message | None = None
        async for ev in provider.stream(
            messages=history,
            tools=vault_tools,
            system=system_prompt,
        ):
            if ev.type == "error":
                raise RuntimeError(ev.error or "stream error")
            if ev.type == "message_done" and ev.message:
                assistant_message = ev.message

        if assistant_message is None:
            raise RuntimeError("agent produced no assistant message")

        history.append(assistant_message)

        pending = [
            b for b in assistant_message.content if isinstance(b, ToolUseBlock)
        ]
        if not pending:
            return  # agent stopped calling tools — session done

        results: list[ToolResultBlock] = []
        for call in pending:
            # Hard-rule guard: refuse mutations on excluded paths before
            # they reach the vault primitive. The error message tells
            # the agent which rule it tripped, so it can self-correct.
            refusal = _check_tool_call(call.name, call.input)
            if refusal:
                log.warning("organize: refused %s call: %s", call.name, refusal)
                results.append(
                    ToolResultBlock(
                        tool_use_id=call.id,
                        content=[TextBlock(text=refusal)],
                        is_error=True,
                    )
                )
                continue
            try:
                res = await registry.call(call.name, call.input)
                results.append(
                    ToolResultBlock(
                        tool_use_id=call.id,
                        content=res.content,
                        is_error=res.is_error,
                    )
                )
            except Exception as exc:
                log.exception("organize: tool dispatch failed: %s", call.name)
                results.append(
                    ToolResultBlock(
                        tool_use_id=call.id,
                        content=[TextBlock(text=f"Tool error: {exc!s}")],
                        is_error=True,
                    )
                )
        history.append(Message(role="user", content=list(results)))


# ── orchestration ────────────────────────────────────────────────────


async def run_organize(
    *,
    scope: str | None = None,
    since: timedelta | None = None,
) -> OrganizeResult:
    """Run the organize pass — one agent session per candidate.

    Returns structured stats; the wrapping nightly job in
    `scheduler.py` captures the actual changes via `git diff --stat`.
    Calling this directly outside the scheduler's `batch_session()` is
    valid but each tool call will then commit + push individually,
    which is rarely what you want.

    `scope` overrides `organize.modified_since` for this run only:
      - "all"             → every note.
      - "since_last_run"  → Inbox + recently-modified (legacy).
      - default           → per-note last_reviewed_at (incremental).

    `since` (timedelta) supersedes `scope` and only keeps files
    modified within that window. Used by `second-brain organize
    --since …`.

    In `dry-run` mode the per-note `last_reviewed_at` and the global
    `last_run_at` are NOT advanced — a dry-run is a preview, the
    scheduler stashes its changes at the end. `apply` mode advances
    both so the next nightly skips notes the LLM has already touched.
    """
    from app.organize import mark_note_reviewed

    settings = get_settings()
    started = datetime.now(timezone.utc)

    # Pre-step: import Raw/Anki/*.md flashcards into the local Anki
    # collection. Runs before the LLM organize pass so archived files
    # don't appear as candidates this run.
    if settings.anki.enabled:
        try:
            from app.anki import import_from_vault

            anki_result = await import_from_vault()
            if anki_result.did_run:
                log.info(
                    "anki vault import: %d created, %d skipped, %d archived",
                    len(anki_result.created),
                    len(anki_result.skipped),
                    len(anki_result.archived),
                )
        except Exception:
            log.exception("anki vault import failed (non-fatal)")

    conn = open_connection()
    try:
        candidates, last_run = _select_candidates(conn, scope=scope, since=since)
    finally:
        conn.close()

    is_apply = settings.organize.mode == "apply"
    candidate_paths = [
        p.relative_to(vault_root()).as_posix() for p in candidates
    ]
    errors: list[tuple[str, str]] = []

    if candidates:
        system_prompt = _load_system_prompt()
        # Generous round budget — vault tools are cheap and the agent
        # may legitimately need many calls (read context, edit, move,
        # update frontmatter on neighbours). Bound it so a runaway
        # agent doesn't loop forever.
        max_rounds = max(20, settings.llm.max_tool_rounds * 3)

        for p, rel in zip(candidates, candidate_paths):
            try:
                content = p.read_text(encoding="utf-8")
            except OSError as exc:
                errors.append((rel, f"read error: {exc}"))
                continue
            log.info("organize: agent session for %s", rel)
            try:
                await _process_with_agent(rel, content, system_prompt, max_rounds)
            except Exception as exc:
                log.exception("organize: agent session failed for %s", rel)
                errors.append((rel, f"agent error: {exc}"))
                continue

            # Mark reviewed only on apply runs — a dry-run is a preview
            # that gets stashed, so the next nightly should re-process
            # the same files until the user runs an apply.
            if is_apply:
                conn = open_connection()
                try:
                    mark_note_reviewed(conn, rel)
                finally:
                    conn.close()

    finished = datetime.now(timezone.utc)
    if is_apply:
        # Only advance the global cursor on apply runs; dry-runs do not
        # count as "execution complete".
        conn = open_connection()
        try:
            _record_run(conn, finished)
        finally:
            conn.close()

    return OrganizeResult(
        started_at=started,
        finished_at=finished,
        mode=settings.organize.mode,
        candidate_paths=candidate_paths,
        errors=errors,
        last_run_at=last_run,
    )
