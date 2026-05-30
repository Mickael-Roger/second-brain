# Project-Specific Agent Guidelines

## Overview
- Purpose: Second-brain web application with FastAPI backend, frontend assets, Obsidian integration, and FreshRSS news ingestion.
- Backend data: SQLite metadata is stored separately from full news article JSON files under the configured `app.data_dir`.

## Rules
- Use English for code, comments, variables, and documentation.
- Keep FreshRSS/GReader ingestion resilient to missing API fields; FreshRSS may omit `updated`, so content freshness must not rely only on timestamps.
- Do not run `security-review` tasks in this project unless the user explicitly asks for one.

## Testing
- Run `uv run pytest` for the Python test suite when tests are available.
- Run `uv run second-brain migrate` or a migration smoke test when changing SQL migrations.

## Lessons Learned
- FreshRSS content edits made by `news-synthesis` can be visible through GReader `summary` while the GReader item omits `updated`. Track article body changes with a stored content hash so `second-brain` refreshes stale JSON caches.
- Trend extraction must be delayed and content-version-aware: process only recent articles inside `trends.backfill_days`, wait `trends.min_article_age_minutes` for `news-synthesis`, and compare `content_hash` with `trends_content_hash` so updated summaries can be reprocessed without sweeping stale RSS backlogs.
- Trend UI must prioritize readable evidence over decorative bubbles: avoid fitting text into circles, use theme-safe text colors, and keep virality sizing as an accent. Trend extraction should reject broad/generic subjects and store only concrete event-level subjects with strong confidence.
