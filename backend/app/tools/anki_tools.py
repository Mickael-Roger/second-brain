"""anki.* — read access + add for the LLM.

Exposes a deliberately small surface:
  - anki.list_decks  — browse decks
  - anki.list_notes  — browse notes (filter by deck and/or text search)
  - anki.read_note   — full content of one note
  - anki.add_note    — add a flashcard (basic or basic_reverse)

Decks are not created here; the user manages decks in Anki desktop /
AnkiWeb and they appear locally after sync_download. The LLM cannot
update or delete notes — those flows live entirely in the user's
hands (Anki desktop, AnkiWeb, or vault file edits).

Tools require anki.enabled = true in config.yml; when disabled they
return a clear error rather than silently failing.
"""

from __future__ import annotations

import logging
from typing import Any

from app.anki import (
    NOTETYPE_BASIC,
    NOTETYPE_BASIC_REVERSE,
    add_note,
    get_note,
    list_decks,
    list_notes,
    open_anki,
)
from app.config import get_settings

from .registry import ToolRegistry, text_result

log = logging.getLogger(__name__)


_NOTETYPE_CHOICES = (NOTETYPE_BASIC, NOTETYPE_BASIC_REVERSE)


def _ensure_enabled() -> str | None:
    if not get_settings().anki.enabled:
        return "Anki is disabled in config.yml (set anki.enabled = true)."
    return None


def _format_tags(tags: list[str]) -> str:
    return " ".join(tags) if tags else "(none)"


# ── Handlers ─────────────────────────────────────────────────────────


async def _list_decks(_args: dict[str, Any]):
    if (err := _ensure_enabled()):
        return text_result(err, is_error=True)
    conn = open_anki()
    try:
        decks = list_decks(conn)
    finally:
        conn.close()
    if not decks:
        return text_result("(no decks)")
    lines = ["Decks:"]
    for d in decks:
        lines.append(
            f"- [{d.id}] {d.name}: {d.card_count} card(s), "
            f"{d.due_count} due, {d.new_count} new"
        )
    return text_result("\n".join(lines))


async def _list_notes(args: dict[str, Any]):
    if (err := _ensure_enabled()):
        return text_result(err, is_error=True)
    deck_id_raw = args.get("deck_id")
    deck_id = int(deck_id_raw) if deck_id_raw is not None else None
    search = args.get("search")
    limit = max(1, min(int(args.get("limit", 50)), 500))
    conn = open_anki()
    try:
        notes = list_notes(conn, deck_id=deck_id, search=search, limit=limit)
    finally:
        conn.close()
    if not notes:
        return text_result("(no notes match)")
    lines: list[str] = []
    for n in notes:
        front = n.fields[0] if n.fields else ""
        back = n.fields[1] if len(n.fields) > 1 else ""
        lines.append(
            f"- [{n.id}] ({n.notetype}, deck={n.deck_id}, cards={len(n.cards)}) "
            f"{front[:80]} | {back[:80]}"
        )
    return text_result("\n".join(lines))


async def _read_note(args: dict[str, Any]):
    if (err := _ensure_enabled()):
        return text_result(err, is_error=True)
    try:
        note_id = int(args["note_id"])
    except (KeyError, TypeError, ValueError):
        return text_result("`note_id` is required (integer).", is_error=True)
    conn = open_anki()
    try:
        n = get_note(conn, note_id)
    finally:
        conn.close()
    if n is None:
        return text_result(f"note {note_id} not found", is_error=True)
    front = n.fields[0] if n.fields else ""
    back = n.fields[1] if len(n.fields) > 1 else ""
    parts = [
        f"Note [{n.id}]",
        f"Notetype: {n.notetype}",
        f"Deck: {n.deck_id}",
        f"Cards: {len(n.cards)} (ids: {', '.join(str(c.id) for c in n.cards)})",
        f"Tags: {_format_tags(n.tags)}",
        "",
        f"Front:\n{front}",
        "",
        f"Back:\n{back}",
    ]
    return text_result("\n".join(parts))


async def _add_note(args: dict[str, Any]):
    if (err := _ensure_enabled()):
        return text_result(err, is_error=True)
    try:
        deck_id = int(args["deck_id"])
    except (KeyError, TypeError, ValueError):
        return text_result("`deck_id` is required (integer).", is_error=True)
    notetype = str(args.get("notetype", NOTETYPE_BASIC))
    if notetype not in _NOTETYPE_CHOICES:
        return text_result(
            f"`notetype` must be one of {_NOTETYPE_CHOICES}", is_error=True,
        )
    front = str(args.get("front", "")).strip()
    back = str(args.get("back", "")).strip()
    if not front or not back:
        return text_result(
            "Both `front` and `back` are required and non-empty.", is_error=True,
        )
    raw_tags = args.get("tags") or []
    if isinstance(raw_tags, str):
        tags = [t for t in raw_tags.split() if t]
    else:
        tags = [str(t).strip() for t in raw_tags if str(t).strip()]

    conn = open_anki()
    try:
        n = add_note(
            conn, deck_id=deck_id, notetype=notetype,
            fields=[front, back], tags=tags,
        )
    except KeyError:
        return text_result(f"deck {deck_id} not found", is_error=True)
    except ValueError as exc:
        return text_result(str(exc), is_error=True)
    finally:
        conn.close()
    return text_result(
        f"Added note [{n.id}] in deck {n.deck_id} "
        f"({n.notetype}, {len(n.cards)} card(s))."
    )


# ── Registration ─────────────────────────────────────────────────────


def register_all(reg: ToolRegistry) -> None:
    reg.register(
        "anki.list_decks",
        "List all Anki decks in the local collection. Returns deck id, "
        "name, and card counts (total / due today / new). Decks are "
        "managed externally (Anki desktop / AnkiWeb); to create a deck, "
        "make it there and wait for the next sync.",
        {"type": "object", "properties": {}},
        _list_decks,
    )
    reg.register(
        "anki.list_notes",
        "List Anki notes, optionally filtered by deck and/or text "
        "search across the front/back fields. Returns one line per note "
        "with id, notetype, deck id, card count, and a truncated preview.",
        {
            "type": "object",
            "properties": {
                "deck_id": {
                    "type": "integer",
                    "description": "Restrict to one deck. Omit for all decks.",
                },
                "search": {
                    "type": "string",
                    "description": "Substring to match against any field.",
                },
                "limit": {
                    "type": "integer",
                    "default": 50, "minimum": 1, "maximum": 500,
                },
            },
        },
        _list_notes,
    )
    reg.register(
        "anki.read_note",
        "Read one Anki note's full content: notetype, deck, tags, "
        "and the Front/Back fields verbatim.",
        {
            "type": "object",
            "properties": {
                "note_id": {"type": "integer", "description": "Note id from anki.list_notes."},
            },
            "required": ["note_id"],
        },
        _read_note,
    )
    reg.register(
        "anki.add_note",
        "Add a flashcard to a deck. `notetype` is either 'basic' "
        "(creates one card front→back) or 'basic_reverse' (creates "
        "two cards, front→back AND back→front). Both `front` and "
        "`back` must be non-empty. `tags` is optional. The deck must "
        "already exist (use anki.list_decks to find its id).",
        {
            "type": "object",
            "properties": {
                "deck_id": {"type": "integer", "description": "Target deck id."},
                "notetype": {
                    "type": "string",
                    "enum": list(_NOTETYPE_CHOICES),
                    "default": NOTETYPE_BASIC,
                    "description": "'basic' = one card; 'basic_reverse' = two cards.",
                },
                "front": {"type": "string", "description": "Front field (the prompt)."},
                "back":  {"type": "string", "description": "Back field (the answer)."},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags. Accepts a list or a space-separated string.",
                },
            },
            "required": ["deck_id", "front", "back"],
        },
        _add_note,
    )
