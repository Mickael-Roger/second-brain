"""Anki feature: local schema-18 collection + AnkiWeb full-sync.

Public surface re-exported here so the rest of the app can import
from `app.anki` without reaching into submodules.

The local Anki collection lives in a separate SQLite file at
`<data_dir>/anki/collection.anki2`, NOT in the main `second-brain.db`.
The schema is Anki's own schema-18 layout, which is what the
AnkiWeb /sync/upload endpoint validates and the /sync/download
endpoint returns.
"""

from app.anki.connection import anki_db_path, open_anki
from app.anki.repo import (
    AnkiCard,
    AnkiDeck,
    AnkiNote,
    NOTETYPE_BASIC,
    NOTETYPE_BASIC_REVERSE,
    add_note,
    create_deck,
    delete_deck,
    delete_note,
    get_note,
    list_decks,
    list_notes,
    next_due_card,
    rename_deck,
    update_note,
)
from app.anki.scheduler import answer_card
from app.anki.schema import bootstrap_collection
from app.anki.service import (
    SyncStatus,
    ensure_collection,
    sync_download,
    sync_status,
    sync_upload,
)
from app.anki.sync import AnkiSyncError

__all__ = [
    "AnkiCard",
    "AnkiDeck",
    "AnkiNote",
    "AnkiSyncError",
    "NOTETYPE_BASIC",
    "NOTETYPE_BASIC_REVERSE",
    "SyncStatus",
    "add_note",
    "anki_db_path",
    "answer_card",
    "bootstrap_collection",
    "create_deck",
    "delete_deck",
    "delete_note",
    "ensure_collection",
    "get_note",
    "list_decks",
    "list_notes",
    "next_due_card",
    "open_anki",
    "rename_deck",
    "sync_download",
    "sync_status",
    "sync_upload",
    "update_note",
]
