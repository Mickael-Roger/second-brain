# Project-Specific Agent Guidelines

## Overview
- Purpose: Second-brain web application with FastAPI backend, frontend assets, Obsidian integration, and FreshRSS news ingestion.
- Backend data: SQLite metadata is stored separately from full news article JSON files under the configured `app.data_dir`.

## Rules
- Use English for code, comments, variables, and documentation.
- Keep FreshRSS/GReader ingestion resilient to missing API fields; FreshRSS may omit `updated`, so content freshness must not rely only on timestamps.

## Testing
- Run `uv run pytest` for the Python test suite when tests are available.
- Run `uv run second-brain migrate` or a migration smoke test when changing SQL migrations.

## Lessons Learned
- FreshRSS content edits made by `news-synthesis` can be visible through GReader `summary` while the GReader item omits `updated`. Track article body changes with a stored content hash so `second-brain` refreshes stale JSON caches.
