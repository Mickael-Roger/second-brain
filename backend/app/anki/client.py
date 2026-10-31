"""HTTP client for the AnkiConnect plugin.

AnkiConnect is the Anki desktop plugin that exposes a JSON-RPC HTTP
endpoint (default http://127.0.0.1:8765). Every call POSTs a JSON
envelope `{"action": ..., "version": 6, "params": {...}, "key": ...}`
and the response shape is `{"result": ..., "error": null | "msg"}`.

The brain owns no local Anki state — every operation is a round-trip
to the user's running desktop client.
"""

from __future__ import annotations

import base64
import binascii
import logging
from dataclasses import dataclass, field
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
class AnkiDeckStats:
    deck_id: int
    name: str
    new_count: int
    learn_count: int
    review_count: int
    total_in_deck: int


@dataclass(slots=True)
class AnkiCardInfo:
    card_id: int
    note_id: int
    deck_name: str
    model_name: str
    front: str
    back: str
    state: str  # new | learning | review | relearning | suspended | buried
    due: int  # interpretation depends on queue (positions / day offsets / unix ts)
    interval: int  # days
    tags: list[str]
    question: str = ""
    answer: str = ""
    css: str = ""
    next_reviews: list[str] = field(default_factory=list)


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
    except ValueError as exc:
        raise AnkiConnectError("AnkiConnect returned an invalid JSON response.") from exc

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


async def deck_stats() -> list[AnkiDeckStats]:
    """Return scheduler counts and totals, including decks with no due cards."""
    decks = await list_decks()
    if not decks:
        return []
    raw = await _invoke("getDeckStats", decks=[deck.name for deck in decks])
    if not isinstance(raw, dict):
        raise AnkiConnectError("AnkiConnect returned incomplete deck statistics.")
    result = []
    try:
        for deck in decks:
            if str(deck.id) not in raw:
                # AnkiConnect can omit empty decks from the scheduler's due tree.
                if await find_card_ids(_deck_query(deck.name)):
                    raise AnkiConnectError("AnkiConnect returned incomplete deck statistics.")
                result.append(
                    AnkiDeckStats(
                        deck_id=deck.id,
                        name=deck.name,
                        new_count=0,
                        learn_count=0,
                        review_count=0,
                        total_in_deck=0,
                    )
                )
                continue
            stats = raw[str(deck.id)]
            total = stats.get("total_in_deck")
            # Older Anki versions do not include totals in getDeckStats.
            if total is None:
                total = len(await find_card_ids(_deck_query(deck.name)))
            result.append(
                AnkiDeckStats(
                    deck_id=deck.id,
                    name=deck.name,
                    new_count=int(stats["new_count"]),
                    learn_count=int(stats["learn_count"]),
                    review_count=int(stats["review_count"]),
                    total_in_deck=int(total),
                )
            )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise AnkiConnectError("AnkiConnect returned incomplete deck statistics.") from exc
    return result


async def answer_card(card_id: int, ease: int) -> bool:
    if ease not in (1, 2, 3, 4):
        raise AnkiConnectError("Review ease must be between 1 (Again) and 4 (Easy).")
    result = await _invoke("answerCards", answers=[{"cardId": card_id, "ease": ease}])
    return bool(result and result[0])


async def find_card_ids(query: str) -> list[int]:
    """Run an Anki browser query and return the matching card ids."""
    raw = await _invoke("findCards", query=query)
    return [int(c) for c in raw]


async def cards_info(card_ids: list[int]) -> list[AnkiCardInfo]:
    """Hydrate card ids into AnkiCardInfo records."""
    if not card_ids:
        return []
    raw = await _invoke("cardsInfo", cards=card_ids)
    return [_parse_card_info(c) for c in raw if c and c.get("cardId")]


def _deck_query(deck: str) -> str:
    escaped = deck.replace("\\", "\\\\").replace('"', '\\"')
    return f'deck:"{escaped}"'


async def list_cards(deck: str | None = None, limit: int = 100) -> list[AnkiCardInfo]:
    """List cards optionally restricted to a deck. Returns front/back/state."""
    query = _deck_query(deck) if deck else "deck:*"
    ids = await find_card_ids(query)
    return await cards_info(ids[:limit])


async def list_due_cards(
    deck: str | None = None,
    limit: int = 100,
    *,
    include_new: bool = False,
    offset: int = 0,
) -> list[AnkiCardInfo]:
    """List due cards, optionally including new cards; never review buried/suspended cards."""
    condition = "(is:due OR is:new) -is:suspended -is:buried" if include_new else "is:due"
    query = f"{_deck_query(deck)} {condition}" if deck else condition
    ids = await find_card_ids(query)
    return await cards_info(ids[offset : offset + limit])


async def retrieve_media(filename: str) -> bytes | None:
    """Read collection media through AnkiConnect; no local collection is needed."""
    raw = await _invoke("retrieveMediaFile", filename=filename)
    if raw is False:
        return None
    try:
        return base64.b64decode(raw, validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise AnkiConnectError("AnkiConnect returned invalid media data.") from exc


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
        raise AnkiConnectError(f"mode must be one of {sorted(_MODE_TO_MODEL)} (got {mode!r})")
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
    intervals = raw.get("nextReviews")
    # Only map the four-button order: Again, Hard, Good, Easy. Older schedulers
    # may return fewer entries; do not assign those to the wrong rating.
    next_reviews = []
    if isinstance(intervals, list) and len(intervals) == 4:
        isolates = dict.fromkeys(map(ord, "\u2066\u2067\u2068\u2069"))
        next_reviews = [
            value.translate(isolates).strip() if isinstance(value, str) else ""
            for value in intervals
        ]
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
        question=str(raw.get("question", "")),
        answer=str(raw.get("answer", "")),
        css=str(raw.get("css", "")),
        next_reviews=next_reviews,
    )


__all__ = [
    "MODEL_BASIC",
    "MODEL_BASIC_REVERSE",
    "MODE_NORMAL",
    "MODE_REVERSE",
    "AnkiCardInfo",
    "AnkiConnectError",
    "AnkiDeck",
    "AnkiDeckStats",
    "add_card",
    "answer_card",
    "cards_info",
    "deck_stats",
    "find_card_ids",
    "list_cards",
    "list_decks",
    "list_due_cards",
    "retrieve_media",
    "sync",
]
