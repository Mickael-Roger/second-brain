-- Spaced-repetition style review of Wiki/ pages.
--
-- `wiki_reviews` keeps the latest state per note: when it was last
-- reviewed, what the user said, when it should be shown again, and a
-- hard "exclude" flag for notes the user marked as uninteresting.
-- Selection reads from this table to pick what to surface next.
--
-- `wiki_review_log` is the append-only history (one row per rating
-- click) — that's the traceability the user asked for, and the source
-- of truth for the "have I reviewed anything today?" badge.

CREATE TABLE wiki_reviews (
    path              TEXT    PRIMARY KEY,
    last_reviewed_at  TEXT    NOT NULL,
    last_rating       TEXT    NOT NULL,
    next_due_at       TEXT    NOT NULL,
    excluded          INTEGER NOT NULL DEFAULT 0,
    review_count      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_wiki_reviews_due
    ON wiki_reviews(excluded, next_due_at);

CREATE TABLE wiki_review_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    path         TEXT    NOT NULL,
    reviewed_at  TEXT    NOT NULL,
    rating       TEXT    NOT NULL
);

CREATE INDEX idx_wiki_review_log_when
    ON wiki_review_log(reviewed_at DESC);
