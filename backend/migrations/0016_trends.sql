-- 0016_trends.sql
--
-- Schema for the Trends feature: a "neural-like" weight system where
-- each incoming news article either reinforces an existing trend or
-- creates a new one. All other trends decay slightly on every event;
-- trends whose weight falls under a threshold are soft-deleted but
-- their history is preserved.
--
-- Three pieces:
--   1. `trends`              — current state (active + soft-deleted).
--   2. `trend_snapshots`     — one row per LLM evolution; weights_json
--                              is the full state-of-the-world after the
--                              event. Lets the bubble UI replay the
--                              evolution without join-heavy queries.
--   3. `trends_processed_at` — per-article marker on news_articles, so
--                              the trends worker knows what's already
--                              been processed. NULL = pending.
--
-- See backend/app/trends/ for the engine and assigner.

CREATE TABLE IF NOT EXISTS trends (
    id                   TEXT    PRIMARY KEY,
    name                 TEXT    NOT NULL,
    description          TEXT    NOT NULL,
    weight               REAL    NOT NULL,
    created_at           TEXT    NOT NULL,
    last_reinforced_at   TEXT    NOT NULL,
    reinforcement_count  INTEGER NOT NULL DEFAULT 1,
    examples_json        TEXT    NOT NULL DEFAULT '[]',
    pruned_at            TEXT
);

CREATE INDEX IF NOT EXISTS idx_trends_active
    ON trends(pruned_at) WHERE pruned_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_trends_weight
    ON trends(weight DESC) WHERE pruned_at IS NULL;


CREATE TABLE IF NOT EXISTS trend_snapshots (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at          TEXT    NOT NULL,
    trigger_article_id   TEXT,
    trigger_action       TEXT    NOT NULL,
    trigger_trend_id     TEXT,
    weights_json         TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trend_snapshots_time
    ON trend_snapshots(recorded_at DESC);


ALTER TABLE news_articles
    ADD COLUMN trends_processed_at TEXT;

CREATE INDEX IF NOT EXISTS idx_news_articles_trends_pending
    ON news_articles(published_at ASC) WHERE trends_processed_at IS NULL;
