"""News-fetch orchestration.

Two callers, both honoured by `fetch_all_sources`:

  - Manual UI fetch — incremental (since_id from last stored max id).
    Fast, just catches new items.

  - Scheduled cron — ranged over the last 30 days. Goes through every
    item in the window so anything previously missed (read articles
    older than the since_id reach, items not yet in our DB at all) is
    captured. INSERT-OR-IGNORE dedups across runs.

Both modes also run the unread-completeness pass: pull every unread
item id from FreshRSS via `?api&unread_item_ids`, diff against what
we've already stored, fetch the missing ids in 50-id batches via
`?api&items&with_ids=…`. That covers very old unread items below the
30-day window.

Each new article gets:
  - A row in news_articles (slim metadata only).
  - A JSON file at <data_dir>/news/<safe_id>.json holding the full
    record (url, author, image, summary, raw html).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.db.connection import open_connection

from . import articles
from .fever_client import (
    FeverClient,
    FeverItem,
    extract_first_image,
    html_to_plain_text,
    published_iso,
)
from .store import (
    RETENTION_DAYS,
    create_fetch_run,
    existing_external_ids,
    finish_fetch_run,
    insert_article,
    purge_old_articles_with_ids,
    reconcile_read_state,
    upsert_feed,
)

log = logging.getLogger(__name__)


@dataclass(slots=True)
class FetchSummary:
    source: str
    fetched: int
    inserted: int
    error: str | None = None


_FAVICON_MAX_BYTES = 8192


def _normalise_favicon(raw: str | None) -> str | None:
    """Coerce Fever's favicon payload into a `data:` URI."""
    if raw is None:
        return None
    s = raw.strip()
    if not s or len(s) > _FAVICON_MAX_BYTES:
        return None
    if s.startswith("data:"):
        return s
    if s.startswith("image/") and ";base64," in s:
        return f"data:{s}"
    return f"data:image/png;base64,{s}"


def _last_external_id(conn: sqlite3.Connection, source: str) -> int:
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


def _store_items(
    items: list[FeverItem],
    *,
    feeds: dict,
    excluded_group_ids: set[str],
    source: str,
) -> tuple[int, int]:
    """Persist FeverItems → SQLite (metadata) + JSON (full record).

    Returns (inserted_count, skipped_excluded_count)."""
    inserted = 0
    skipped_excluded = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = open_connection()
    try:
        for it in items:
            f = feeds.get(it.feed_id)
            if f and f.group_id and f.group_id in excluded_group_ids:
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
                title=it.title,
                published_at=published_iso(it),
                is_read=it.is_read,
            )
            if is_new or not articles.article_exists(article_id):
                # Always make sure the JSON file exists for any row in
                # the DB. On UPSERT we keep the existing JSON to avoid
                # rewriting unchanged content; if the file went missing
                # (manual deletion, partial corruption) we recreate.
                articles.write_article(
                    articles.ArticleRecord(
                        id=article_id,
                        source=source,
                        external_id=it.id,
                        feed_id=it.feed_id or None,
                        feed_title=f.title if f else None,
                        feed_group=f.group_name if f else None,
                        site_url=f.site_url if f else None,
                        url=it.url,
                        title=it.title,
                        author=it.author,
                        published_at=published_iso(it),
                        fetched_at=now_iso,
                        image_url=extract_first_image(it.html),
                        summary=html_to_plain_text(it.html),
                        raw_html=it.html or None,
                    )
                )
                if is_new:
                    inserted += 1
    finally:
        conn.close()
    return inserted, skipped_excluded


async def fetch_freshrss(
    *,
    from_ts: int | None = None,
    to_ts: int | None = None,
) -> FetchSummary:
    """One fetch pass over the FreshRSS source.

    `from_ts is None` → incremental (fast). With from_ts → ranged."""
    settings = get_settings()
    cfg = settings.news.sources.freshrss
    if cfg is None:
        raise RuntimeError("news.sources.freshrss is not configured")

    source = "freshrss"
    conn = open_connection()
    try:
        run_id = create_fetch_run(conn, kind="fetch", source=source)
        # Read-only retention: purge READ articles older than the
        # window AND remove their JSON files. Unread are kept forever.
        purged_ids = purge_old_articles_with_ids(conn)
        if purged_ids:
            log.info(
                "news fetch: purged %d read article(s) older than %d days",
                len(purged_ids), RETENTION_DAYS,
            )
        for aid in purged_ids:
            articles.delete_article(aid)
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
                log.exception("news fetch: favicons endpoint failed (non-fatal)")
                favicons = {}
            # Refresh per-feed metadata.
            conn = open_connection()
            try:
                for f in feeds.values():
                    fav = favicons.get(f.favicon_id) if f.favicon_id else None
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

            # ── Primary fetch pass ───────────────────────────────────
            if from_ts is None:
                items = await client.items_since(
                    since_id=since, max_items=cfg.max_items_per_run
                )
            else:
                # Ranged walks aren't capped by max_items_per_run — we
                # want full 30-day coverage. The walk stops on its own
                # once a whole page falls below from_ts.
                items = await client.items_in_range(
                    from_ts=from_ts,
                    to_ts=to_ts,
                )
            fetched = len(items)
            excluded = set(cfg.excluded_group_ids or [])
            if items:
                read_count = sum(1 for it in items if it.is_read)
                log.info(
                    "news fetch: freshrss got %d items (read=%d, unread=%d)",
                    fetched, read_count, fetched - read_count,
                )
                ins, skipped = _store_items(
                    items,
                    feeds=feeds,
                    excluded_group_ids=excluded,
                    source=source,
                )
                inserted += ins
                if skipped:
                    log.info(
                        "news fetch: skipped %d items from excluded folders %s",
                        skipped, sorted(excluded),
                    )

            # ── Unread completeness + read-state reconciliation ──────
            # Pull every unread id from FreshRSS. Used for two things:
            # (1) reconcile is_read in BOTH directions on every locally
            #     known article — covers toggles on items outside the
            #     30-day ranged window, which the walk would otherwise
            #     miss. Skipped if the call failed so we don't falsely
            #     mark everything as read on a transient API error.
            # (2) backfill unread items missing from our DB (very old
            #     unread articles below the since_id walk's reach).
            #     items_by_ids batches by 50 with no cap.
            unread_ids: list[str] | None
            try:
                unread_ids = await client.unread_item_ids()
            except Exception:
                log.exception("news fetch: unread_item_ids failed (non-fatal)")
                unread_ids = None
            if unread_ids is not None:
                unread_set = set(unread_ids)
                conn = open_connection()
                try:
                    newly_read, newly_unread = reconcile_read_state(
                        conn, source=source, unread_external_ids=unread_set,
                    )
                    if newly_read or newly_unread:
                        log.info(
                            "news fetch: reconciled read-state "
                            "(newly_read=%d, newly_unread=%d)",
                            newly_read, newly_unread,
                        )
                    already = existing_external_ids(conn, source)
                finally:
                    conn.close()
                missing = [i for i in unread_ids if i not in already]
                if missing:
                    log.info(
                        "news fetch: %d unread item(s) missing locally; "
                        "backfilling all of them",
                        len(missing),
                    )
                    extra = await client.items_by_ids(missing)
                    fetched += len(extra)
                    if extra:
                        ins, _ = _store_items(
                            extra,
                            feeds=feeds,
                            excluded_group_ids=excluded,
                            source=source,
                        )
                        inserted += ins
                        log.info(
                            "news fetch: backfilled %d unread (caught up)",
                            ins,
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
    settings = get_settings()
    out: list[FetchSummary] = []
    if settings.news.sources.freshrss is None:
        log.warning("news fetch: no source configured (news.sources.freshrss is null)")
        return out
    try:
        out.append(await fetch_freshrss(from_ts=from_ts, to_ts=to_ts))
    except Exception as exc:
        log.exception("news fetch: freshrss raised")
        out.append(
            FetchSummary(source="freshrss", fetched=0, inserted=0, error=str(exc))
        )
    return out


def thirty_days_ago_ts() -> int:
    """Unix-seconds 30 days back, used by the cron to scope its
    ranged walk. The cron always fetches the full 30-day window so
    nothing is missed across restarts."""
    return int(
        (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).timestamp()
    )


async def push_read_state(
    article_id: str,
    *,
    source: str,
    external_id: str,
    is_read: bool,
) -> None:
    """Push a read-state change to the upstream source. `is_read=True`
    sends Fever's `mark=item&as=read`, False sends `as=unread`."""
    settings = get_settings()
    if source != "freshrss":
        log.warning("push_read_state: unsupported source %s", source)
        return
    cfg = settings.news.sources.freshrss
    if cfg is None:
        return
    try:
        async with FeverClient(base_url=cfg.base_url, api_key=cfg.api_key) as client:
            if is_read:
                await client.mark_item_read(external_id)
            else:
                await client.mark_item_unread(external_id)
    except Exception:
        log.exception("push_read_state: failed for %s (is_read=%s)", article_id, is_read)


# Backwards-compat alias kept for callers that still use the old name.
async def push_mark_read(article_id: str, *, source: str, external_id: str) -> None:
    await push_read_state(article_id, source=source, external_id=external_id, is_read=True)


__all__ = [
    "FetchSummary",
    "fetch_all_sources",
    "fetch_freshrss",
    "push_mark_read",
    "push_read_state",
    "thirty_days_ago_ts",
]
