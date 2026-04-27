"""Vault-driven flashcard import.

Drops in flashcards from `Raw/Anki/*.md` into the local Anki collection
and syncs them to AnkiWeb. Run as part of the nightly organize job.

If `Raw/Anki/` is empty (or doesn't exist), this is a no-op — neither
sync runs.

# File format

Each markdown file under `Raw/Anki/` is one flashcard:

    ---
    deck: French Vocabulary
    notetype: basic_reverse        # optional, default 'basic'
    tags: [vocab, food]            # optional
    ---

    # Front

    What is "apple" in French?

    # Back

    pomme

The deck must already exist locally (managed via Anki desktop /
AnkiWeb and pulled in by `sync_download`). If it doesn't, the file
is skipped and left in place for the user to fix.

# Pipeline

  1. List `Raw/Anki/*.md`. If empty, return.
  2. `sync_download` — pull the latest state from AnkiWeb so the
     deck list is fresh.
  3. Parse each file; for valid ones, `add_note` to the local
     collection.
  4. `sync_upload` — push the new cards to AnkiWeb.
  5. Move each successfully-imported file to `Trash/Raw/Anki/`.
     Failed files stay in `Raw/Anki/` for the next run.

The two-phase sync (download → mutate → upload) is the safest order
for a single-user system: we get the latest decks before importing,
and we only push once the imports are atomically committed locally.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from app.anki import (
    NOTETYPE_BASIC,
    NOTETYPE_BASIC_REVERSE,
    add_note,
    find_deck_by_name,
    open_anki,
    sync_download,
    sync_upload,
)
from app.config import get_settings
from app.vault import create_folder, move_note, vault_root

log = logging.getLogger(__name__)


VAULT_ANKI_DIR = "Raw/Anki"
VAULT_TRASH_DIR = "Trash/Raw/Anki"

_NOTETYPE_CHOICES = (NOTETYPE_BASIC, NOTETYPE_BASIC_REVERSE)


# ── Parsing ──────────────────────────────────────────────────────────


@dataclass(slots=True)
class ParsedFlashcard:
    rel_path: str           # vault-relative source path
    deck: str
    notetype: str
    tags: list[str]
    front: str
    back: str


@dataclass(slots=True)
class ImportResult:
    created: list[str] = field(default_factory=list)         # rel paths created
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (rel, reason)
    archived: list[str] = field(default_factory=list)        # moved to Trash
    sync_download_error: str | None = None
    sync_upload_error: str | None = None

    @property
    def did_run(self) -> bool:
        """Did the pipeline actually execute (i.e. there was at least
        one .md to process)?"""
        return bool(self.created or self.skipped)


_HEADING_RE = re.compile(r"(?im)^\s*#\s+(front|back)\s*$")


def _split_front_back(body: str) -> tuple[str | None, str | None]:
    """Extract `# Front` and `# Back` sections from the body.

    Returns (front, back), each None if its heading is missing.
    Sections run from the heading line until the next H1 or EOF.
    """
    matches = list(_HEADING_RE.finditer(body))
    if not matches:
        return None, None

    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(1).lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[name] = body[start:end].strip()
    return sections.get("front"), sections.get("back")


def parse_flashcard_file(path: Path, rel_path: str) -> ParsedFlashcard:
    """Parse one Raw/Anki/*.md file. Raises ValueError on invalid input."""
    try:
        post = frontmatter.load(path)
    except Exception as exc:
        raise ValueError(f"frontmatter parse error: {exc}") from exc

    meta = post.metadata or {}
    deck = meta.get("deck")
    if not deck or not isinstance(deck, str) or not deck.strip():
        raise ValueError("frontmatter is missing required `deck` field")

    notetype = meta.get("notetype", NOTETYPE_BASIC)
    if not isinstance(notetype, str):
        raise ValueError("`notetype` must be a string")
    notetype = notetype.strip().lower()
    if notetype not in _NOTETYPE_CHOICES:
        raise ValueError(
            f"`notetype` must be one of {_NOTETYPE_CHOICES}, got {notetype!r}"
        )

    raw_tags = meta.get("tags", []) or []
    if isinstance(raw_tags, str):
        tags = [t for t in raw_tags.split() if t]
    elif isinstance(raw_tags, list):
        tags = [str(t).strip() for t in raw_tags if str(t).strip()]
    else:
        raise ValueError("`tags` must be a list or a string")

    front, back = _split_front_back(post.content)
    if not front or not back:
        raise ValueError(
            "body must contain `# Front` and `# Back` H1 sections, both non-empty"
        )

    return ParsedFlashcard(
        rel_path=rel_path,
        deck=deck.strip(),
        notetype=notetype,
        tags=tags,
        front=front,
        back=back,
    )


# ── Pipeline ─────────────────────────────────────────────────────────


def _list_pending_files() -> list[Path]:
    """Return absolute paths of every .md directly inside Raw/Anki/.
    Returns [] if the folder doesn't exist."""
    settings = get_settings()
    if settings.obsidian.vault_path is None:
        return []
    folder = vault_root() / VAULT_ANKI_DIR
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix == ".md")


async def import_from_vault() -> ImportResult:
    """Run the full pipeline: scan, sync down, add, sync up, archive.

    Idempotent and safe to call when nothing is pending — returns
    an empty result with `did_run=False`.
    """
    result = ImportResult()
    settings = get_settings()
    if not settings.anki.enabled:
        log.debug("anki vault import: feature disabled, skipping")
        return result

    files = _list_pending_files()
    if not files:
        return result

    log.info("anki vault import: %d pending file(s) in %s", len(files), VAULT_ANKI_DIR)

    # 1) Pre-sync: pull AnkiWeb's latest state so we know about decks the
    # user may have created on another device.
    try:
        await sync_download()
    except Exception as exc:
        log.warning("anki vault import: pre-sync download failed: %s", exc)
        result.sync_download_error = str(exc)
        # Continue anyway — the local collection might already be fine.

    # 2) Parse + add notes locally. We hold the connection across all
    # files so we can issue many adds without re-opening.
    parsed: list[ParsedFlashcard] = []
    root = vault_root()
    for abs_path in files:
        rel = abs_path.relative_to(root).as_posix()
        try:
            parsed.append(parse_flashcard_file(abs_path, rel))
        except ValueError as exc:
            log.warning("anki vault import: skip %s: %s", rel, exc)
            result.skipped.append((rel, str(exc)))

    if parsed:
        conn = open_anki()
        try:
            for fc in parsed:
                deck = find_deck_by_name(conn, fc.deck)
                if deck is None:
                    msg = (
                        f"deck {fc.deck!r} not found locally — create it in "
                        "Anki desktop / AnkiWeb and sync first"
                    )
                    log.warning("anki vault import: skip %s: %s", fc.rel_path, msg)
                    result.skipped.append((fc.rel_path, msg))
                    continue
                try:
                    note = add_note(
                        conn,
                        deck_id=deck.id,
                        notetype=fc.notetype,
                        fields=[fc.front, fc.back],
                        tags=fc.tags,
                    )
                    log.info(
                        "anki vault import: created note %d in deck %r from %s",
                        note.id, fc.deck, fc.rel_path,
                    )
                    result.created.append(fc.rel_path)
                except (KeyError, ValueError) as exc:
                    log.warning("anki vault import: add_note failed for %s: %s", fc.rel_path, exc)
                    result.skipped.append((fc.rel_path, str(exc)))
        finally:
            conn.close()

    # 3) Post-sync: push our new notes to AnkiWeb.
    if result.created:
        try:
            await sync_upload()
        except Exception as exc:
            log.warning("anki vault import: post-sync upload failed: %s", exc)
            result.sync_upload_error = str(exc)
            # We still archive the files — local state has the cards;
            # the next nightly sync_upload (or a manual one) will catch
            # up. Leaving them in Raw/Anki would re-create them on the
            # next run, which is worse.

    # 4) Archive imported files to Trash/Raw/Anki/, creating the folder
    # if needed.
    if result.created:
        try:
            await create_folder(VAULT_TRASH_DIR)
        except Exception as exc:
            log.exception("anki vault import: cannot create %s: %s", VAULT_TRASH_DIR, exc)
            return result

        for rel in result.created:
            src = rel
            name = Path(rel).name
            dst = f"{VAULT_TRASH_DIR}/{name}"
            try:
                await move_note(src, dst, message=f"anki: archive imported flashcard {name}")
                result.archived.append(dst)
            except FileExistsError:
                # Name clash in Trash: append a numeric suffix and retry.
                stem = Path(name).stem
                suffix = Path(name).suffix
                for i in range(2, 100):
                    alt = f"{VAULT_TRASH_DIR}/{stem} ({i}){suffix}"
                    try:
                        await move_note(src, alt, message=f"anki: archive imported flashcard {name}")
                        result.archived.append(alt)
                        break
                    except FileExistsError:
                        continue
                else:
                    log.warning("anki vault import: could not archive %s — too many name clashes", rel)
            except Exception as exc:
                log.exception("anki vault import: archive failed for %s: %s", rel, exc)

    return result
