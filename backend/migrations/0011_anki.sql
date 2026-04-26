-- Anki sync state. The bulk of Anki data — decks, notes, cards,
-- review log — lives in a separate SQLite file at
-- <data_dir>/anki/collection.anki2 in Anki's own schema-18 layout
-- (so we can upload it byte-for-byte to AnkiWeb's /sync/upload).
--
-- This table is just the bookkeeping the Anki feature needs in the
-- main app DB: a singleton row tracking when we last synced and the
-- cached hostkey from AnkiWeb. We don't put the password here — it
-- comes from config.yml at request time.

CREATE TABLE IF NOT EXISTS anki_sync_state (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    last_sync_ms  INTEGER,
    last_action   TEXT,                       -- 'upload' | 'download'
    last_error    TEXT,
    hostkey       TEXT,
    username      TEXT,
    endpoint      TEXT
);

INSERT OR IGNORE INTO anki_sync_state (id) VALUES (1);
