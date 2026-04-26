-- Capture FreshRSS read/unread state at fetch time so the News tab
-- can filter to unread items. We refresh `is_read` on every fetch via
-- an UPSERT (see app.news.store.insert_article) so reading an article
-- in FreshRSS propagates to our DB on the next fetch pass.
--
-- The composite index supports the dominant articles-list query:
-- "newest articles in feed X, optionally only unread".

ALTER TABLE news_articles ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0;

CREATE INDEX idx_news_articles_feed_published
    ON news_articles(feed_id, published_at DESC);
