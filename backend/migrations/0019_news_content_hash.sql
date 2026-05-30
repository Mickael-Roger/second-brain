-- 0019_news_content_hash.sql
--
-- Track a fingerprint of the article body returned by FreshRSS/GReader.
-- FreshRSS does not always expose `lastUserModified` as GReader `updated`,
-- so timestamp-only refresh misses content edits made by news-synthesis.

ALTER TABLE news_articles
    ADD COLUMN content_hash TEXT;
