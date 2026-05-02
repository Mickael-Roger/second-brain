-- The Anki integration was rewritten to talk to AnkiConnect (the
-- desktop plugin) over HTTP. There is no longer a local Anki
-- collection nor a custom AnkiWeb sync, so the bookkeeping table
-- 0011_anki.sql introduced is dead. Drop it.
DROP TABLE IF EXISTS anki_sync_state;
