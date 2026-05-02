"""HTTP client for the AnkiConnect plugin.

AnkiConnect is the Anki desktop plugin that exposes a JSON-RPC HTTP
endpoint (default http://127.0.0.1:8765). Every call POSTs a JSON
envelope `{"action": ..., "version": 6, "params": {...}, "key": ...}`
and the response shape is `{"result": ..., "error": null | "msg"}`.

The brain owns no local Anki state — every operation is a round-trip
to the user's running desktop client.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

ANKICONNECT_VERSION = 6

# Note types we know how to create. AnkiConnect uses the model's
# display name verbatim, and these two ship with every Anki install.
MODEL_BASIC = "Basic"
MODEL_BASIC_REVERSE = "Basic (and reversed card)"

MODE_NORMAL = "normal"
MODE_REVERSE = "reverse"
_MODE_TO_MODEL = {
    MODE_NORMAL: MODEL_BASIC,
    MODE_REVERSE: MODEL_BASIC_REVERSE,
}


class AnkiConnectError(RuntimeError):
    """Raised when AnkiConnect returns an error or is unreachable."""


@dataclass(slots=True)
class AnkiDeck:
    id: int
    name: str


@dataclass(slots=True)
class AnkiCardInfo:
    card_id: int
    note_id: int
    deck_name: str
    model_name: str
    front: str
    back: str
    state: str            # new | learning | review | relearning | suspended | buried
    due: int              # interpretation depends on queue (positions / day offsets / unix ts)
    interval: int         # days
    tags: list[str]


# ── State decoding ───────────────────────────────────────────────────


# AnkiConnect's `cardsInfo` returns both `type` (card lifecycle) and
# `queue` (current scheduling slot). `queue` wins because it captures
# transient states (suspended, buried) that `type` doesn't.
#
#   queue: -3 user-buried, -2 sched-buried, -1 suspended,
#           0 new,  1 learning,  2 review,  3 day-learning,  4 preview
#   type:   0 new,  1 learning, 2 review,   3 relearning
def _state_from(queue: int, type_: int) -> str:
    if queue == -1:
        return "suspended"
    if queue in (-2, -3):
        return "buried"
    if queue == 4:
        return "preview"
    if type_ == 3:
        return "relearning"
    if type_ == 2:
        return "review"
    if type_ == 1 or queue in (1, 3):
        return "learning"
    return "new"


# ── Transport ────────────────────────────────────────────────────────


async def _invoke(action: str, **params: Any) -> Any:
    """POST one AnkiConnect call. Raises AnkiConnectError on transport
    failure, plugin error response, or `enabled = false`."""
    settings = get_settings().anki
    if not settings.enabled:
        raise AnkiConnectError("Anki is disabled in config.yml (set anki.enabled = true).")

    payload: dict[str, Any] = {
        "action": action,
        "version": ANKICONNECT_VERSION,
    }
    if params:
        payload["params"] = params
    if settings.api_key:
        payload["key"] = settings.api_key

    try:
        async with httpx.AsyncClient(timeout=settings.timeout_seconds) as http:
            resp = await http.post(settings.url, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise AnkiConnectError(
            f"AnkiConnect unreachable at {settings.url}: {exc}. "
            "Make sure Anki desktop is running with the AnkiConnect add-on."
        ) from exc

    if not isinstance(data, dict) or "result" not in data or "error" not in data:
        raise AnkiConnectError(f"unexpected AnkiConnect response: {data!r}")

    err = data.get("error")
    if err is not None:
        raise AnkiConnectError(f"AnkiConnect error on '{action}': {err}")
    return data["result"]


# ── Public API ───────────────────────────────────────────────────────


async def sync() -> None:
    """Trigger an AnkiWeb sync from the running desktop client."""
    await _invoke("sync")


async def list_decks() -> list[AnkiDeck]:
    """Return every deck the desktop knows about, sorted by name."""
    raw = await _invoke("deckNamesAndIds")
    decks = [AnkiDeck(id=int(did), name=name) for name, did in raw.items()]
    decks.sort(key=lambda d: d.name.lower())
    return decks


async def find_card_ids(query: str) -> list[int]:
    """Run an Anki browser query and return the matching card ids."""
    raw = await _invoke("findCards", query=query)
    return [int(c) for c in raw]


async def cards_info(card_ids: list[int]) -> list[AnkiCardInfo]:
    """Hydrate card ids into AnkiCardInfo records."""
    if not card_ids:
        return []
    raw = await _invoke("cardsInfo", cards=card_ids)
    return [_parse_card_info(c) for c in raw]


async def list_cards(deck: str | None = None, limit: int = 100) -> list[AnkiCardInfo]:
    """List cards optionally restricted to a deck. Returns front/back/state."""
    query = f'deck:"{deck}"' if deck else "deck:*"
    ids = await find_card_ids(query)
    return await cards_info(ids[:limit])


async def list_due_cards(deck: str | None = None, limit: int = 100) -> list[AnkiCardInfo]:
    """List cards due for review (Anki's `is:due` operator)."""
    query = f'deck:"{deck}" is:due' if deck else "is:due"
    ids = await find_card_ids(query)
    return await cards_info(ids[:limit])


async def add_card(
    deck: str,
    front: str,
    back: str,
    mode: str = MODE_NORMAL,
    tags: list[str] | None = None,
) -> int:
    """Add one note (one or two cards depending on `mode`).

    Returns the new note id. Raises AnkiConnectError if the deck or
    model is unknown to AnkiConnect, or if the note is a duplicate.
    """
    model = _MODE_TO_MODEL.get(mode)
    if model is None:
        raise AnkiConnectError(
            f"mode must be one of {sorted(_MODE_TO_MODEL)} (got {mode!r})"
        )
    note: dict[str, Any] = {
        "deckName": deck,
        "modelName": model,
        "fields": {"Front": front, "Back": back},
        "tags": list(tags) if tags else [],
        "options": {"allowDuplicate": False},
    }
    return int(await _invoke("addNote", note=note))


# ── Internals ────────────────────────────────────────────────────────


def _parse_card_info(raw: dict[str, Any]) -> AnkiCardInfo:
    fields = raw.get("fields") or {}
    front = (fields.get("Front") or {}).get("value", "") if isinstance(fields, dict) else ""
    back = (fields.get("Back") or {}).get("value", "") if isinstance(fields, dict) else ""
    return AnkiCardInfo(
        card_id=int(raw.get("cardId", 0)),
        note_id=int(raw.get("note", 0)),
        deck_name=str(raw.get("deckName", "")),
        model_name=str(raw.get("modelName", "")),
        front=front,
        back=back,
        state=_state_from(int(raw.get("queue", 0)), int(raw.get("type", 0))),
        due=int(raw.get("due", 0)),
        interval=int(raw.get("interval", 0)),
        tags=list(raw.get("tags") or []),
    )


__all__ = [
    "AnkiCardInfo",
    "AnkiConnectError",
    "AnkiDeck",
    "MODE_NORMAL",
    "MODE_REVERSE",
    "MODEL_BASIC",
    "MODEL_BASIC_REVERSE",
    "add_card",
    "cards_info",
    "find_card_ids",
    "list_cards",
    "list_decks",
    "list_due_cards",
    "sync",
]
