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
import unicodedata
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import datetime, timedelta, timezone
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

# Hard exclusions matching ORGANIZE.md "What you NEVER touch" section.
# A candidate path is skipped if any of these match:
#   * its first path component equals an entry in EXCLUDED_TOP_DIRS, OR
#   * any prefix of the path matches EXCLUDED_PREFIXES, OR
#   * the full vault-relative path is in EXCLUDED_FILES, OR
#   * the path is today's flat-journal note (handled separately).
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
)
EXCLUDED_FILES = frozenset({
    "USER.md",
    "PREFERENCES.md",
    "INDEX.md",
    "AGENTS.md",
    "INGEST.md",
    "ORGANIZE.md",
    "README.md",
    "Cheatsheet.md",
    "Raw/Inbox/Notes.md",
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
        always candidates regardless. This is much sharper than the old
        global `last_run_at` cutoff — a note you edited yesterday is
        reviewed even if you've run the organizer once since you wrote
        an unrelated note last week.

    Other scopes:
      - "all" / "always_full" → every note in the vault.
      - "since_last_run" → legacy global cutoff (kept for cron back-compat).

    `since`, when set, takes precedence over `scope`: only files modified
    within that window are considered (Inbox included). Used by the
    `--since 24h` CLI flag for ad-hoc debug runs.
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
        # `--since` is a hard time-window filter that overrides everything
        # below — applied uniformly (Inbox included) so the operator gets
        # exactly what they asked for.
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
        # Default: per-note last_reviewed_at.
        last_reviewed = review_map.get(rel, 0.0)
        if p.stat().st_mtime > last_reviewed:
            modified.append(p)

    in_inbox.sort(key=lambda x: x.stat().st_mtime)
    modified.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return (in_inbox + modified)[:MAX_NOTES_PER_RUN], last_run


# ── prompt + parser ──────────────────────────────────────────────────


# The user's ORGANIZE.md describes intent, philosophy, and end-state in
# prose. It deliberately doesn't talk about wire format. We append this
# block to whatever ORGANIZE.md provides so the LLM always emits a
# parseable JSON proposal — without it, the LLM responds conversationally
# ("I'd merge this into…") and parse_proposal fails for every note.
_JSON_OUTPUT_CONTRACT = """\
## Output contract (strict, machine-parsed)

You receive ONE note per call. The orchestrator processes notes one
at a time; you cannot edit other files via this response — anything
cross-cutting goes in `notes` so the user can act later.

Return ONE JSON object and nothing else: no preamble, no commentary,
no markdown code fences around it. Schema:

```json
{
  "move_to": "<vault-relative .md path>" | null,
  "tags": ["tag1", "tag2"] | null,
  "wikilinks": [{"target": "Note Name", "context": "why"}] | null,
  "refactor": "<the full rewritten note content, including frontmatter>" | null,
  "notes": "<short prose comment, optional>" | null
}
```

Rules:
- Use `null` for fields you don't want to change.
- `move_to` only when the note clearly belongs in a different folder per
  INDEX.md.
- `tags` only when the existing frontmatter is missing or wrong.
- `refactor` is the FULL new file content (frontmatter included), used
  only when content needs rewriting per ORGANIZE.md (not for moves).
- `wikilinks` are guidance — the system does NOT auto-insert them.
- If the note is already in good shape, return all-null fields. **This
  is the correct answer for many notes per pass — not a failure.**

Do not respond with prose explanations even if the note is hard to
classify; the JSON object (with `notes` filled in) is the only valid
output channel.

### Hard rules the orchestrator enforces (proposals violating these are rejected)

1. **Raw/WebClipper/ clips are input, never edited in place.** If the
   note path starts with `Raw/WebClipper/`, you may NOT set `refactor`
   without also setting `move_to` to a path OUTSIDE `Raw/WebClipper/`
   (typically `Trash/Raw/WebClipper/<filename>.md`). A refactor that
   gutifies or shortens a WebClipper clip while leaving its path
   unchanged WILL be rejected. If you cannot do the proper move-and-
   archive on this pass, set both `refactor` and `move_to` to null
   and use `notes` to record what should happen later.

2. **Filenames in `move_to` are ASCII only.** No accents, no diacritics,
   no non-ASCII glyphs in the BASENAME of the move_to path. Examples:
   `Wiki/Foo/Sécurité réseau.md` → must be `Wiki/Foo/Securite reseau.md`.
   The orchestrator silently folds accents in your `move_to` if it
   detects them, so prefer ASCII directly. The CONTENT (note body /
   refactor) keeps original accents — this rule is about filenames only.

3. **No empty refactors.** A `refactor` field set to "" or to content
   shorter than half the original file size with no `move_to` will be
   rejected as a "gutting" attempt. Either ship a real rewrite or null
   the field.
"""


_DEFAULT_SYSTEM_PROMPT = (
    "You are reviewing one note from the user's Obsidian vault. Use "
    "INDEX.md (the vault's structural map), USER.md (facts about the "
    "user), and PREFERENCES.md (operating preferences) — all three "
    "provided below — as authoritative context for your proposals.\n\n"
    + _JSON_OUTPUT_CONTRACT
)


def _load_system_prompt() -> str:
    """Load the Organize task's system prompt from the vault.

    Reads `<vault>/<obsidian.organize_prompt_file>` (default `ORGANIZE.md`),
    strips an optional YAML frontmatter block, and ALWAYS appends
    `_JSON_OUTPUT_CONTRACT` so the LLM emits a parseable proposal even
    when ORGANIZE.md is written in pure prose. Falls back to the built-in
    default when the file is missing, empty, or the vault is unconfigured.
    """
    s = get_settings()
    if s.obsidian.vault_path is None:
        return _DEFAULT_SYSTEM_PROMPT
    try:
        path = vault_root() / s.obsidian.organize_prompt_file
    except RuntimeError:
        return _DEFAULT_SYSTEM_PROMPT
    if not path.is_file():
        return _DEFAULT_SYSTEM_PROMPT
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("could not read organize prompt %s: %s", path, exc)
        return _DEFAULT_SYSTEM_PROMPT
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end >= 0:
            text = text[end + 4 :].lstrip("\n")
    body = text.strip()
    if not body:
        return _DEFAULT_SYSTEM_PROMPT
    return f"{body}\n\n---\n\n{_JSON_OUTPUT_CONTRACT}"


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
        rel = p.relative_to(root).as_posix()
        if _is_excluded(rel):
            continue
        paths.append(rel)
        if len(paths) >= MAX_VAULT_PATHS_IN_PROMPT:
            break
    return paths


def _build_user_prompt(
    note_path: str,
    content: str,
    context_files: list[Any],
    paths: list[str],
    *,
    extra_instruction: str | None = None,
) -> str:
    body = content
    if len(body) > MAX_NOTE_CHARS:
        body = body[:MAX_NOTE_CHARS] + "\n\n[…content truncated for review]"

    parts: list[str] = [f"## Note path\n{note_path}"]
    for cf in context_files:
        parts.append(f"## {cf.label}\n```\n{cf.content.strip() or '(empty)'}\n```")
    parts.append("## Vault paths (sample)\n" + "\n".join(f"- {p}" for p in paths))
    parts.append(f"## Note content\n```markdown\n{body}\n```")
    if extra_instruction and extra_instruction.strip():
        parts.append(
            "## Extra instructions for THIS run\n"
            + extra_instruction.strip()
        )
    return "\n\n".join(parts)


async def _propose(
    note_path: str,
    content: str,
    context_files: list[Any],
    paths: list[str],
    system_prompt: str,
    *,
    extra_instruction: str | None = None,
) -> Proposal:
    user = _build_user_prompt(
        note_path, content, context_files, paths, extra_instruction=extra_instruction
    )
    msgs = [Message(role="user", content=[TextBlock(text=user)])]
    raw = await complete(system_prompt, msgs)
    return parse_proposal(note_path, raw)


# ── apply ────────────────────────────────────────────────────────────


def _apply_tags(content: str, new_tags: list[str]) -> str:
    """Rewrite a note's frontmatter to set `tags = new_tags`. Preserves
    existing frontmatter for other keys; adds frontmatter if missing."""
    post = frontmatter.loads(content)
    post["tags"] = new_tags
    return frontmatter.dumps(post)


_WEBCLIPPER_PREFIX = "Raw/WebClipper/"


def _ascii_fold_basename(path: str) -> str:
    """Strip diacritics from the BASENAME of a vault-relative path.
    Folder portions are left alone (existing folders may be accented and
    we shouldn't move files across renamed dirs as a side effect of one
    proposal). Spaces and hyphens are preserved.

    Example: 'Wiki/Foo/Sécurité réseau.md' → 'Wiki/Foo/Securite reseau.md'.
    """
    head, _, tail = path.rpartition("/")
    nfkd = unicodedata.normalize("NFKD", tail)
    folded = "".join(c for c in nfkd if not unicodedata.combining(c))
    folded = folded.encode("ascii", "ignore").decode("ascii")
    if not folded:
        return path  # fold produced nothing; leave the original alone
    return f"{head}/{folded}" if head else folded


def _validate_proposal(proposal: Proposal) -> str | None:
    """Pre-flight checks. Return an error string when the proposal would
    violate a hard rule the LLM is supposed to honor but sometimes
    doesn't. Hard rules belong in code, not just the prompt — the LLM
    cannot be relied on to enforce them."""
    src = proposal.path
    refactor = proposal.refactor
    move_to = proposal.move_to

    # Rule 1: a Raw/WebClipper/ clip is INPUT, not a file you edit. The
    # only legal mutations are (a) move it to Trash/Raw/WebClipper/ with
    # a refactor that prepends a trash callout to the verbatim body, or
    # (b) leave it alone. A refactor without a move out of WebClipper/
    # always means "the LLM gutted the source in place" — we saw this
    # happen and it loses data.
    if src.startswith(_WEBCLIPPER_PREFIX) and refactor is not None:
        if not move_to or move_to.startswith(_WEBCLIPPER_PREFIX):
            return (
                "WebClipper guard: refactor is set but move_to does not "
                "take the file out of Raw/WebClipper/. Refactor-in-place "
                "would gut the source. Either propose move_to outside "
                "Raw/WebClipper/ (typically Trash/Raw/WebClipper/<file>.md) "
                "or null both fields."
            )

    # Rule 2: a refactor that drastically shrinks the body without a move
    # is suspicious — the LLM is probably "clearing the source" instead
    # of doing real work. Only fail when no move_to is set (a move can
    # legitimately ship a leaner version to its destination).
    if refactor is not None and move_to is None:
        # Use a 50% shrink threshold; legitimate refactors stay close in
        # size, gutting drops content to near-zero.
        try:
            original = (vault_root() / src).read_text(encoding="utf-8")
        except OSError:
            original = ""
        if original and len(refactor) < max(64, len(original) // 2):
            return (
                "Anti-gut guard: refactor would drop "
                f"{len(original) - len(refactor)} chars "
                f"(from {len(original)} to {len(refactor)}) without "
                "moving the file. Set move_to or null the refactor."
            )

    return None


async def _apply_proposal(proposal: Proposal) -> AppliedNote:
    """Best-effort apply. Order: refactor → tags → move. Each step is its
    own commit (vault primitives wrap their own git transaction)."""
    applied = AppliedNote(path=proposal.path)

    # Hard-rule guards: catch the cases ORGANIZE.md describes but the LLM
    # sometimes ignores. Surfacing them as apply-time errors keeps the
    # bad proposals out of the working tree and the diff stat stays clean.
    guard_error = _validate_proposal(proposal)
    if guard_error:
        applied.error = guard_error
        return applied

    # ASCII-fold the destination basename when needed. Existing accented
    # files keep their names; only newly proposed move targets are
    # normalised. This is a silent fix-up, not an error — the user wants
    # ASCII filenames per ORGANIZE.md and the LLM is unreliable at it.
    if proposal.move_to:
        folded = _ascii_fold_basename(proposal.move_to)
        if folded != proposal.move_to:
            log.info(
                "ascii-folded move_to: %r → %r", proposal.move_to, folded
            )
            proposal.move_to = folded

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


async def run_organize(
    *,
    extra_instruction: str | None = None,
    scope: str | None = None,
    since: timedelta | None = None,
) -> OrganizeResult:
    """Execute one organize pass.

    Runs from the nightly cron or the `second-brain organize` CLI —
    there is no webapp UI for it. Returns an in-memory `OrganizeResult`
    that the email renderer consumes.

    `extra_instruction`, when given, is appended to every per-note user
    prompt in this run.

    `scope` overrides `organize.modified_since` for this run only:
        "all"             → every note.
        "since_last_run"  → Inbox + recently-modified (legacy global cutoff).
        default           → per-note last_reviewed_at (incremental).

    `since`, when set, restricts the candidate list to files modified
    within that window and supersedes `scope`. Used by the
    `second-brain organize --since …` CLI flag.

    In `dry-run` mode the per-note `last_reviewed_at` and the global
    `last_run_at` are NOT advanced — a dry-run is a preview, not an
    "execution complete". The next nightly cron will see the same
    candidates again until the user re-runs in `apply` mode.
    """
    from app.organize import mark_note_reviewed

    settings = get_settings()
    started = datetime.now(timezone.utc)

    # Pre-step: import Raw/Anki/*.md flashcards into the local Anki
    # collection and sync with AnkiWeb. No-op when anki is disabled or
    # no pending files exist. Runs before the LLM organize pass so
    # archived files don't appear as candidates this run.
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

    proposals: list[Proposal] = []
    skipped: list[tuple[str, str]] = []
    if candidates:
        from app.vault import read_context_files

        context_files = read_context_files()  # INDEX, USER, PREFERENCES (each optional)
        paths = _vault_paths_sample()
        # Load the Organize system prompt once per run so updates to the
        # vault file (default ORGANIZE.md) take effect on the next nightly
        # without a restart.
        system_prompt = _load_system_prompt()
        for p in candidates:
            rel = p.relative_to(vault_root()).as_posix()
            try:
                content = p.read_text(encoding="utf-8")
            except OSError as exc:
                skipped.append((rel, f"read error: {exc}"))
                continue
            try:
                proposal = await _propose(
                    rel,
                    content,
                    context_files,
                    paths,
                    system_prompt,
                    extra_instruction=extra_instruction,
                )
                proposals.append(proposal)
                # Mark reviewed only when we're actually going to commit
                # — a dry-run is a preview that gets stashed at the end,
                # so the next nightly should re-propose the same files
                # until the user runs an apply.
                if is_apply and not proposal.parse_error:
                    conn = open_connection()
                    try:
                        mark_note_reviewed(conn, rel)
                    finally:
                        conn.close()
            except Exception as exc:
                log.exception("organize: proposal failed for %s", rel)
                skipped.append((rel, f"LLM error: {exc}"))

    # Always apply — the dry-run vs apply distinction is now decided at
    # the git boundary (stash vs commit), not here. The caller wraps us
    # in `batch_session()` so vault primitives don't commit individually.
    applied: list[AppliedNote] = []
    for proposal in proposals:
        if proposal.parse_error or proposal.is_no_op:
            continue
        try:
            applied.append(await _apply_proposal(proposal))
        except Exception as exc:
            log.exception("apply failed for %s", proposal.path)
            applied.append(AppliedNote(path=proposal.path, error=f"apply: {exc}"))

    finished = datetime.now(timezone.utc)
    if is_apply:
        # Only advance the global `last_run_at` cursor on apply runs —
        # the legacy `since_last_run` scope keys off this and dry-runs
        # shouldn't make the next nightly skip files just because a
        # preview happened in between.
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
        last_run_at=last_run,
    )


# ── markdown rendering ───────────────────────────────────────────────


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
    """Operational summary of the run. The actual file changes are
    surfaced via `git diff --stat` in the nightly wrapper — this report
    intentionally does NOT enumerate them per file."""
    actionable = sum(1 for p in proposals if not p.is_no_op and not p.parse_error)
    parse_failed = sum(1 for p in proposals if p.parse_error)
    applied_ok = sum(1 for a in applied if not a.error and a.operations)
    apply_failed = sum(1 for a in applied if a.error)

    # "Skipped" = every considered note that did NOT end up modified.
    # Union of: read/LLM errors, parse failures, no-op proposals, apply
    # errors, apply no-ops. Paths only — the user just wants to see what
    # was looked at and left alone.
    modified_paths = {a.path for a in applied if not a.error and a.operations}
    considered_paths: list[str] = [p.path for p in proposals]
    considered_paths.extend(path for path, _ in skipped)
    skipped_paths = sorted({p for p in considered_paths if p not in modified_paths})

    lines = [
        f"## Organize — {started.date().isoformat()}",
        "",
        f"Started:  {started.isoformat()}",
        f"Finished: {finished.isoformat()} ({(finished - started).total_seconds():.1f}s)",
        f"Last run: {last_run.isoformat() if last_run else '(first run)'}",
        f"Notes considered: {total}",
        f"Modified: {applied_ok}",
        f"Skipped: {len(skipped_paths)}",
        f"  · proposals (actionable): {actionable}",
        f"  · apply errors: {apply_failed}",
        f"  · LLM parse failures: {parse_failed}",
        f"  · LLM/read errors: {len(skipped)}",
        "",
    ]
    if skipped_paths:
        lines.append("### Skipped")
        lines.append("")
        for p in skipped_paths:
            lines.append(f"- `{p}`")
        lines.append("")
    if skipped:
        lines.append("### Errors (subset of skipped)")
        lines.append("")
        for path, reason in skipped:
            lines.append(f"- `{path}` — {reason}")
        lines.append("")
    return "\n".join(lines)
