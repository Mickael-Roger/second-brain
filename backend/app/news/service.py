"""News-fetch orchestration.

Pulls items from each configured source, persists new articles to
SQLite, and records a fetch-run row. Clustering is a separate pass
(see `cluster.py`) because it's expensive enough that we don't want
to run it on every 30-minute fetch.
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


async def fetch_freshrss() -> FetchSummary:
    """One fetch pass over the FreshRSS source."""
    settings = get_settings()
    cfg = settings.news.sources.freshrss
    if cfg is None:
        raise RuntimeError("news.sources.freshrss is not configured")

    source = "freshrss"
    conn = open_connection()
    try:
        run_id = create_fetch_run(conn, kind="fetch", source=source)
        since = _last_external_id(conn, source)
    finally:
        conn.close()

    fetched = 0
    inserted = 0
    error: str | None = None
    try:
        async with FeverClient(base_url=cfg.base_url, api_key=cfg.api_key) as client:
            feeds = await client.feeds()
            items = await client.items_since(
                since_id=since, max_items=cfg.max_items_per_run
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
        "news fetch: freshrss fetched=%d inserted=%d (since_id=%d)",
        fetched, inserted, since,
    )
    return FetchSummary(source=source, fetched=fetched, inserted=inserted)


async def fetch_all_sources() -> list[FetchSummary]:
    """Fetch every configured source, swallowing per-source failures so a
    single bad source doesn't stop the rest. Used by the scheduled job
    and the manual API trigger."""
    settings = get_settings()
    summaries: list[FetchSummary] = []
    if settings.news.sources.freshrss is not None:
        try:
            summaries.append(await fetch_freshrss())
        except Exception as exc:
            log.exception("news fetch: freshrss raised")
            summaries.append(
                FetchSummary(source="freshrss", fetched=0, inserted=0, error=str(exc))
            )
    return summaries


__all__ = ["FetchSummary", "fetch_all_sources", "fetch_freshrss"]
