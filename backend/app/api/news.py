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
from datetime import date, timedelta

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


@router.post("/fetch", response_model=TriggerResponse, status_code=202)
async def trigger_fetch(_user: str = Depends(current_user)) -> TriggerResponse:
    """Kick off an immediate fetch pass in the background. The UI polls
    /api/news/runs (or just refreshes /events) to see the result."""
    from app.news.service import fetch_all_sources

    async def _go() -> None:
        try:
            await fetch_all_sources()
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
