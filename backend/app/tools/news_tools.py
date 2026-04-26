"""news.* — read access to the news cache + ability to mark articles
read from a chat.

These tools let the user have a conversation about what's in their
news feeds. The LLM can list categories, list feeds within a
category, list articles, read one article's body, and mark articles
as read (which also pushes the change back to FreshRSS via Fever).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date as _date, timedelta
from typing import Any

from app.db.connection import open_connection
from app.news import (
    articles,
    get_article,
    list_articles,
    list_feeds_with_counts,
    mark_article_read,
)

from .registry import ToolRegistry, text_result

log = logging.getLogger(__name__)


def _resolve_period(period: str | None) -> tuple[str, str]:
    """today | 7d | 30d | YYYY-MM-DD..YYYY-MM-DD → (from, to) ISO."""
    today = _date.today()
    if period and ".." in period:
        f, t = period.split("..", 1)
        return f.strip(), t.strip()
    if period == "today":
        d = today.isoformat()
        return d, d
    if period == "30d":
        return (today - timedelta(days=30)).isoformat(), today.isoformat()
    # default: 7d
    return (today - timedelta(days=7)).isoformat(), today.isoformat()


async def _list_categories(args: dict[str, Any]):
    period = str(args.get("period", "7d"))
    f, t = _resolve_period(period)
    conn = open_connection()
    try:
        feeds = list_feeds_with_counts(conn, from_iso=f, to_iso=t)
    finally:
        conn.close()
    by_cat: dict[str, dict[str, int]] = {}
    for s in feeds:
        cat = s.feed_group or "(uncategorized)"
        slot = by_cat.setdefault(cat, {"total": 0, "unread": 0, "feeds": 0})
        slot["total"] += s.total
        slot["unread"] += s.unread
        slot["feeds"] += 1
    if not by_cat:
        return text_result("(no categories in window)")
    lines = [f"Categories ({f} → {t}):"]
    for name in sorted(by_cat.keys(), key=str.casefold):
        d = by_cat[name]
        lines.append(
            f"- {name}: {d['feeds']} feed(s), {d['total']} article(s), "
            f"{d['unread']} unread"
        )
    return text_result("\n".join(lines))


async def _list_feeds(args: dict[str, Any]):
    period = str(args.get("period", "7d"))
    category = args.get("category")
    f, t = _resolve_period(period)
    conn = open_connection()
    try:
        feeds = list_feeds_with_counts(conn, from_iso=f, to_iso=t)
    finally:
        conn.close()
    if category:
        feeds = [s for s in feeds if (s.feed_group or "") == str(category)]
    if not feeds:
        return text_result("(no feeds match)")
    lines: list[str] = []
    for s in feeds:
        cat = s.feed_group or "(uncategorized)"
        lines.append(
            f"- [{s.feed_id}] {s.feed_title} ({cat}): "
            f"{s.unread} unread / {s.total} total"
        )
    return text_result("\n".join(lines))


async def _list_news(args: dict[str, Any]):
    period = str(args.get("period", "7d"))
    f, t = _resolve_period(period)
    feed_id = args.get("feed_id")
    category = args.get("category")
    unread_only = bool(args.get("unread_only", False))
    limit = int(args.get("limit", 25))
    conn = open_connection()
    try:
        arts = list_articles(
            conn,
            from_iso=f,
            to_iso=t,
            feed_id=str(feed_id) if feed_id else None,
            feed_group=str(category) if category else None,
            unread_only=unread_only,
            limit=max(1, min(limit, 200)),
        )
    finally:
        conn.close()
    if not arts:
        return text_result("(no articles match)")
    lines: list[str] = []
    for a in arts:
        flag = " " if a.is_read else "•"
        feed = a.feed_title or a.source
        lines.append(
            f"{flag} [{a.id}] {a.published_at[:10]} {feed}: {a.title}"
        )
    return text_result("\n".join(lines))


async def _read_news(args: dict[str, Any]):
    article_id = str(args["article_id"])
    conn = open_connection()
    try:
        a = get_article(conn, article_id)
    finally:
        conn.close()
    if a is None:
        return text_result(f"article {article_id!r} not found", is_error=True)
    record = articles.read_article(article_id)
    body = record.summary if record else "(article body not on disk)"
    url = record.url if record else None
    parts = [
        f"# {a.title}",
        f"Feed: {a.feed_title or a.source}"
        + (f" / {a.feed_group}" if a.feed_group else ""),
        f"Published: {a.published_at}",
        f"URL: {url or '(none)'}",
        f"Read: {'yes' if a.is_read else 'no'}",
        "",
        body,
    ]
    return text_result("\n".join(parts))


async def _mark_read(args: dict[str, Any]):
    article_id = str(args["article_id"])
    conn = open_connection()
    try:
        a = get_article(conn, article_id)
    finally:
        conn.close()
    if a is None:
        return text_result(f"article {article_id!r} not found", is_error=True)
    if a.is_read:
        return text_result(f"already read: {article_id}")
    conn = open_connection()
    try:
        mark_article_read(conn, article_id, is_read=True)
    finally:
        conn.close()

    async def _push() -> None:
        from app.news.service import push_mark_read

        try:
            await push_mark_read(
                article_id, source=a.source, external_id=a.external_id
            )
        except Exception:
            log.exception("news.mark_read: upstream push failed for %s", article_id)

    asyncio.create_task(_push())
    return text_result(f"marked read: {article_id}")


def register_all(reg: ToolRegistry) -> None:
    reg.register(
        "news.list_categories",
        "List the FreshRSS categories (folders) that have at least one "
        "article in the period, with per-category total + unread counts.",
        {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "today | 7d | 30d | YYYY-MM-DD..YYYY-MM-DD. Default: 7d.",
                },
            },
        },
        _list_categories,
    )
    reg.register(
        "news.list_feeds",
        "List the feeds in the news cache, optionally filtered to one "
        "category (folder). Returns each feed's id, title, category, and "
        "unread/total counts.",
        {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "today | 7d | 30d. Default: 7d."},
                "category": {
                    "type": "string",
                    "description": "Optional category/folder name (matches feed_group exactly).",
                },
            },
        },
        _list_feeds,
    )
    reg.register(
        "news.list_news",
        "List article headers in the period. Filters: feed_id, category, "
        "unread_only. Returns one line per article with id, date, feed, "
        "and title. Use the id with news.read_news / news.mark_read.",
        {
            "type": "object",
            "properties": {
                "period": {"type": "string"},
                "feed_id": {"type": "string"},
                "category": {"type": "string"},
                "unread_only": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 25, "minimum": 1, "maximum": 200},
            },
        },
        _list_news,
    )
    reg.register(
        "news.read_news",
        "Read one article's title, source, and full summary body (loaded "
        "from disk).",
        {
            "type": "object",
            "properties": {
                "article_id": {"type": "string"},
            },
            "required": ["article_id"],
        },
        _read_news,
    )
    reg.register(
        "news.mark_read",
        "Mark a news article as read locally and push the change to "
        "FreshRSS via the Fever API. No-ops if the article is already "
        "read.",
        {
            "type": "object",
            "properties": {
                "article_id": {"type": "string"},
            },
            "required": ["article_id"],
        },
        _mark_read,
    )
