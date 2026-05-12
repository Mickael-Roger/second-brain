"""News endpoints.

Reads:
  - GET    /api/news/feeds                         feed list with counts
  - GET    /api/news/articles                      article list (per feed/category/label/starred)
  - GET    /api/news/articles/{id}                 one article (header + body)
  - GET    /api/news/labels                        user-defined labels
  - GET    /api/news/categories                    distinct feed categories
  - GET    /api/news/runs                          recent fetch runs (debug)

Item-state writes (local flip + upstream push):
  - POST   /api/news/articles/{id}/read
  - POST   /api/news/articles/{id}/unread
  - POST   /api/news/articles/{id}/star
  - POST   /api/news/articles/{id}/unstar
  - POST   /api/news/articles/{id}/labels          {"label": str}
  - DELETE /api/news/articles/{id}/labels/{label}

Feed CRUD (synchronous round-trip to FreshRSS; locally upserted on success):
  - POST   /api/news/feeds                         {"url", "title"?, "category"?}
  - PATCH  /api/news/feeds/{feed_id}               {"title"?, "category"?}
  - DELETE /api/news/feeds/{feed_id}

Category CRUD (FreshRSS-side; folder lifecycle is feed-driven so creating
an empty category is a no-op, just remembered in `news_labels`):
  - PATCH  /api/news/categories/{old}              {"name": str}
  - DELETE /api/news/categories/{name}

Plus the existing capture flows and POST /fetch.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import current_user
from app.db.connection import get_db
from app.news import (
    StoredArticle,
    add_article_label,
    articles,
    forget_label_everywhere,
    get_article,
    list_articles,
    list_categories,
    list_feeds_with_counts,
    list_labels,
    list_recent_runs,
    mark_article_read,
    mark_article_starred,
    remember_label,
    remove_article_label,
    upsert_feed,
)

router = APIRouter(prefix="/api/news", tags=["news"])
log = logging.getLogger(__name__)


# ── DTOs ────────────────────────────────────────────────────────────


class ArticleSummaryDTO(BaseModel):
    """Article header — what the per-feed list shows. Slim by design:
    list rendering only needs id, title, feed bits, date, read/star
    flags, labels, favicon. Body / image / url are loaded lazily from
    JSON in the detail endpoint."""

    id: str
    source: str
    feed_id: str | None
    feed_title: str | None
    feed_group: str | None
    feed_favicon: str | None
    title: str
    published_at: str
    is_read: bool
    is_starred: bool
    labels: list[str]


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
        is_starred=a.is_starred,
        labels=list(a.labels),
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
    label: str | None = Query(None),
    starred_only: bool = Query(False),
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
        label=label,
        starred_only=starred_only,
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


# ── Starred ────────────────────────────────────────────────────────


class MarkStarredResponse(BaseModel):
    article_id: str
    is_starred: bool


def _toggle_starred(
    article_id: str, conn: sqlite3.Connection, *, is_starred: bool,
) -> MarkStarredResponse:
    a = get_article(conn, article_id)
    if a is None:
        raise HTTPException(status_code=404, detail="article not found")
    mark_article_starred(conn, article_id, is_starred=is_starred)

    async def _push() -> None:
        from app.news.service import push_starred_state

        try:
            await push_starred_state(
                article_id,
                source=a.source,
                external_id=a.external_id,
                is_starred=is_starred,
            )
        except Exception:
            log.exception(
                "push_starred_state background failed for %s (is_starred=%s)",
                article_id, is_starred,
            )

    asyncio.create_task(_push())
    return MarkStarredResponse(article_id=article_id, is_starred=is_starred)


@router.post("/articles/{article_id}/star", response_model=MarkStarredResponse)
async def mark_starred(
    article_id: str,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> MarkStarredResponse:
    return _toggle_starred(article_id, conn, is_starred=True)


@router.post("/articles/{article_id}/unstar", response_model=MarkStarredResponse)
async def mark_unstarred(
    article_id: str,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> MarkStarredResponse:
    return _toggle_starred(article_id, conn, is_starred=False)


# ── Labels ─────────────────────────────────────────────────────────


_LABEL_NAME_RE = "[A-Za-z0-9 _.\\-/]{1,64}"


class LabelRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=64, pattern=_LABEL_NAME_RE)


class LabelsResponse(BaseModel):
    article_id: str
    labels: list[str]


@router.get("/labels", response_model=list[str])
def get_labels(
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[str]:
    return list_labels(conn)


@router.delete("/labels/{name}")
async def delete_label_endpoint(
    name: str,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, int | str]:
    """Detach a label from every article and drop it from the
    autocomplete index. Pushes the removal upstream for every
    article that had it. Best-effort on the upstream side — local
    state still reflects the deletion if FreshRSS is unreachable
    for a given article."""
    from app.news.service import push_label

    affected = [
        (r["article_id"], r["source"], r["external_id"])
        for r in conn.execute(
            "SELECT al.article_id, a.source, a.external_id "
            "FROM news_article_labels al "
            "JOIN news_articles a ON a.id = al.article_id "
            "WHERE al.label = ?",
            (name,),
        ).fetchall()
    ]
    count = forget_label_everywhere(conn, name)

    async def _push() -> None:
        for aid, source, ext in affected:
            try:
                await push_label(
                    aid, source=source, external_id=ext, label=name, add=False,
                )
            except Exception:
                log.exception(
                    "push_label remove (bulk) failed for %s label=%s",
                    aid, name,
                )

    asyncio.create_task(_push())
    return {"label": name, "affected": count}


@router.post(
    "/articles/{article_id}/labels", response_model=LabelsResponse,
)
async def add_label_endpoint(
    article_id: str,
    body: LabelRequest,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> LabelsResponse:
    a = get_article(conn, article_id)
    if a is None:
        raise HTTPException(status_code=404, detail="article not found")
    add_article_label(conn, article_id, body.label)

    async def _push() -> None:
        from app.news.service import push_label

        try:
            await push_label(
                article_id,
                source=a.source,
                external_id=a.external_id,
                label=body.label,
                add=True,
            )
        except Exception:
            log.exception(
                "push_label add failed for %s (label=%s)", article_id, body.label,
            )

    asyncio.create_task(_push())
    # Re-fetch to return the canonical list.
    updated = get_article(conn, article_id)
    return LabelsResponse(
        article_id=article_id,
        labels=list(updated.labels) if updated else [body.label],
    )


@router.delete(
    "/articles/{article_id}/labels/{label}", response_model=LabelsResponse,
)
async def remove_label_endpoint(
    article_id: str,
    label: str,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> LabelsResponse:
    a = get_article(conn, article_id)
    if a is None:
        raise HTTPException(status_code=404, detail="article not found")
    remove_article_label(conn, article_id, label)

    async def _push() -> None:
        from app.news.service import push_label

        try:
            await push_label(
                article_id,
                source=a.source,
                external_id=a.external_id,
                label=label,
                add=False,
            )
        except Exception:
            log.exception(
                "push_label remove failed for %s (label=%s)", article_id, label,
            )

    asyncio.create_task(_push())
    updated = get_article(conn, article_id)
    return LabelsResponse(
        article_id=article_id,
        labels=list(updated.labels) if updated else [],
    )


# ── Categories ─────────────────────────────────────────────────────


class CategoryRenameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, pattern=_LABEL_NAME_RE)


@router.get("/categories", response_model=list[str])
def get_categories(
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[str]:
    return list_categories(conn)


@router.patch("/categories/{old_name}")
async def rename_category_endpoint(
    old_name: str,
    body: CategoryRenameRequest,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, str]:
    """Rename a FreshRSS category. Local feed rows are renamed in step
    so the sidebar reflects the change without waiting for the next
    fetch cron."""
    from app.news.service import rename_category

    if old_name == body.name:
        return {"old": old_name, "new": body.name}
    try:
        await rename_category(old_name, body.name)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    conn.execute(
        "UPDATE news_feeds SET feed_group = ? WHERE feed_group = ?",
        (body.name, old_name),
    )
    conn.execute(
        "UPDATE news_articles SET feed_group = ? WHERE feed_group = ?",
        (body.name, old_name),
    )
    return {"old": old_name, "new": body.name}


@router.delete("/categories/{name}")
async def delete_category_endpoint(
    name: str,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, str]:
    """Delete a FreshRSS category. Tries `disable-tag` first; if the
    instance doesn't expose it the service falls back to removing the
    label from every member feed (FreshRSS auto-prunes empty
    categories). Member feeds remain — they just go to the root."""
    from app.news.service import delete_category

    member_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM news_feeds WHERE feed_group = ?", (name,)
        ).fetchall()
    ]
    try:
        await delete_category(name, member_feed_ids=member_ids)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    conn.execute(
        "UPDATE news_feeds SET feed_group = NULL WHERE feed_group = ?", (name,)
    )
    conn.execute(
        "UPDATE news_articles SET feed_group = NULL WHERE feed_group = ?", (name,)
    )
    return {"deleted": name}


# ── Feeds CRUD ─────────────────────────────────────────────────────


class FeedCreateRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=200)
    category: str | None = Field(
        default=None, max_length=64, pattern=_LABEL_NAME_RE,
    )


class FeedUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    category: str | None = Field(
        default=None, max_length=64, pattern=_LABEL_NAME_RE,
    )
    # Explicit detach: pass {"category": null, "detach": true} to drop
    # the feed out of its current category without re-filing it. (Without
    # this flag, ``category=None`` means "leave unchanged".)
    detach: bool = False


class FeedResponse(BaseModel):
    feed_id: str
    title: str | None
    feed_group: str | None
    site_url: str | None


@router.post("/feeds", response_model=FeedResponse, status_code=201)
async def subscribe_feed_endpoint(
    body: FeedCreateRequest,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> FeedResponse:
    from app.news.service import subscribe_feed

    try:
        stream_id = await subscribe_feed(
            body.url, title=body.title, category=body.category,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    feed_id = stream_id[len("feed/"):] if stream_id.startswith("feed/") else stream_id
    title = body.title or body.url
    upsert_feed(
        conn,
        feed_id=feed_id,
        title=title,
        feed_group=body.category,
        site_url=None,
        favicon_data_uri=None,
    )
    if body.category:
        # Keep the autocomplete index in sync so the new category
        # shows up immediately in the sidebar / picker.
        remember_label(conn, body.category)
    return FeedResponse(
        feed_id=feed_id, title=title,
        feed_group=body.category, site_url=None,
    )


@router.patch("/feeds/{feed_id}", response_model=FeedResponse)
async def edit_feed_endpoint(
    feed_id: str,
    body: FeedUpdateRequest,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> FeedResponse:
    from app.news.service import edit_feed

    row = conn.execute(
        "SELECT id, title, feed_group, site_url FROM news_feeds WHERE id = ?",
        (feed_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="feed not found")
    current_cat = row["feed_group"]
    add_cat: str | None = None
    remove_cat: str | None = None
    if body.detach and current_cat:
        remove_cat = current_cat
    elif body.category and body.category != current_cat:
        add_cat = body.category
        if current_cat:
            remove_cat = current_cat
    try:
        await edit_feed(
            feed_id, title=body.title, add_category=add_cat,
            remove_category=remove_cat,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    new_title = body.title if body.title is not None else row["title"]
    new_cat = None if body.detach else (
        body.category if body.category is not None else current_cat
    )
    conn.execute(
        "UPDATE news_feeds SET title = COALESCE(?, title), feed_group = ? "
        "WHERE id = ?",
        (new_title, new_cat, feed_id),
    )
    # Keep article rows aligned for sidebar counts / filtering.
    conn.execute(
        "UPDATE news_articles SET feed_title = COALESCE(?, feed_title), "
        "feed_group = ? WHERE feed_id = ?",
        (new_title, new_cat, feed_id),
    )
    if new_cat:
        remember_label(conn, new_cat)
    return FeedResponse(
        feed_id=feed_id, title=new_title,
        feed_group=new_cat, site_url=row["site_url"],
    )


@router.delete("/feeds/{feed_id}")
async def unsubscribe_feed_endpoint(
    feed_id: str,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, str]:
    from app.news.service import unsubscribe_feed

    row = conn.execute(
        "SELECT id FROM news_feeds WHERE id = ?", (feed_id,)
    ).fetchone()
    if row is None:
        # Tolerate orphan rows in news_articles — still try the
        # upstream unsubscribe in case it's a feed we know upstream
        # but never persisted locally.
        log.info("unsubscribe: feed %s not in news_feeds, calling upstream anyway", feed_id)
    try:
        await unsubscribe_feed(feed_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # Local cleanup: drop the feed row + every article it owns. The
    # cascade on news_article_labels removes any labels too.
    aids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM news_articles WHERE feed_id = ?", (feed_id,)
        ).fetchall()
    ]
    conn.execute("DELETE FROM news_articles WHERE feed_id = ?", (feed_id,))
    conn.execute("DELETE FROM news_feeds WHERE id = ?", (feed_id,))
    for aid in aids:
        articles.delete_article(aid)
    return {"feed_id": feed_id, "deleted": "ok"}


# ── Capture: turn an article into an Obsidian vault note ───────────


class CaptureResponse(BaseModel):
    path: str


class CustomCaptureRequest(BaseModel):
    instruction: str = Field(
        ..., min_length=1, max_length=400,
        description="Free-form instruction the agent will apply on this article via news.read_news + vault.* tools. Examples: 'save the link in TODO.md under Pro', 'append a paragraph to Wiki/Tech/AI-ML/Mistral.md', 'create a note in Notes/Cooking/'.",
    )


class CustomActionResponse(BaseModel):
    summary: str
    files_touched: list[str] = []


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


@router.post("/articles/{article_id}/custom", response_model=CustomActionResponse)
async def custom_action_endpoint(
    article_id: str,
    body: CustomCaptureRequest,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> CustomActionResponse:
    """Run an LLM agent NOW that applies the user's free-form
    instruction on this article. The agent has `news.read_news` (to
    fetch the article body) and the full `vault.*` toolset (to write
    anywhere in the vault). Returns the agent's final text turn plus
    the list of files it touched."""
    from app.news.capture import apply_custom_action

    record = _load_article_record(article_id, conn)
    try:
        result = await apply_custom_action(record, body.instruction)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("news custom action failed for %s", article_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return CustomActionResponse(
        summary=result.summary,
        files_touched=result.files_touched,
    )


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
