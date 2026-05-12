"""News-fetch orchestration (FreshRSS GReader API).

Two callers, both honoured by `fetch_all_sources`:

  - Manual UI fetch — incremental (items newer than the latest
    `published_at` we already have). Fast, just catches new items.

  - Scheduled cron — ranged over the last 30 days. Goes through every
    item in the window so anything previously missed (read articles
    older than the incremental reach, items not yet in our DB at all)
    is captured. INSERT-OR-IGNORE dedups across runs.

Both modes also run two completeness/reconciliation passes:

  - Pull every unread id from FreshRSS via
    `stream/items/ids?s=…/reading-list&xt=…/read`. Used to:
      (1) reconcile is_read in BOTH directions on every locally known
          article (covers toggles on items outside the 30-day window).
      (2) backfill unread items missing from our DB (very old unread
          articles below the incremental reach).

  - Pull every starred id from FreshRSS via
    `stream/items/ids?s=…/starred` and reconcile is_starred the same
    way. We do NOT backfill missing starred items here — they'll come
    through naturally on the next ranged pass.

Each new article gets a slim row in `news_articles` plus a JSON file
at `<data_dir>/news/<safe_id>.json` holding the full record.

Item ids
--------
GReader hands us 16-char zero-padded lowercase hex item ids. Existing
rows that pre-date the migration are stored as decimal (Fever's
format); the first run under this module converts them on the fly
(see :func:`_migrate_external_ids_to_hex`), guarded by a sentinel row
in `news_fetch_runs` so it can never run twice.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from app.config import get_settings
from app.db.connection import open_connection

from . import articles
from .greader_client import (
    GReaderClient,
    GReaderItem,
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
    list_id_mappings,
    purge_old_articles_with_ids,
    reconcile_read_state,
    reconcile_starred_state,
    replace_article_labels,
    rewrite_article_id,
    upsert_feed,
)

log = logging.getLogger(__name__)


@dataclass(slots=True)
class FetchSummary:
    source: str
    fetched: int
    inserted: int
    error: str | None = None


_FAVICON_MAX_BYTES = 52224
_FAVICON_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_FAVICON_REFRESH_DAYS = 30
# Marker recorded in `news_fetch_runs` once the decimal→hex re-encode
# completes. Presence of the row means "do not run again".
_HEX_MIGRATION_MARKER = "greader_external_id_hex_reencode_v1"


def _normalise_favicon(raw: str | bytes | None) -> str | None:
    """Coerce a favicon payload into a `data:` URI. Accepts a raw
    base64 string, a complete data URI, or raw bytes."""
    if raw is None:
        return None
    if isinstance(raw, bytes):
        if not raw or len(raw) > _FAVICON_MAX_BYTES:
            return None
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:image/png;base64,{b64}"
    s = raw.strip()
    if not s or len(s) > _FAVICON_MAX_BYTES:
        return None
    if s.startswith("data:"):
        return s
    if s.startswith("image/") and ";base64," in s:
        return f"data:{s}"
    return f"data:image/png;base64,{s}"


async def _maybe_fetch_favicon(
    icon_url: str | None,
    *,
    feed_id: str,
    existing_data_uri: str | None,
    existing_updated_at: str | None,
    http: httpx.AsyncClient,
) -> str | None:
    """Lazily refresh the on-disk favicon for a feed.

    Skip the network call when we already have a data URI that's
    younger than `_FAVICON_REFRESH_DAYS`. Errors are swallowed —
    favicons are decoration, not data."""
    if existing_data_uri and existing_updated_at:
        try:
            updated = datetime.fromisoformat(existing_updated_at)
            if (datetime.now(timezone.utc) - updated).days < _FAVICON_REFRESH_DAYS:
                return existing_data_uri
        except ValueError:
            pass
    if not icon_url:
        return existing_data_uri
    try:
        resp = await http.get(icon_url)
        if resp.status_code != 200:
            return existing_data_uri
        data = resp.content
        if not data or len(data) > _FAVICON_MAX_BYTES:
            return existing_data_uri
        ctype = (resp.headers.get("content-type") or "image/png").split(";")[0].strip()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{ctype};base64,{b64}"
    except Exception:
        log.debug("favicon fetch failed for feed %s (%s)", feed_id, icon_url)
        return existing_data_uri


def _published_to_unix(iso_str: str) -> int:
    """Best-effort ISO → unix seconds. Returns 0 on parse failure."""
    if not iso_str:
        return 0
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return 0


def _last_published_ts(conn: sqlite3.Connection, source: str) -> int:
    """Latest `published_at` we know about, as unix seconds. 0 if no rows."""
    row = conn.execute(
        "SELECT published_at FROM news_articles WHERE source = ? "
        "ORDER BY published_at DESC LIMIT 1",
        (source,),
    ).fetchone()
    if row is None:
        return 0
    return _published_to_unix(str(row["published_at"]))


# ── External-id re-encode (one-shot Fever → GReader) ───────────────


def _hex_migration_marker_present(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM news_fetch_runs WHERE kind = ? LIMIT 1",
        (_HEX_MIGRATION_MARKER,),
    ).fetchone()
    return row is not None


def _record_hex_migration(conn: sqlite3.Connection, *, count: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO news_fetch_runs "
        "(kind, source, started_at, finished_at, status, fetched, inserted, error) "
        "VALUES (?, ?, ?, ?, 'ok', ?, ?, NULL)",
        (
            _HEX_MIGRATION_MARKER,
            "freshrss",
            now,
            now,
            count,
            count,
        ),
    )


def _to_canonical_hex(external_id: str) -> str | None:
    """Return the 16-char hex form if `external_id` needs rewriting;
    None if it's already canonical (or unparseable, leave it alone).

    Disambiguation trap: a 16-char all-digit string is BOTH valid
    decimal and valid hex (since 0-9 ⊂ hex alphabet). Fever-style ids
    are timestamps in microseconds since epoch — currently ~1.7e15,
    so 16 digits — and need decimal→hex. Real GReader hex ids for
    any modern timestamp always contain a-f letters. We treat any
    pure-digit input as decimal-needing-conversion."""
    s = external_id.strip().lower()
    if not s:
        return None
    if s.isdigit():
        try:
            new = f"{int(s):016x}"
        except ValueError:
            return None
        return new if new != s else None
    if all(c in "0123456789abcdef" for c in s):
        if len(s) == 16:
            return None  # already canonical
        return s.rjust(16, "0")
    return None


def _migrate_external_ids_to_hex(conn: sqlite3.Connection) -> int:
    """One-shot rewrite of every freshrss row's external_id to the
    canonical 16-char lowercase hex form GReader uses. Also renames
    the corresponding JSON files under <data_dir>/news/.

    Idempotent via a sentinel row in `news_fetch_runs`. Returns the
    number of rows actually rewritten (0 on subsequent runs)."""
    if _hex_migration_marker_present(conn):
        return 0
    mappings = list_id_mappings(conn, "freshrss")
    rewrites: list[tuple[str, str, str, str]] = []
    for old_id, ext in mappings:
        new_ext = _to_canonical_hex(ext)
        if new_ext is None:
            continue
        new_id = f"freshrss:{new_ext}"
        rewrites.append((old_id, ext, new_id, new_ext))
    if not rewrites:
        _record_hex_migration(conn, count=0)
        return 0

    conn.execute("BEGIN")
    try:
        # FK news_article_labels(article_id) → news_articles(id) has
        # no ON UPDATE CASCADE; defer the check so we can rewire
        # children inside the same transaction as the parent rename.
        conn.execute("PRAGMA defer_foreign_keys = ON")
        for old_id, _old_ext, new_id, new_ext in rewrites:
            rewrite_article_id(conn, old_id, new_id, new_ext)
        _record_hex_migration(conn, count=len(rewrites))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    # Rename the on-disk JSON files outside the transaction — these
    # are best-effort; a missing file is recreated on next ingest.
    for old_id, _old_ext, new_id, _new_ext in rewrites:
        try:
            articles.rename_article(old_id, new_id)
        except Exception:
            log.exception("could not rename article JSON %s → %s", old_id, new_id)

    log.info(
        "news fetch: re-encoded %d freshrss external_id(s) to canonical hex",
        len(rewrites),
    )
    return len(rewrites)


# ── Item persistence ────────────────────────────────────────────────


def _store_items(
    items: list[GReaderItem],
    *,
    feeds: dict[str, "GReaderFeedMeta"],
    excluded_categories: set[str],
    source: str,
) -> tuple[int, int]:
    """Persist GReaderItems → SQLite + on-disk JSON.

    Returns (inserted_count, skipped_excluded_count)."""
    inserted = 0
    skipped_excluded = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = open_connection()
    try:
        for it in items:
            f = feeds.get(it.feed_id)
            cat = f.group_name if f else None
            if cat and cat in excluded_categories:
                skipped_excluded += 1
                continue
            article_id = f"{source}:{it.id}"
            is_new = insert_article(
                conn,
                source=source,
                external_id=it.id,
                feed_id=it.feed_id or None,
                feed_title=f.title if f else None,
                feed_group=cat,
                title=it.title,
                published_at=published_iso(it),
                is_read=it.is_read,
                is_starred=it.is_starred,
            )
            # Always align labels with whatever GReader currently says
            # — keeps local in sync with edits made elsewhere (web UI,
            # other clients).
            replace_article_labels(conn, article_id, it.labels)
            if is_new or not articles.article_exists(article_id):
                articles.write_article(
                    articles.ArticleRecord(
                        id=article_id,
                        source=source,
                        external_id=it.id,
                        feed_id=it.feed_id or None,
                        feed_title=f.title if f else None,
                        feed_group=cat,
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


# Lightweight typed view of the bits we keep from GReaderFeed at
# call sites. Reduces churn if greader_client's dataclass changes.
@dataclass(slots=True)
class GReaderFeedMeta:
    id: str
    title: str
    site_url: str | None
    group_name: str | None


def _condense_feeds(feeds_raw) -> dict[str, GReaderFeedMeta]:
    return {
        fid: GReaderFeedMeta(
            id=f.id,
            title=f.title,
            site_url=f.site_url,
            group_name=f.group_name,
        )
        for fid, f in feeds_raw.items()
    }


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
        _migrate_external_ids_to_hex(conn)
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
        since_ts = _last_published_ts(conn, source) if from_ts is None else 0
    finally:
        conn.close()

    if from_ts is None:
        log.info(
            "news fetch: freshrss starting (incremental, since_ts=%d)", since_ts,
        )
    else:
        log.info(
            "news fetch: freshrss starting (range, from_ts=%d, to_ts=%s)",
            from_ts, "now" if to_ts is None else str(to_ts),
        )

    fetched = 0
    inserted = 0
    error: str | None = None
    try:
        async with GReaderClient(
            base_url=cfg.base_url,
            username=cfg.username,
            password=cfg.password,
        ) as client:
            feeds_raw = await client.subscriptions()
            feeds = _condense_feeds(feeds_raw)

            # Refresh per-feed metadata + favicons (lazy).
            conn = open_connection()
            try:
                existing_favicons: dict[str, tuple[str | None, str | None]] = {}
                for row in conn.execute(
                    "SELECT id, favicon_data_uri, updated_at FROM news_feeds"
                ).fetchall():
                    existing_favicons[str(row["id"])] = (
                        row["favicon_data_uri"], row["updated_at"],
                    )
            finally:
                conn.close()

            sem = asyncio.Semaphore(4)
            async with httpx.AsyncClient(timeout=_FAVICON_TIMEOUT) as http:
                async def _resolve_favicon(fid: str) -> tuple[str, str | None]:
                    f = feeds_raw[fid]
                    existing_uri, existing_at = existing_favicons.get(fid, (None, None))
                    async with sem:
                        new_uri = await _maybe_fetch_favicon(
                            f.icon_url,
                            feed_id=fid,
                            existing_data_uri=existing_uri,
                            existing_updated_at=existing_at,
                            http=http,
                        )
                    return fid, new_uri

                favicon_results = await asyncio.gather(
                    *(_resolve_favicon(fid) for fid in feeds_raw),
                    return_exceptions=False,
                )
            favicons_by_id = dict(favicon_results)

            conn = open_connection()
            try:
                for f in feeds_raw.values():
                    fav = favicons_by_id.get(f.id)
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
                    since_ts=since_ts, max_items=cfg.max_items_per_run,
                )
            else:
                items = await client.items_in_range(
                    from_ts=from_ts, to_ts=to_ts,
                )
            fetched = len(items)
            excluded = set(cfg.excluded_categories or [])
            if items:
                read_count = sum(1 for it in items if it.is_read)
                log.info(
                    "news fetch: freshrss got %d items (read=%d, unread=%d)",
                    fetched, read_count, fetched - read_count,
                )
                ins, skipped = _store_items(
                    items,
                    feeds=feeds,
                    excluded_categories=excluded,
                    source=source,
                )
                inserted += ins
                if skipped:
                    log.info(
                        "news fetch: skipped %d items from excluded categories %s",
                        skipped, sorted(excluded),
                    )

            # ── Unread completeness + reconciliation ────────────────
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
                        "news fetch: %d unread item(s) missing locally; backfilling",
                        len(missing),
                    )
                    extra = await client.items_by_ids(missing)
                    fetched += len(extra)
                    if extra:
                        ins, _ = _store_items(
                            extra,
                            feeds=feeds,
                            excluded_categories=excluded,
                            source=source,
                        )
                        inserted += ins
                        log.info(
                            "news fetch: backfilled %d unread (caught up)", ins,
                        )

            # ── Starred reconciliation ──────────────────────────────
            try:
                starred_ids = await client.starred_item_ids()
            except Exception:
                log.exception("news fetch: starred_item_ids failed (non-fatal)")
                starred_ids = None
            if starred_ids is not None:
                conn = open_connection()
                try:
                    newly_starred, newly_unstarred = reconcile_starred_state(
                        conn, source=source, starred_external_ids=set(starred_ids),
                    )
                    if newly_starred or newly_unstarred:
                        log.info(
                            "news fetch: reconciled starred-state "
                            "(newly_starred=%d, newly_unstarred=%d)",
                            newly_starred, newly_unstarred,
                        )
                finally:
                    conn.close()
    except Exception as exc:
        error = str(exc)
        log.exception("news fetch: freshrss failed")

    conn = open_connection()
    try:
        finish_fetch_run(
            conn, run_id,
            status="error" if error else "ok",
            fetched=fetched, inserted=inserted, error=error,
        )
    finally:
        conn.close()

    if error:
        return FetchSummary(source=source, fetched=fetched, inserted=inserted, error=error)
    log.info(
        "news fetch: freshrss done fetched=%d inserted=%d", fetched, inserted,
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


# ── Upstream pushes (called from the API layer) ────────────────────


def _settings_or_none():
    settings = get_settings()
    cfg = settings.news.sources.freshrss
    return cfg


async def _with_client(coro_fn):
    """Boilerplate: open a GReaderClient on the configured FreshRSS
    instance and run `coro_fn(client)`. No-op if FreshRSS is not
    configured."""
    cfg = _settings_or_none()
    if cfg is None:
        log.warning("news push: freshrss not configured; skipping")
        return None
    async with GReaderClient(
        base_url=cfg.base_url,
        username=cfg.username,
        password=cfg.password,
    ) as client:
        return await coro_fn(client)


async def push_read_state(
    article_id: str,
    *,
    source: str,
    external_id: str,
    is_read: bool,
) -> None:
    if source != "freshrss":
        log.warning("push_read_state: unsupported source %s", source)
        return
    try:
        await _with_client(
            lambda c: c.mark_read(external_id) if is_read
            else c.mark_unread(external_id)
        )
    except Exception:
        log.exception("push_read_state: failed for %s (is_read=%s)", article_id, is_read)


async def push_starred_state(
    article_id: str,
    *,
    source: str,
    external_id: str,
    is_starred: bool,
) -> None:
    if source != "freshrss":
        log.warning("push_starred_state: unsupported source %s", source)
        return
    try:
        await _with_client(
            lambda c: c.set_starred(external_id, starred=is_starred)
        )
    except Exception:
        log.exception(
            "push_starred_state: failed for %s (is_starred=%s)", article_id, is_starred,
        )


async def push_label(
    article_id: str,
    *,
    source: str,
    external_id: str,
    label: str,
    add: bool,
) -> None:
    if source != "freshrss":
        log.warning("push_label: unsupported source %s", source)
        return
    try:
        await _with_client(
            lambda c: c.add_label(external_id, label) if add
            else c.remove_label(external_id, label)
        )
    except Exception:
        log.exception(
            "push_label: failed for %s (label=%s add=%s)", article_id, label, add,
        )


async def subscribe_feed(
    url: str, *, title: str | None = None, category: str | None = None,
) -> str:
    """Subscribe to ``url`` on FreshRSS. Returns the new ``feed/<id>``
    stream id so the caller can immediately fetch + persist the feed
    in `news_feeds`."""
    async def go(c: GReaderClient) -> str:
        return await c.subscribe(url, title=title, category=category)
    result = await _with_client(go)
    if result is None:
        raise RuntimeError("FreshRSS not configured")
    return result


async def unsubscribe_feed(feed_id: str) -> None:
    await _with_client(lambda c: c.unsubscribe(feed_id))


async def edit_feed(
    feed_id: str,
    *,
    title: str | None = None,
    add_category: str | None = None,
    remove_category: str | None = None,
) -> None:
    await _with_client(
        lambda c: c.edit_subscription(
            feed_id,
            title=title,
            add_category=add_category,
            remove_category=remove_category,
        )
    )


async def rename_category(old: str, new: str) -> None:
    await _with_client(lambda c: c.rename_category(old, new))


async def delete_category(
    name: str, *, member_feed_ids: list[str] | None = None,
) -> None:
    """Delete category ``name``. Tries ``disable-tag`` first; if the
    instance doesn't support it, falls back to removing the label
    from every member feed in ``member_feed_ids`` (FreshRSS prunes
    empty categories on its own)."""
    cfg = _settings_or_none()
    if cfg is None:
        log.warning("delete_category: freshrss not configured")
        return
    async with GReaderClient(
        base_url=cfg.base_url,
        username=cfg.username,
        password=cfg.password,
    ) as client:
        ok = await client.delete_category(name)
        if ok:
            return
        log.info(
            "delete_category: disable-tag not supported, falling back to "
            "per-feed label removal for %s feeds",
            len(member_feed_ids or []),
        )
        for fid in member_feed_ids or []:
            try:
                await client.edit_subscription(fid, remove_category=name)
            except Exception:
                log.exception(
                    "delete_category: failed to unfile feed %s from %s",
                    fid, name,
                )


# Backwards-compat alias kept for callers that still use the old name.
async def push_mark_read(article_id: str, *, source: str, external_id: str) -> None:
    await push_read_state(article_id, source=source, external_id=external_id, is_read=True)


__all__ = [
    "FetchSummary",
    "delete_category",
    "edit_feed",
    "fetch_all_sources",
    "fetch_freshrss",
    "push_label",
    "push_mark_read",
    "push_read_state",
    "push_starred_state",
    "rename_category",
    "subscribe_feed",
    "thirty_days_ago_ts",
    "unsubscribe_feed",
]
