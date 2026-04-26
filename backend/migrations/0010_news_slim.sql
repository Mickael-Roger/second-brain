-- Slim news_articles down to indexed metadata only. The full article
-- record (url, author, image, summary, …) now lives at
-- <data_dir>/news/<safe_id>.json so SQLite stays compact and SELECT *
-- doesn't haul body text on every list query.
--
-- The user has stated they will drop the existing news_* tables before
-- restarting, so this migration recreates news_articles from scratch
-- with the new shape. If the old table still exists (typical migration
-- path), we copy across the fields that survive.

DROP INDEX IF EXISTS idx_news_articles_published;
DROP INDEX IF EXISTS idx_news_articles_feed_published;
DROP INDEX IF EXISTS idx_news_articles_pending_tags;
DROP INDEX IF EXISTS idx_news_articles_event;
DROP INDEX IF EXISTS idx_news_articles_unclustered;

CREATE TABLE IF NOT EXISTS news_articles_new (
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

INSERT OR IGNORE INTO news_articles_new
    (id, source, external_id, feed_id, feed_title, feed_group,
     title, published_at, is_read)
SELECT id, source, external_id, feed_id, feed_title, feed_group,
       title, published_at, is_read
FROM news_articles;

DROP TABLE IF EXISTS news_articles;
ALTER TABLE news_articles_new RENAME TO news_articles;

CREATE INDEX idx_news_articles_published
    ON news_articles(published_at DESC);
CREATE INDEX idx_news_articles_feed_published
    ON news_articles(feed_id, published_at DESC);
