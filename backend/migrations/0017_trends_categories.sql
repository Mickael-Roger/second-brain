-- 0017_trends_categories.sql
--
-- Add a `category` column to `trends`. The set of allowed categories
-- lives in config (`trends.categories`); the LLM picks one when
-- creating a trend and may move a trend between categories via
-- `rename_trend`. Existing rows default to 'Other' so the migration
-- is idempotent for users who already enabled trends.

ALTER TABLE trends
    ADD COLUMN category TEXT NOT NULL DEFAULT 'Other';

CREATE INDEX IF NOT EXISTS idx_trends_category_active
    ON trends(category, weight DESC) WHERE pruned_at IS NULL;
