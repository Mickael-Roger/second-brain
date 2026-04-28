-- The Organize webapp UI is gone — the job now runs only via the
-- nightly cron or the `second-brain organize` CLI. Per-run history
-- and per-note proposal review tables are no longer used; only
-- `note_reviews` (per-note last_reviewed_at, drives candidate
-- selection) remains.
DROP INDEX IF EXISTS idx_organize_runs_started;
DROP TABLE IF EXISTS organize_proposals;
DROP TABLE IF EXISTS organize_runs;
