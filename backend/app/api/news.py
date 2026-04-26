"""News endpoints.

The webapp's News view drives this:

  - GET  /api/news/feeds                       feed list with counts
  - GET  /api/news/articles                    article list (per feed/category, optionally unread-only)
  - GET  /api/news/articles/{id}               one article + summary + image
  - POST /api/news/articles/{id}/read          flip local + push to FreshRSS
  - POST /api/news/fetch                       manual trigger of a fetch pass
  - GET  /api/news/runs                        recent fetch runs (debug)
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
    get_article,
    list_articles,
    list_feeds_with_counts,
    list_recent_runs,
    mark_article_read,
    summaries,
)

router = APIRouter(prefix="/api/news", tags=["news"])
log = logging.getLogger(__name__)


# ── DTOs ────────────────────────────────────────────────────────────


class ArticleSummaryDTO(BaseModel):
    """Article header — what the per-feed list shows."""

    id: str
    source: str
    feed_id: str | None
    feed_title: str | None
    feed_group: str | None
    url: str | None
    title: str
    author: str | None
    published_at: str
    is_read: bool
    image_url: str | None


class ArticleDetailDTO(ArticleSummaryDTO):
    """Article detail — the right pane of the News tab. The summary is
    loaded from disk, not the DB."""

    summary: str | None


class FeedSummaryDTO(BaseModel):
    feed_id: str
    feed_title: str
    feed_group: str | None
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
        url=a.url,
        title=a.title,
        author=a.author,
        published_at=a.published_at,
        is_read=a.is_read,
        image_url=a.image_url,
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
    """Period selector → (from_ts, to_ts) in unix seconds (UTC).
    Inclusive boundaries: from = 00:00 UTC, to = 23:59:59 UTC."""
    today = date.today()
    if period == "custom":
        f = date.fromisoformat(from_) if from_ else today
        t = date.fromisoformat(to) if to else today
    elif period == "today":
        f = t = today
    elif period == "30d":
        f = today - timedelta(days=30)
        t = today
    else:  # default: 7d
        f = today - timedelta(days=7)
        t = today
    from_dt = datetime.combine(f, time.min, tzinfo=timezone.utc)
    to_dt = datetime.combine(t, time.max, tzinfo=timezone.utc)
    return int(from_dt.timestamp()), int(to_dt.timestamp())


# ── Endpoints ───────────────────────────────────────────────────────


@router.get("/feeds", response_model=list[FeedSummaryDTO])
def get_feeds(
    period: str = Query("7d"),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[FeedSummaryDTO]:
    """Feed sidebar for the News tab. The frontend groups by
    `feed_group` (the FreshRSS folder) to reproduce the FreshRSS
    sidebar layout."""
    f, t = _resolve_period_iso(period, from_, to)
    return [
        FeedSummaryDTO(
            feed_id=s.feed_id,
            feed_title=s.feed_title,
            feed_group=s.feed_group,
            total=s.total,
            unread=s.unread,
        )
        for s in list_feeds_with_counts(conn, from_iso=f, to_iso=t)
    ]


@router.get("/articles", response_model=list[ArticleSummaryDTO])
def get_articles(
    period: str = Query("7d"),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    feed_id: str | None = Query(None),
    feed_group: str | None = Query(None),
    unread_only: bool = Query(False),
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ArticleSummaryDTO]:
    """Article list for the News tab — newest-first, optionally
    filtered to one feed, one category (folder), or unread-only."""
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
    """Single article — the right pane. Summary is read from disk
    (the DB only stores metadata + the image URL)."""
    a = get_article(conn, article_id)
    if a is None:
        raise HTTPException(status_code=404, detail="article not found")
    summary = summaries.read_summary(article_id)
    base = _article_summary_dto(a)
    return ArticleDetailDTO(**base.model_dump(), summary=summary)


class MarkReadResponse(BaseModel):
    article_id: str
    is_read: bool


@router.post("/articles/{article_id}/read", response_model=MarkReadResponse)
async def mark_read(
    article_id: str,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> MarkReadResponse:
    """Mark an article as read locally AND push the change to
    FreshRSS via Fever's `mark=item&as=read` action. The FreshRSS
    push runs in the background — local state is what the UI
    reflects immediately."""
    a = get_article(conn, article_id)
    if a is None:
        raise HTTPException(status_code=404, detail="article not found")
    mark_article_read(conn, article_id, is_read=True)

    async def _push() -> None:
        from app.news.service import push_mark_read

        try:
            await push_mark_read(
                article_id, source=a.source, external_id=a.external_id
            )
        except Exception:
            log.exception("push_mark_read background failed for %s", article_id)

    asyncio.create_task(_push())
    return MarkReadResponse(article_id=article_id, is_read=True)


@router.post("/fetch", response_model=TriggerResponse, status_code=202)
async def trigger_fetch(
    period: str = Query(
        "7d", description="today | 7d | 30d | custom — scopes the manual fetch"
    ),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    _user: str = Depends(current_user),
) -> TriggerResponse:
    """Kick off an immediate fetch pass in the background, scoped to
    the selected period."""
    from app.config import get_settings
    from app.news.service import fetch_all_sources

    if get_settings().news.sources.freshrss is None:
        raise HTTPException(
            status_code=400,
            detail="news.sources.freshrss is not configured",
        )

    from_ts, to_ts = _resolve_period_ts(period, from_, to)
    log.info(
        "manual news fetch requested (period=%s, from_ts=%d, to_ts=%d)",
        period, from_ts, to_ts,
    )

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
