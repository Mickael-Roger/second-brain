"""anki.* — flashcard tools backed by the AnkiConnect plugin.

Exposes five tools for the LLM:
  - anki.sync        — trigger AnkiWeb sync from the running desktop.
  - anki.list_decks  — list every deck.
  - anki.list_cards  — list cards (front/back/state), optional deck filter.
  - anki.add_card    — add one card (mode: normal | reverse).
  - anki.list_due    — list cards currently due for review.

All tools require `anki.enabled = true` in config.yml AND a running
Anki desktop with the AnkiConnect add-on installed at the configured
host:port. If either is missing the tool returns a clear error
instead of silently failing.
"""

from __future__ import annotations

import logging
from typing import Any

from app.anki import (
    MODE_NORMAL,
    MODE_REVERSE,
    AnkiCardInfo,
    AnkiConnectError,
    add_card,
    list_cards,
    list_decks,
    list_due_cards,
    sync,
)
from app.config import get_settings

from .registry import ToolRegistry, text_result

log = logging.getLogger(__name__)


_MODES = (MODE_NORMAL, MODE_REVERSE)
_PREVIEW_LEN = 100
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 500


def _ensure_enabled() -> str | None:
    if not get_settings().anki.enabled:
        return "Anki is disabled in config.yml (set anki.enabled = true)."
    return None


def _normalize_tags(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        return [t for t in raw.split() if t]
    return [str(t).strip() for t in raw if str(t).strip()]


def _truncate(s: str, n: int = _PREVIEW_LEN) -> str:
    s = s.replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _format_card(c: AnkiCardInfo) -> str:
    return (
        f"- [{c.card_id}] ({c.state}, deck={c.deck_name}, ivl={c.interval}d) "
        f"{_truncate(c.front)} | {_truncate(c.back)}"
    )


def _coerce_limit(args: dict[str, Any]) -> int:
    raw = args.get("limit", _DEFAULT_LIMIT)
    try:
        return max(1, min(int(raw), _MAX_LIMIT))
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT


# ── Handlers ─────────────────────────────────────────────────────────


async def _sync(_args: dict[str, Any]):
    if (err := _ensure_enabled()):
        return text_result(err, is_error=True)
    try:
        await sync()
    except AnkiConnectError as exc:
        return text_result(str(exc), is_error=True)
    return text_result("Anki sync triggered on the desktop client.")


async def _list_decks(_args: dict[str, Any]):
    if (err := _ensure_enabled()):
        return text_result(err, is_error=True)
    try:
        decks = await list_decks()
    except AnkiConnectError as exc:
        return text_result(str(exc), is_error=True)
    if not decks:
        return text_result("(no decks)")
    lines = ["Decks:"] + [f"- {d.name} (id={d.id})" for d in decks]
    return text_result("\n".join(lines))


async def _list_cards(args: dict[str, Any]):
    if (err := _ensure_enabled()):
        return text_result(err, is_error=True)
    deck = args.get("deck")
    deck = str(deck).strip() if deck else None
    limit = _coerce_limit(args)
    try:
        cards = await list_cards(deck=deck, limit=limit)
    except AnkiConnectError as exc:
        return text_result(str(exc), is_error=True)
    if not cards:
        scope = f"deck '{deck}'" if deck else "any deck"
        return text_result(f"(no cards in {scope})")
    header = f"Cards ({len(cards)}{' / limit reached' if len(cards) >= limit else ''}):"
    return text_result("\n".join([header] + [_format_card(c) for c in cards]))


async def _add_card(args: dict[str, Any]):
    if (err := _ensure_enabled()):
        return text_result(err, is_error=True)
    deck = str(args.get("deck", "")).strip()
    front = str(args.get("front", "")).strip()
    back = str(args.get("back", "")).strip()
    mode = str(args.get("mode", MODE_NORMAL)).strip().lower()

    if not deck:
        return text_result("`deck` is required (deck name).", is_error=True)
    if not front or not back:
        return text_result(
            "Both `front` and `back` are required and non-empty.", is_error=True,
        )
    if mode not in _MODES:
        return text_result(
            f"`mode` must be one of {list(_MODES)} (got {mode!r}).", is_error=True,
        )

    tags = _normalize_tags(args.get("tags"))
    try:
        note_id = await add_card(deck=deck, front=front, back=back, mode=mode, tags=tags)
    except AnkiConnectError as exc:
        return text_result(str(exc), is_error=True)
    cards_made = 2 if mode == MODE_REVERSE else 1
    return text_result(
        f"Added note {note_id} in deck '{deck}' "
        f"({mode}, {cards_made} card{'s' if cards_made > 1 else ''})."
    )


async def _list_due(args: dict[str, Any]):
    if (err := _ensure_enabled()):
        return text_result(err, is_error=True)
    deck = args.get("deck")
    deck = str(deck).strip() if deck else None
    limit = _coerce_limit(args)
    try:
        cards = await list_due_cards(deck=deck, limit=limit)
    except AnkiConnectError as exc:
        return text_result(str(exc), is_error=True)
    if not cards:
        scope = f"deck '{deck}'" if deck else "any deck"
        return text_result(f"(nothing due in {scope})")
    header = f"Due cards ({len(cards)}{' / limit reached' if len(cards) >= limit else ''}):"
    return text_result("\n".join([header] + [_format_card(c) for c in cards]))


# ── Registration ─────────────────────────────────────────────────────


def register_all(reg: ToolRegistry) -> None:
    reg.register(
        "anki.sync",
        "Trigger an AnkiWeb sync from the running Anki desktop client. "
        "Use this after adding cards so they propagate to the user's "
        "phone/web. Returns once Anki has accepted the sync request.",
        {"type": "object", "properties": {}},
        _sync,
    )
    reg.register(
        "anki.list_decks",
        "List every deck the user has in Anki. Returns deck name + id. "
        "Use the deck *name* when calling other tools (anki.list_cards, "
        "anki.add_card, anki.list_due).",
        {"type": "object", "properties": {}},
        _list_decks,
    )
    reg.register(
        "anki.list_cards",
        "List cards with their front, back, and current state "
        "(new / learning / review / relearning / suspended / buried). "
        "Pass `deck` to restrict to one deck, otherwise lists across all "
        "decks. Use this to inspect what's in the collection.",
        {
            "type": "object",
            "properties": {
                "deck": {
                    "type": "string",
                    "description": "Deck name to restrict to. Omit for all decks.",
                },
                "limit": {
                    "type": "integer",
                    "default": _DEFAULT_LIMIT,
                    "minimum": 1,
                    "maximum": _MAX_LIMIT,
                },
            },
        },
        _list_cards,
    )
    reg.register(
        "anki.add_card",
        "Add one flashcard. `mode` is 'normal' (one card front→back) "
        "or 'reverse' (two cards: front→back AND back→front, using "
        "Anki's built-in 'Basic (and reversed card)' note type). "
        "The deck must already exist — call anki.list_decks first if "
        "unsure. `tags` is optional.",
        {
            "type": "object",
            "properties": {
                "deck": {"type": "string", "description": "Target deck name."},
                "front": {"type": "string", "description": "Front field (the prompt)."},
                "back":  {"type": "string", "description": "Back field (the answer)."},
                "mode": {
                    "type": "string",
                    "enum": list(_MODES),
                    "default": MODE_NORMAL,
                    "description": "'normal' = one card; 'reverse' = two cards.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags. Accepts a list or space-separated string.",
                },
            },
            "required": ["deck", "front", "back"],
        },
        _add_card,
    )
    reg.register(
        "anki.list_due",
        "List cards currently due for review (Anki's `is:due` filter). "
        "Pass `deck` to restrict to one deck, otherwise covers everything. "
        "Returns the same front/back/state shape as anki.list_cards.",
        {
            "type": "object",
            "properties": {
                "deck": {
                    "type": "string",
                    "description": "Deck name to restrict to. Omit for all decks.",
                },
                "limit": {
                    "type": "integer",
                    "default": _DEFAULT_LIMIT,
                    "minimum": 1,
                    "maximum": _MAX_LIMIT,
                },
            },
        },
        _list_due,
    )
