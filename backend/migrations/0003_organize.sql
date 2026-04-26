CREATE TABLE organize_runs (
    id            TEXT    PRIMARY KEY,
    started_at    TEXT    NOT NULL,
    finished_at   TEXT,
    mode          TEXT    NOT NULL,
    status        TEXT    NOT NULL,        -- running | completed | applied | discarded | failed
    notes_total   INTEGER NOT NULL DEFAULT 0,
    summary       TEXT,
    error         TEXT
);

CREATE INDEX idx_organize_runs_started ON organize_runs(started_at DESC);

CREATE TABLE organize_proposals (
    run_id         TEXT    NOT NULL,
    path           TEXT    NOT NULL,
    move_to        TEXT,
    tags_json      TEXT,
    wikilinks_json TEXT,
    refactor       TEXT,
    notes          TEXT,
    parse_error    TEXT,
    raw_response   TEXT,
    state          TEXT    NOT NULL DEFAULT 'pending',
    apply_error    TEXT,
    apply_ops      TEXT,
    created_at     TEXT    NOT NULL,
    PRIMARY KEY (run_id, path)
);
