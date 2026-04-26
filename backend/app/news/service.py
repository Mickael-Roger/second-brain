"""News-fetch orchestration.

Two modes:

  - Incremental (`from_ts is None`): used by the scheduler. Walks Fever
    forward from the last `external_id` we already stored — efficient,
    pulls only new items.

  - Ranged (`from_ts` set): used by the manual API trigger. Walks Fever
    backwards from the newest item via `max_id`, keeping only items
    whose `created_on_time` falls in the [`from_ts`, `to_ts`] window.
    Slower but lets the user say "fetch only the last 7 days" even on
    a fresh DB or after deleting old articles.

Clustering is a separate pass (see `cluster.py`) because it's expensive
enough that we don't want to run it on every fetch.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from app.config import get_settings
from app.db.connection import open_connection

from .fever_client import FeverClient, html_to_plain_text, published_iso
from .store import create_fetch_run, finish_fetch_run, insert_article

log = logging.getLogger(__name__)


@dataclass(slots=True)
class FetchSummary:
    source: str
    fetched: int
    inserted: int
    error: str | None = None


def _last_external_id(conn: sqlite3.Connection, source: str) -> int:
    """Highest external_id we've already stored for `source`. Fever ids
    are monotonically-increasing integers, so the next fetch can use
    `since_id=this` and only get new items."""
    row = conn.execute(
        "SELECT external_id FROM news_articles WHERE source = ? "
        "ORDER BY CAST(external_id AS INTEGER) DESC LIMIT 1",
        (source,),
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(row["external_id"])
    except (TypeError, ValueError):
        return 0


async def fetch_freshrss(
    *,
    from_ts: int | None = None,
    to_ts: int | None = None,
) -> FetchSummary:
    """One fetch pass over the FreshRSS source.

    When `from_ts` is set, walk newest-first via `max_id` and keep only
    items in [from_ts, to_ts]. Otherwise do an incremental walk from
    the last stored `external_id`."""
    settings = get_settings()
    cfg = settings.news.sources.freshrss
    if cfg is None:
        raise RuntimeError("news.sources.freshrss is not configured")

    source = "freshrss"
    conn = open_connection()
    try:
        run_id = create_fetch_run(conn, kind="fetch", source=source)
        since = _last_external_id(conn, source) if from_ts is None else 0
    finally:
        conn.close()

    if from_ts is None:
        log.info("news fetch: freshrss starting (incremental, since_id=%d)", since)
    else:
        log.info(
            "news fetch: freshrss starting (range, from_ts=%d, to_ts=%s)",
            from_ts, "now" if to_ts is None else str(to_ts),
        )

    fetched = 0
    inserted = 0
    error: str | None = None
    try:
        async with FeverClient(base_url=cfg.base_url, api_key=cfg.api_key) as client:
            feeds = await client.feeds()
            if from_ts is None:
                items = await client.items_since(
                    since_id=since, max_items=cfg.max_items_per_run
                )
            else:
                items = await client.items_in_range(
                    from_ts=from_ts,
                    to_ts=to_ts,
                    max_items=cfg.max_items_per_run,
                )
            fetched = len(items)
            if items:
                conn = open_connection()
                try:
                    for it in items:
                        feed_title = (
                            feeds[it.feed_id].title if it.feed_id in feeds else None
                        )
                        if insert_article(
                            conn,
                            source=source,
                            external_id=it.id,
                            feed_id=it.feed_id or None,
                            feed_title=feed_title,
                            url=it.url,
                            title=it.title,
                            description=html_to_plain_text(it.html),
                            author=it.author,
                            published_at=published_iso(it),
                        ):
                            inserted += 1
                finally:
                    conn.close()
    except Exception as exc:
        error = str(exc)
        log.exception("news fetch: freshrss failed")

    conn = open_connection()
    try:
        finish_fetch_run(
            conn,
            run_id,
            status="error" if error else "ok",
            fetched=fetched,
            inserted=inserted,
            error=error,
        )
    finally:
        conn.close()

    if error:
        return FetchSummary(source=source, fetched=fetched, inserted=inserted, error=error)
    log.info(
        "news fetch: freshrss done fetched=%d inserted=%d", fetched, inserted
    )
    return FetchSummary(source=source, fetched=fetched, inserted=inserted)


async def fetch_all_sources(
    *,
    from_ts: int | None = None,
    to_ts: int | None = None,
) -> list[FetchSummary]:
    """Fetch every configured source, swallowing per-source failures so a
    single bad source doesn't stop the rest. Used by the scheduled job
    (no range = incremental) and the manual API trigger (range from the
    UI's period selector)."""
    settings = get_settings()
    summaries: list[FetchSummary] = []
    if settings.news.sources.freshrss is None:
        log.warning("news fetch: no source configured (news.sources.freshrss is null)")
        return summaries
    try:
        summaries.append(await fetch_freshrss(from_ts=from_ts, to_ts=to_ts))
    except Exception as exc:
        log.exception("news fetch: freshrss raised")
        summaries.append(
            FetchSummary(source="freshrss", fetched=0, inserted=0, error=str(exc))
        )
    return summaries


__all__ = ["FetchSummary", "fetch_all_sources", "fetch_freshrss"]
