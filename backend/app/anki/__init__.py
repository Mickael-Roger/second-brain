"""Anki feature: local schema-18 collection + AnkiWeb full-sync.

Public surface:
  - Read access: list_decks, list_notes, get_note, find_deck_by_name
  - Write access: add_note (the only mutation we expose)
  - Sync: ensure_collection, sync_status, sync_upload, sync_download
  - Vault import: import_from_vault (called by the nightly organize job)

Decks are managed externally (Anki desktop / AnkiWeb) and pulled in
via sync_download. Mutations beyond add_note are not implemented —
the LLM should not silently destroy or rewrite flashcards.
"""

from app.anki.connection import anki_db_path, open_anki
from app.anki.repo import (
    AnkiCard,
    AnkiDeck,
    AnkiNote,
    NOTETYPE_BASIC,
    NOTETYPE_BASIC_REVERSE,
    add_note,
    find_deck_by_name,
    get_note,
    list_decks,
    list_notes,
)
from app.anki.schema import bootstrap_collection
from app.anki.service import (
    SyncStatus,
    ensure_collection,
    sync_download,
    sync_status,
    sync_upload,
)
from app.anki.sync import AnkiSyncError
from app.anki.vault_importer import (
    ImportResult,
    import_from_vault,
)

__all__ = [
    "AnkiCard",
    "AnkiDeck",
    "AnkiNote",
    "AnkiSyncError",
    "ImportResult",
    "NOTETYPE_BASIC",
    "NOTETYPE_BASIC_REVERSE",
    "SyncStatus",
    "add_note",
    "anki_db_path",
    "bootstrap_collection",
    "ensure_collection",
    "find_deck_by_name",
    "get_note",
    "import_from_vault",
    "list_decks",
    "list_notes",
    "open_anki",
    "sync_download",
    "sync_status",
    "sync_upload",
]
