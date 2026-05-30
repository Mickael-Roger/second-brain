-- 0020_trend_moments.sql
--
-- Moment-oriented trends. The older `trends` / `trend_snapshots` tables
-- are kept for history, but new processing stores article-level mentions
-- and clusters them into recent event trends.

ALTER TABLE news_articles
    ADD COLUMN trends_content_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_news_articles_trends_content_pending
    ON news_articles(published_at ASC)
    WHERE content_hash IS NOT NULL
      AND (trends_content_hash IS NULL OR trends_content_hash IS NOT content_hash);

CREATE TABLE IF NOT EXISTS trend_clusters (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    category        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_trend_clusters_recent
    ON trend_clusters(status, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS trend_mentions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id            TEXT NOT NULL REFERENCES trend_clusters(id) ON DELETE CASCADE,
    article_id            TEXT NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
    article_content_hash  TEXT NOT NULL,
    subject_title         TEXT NOT NULL,
    subject_description   TEXT NOT NULL,
    article_type          TEXT NOT NULL,
    intensity             REAL NOT NULL,
    confidence            REAL NOT NULL,
    evidence              TEXT NOT NULL DEFAULT '',
    created_at            TEXT NOT NULL,
    UNIQUE(article_id, article_content_hash, subject_title)
);

CREATE INDEX IF NOT EXISTS idx_trend_mentions_cluster_recent
    ON trend_mentions(cluster_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_trend_mentions_article
    ON trend_mentions(article_id);
