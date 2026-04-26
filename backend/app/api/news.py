"""News & Events endpoints.

The webapp's News view drives this:

  - GET  /api/news/events?from=&to=    bubbles for a period
  - GET  /api/news/events/{id}         one event + its articles
  - POST /api/news/fetch               manual trigger of a fetch pass
  - POST /api/news/cluster             manual trigger of a cluster pass
  - GET  /api/news/runs                recent fetch + cluster runs (debug)
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
    StoredEvent,
    get_event,
    get_event_articles,
    list_events,
    list_recent_runs,
)

router = APIRouter(prefix="/api/news", tags=["news"])
log = logging.getLogger(__name__)


# ── DTOs ────────────────────────────────────────────────────────────


class ArticleDTO(BaseModel):
    id: str
    source: str
    feed_title: str | None
    url: str | None
    title: str
    description: str | None
    author: str | None
    published_at: str


class EventBubbleDTO(BaseModel):
    """One bubble in the News view. The frontend sizes the bubble by
    `article_count` and shows the article list on hover."""

    id: str
    title: str
    summary: str | None
    occurred_on: str
    article_count: int


class EventDetailDTO(EventBubbleDTO):
    articles: list[ArticleDTO]


class RunDTO(BaseModel):
    id: int
    kind: str
    source: str | None
    started_at: str
    finished_at: str | None
    status: str
    fetched: int
    inserted: int
    clustered: int
    error: str | None


class TriggerResponse(BaseModel):
    started: bool


def _event_to_bubble(e: StoredEvent) -> EventBubbleDTO:
    return EventBubbleDTO(
        id=e.id,
        title=e.title,
        summary=e.summary,
        occurred_on=e.occurred_on,
        article_count=e.article_count,
    )


# ── Endpoints ───────────────────────────────────────────────────────


@router.get("/events", response_model=list[EventBubbleDTO])
def get_events(
    period: str = Query("7d", description="today | 7d | 30d | custom"),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[EventBubbleDTO]:
    """List event bubbles for a period.

    Convenience aliases: `period=today | 7d | 30d`. Or pass explicit
    `from=YYYY-MM-DD&to=YYYY-MM-DD`. Custom range overrides the alias."""
    today = date.today()
    if from_ and to:
        f, t = from_, to
    elif period == "today":
        d = today.isoformat()
        f, t = d, d
    elif period == "30d":
        f = (today - timedelta(days=30)).isoformat()
        t = today.isoformat()
    else:  # default: 7d
        f = (today - timedelta(days=7)).isoformat()
        t = today.isoformat()
    events = list_events(conn, from_iso=f, to_iso=t)
    return [_event_to_bubble(e) for e in events]


@router.get("/events/{event_id}", response_model=EventDetailDTO)
def get_event_detail(
    event_id: str,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> EventDetailDTO:
    e = get_event(conn, event_id)
    if e is None:
        raise HTTPException(status_code=404, detail="event not found")
    articles = get_event_articles(conn, event_id)
    return EventDetailDTO(
        id=e.id,
        title=e.title,
        summary=e.summary,
        occurred_on=e.occurred_on,
        article_count=e.article_count,
        articles=[
            ArticleDTO(
                id=a.id,
                source=a.source,
                feed_title=a.feed_title,
                url=a.url,
                title=a.title,
                description=a.description,
                author=a.author,
                published_at=a.published_at,
            )
            for a in articles
        ],
    )


def _resolve_period_ts(
    period: str, from_: str | None, to: str | None
) -> tuple[int, int | None]:
    """Map the UI's period selector to a (from_ts, to_ts) pair, in unix
    seconds (UTC). `to_ts=None` means "no upper bound" (i.e. up to now).

    Boundaries are inclusive — the date `from_` becomes 00:00 UTC of
    that day, the date `to` becomes 23:59:59 UTC of that day, so an
    article published any time on `to` is captured."""
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


@router.post("/fetch", response_model=TriggerResponse, status_code=202)
async def trigger_fetch(
    period: str = Query(
        "7d", description="today | 7d | 30d | custom — scopes the manual fetch"
    ),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    _user: str = Depends(current_user),
) -> TriggerResponse:
    """Kick off an immediate fetch pass in the background, scoped to the
    selected period. Only articles published in that range are stored.

    The scheduler uses incremental fetches (no period) for efficiency;
    manual fetches use a date range so the user gets predictable
    results regardless of what's already in the DB."""
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
            summaries = await fetch_all_sources(from_ts=from_ts, to_ts=to_ts)
            for s in summaries:
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


@router.post("/cluster", response_model=TriggerResponse, status_code=202)
async def trigger_cluster(_user: str = Depends(current_user)) -> TriggerResponse:
    """Kick off an immediate cluster pass in the background."""
    from app.news.cluster import run_cluster_pass

    async def _go() -> None:
        try:
            await run_cluster_pass()
        except Exception:
            log.exception("manual news cluster failed")

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
            clustered=r.clustered,
            error=r.error,
        )
        for r in runs
    ]
