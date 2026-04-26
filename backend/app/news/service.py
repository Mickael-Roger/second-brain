"""News-fetch orchestration.

Two modes:

  - Incremental (`from_ts is None`): used by the scheduler. Walks Fever
    forward from the last `external_id` we already stored — efficient,
    pulls only new items.

  - Ranged (`from_ts` set): used by the manual API trigger. Walks Fever
    backwards from the newest item via `max_id`, keeping only items
    whose `created_on_time` falls in the [`from_ts`, `to_ts`] window.

Each fetch:
  1. Purges articles older than `RETENTION_DAYS` (with disk cleanup).
  2. Pulls items from Fever.
  3. For each new item: stores metadata in SQLite, full body on disk,
     extracts the first image URL into the row.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from app.config import get_settings
from app.db.connection import open_connection

from . import summaries
from .fever_client import (
    FeverClient,
    extract_first_image,
    html_to_plain_text,
    published_iso,
)
from .store import (
    RETENTION_DAYS,
    create_fetch_run,
    finish_fetch_run,
    insert_article,
    purge_old_articles_with_ids,
    upsert_feed,
)

log = logging.getLogger(__name__)


@dataclass(slots=True)
class FetchSummary:
    source: str
    fetched: int
    inserted: int
    error: str | None = None


_FAVICON_MAX_BYTES = 8192  # ~8 KB, plenty for a 16-32px PNG/ICO


def _normalise_favicon(raw: str | None) -> str | None:
    """Coerce Fever's favicon payload to a `data:` URI we can plug
    straight into an <img src>. FreshRSS returns either a bare base64
    string (e.g. 'iVBORw0KGgo…') or already-prefixed
    'image/png;base64,iVBORw…' — guard both shapes. Drops payloads
    above the size cap so a runaway favicon can't bloat the DB."""
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    if len(s) > _FAVICON_MAX_BYTES:
        return None
    if s.startswith("data:"):
        return s
    if s.startswith("image/") and ";base64," in s:
        return f"data:{s}"
    # Bare base64 — assume PNG (the common case in FreshRSS).
    return f"data:image/png;base64,{s}"


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
        # Purge old articles AND their on-disk summaries before we
        # fetch — keeps both the DB and the data dir bounded without
        # a separate housekeeping job.
        purged_ids = purge_old_articles_with_ids(conn)
        if purged_ids:
            log.info(
                "news fetch: purged %d article(s) older than %d days",
                len(purged_ids), RETENTION_DAYS,
            )
        for aid in purged_ids:
            summaries.delete_summary(aid)
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
            try:
                favicons = await client.favicons()
            except Exception:
                # Favicons are nice-to-have — a 5xx on this endpoint
                # shouldn't kill the fetch.
                log.exception("news fetch: favicons endpoint failed (non-fatal)")
                favicons = {}
            # Refresh per-feed metadata (title, group, favicon) into
            # news_feeds so the UI's article list can render the icon.
            conn = open_connection()
            try:
                for f in feeds.values():
                    fav = (
                        favicons.get(f.favicon_id)
                        if f.favicon_id
                        else None
                    )
                    upsert_feed(
                        conn,
                        feed_id=f.id,
                        title=f.title,
                        feed_group=f.group_name,
                        site_url=f.site_url,
                        favicon_data_uri=_normalise_favicon(fav),
                    )
            finally:
                conn.close()
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
                read_count = sum(1 for it in items if it.is_read)
                log.info(
                    "news fetch: freshrss got %d items (read=%d, unread=%d)",
                    fetched, read_count, fetched - read_count,
                )
                excluded = set(cfg.excluded_group_ids or [])
                skipped_excluded = 0
                conn = open_connection()
                try:
                    for it in items:
                        f = feeds.get(it.feed_id)
                        if f and f.group_id and f.group_id in excluded:
                            skipped_excluded += 1
                            continue
                        article_id = f"{source}:{it.id}"
                        is_new = insert_article(
                            conn,
                            source=source,
                            external_id=it.id,
                            feed_id=it.feed_id or None,
                            feed_title=f.title if f else None,
                            feed_group=f.group_name if f else None,
                            url=it.url,
                            title=it.title,
                            author=it.author,
                            published_at=published_iso(it),
                            is_read=it.is_read,
                            image_url=extract_first_image(it.html),
                        )
                        if is_new:
                            # Persist the full body to disk only on
                            # first sight. On a duplicate the row
                            # already has the body file from before
                            # and the content is unchanged.
                            summaries.write_summary(
                                article_id, html_to_plain_text(it.html)
                            )
                            inserted += 1
                finally:
                    conn.close()
                if skipped_excluded:
                    log.info(
                        "news fetch: skipped %d items from excluded folders %s",
                        skipped_excluded, sorted(excluded),
                    )
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
    single bad source doesn't stop the rest."""
    settings = get_settings()
    summaries_out: list[FetchSummary] = []
    if settings.news.sources.freshrss is None:
        log.warning("news fetch: no source configured (news.sources.freshrss is null)")
        return summaries_out
    try:
        summaries_out.append(await fetch_freshrss(from_ts=from_ts, to_ts=to_ts))
    except Exception as exc:
        log.exception("news fetch: freshrss raised")
        summaries_out.append(
            FetchSummary(source="freshrss", fetched=0, inserted=0, error=str(exc))
        )
    return summaries_out


async def push_mark_read(article_id: str, *, source: str, external_id: str) -> None:
    """Push a read-state change to the upstream source. Today only
    freshrss is supported; other sources will land later."""
    settings = get_settings()
    if source != "freshrss":
        log.warning("push_mark_read: unsupported source %s", source)
        return
    cfg = settings.news.sources.freshrss
    if cfg is None:
        return
    try:
        async with FeverClient(base_url=cfg.base_url, api_key=cfg.api_key) as client:
            await client.mark_item_read(external_id)
    except Exception:
        log.exception("push_mark_read: failed for %s", article_id)


__all__ = [
    "FetchSummary",
    "fetch_all_sources",
    "fetch_freshrss",
    "push_mark_read",
]
