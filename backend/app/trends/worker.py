"""Background worker that processes new articles into the trends DB.

Polls `news_articles WHERE trends_processed_at IS NULL` and runs each
through the assigner. Independent from the news fetch so a slow / down
LLM never blocks ingestion.

Triggering
----------
  - On `news.fetch_all_sources` completion (best-effort fire-and-forget).
  - Periodically via APScheduler as a safety net.
  - Manually via the API endpoint `POST /api/trends/process` (later).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.db.connection import open_connection

from . import store
from .assigner import AssignerError, process_article

log = logging.getLogger(__name__)


_WORKER_LOCK = asyncio.Lock()


async def process_pending(*, max_per_run: int | None = None) -> int:
    """Process all currently-pending articles (oldest first). Returns
    the number of articles successfully processed.

    Protected by an asyncio Lock — concurrent invocations (e.g. cron
    + post-fetch hook firing in the same second) coalesce: the second
    awaits the first, then finds an empty queue and returns 0.
    """
    settings = get_settings()
    if not settings.trends.enabled:
        return 0

    cap = max_per_run if max_per_run is not None else settings.trends.max_per_run
    window_days = settings.trends.backfill_days
    since_iso = (
        (datetime.now(UTC) - timedelta(days=window_days)).isoformat() if window_days > 0 else None
    )
    until_iso = (
        datetime.now(UTC) - timedelta(minutes=settings.trends.min_article_age_minutes)
    ).isoformat()

    async with _WORKER_LOCK:
        processed = 0
        while processed < cap:
            conn = open_connection()
            try:
                batch = store.pending_articles(
                    conn,
                    limit=min(20, cap - processed),
                    since_iso=since_iso,
                    until_iso=until_iso,
                )
            finally:
                conn.close()
            if not batch:
                break

            for pending in batch:
                if processed >= cap:
                    break
                try:
                    await process_article(
                        pending.article_id,
                        article_title=pending.title,
                    )
                except AssignerError as exc:
                    log.warning(
                        "trends worker: assigner failed for %s: %s — "
                        "marking processed to avoid infinite retry",
                        pending.article_id,
                        exc,
                    )
                except Exception:
                    log.exception(
                        "trends worker: unexpected failure on %s — "
                        "marking processed to avoid infinite retry",
                        pending.article_id,
                    )
                # Always mark processed: a permanently-failing article
                # would otherwise jam the queue. We accept losing the
                # signal from one news on rare provider hiccups.
                conn = open_connection()
                try:
                    store.mark_article_processed(
                        conn,
                        pending.article_id,
                        pending.content_hash,
                    )
                finally:
                    conn.close()
                processed += 1

        if processed:
            log.info("trends worker: processed %d article(s)", processed)
        return processed


# ── Lifecycle hooks called from main.py / scheduler ────────────────


_WORKER_TASK: asyncio.Task | None = None


def start_worker() -> None:
    """No-op placeholder for symmetry with start_scheduler. The actual
    triggering happens from the news fetch path and the scheduler."""
    return None


def stop_worker() -> None:
    global _WORKER_TASK
    if _WORKER_TASK is not None:
        _WORKER_TASK.cancel()
        _WORKER_TASK = None


def trigger_in_background() -> None:
    """Fire-and-forget the worker without awaiting. Safe to call from
    sync code or from a coroutine that doesn't want to block on the
    LLM."""
    settings = get_settings()
    if not settings.trends.enabled:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.debug("trends: no event loop, skipping background trigger")
        return

    async def _run() -> None:
        try:
            await process_pending()
        except Exception:
            log.exception("trends worker: background run failed")

    global _WORKER_TASK
    _WORKER_TASK = loop.create_task(_run())
