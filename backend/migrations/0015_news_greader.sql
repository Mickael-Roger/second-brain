-- 0015_news_greader.sql
--
-- Adds the schema bits needed by the FreshRSS Google-Reader API
-- migration: an is_starred flag on articles plus per-article labels.
-- Categories are still a property of the feed (news_feeds.feed_group)
-- and need no new table.
--
-- The decimal→hex re-encode of existing external_ids is handled in
-- Python at first start (see news.service._migrate_external_ids_to_hex)
-- because it also has to rename the JSON files under <data_dir>/news/,
-- which raw SQL can't do.

ALTER TABLE news_articles
    ADD COLUMN is_starred INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_news_articles_starred
    ON news_articles(is_starred) WHERE is_starred = 1;

CREATE TABLE IF NOT EXISTS news_article_labels (
    article_id  TEXT NOT NULL,
    label       TEXT NOT NULL,
    PRIMARY KEY (article_id, label),
    FOREIGN KEY (article_id) REFERENCES news_articles(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_news_article_labels_label
    ON news_article_labels(label);

CREATE TABLE IF NOT EXISTS news_labels (
    name        TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL
);
