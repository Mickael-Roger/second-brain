-- Slim news_articles down to indexed metadata only. The full article
-- record (url, author, image, summary, …) now lives at
-- <data_dir>/news/<safe_id>.json so SQLite stays compact and SELECT *
-- doesn't haul body text on every list query.
--
-- This migration is self-contained: it drops and recreates
-- news_articles in its final slim shape (no in-place copy from the
-- previous schema), and re-creates news_feeds / news_fetch_runs
-- defensively so it also works when the operator has manually
-- dropped the entire news_* set before deploying. Any existing
-- news_articles data is wiped — the operator is expected to
-- re-fetch from FreshRSS.

DROP INDEX IF EXISTS idx_news_articles_published;
DROP INDEX IF EXISTS idx_news_articles_feed_published;
DROP INDEX IF EXISTS idx_news_articles_pending_tags;
DROP INDEX IF EXISTS idx_news_articles_event;
DROP INDEX IF EXISTS idx_news_articles_unclustered;

DROP TABLE IF EXISTS news_articles;

CREATE TABLE news_articles (
    id            TEXT    PRIMARY KEY,
    source        TEXT    NOT NULL,
    external_id   TEXT    NOT NULL,
    feed_id       TEXT,
    feed_title    TEXT,
    feed_group    TEXT,
    title         TEXT    NOT NULL,
    published_at  TEXT    NOT NULL,
    is_read       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (source, external_id)
);

CREATE INDEX idx_news_articles_published
    ON news_articles(published_at DESC);
CREATE INDEX idx_news_articles_feed_published
    ON news_articles(feed_id, published_at DESC);

CREATE TABLE IF NOT EXISTS news_feeds (
    id                TEXT    PRIMARY KEY,
    title             TEXT,
    feed_group        TEXT,
    site_url          TEXT,
    favicon_data_uri  TEXT,
    updated_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS news_fetch_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT    NOT NULL,
    source       TEXT,
    started_at   TEXT    NOT NULL,
    finished_at  TEXT,
    status       TEXT    NOT NULL,
    fetched      INTEGER NOT NULL DEFAULT 0,
    inserted     INTEGER NOT NULL DEFAULT 0,
    clustered    INTEGER NOT NULL DEFAULT 0,
    error        TEXT
);

CREATE INDEX IF NOT EXISTS idx_news_runs_started
    ON news_fetch_runs(started_at DESC);
