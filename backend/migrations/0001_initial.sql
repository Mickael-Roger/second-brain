-- Initial schema. Datetimes are ISO-8601 UTC text; booleans are 0/1 INTEGER.

CREATE TABLE chats (
    id          TEXT    PRIMARY KEY,
    title       TEXT    NOT NULL,
    path        TEXT    NOT NULL,
    module_id   TEXT,
    model       TEXT,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    archived    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_chats_module ON chats(module_id, updated_at DESC);

CREATE TABLE sessions (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    user_agent  TEXT,
    ip          TEXT
);

CREATE TABLE module_state (
    module_id   TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (module_id, key)
);

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
