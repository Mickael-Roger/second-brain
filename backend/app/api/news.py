"""News endpoints.

  - GET  /api/news/feeds                       feed list with counts
  - GET  /api/news/articles                    article list (per feed/category, optionally unread-only)
  - GET  /api/news/articles/{id}               one article (header from SQLite, body from JSON)
  - POST /api/news/articles/{id}/read          flip local + push to FreshRSS
  - POST /api/news/fetch                       manual trigger (incremental by default)
  - GET  /api/news/runs                        recent fetch runs (debug)

The list endpoint stays metadata-only (cheap to scroll). The detail
endpoint joins in the JSON-on-disk record so we can show the full
body / image / source link.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import current_user
from app.db.connection import get_db
from app.news import (
    StoredArticle,
    articles,
    get_article,
    list_articles,
    list_feeds_with_counts,
    list_recent_runs,
    mark_article_read,
)

router = APIRouter(prefix="/api/news", tags=["news"])
log = logging.getLogger(__name__)


# ── DTOs ────────────────────────────────────────────────────────────


class ArticleSummaryDTO(BaseModel):
    """Article header — what the per-feed list shows. Slim by design:
    list rendering only needs id, title, feed bits, date, read flag,
    favicon. Body / image / url are loaded lazily from JSON in the
    detail endpoint."""

    id: str
    source: str
    feed_id: str | None
    feed_title: str | None
    feed_group: str | None
    feed_favicon: str | None
    title: str
    published_at: str
    is_read: bool


class ArticleDetailDTO(ArticleSummaryDTO):
    url: str | None
    author: str | None
    image_url: str | None
    summary: str | None
    raw_html: str | None


class FeedSummaryDTO(BaseModel):
    feed_id: str
    feed_title: str
    feed_group: str | None
    favicon: str | None
    total: int
    unread: int


class RunDTO(BaseModel):
    id: int
    kind: str
    source: str | None
    started_at: str
    finished_at: str | None
    status: str
    fetched: int
    inserted: int
    error: str | None


class TriggerResponse(BaseModel):
    started: bool


def _article_summary_dto(a: StoredArticle) -> ArticleSummaryDTO:
    return ArticleSummaryDTO(
        id=a.id,
        source=a.source,
        feed_id=a.feed_id,
        feed_title=a.feed_title,
        feed_group=a.feed_group,
        feed_favicon=a.feed_favicon,
        title=a.title,
        published_at=a.published_at,
        is_read=a.is_read,
    )


def _resolve_period_iso(
    period: str, from_: str | None, to: str | None
) -> tuple[str, str]:
    today = date.today()
    if period == "custom" and from_ and to:
        return from_, to
    if period == "today":
        d = today.isoformat()
        return d, d
    if period == "30d":
        return (today - timedelta(days=30)).isoformat(), today.isoformat()
    return (today - timedelta(days=7)).isoformat(), today.isoformat()


def _resolve_period_ts(
    period: str, from_: str | None, to: str | None
) -> tuple[int, int | None]:
    today = date.today()
    if period == "custom":
        f = date.fromisoformat(from_) if from_ else today
        t = date.fromisoformat(to) if to else today
    elif period == "today":
        f = t = today
    elif period == "30d":
        f = today - timedelta(days=30)
        t = today
    else:
        f = today - timedelta(days=7)
        t = today
    from_dt = datetime.combine(f, time.min, tzinfo=timezone.utc)
    to_dt = datetime.combine(t, time.max, tzinfo=timezone.utc)
    return int(from_dt.timestamp()), int(to_dt.timestamp())


# ── Endpoints ───────────────────────────────────────────────────────


@router.get("/feeds", response_model=list[FeedSummaryDTO])
def get_feeds(
    period: str = Query("30d"),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[FeedSummaryDTO]:
    f, t = _resolve_period_iso(period, from_, to)
    return [
        FeedSummaryDTO(
            feed_id=s.feed_id,
            feed_title=s.feed_title,
            feed_group=s.feed_group,
            favicon=s.favicon,
            total=s.total,
            unread=s.unread,
        )
        for s in list_feeds_with_counts(conn, from_iso=f, to_iso=t)
    ]


@router.get("/articles", response_model=list[ArticleSummaryDTO])
def get_articles(
    period: str = Query("30d"),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    feed_id: str | None = Query(None),
    feed_group: str | None = Query(None),
    unread_only: bool = Query(False),
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ArticleSummaryDTO]:
    f, t = _resolve_period_iso(period, from_, to)
    arts = list_articles(
        conn,
        from_iso=f,
        to_iso=t,
        feed_id=feed_id,
        feed_group=feed_group,
        unread_only=unread_only,
    )
    return [_article_summary_dto(a) for a in arts]


@router.get("/articles/{article_id}", response_model=ArticleDetailDTO)
def get_article_detail(
    article_id: str,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> ArticleDetailDTO:
    """Article detail: header from SQLite, full body from JSON.
    Returns the SQLite-only fields if the JSON file is missing
    (shouldn't happen but guards against partial state)."""
    a = get_article(conn, article_id)
    if a is None:
        raise HTTPException(status_code=404, detail="article not found")
    record = articles.read_article(article_id)
    base = _article_summary_dto(a)
    return ArticleDetailDTO(
        **base.model_dump(),
        url=record.url if record else None,
        author=record.author if record else None,
        image_url=record.image_url if record else None,
        summary=record.summary if record else None,
        raw_html=record.raw_html if record else None,
    )


class MarkReadResponse(BaseModel):
    article_id: str
    is_read: bool


def _toggle_read(
    article_id: str, conn: sqlite3.Connection, *, is_read: bool
) -> MarkReadResponse:
    """Shared body for the /read and /unread endpoints. Updates local
    state synchronously, then fires a background task to push the
    change upstream to FreshRSS."""
    a = get_article(conn, article_id)
    if a is None:
        raise HTTPException(status_code=404, detail="article not found")
    mark_article_read(conn, article_id, is_read=is_read)

    async def _push() -> None:
        from app.news.service import push_read_state

        try:
            await push_read_state(
                article_id,
                source=a.source,
                external_id=a.external_id,
                is_read=is_read,
            )
        except Exception:
            log.exception(
                "push_read_state background failed for %s (is_read=%s)",
                article_id, is_read,
            )

    asyncio.create_task(_push())
    return MarkReadResponse(article_id=article_id, is_read=is_read)


@router.post("/articles/{article_id}/read", response_model=MarkReadResponse)
async def mark_read(
    article_id: str,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> MarkReadResponse:
    return _toggle_read(article_id, conn, is_read=True)


@router.post("/articles/{article_id}/unread", response_model=MarkReadResponse)
async def mark_unread(
    article_id: str,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> MarkReadResponse:
    """Flip a read article back to unread, locally and on FreshRSS."""
    return _toggle_read(article_id, conn, is_read=False)


# ── Capture: turn an article into an Obsidian vault note ───────────


class CaptureResponse(BaseModel):
    path: str


def _load_article_record(article_id: str, conn: sqlite3.Connection):
    """Fetch the SQLite header + JSON body, raising HTTPException on
    misses. Returns the ArticleRecord usable by capture flows."""
    a = get_article(conn, article_id)
    if a is None:
        raise HTTPException(status_code=404, detail="article not found")
    record = articles.read_article(article_id)
    if record is None:
        # The detail JSON should always exist for a row we have in
        # SQLite; if it's missing, capture cannot synthesize content.
        raise HTTPException(
            status_code=409,
            detail="article body not yet captured on disk; try fetching again",
        )
    return record


@router.post("/articles/{article_id}/keep", response_model=CaptureResponse)
async def capture_keep_endpoint(
    article_id: str,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> CaptureResponse:
    """LLM-generated short digest → Raw/Feeds/Notes/<title>.md."""
    from app.news.capture import capture_keep

    record = _load_article_record(article_id, conn)
    try:
        path = await capture_keep(record)
    except Exception as exc:
        log.exception("news capture (keep) failed for %s", article_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return CaptureResponse(path=path)


@router.post("/articles/{article_id}/article", response_model=CaptureResponse)
async def capture_article_endpoint(
    article_id: str,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> CaptureResponse:
    """LLM-generated full article → Raw/Feeds/Articles/<title>.md."""
    from app.news.capture import capture_article

    record = _load_article_record(article_id, conn)
    try:
        path = await capture_article(record)
    except Exception as exc:
        log.exception("news capture (article) failed for %s", article_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return CaptureResponse(path=path)


@router.post("/articles/{article_id}/watched", response_model=CaptureResponse)
async def capture_watched_endpoint(
    article_id: str,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> CaptureResponse:
    """Bare stub (link only) → Raw/Feeds/Youtube/<title>.md."""
    from app.news.capture import capture_watched

    record = _load_article_record(article_id, conn)
    try:
        path = await capture_watched(record)
    except Exception as exc:
        log.exception("news capture (watched) failed for %s", article_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return CaptureResponse(path=path)


@router.post("/fetch", response_model=TriggerResponse, status_code=202)
async def trigger_fetch(
    period: str | None = Query(
        None,
        description=(
            "Optional period scope (today | 7d | 30d | custom). When "
            "omitted, the manual fetch runs in incremental mode (since_id) "
            "for speed."
        ),
    ),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    _user: str = Depends(current_user),
) -> TriggerResponse:
    from app.config import get_settings
    from app.news.service import fetch_all_sources

    if get_settings().news.sources.freshrss is None:
        raise HTTPException(
            status_code=400,
            detail="news.sources.freshrss is not configured",
        )

    if period:
        from_ts, to_ts = _resolve_period_ts(period, from_, to)
        log.info(
            "manual news fetch requested (period=%s, from_ts=%d, to_ts=%d)",
            period, from_ts, to_ts,
        )
    else:
        from_ts = None
        to_ts = None
        log.info("manual news fetch requested (incremental, no period)")

    async def _go() -> None:
        try:
            results = await fetch_all_sources(from_ts=from_ts, to_ts=to_ts)
            for s in results:
                if s.error:
                    log.warning("manual news fetch: %s error=%s", s.source, s.error)
                else:
                    log.info(
                        "manual news fetch: %s fetched=%d inserted=%d",
                        s.source, s.fetched, s.inserted,
                    )
        except Exception:
            log.exception("manual news fetch failed")

    asyncio.create_task(_go())
    return TriggerResponse(started=True)


@router.get("/runs", response_model=list[RunDTO])
def get_runs(
    limit: int = Query(20, ge=1, le=100),
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[RunDTO]:
    runs = list_recent_runs(conn, limit=limit)
    return [
        RunDTO(
            id=r.id,
            kind=r.kind,
            source=r.source,
            started_at=r.started_at,
            finished_at=r.finished_at,
            status=r.status,
            fetched=r.fetched,
            inserted=r.inserted,
            error=r.error,
        )
        for r in runs
    ]
