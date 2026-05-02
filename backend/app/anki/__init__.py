"""Anki integration via the AnkiConnect plugin.

Every operation is a JSON-RPC round-trip to the user's running Anki
desktop. There is no local mirror, no sqlite collection, no AnkiWeb
credentials in this app. See `client.py` for the transport.
"""

from app.anki.client import (
    AnkiCardInfo,
    AnkiConnectError,
    AnkiDeck,
    MODE_NORMAL,
    MODE_REVERSE,
    MODEL_BASIC,
    MODEL_BASIC_REVERSE,
    add_card,
    cards_info,
    find_card_ids,
    list_cards,
    list_decks,
    list_due_cards,
    sync,
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
