"""On-disk article storage.

Each article gets a JSON file at `<data_dir>/news/<safe_id>.json`
holding the full record (url, author, image, summary, etc.). SQLite
keeps only the indexed metadata needed for list queries and
filtering. This split lets us:
  - Keep the SQLite file small and fast on SELECT * over the list
    pane.
  - Store full bodies (which can be substantial) without bloating
    the DB.
  - Capture every field FreshRSS hands us, even ones the UI doesn't
    surface yet, so we don't lose information at fetch time.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.config import get_settings

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ArticleRecord:
    """Full per-article record persisted to disk. Fields mirror the
    FeverItem we got from the upstream API plus the bits we computed
    at fetch time (image extraction, html→text)."""

    id: str                      # source + ":" + external_id
    source: str
    external_id: str
    feed_id: str | None
    feed_title: str | None
    feed_group: str | None
    site_url: str | None
    url: str | None              # the article's URL on the publisher's site
    title: str
    author: str | None
    published_at: str
    fetched_at: str
    image_url: str | None
    summary: str                 # plain-text body, stripped from html
    raw_html: str | None = None  # original feed body, kept verbatim
    extra: dict = field(default_factory=dict)


def _articles_dir() -> Path:
    return get_settings().app.data_dir / "news"


def _path_for(article_id: str) -> Path:
    safe = article_id.replace(":", "_").replace("/", "_").replace("..", "_")
    return _articles_dir() / f"{safe}.json"


def write_article(record: ArticleRecord) -> None:
    """Persist (or overwrite) the per-article JSON file."""
    path = _path_for(record.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(
            json.dumps(asdict(record), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        log.exception("could not write article JSON for %s", record.id)


def read_article(article_id: str) -> ArticleRecord | None:
    """Load the JSON file for an article. Returns None if missing or
    unreadable; the caller treats that as 'detail not yet captured'."""
    path = _path_for(article_id)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        log.exception("could not read article JSON for %s", article_id)
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.exception("article JSON for %s is malformed", article_id)
        return None
    if not isinstance(data, dict):
        return None
    return ArticleRecord(
        id=str(data.get("id") or article_id),
        source=str(data.get("source") or ""),
        external_id=str(data.get("external_id") or ""),
        feed_id=data.get("feed_id"),
        feed_title=data.get("feed_title"),
        feed_group=data.get("feed_group"),
        site_url=data.get("site_url"),
        url=data.get("url"),
        title=str(data.get("title") or ""),
        author=data.get("author"),
        published_at=str(data.get("published_at") or ""),
        fetched_at=str(data.get("fetched_at") or ""),
        image_url=data.get("image_url"),
        summary=str(data.get("summary") or ""),
        raw_html=data.get("raw_html"),
        extra=data.get("extra") or {},
    )


def delete_article(article_id: str) -> None:
    """Remove the JSON file. Called after the SQLite row is deleted
    by retention so disk and DB stay in sync."""
    path = _path_for(article_id)
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            log.exception("could not delete article JSON for %s", article_id)


def article_exists(article_id: str) -> bool:
    """Cheap existence check — used by the fetch path to decide
    whether to write the JSON file (we only write on first sight)."""
    return _path_for(article_id).is_file()
