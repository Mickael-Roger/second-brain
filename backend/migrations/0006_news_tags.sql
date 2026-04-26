-- Hashtag extraction + folder-of-origin tracking.
-- Each article now carries the FreshRSS folder/group it came from
-- (feed_group), and once the tagger has run on it, the JSON array of
-- topic hashtags it surfaced (tags_json) plus the timestamp of that
-- extraction (tags_extracted_at). Articles where tags_extracted_at IS
-- NULL are pending and get processed on the next tagger pass.
--
-- The partial index makes "what's pending" a constant-time lookup
-- regardless of how many already-processed articles sit in the table.

ALTER TABLE news_articles ADD COLUMN feed_group TEXT;
ALTER TABLE news_articles ADD COLUMN tags_json TEXT;
ALTER TABLE news_articles ADD COLUMN tags_extracted_at TEXT;

CREATE INDEX idx_news_articles_pending_tags
    ON news_articles(published_at DESC)
    WHERE tags_extracted_at IS NULL;
