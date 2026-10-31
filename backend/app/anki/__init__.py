"""Anki integration via the AnkiConnect plugin.

Every operation is a JSON-RPC round-trip to the user's running Anki
desktop. There is no local mirror, no sqlite collection, no AnkiWeb
credentials in this app. See `client.py` for the transport.
"""

from app.anki.client import (
    MODE_NORMAL,
    MODE_REVERSE,
    MODEL_BASIC,
    MODEL_BASIC_REVERSE,
    AnkiCardInfo,
    AnkiConnectError,
    AnkiDeck,
    AnkiDeckStats,
    add_card,
    answer_card,
    cards_info,
    deck_stats,
    find_card_ids,
    list_cards,
    list_decks,
    list_due_cards,
    retrieve_media,
    sync,
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
