-- Per-note last-reviewed timestamp. The Organize default scope re-reviews
-- a note when its mtime exceeds its last_reviewed_at (or when there's no
-- record yet — i.e. the note was created since the last pass).
CREATE TABLE note_reviews (
    path              TEXT    PRIMARY KEY,
    last_reviewed_at  TEXT    NOT NULL
);
