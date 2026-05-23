-- 0018_news_updated_at.sql
--
-- Add `updated_at` (unix seconds) to news_articles so we can detect
-- when FreshRSS reports a content change for an article we already
-- have. The fetch path compares the GReader `updated` field against
-- this column and refreshes title/published_at/JSON body when newer.
--
-- Backfill: best-effort parse of the existing ISO `published_at`.
-- Anything unparseable lands as 0; the next FreshRSS fetch overwrites
-- it with the real upstream `updated` value so this isn't load-bearing.

ALTER TABLE news_articles
    ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0;

UPDATE news_articles
   SET updated_at = COALESCE(
       CAST(strftime('%s', substr(published_at, 1, 19)) AS INTEGER),
       0
   )
 WHERE updated_at = 0;
