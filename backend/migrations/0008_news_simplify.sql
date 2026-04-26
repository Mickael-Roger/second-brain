-- News & Events simplification:
--   - Trend bubbles + LLM tagger removed; the /trends pivot is gone.
--   - Article descriptions move out of SQLite to the filesystem
--     (<data_dir>/news_summaries/<article_id>.md) so we can keep full
--     bodies without bloating the DB.
--   - New image_url column for the article-detail thumbnail.
--
-- Existing data: the operator is expected to have wiped news_articles
-- before deploying this migration (the user mentioned doing so on
-- their side). For other deployments, the descriptions in the dropped
-- column are lost — re-fetching repopulates them on disk.

DROP INDEX IF EXISTS idx_news_articles_pending_tags;
DROP INDEX IF EXISTS idx_news_articles_event;
DROP INDEX IF EXISTS idx_news_articles_unclustered;
DROP INDEX IF EXISTS idx_news_events_occurred;

DROP TABLE IF EXISTS news_events;

ALTER TABLE news_articles DROP COLUMN tags_json;
ALTER TABLE news_articles DROP COLUMN tags_extracted_at;
ALTER TABLE news_articles DROP COLUMN event_id;
ALTER TABLE news_articles DROP COLUMN description;
ALTER TABLE news_articles ADD COLUMN image_url TEXT;
