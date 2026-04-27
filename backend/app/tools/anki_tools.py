"""anki.* — deck and card management tools for the LLM.

Exposes deck and note CRUD minus delete. The LLM can:
  - browse decks (anki.list_decks)
  - create / rename decks (anki.create_deck, anki.rename_deck)
  - browse notes (anki.list_notes, anki.read_note)
  - add / edit notes (anki.add_note, anki.update_note)

Delete operations are intentionally NOT exposed: the LLM should not
be able to silently destroy flashcards. The user deletes via the UI.

These tools require anki.enabled = true in config.yml. When disabled
they return a clear error message rather than silently failing.
"""

from __future__ import annotations

import logging
from typing import Any

from app.anki import (
    NOTETYPE_BASIC,
    NOTETYPE_BASIC_REVERSE,
    add_note,
    create_deck,
    get_note,
    list_decks,
    list_notes,
    open_anki,
    rename_deck,
    update_note,
)
from app.config import get_settings

from .registry import ToolRegistry, text_result

log = logging.getLogger(__name__)


_NOTETYPE_CHOICES = (NOTETYPE_BASIC, NOTETYPE_BASIC_REVERSE)


def _ensure_enabled() -> str | None:
    """Return an error message if anki is disabled, else None."""
    if not get_settings().anki.enabled:
        return "Anki is disabled in config.yml (set anki.enabled = true)."
    return None


def _format_tags(tags: list[str]) -> str:
    return " ".join(tags) if tags else "(none)"


# ── Deck handlers ────────────────────────────────────────────────────


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


async def _create_deck(args: dict[str, Any]):
    if (err := _ensure_enabled()):
        return text_result(err, is_error=True)
    name = str(args.get("name", "")).strip()
    if not name:
        return text_result("`name` is required.", is_error=True)
    conn = open_anki()
    try:
        deck = create_deck(conn, name)
    except ValueError as exc:
        return text_result(str(exc), is_error=True)
    finally:
        conn.close()
    return text_result(f"Created deck [{deck.id}] {deck.name}.")


async def _rename_deck(args: dict[str, Any]):
    if (err := _ensure_enabled()):
        return text_result(err, is_error=True)
    try:
        deck_id = int(args["deck_id"])
    except (KeyError, TypeError, ValueError):
        return text_result("`deck_id` is required (integer).", is_error=True)
    new_name = str(args.get("name", "")).strip()
    if not new_name:
        return text_result("`name` is required.", is_error=True)
    conn = open_anki()
    try:
        rename_deck(conn, deck_id, new_name)
    except KeyError:
        return text_result(f"deck {deck_id} not found", is_error=True)
    except ValueError as exc:
        return text_result(str(exc), is_error=True)
    finally:
        conn.close()
    return text_result(f"Renamed deck [{deck_id}] to {new_name!r}.")


# ── Note handlers ────────────────────────────────────────────────────


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


async def _update_note(args: dict[str, Any]):
    if (err := _ensure_enabled()):
        return text_result(err, is_error=True)
    try:
        note_id = int(args["note_id"])
    except (KeyError, TypeError, ValueError):
        return text_result("`note_id` is required (integer).", is_error=True)

    front = args.get("front")
    back = args.get("back")
    fields: list[str] | None = None
    if front is not None or back is not None:
        # Need both — if only one is given, fill the other from the existing
        # note so the caller doesn't have to re-send unchanged data.
        conn = open_anki()
        try:
            existing = get_note(conn, note_id)
        finally:
            conn.close()
        if existing is None:
            return text_result(f"note {note_id} not found", is_error=True)
        fields = [
            str(front).strip() if front is not None else (existing.fields[0] if existing.fields else ""),
            str(back).strip() if back is not None else (existing.fields[1] if len(existing.fields) > 1 else ""),
        ]
        if not fields[0] or not fields[1]:
            return text_result(
                "Both front and back must be non-empty after update.", is_error=True,
            )

    raw_tags = args.get("tags")
    tags: list[str] | None = None
    if raw_tags is not None:
        if isinstance(raw_tags, str):
            tags = [t for t in raw_tags.split() if t]
        else:
            tags = [str(t).strip() for t in raw_tags if str(t).strip()]

    if fields is None and tags is None:
        return text_result(
            "Nothing to update — supply `front`, `back`, and/or `tags`.",
            is_error=True,
        )

    conn = open_anki()
    try:
        n = update_note(conn, note_id, fields=fields, tags=tags)
    except KeyError:
        return text_result(f"note {note_id} not found", is_error=True)
    except ValueError as exc:
        return text_result(str(exc), is_error=True)
    finally:
        conn.close()
    return text_result(
        f"Updated note [{n.id}] (tags: {_format_tags(n.tags)})."
    )


# ── Registration ─────────────────────────────────────────────────────


def register_all(reg: ToolRegistry) -> None:
    reg.register(
        "anki.list_decks",
        "List all Anki decks in the local collection. Returns deck id, "
        "name, and card counts (total / due today / new).",
        {"type": "object", "properties": {}},
        _list_decks,
    )
    reg.register(
        "anki.create_deck",
        "Create a new Anki deck. Use this before adding notes if the "
        "user asks for a topic-specific deck.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Deck name."},
            },
            "required": ["name"],
        },
        _create_deck,
    )
    reg.register(
        "anki.rename_deck",
        "Rename an existing Anki deck. Cannot rename the Default deck.",
        {
            "type": "object",
            "properties": {
                "deck_id": {"type": "integer", "description": "Deck id from anki.list_decks."},
                "name": {"type": "string", "description": "New deck name."},
            },
            "required": ["deck_id", "name"],
        },
        _rename_deck,
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
        "`back` must be non-empty. `tags` is optional.",
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
    reg.register(
        "anki.update_note",
        "Update an existing note's fields and/or tags. You can pass "
        "just `front`, just `back`, just `tags`, or any combination — "
        "unsupplied fields are kept as-is. Tags fully replace the "
        "previous tag set when supplied.",
        {
            "type": "object",
            "properties": {
                "note_id": {"type": "integer"},
                "front": {"type": "string"},
                "back":  {"type": "string"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["note_id"],
        },
        _update_note,
    )
