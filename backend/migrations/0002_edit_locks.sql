CREATE TABLE edit_locks (
    path        TEXT    PRIMARY KEY,
    token_hash  TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    expires_at  TEXT    NOT NULL
);
