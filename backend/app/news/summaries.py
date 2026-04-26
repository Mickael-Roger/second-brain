"""On-disk article summaries.

Article descriptions used to live in `news_articles.description` —
small samples of HTML-stripped text. They were already non-trivial in
size (a few KB each) and the user wanted to keep the FULL body,
so storing in SQLite would have bloated both the DB file and every
SELECT *. Filesystem is a better fit: one file per article, lazy
read on detail view, atomic write at fetch time.

Files live at `<data_dir>/news_summaries/<safe_id>.md`. The id-to-
filename mapping replaces ':' and '/' with '_' so we never hit a
path traversal or platform-illegal character. Markdown extension is
declarative — bodies are usually plain text but we may store
markdown / light HTML in the future.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import get_settings

log = logging.getLogger(__name__)


def _summaries_dir() -> Path:
    return get_settings().app.data_dir / "news_summaries"


def _path_for(article_id: str) -> Path:
    safe = article_id.replace(":", "_").replace("/", "_").replace("..", "_")
    return _summaries_dir() / f"{safe}.md"


def write_summary(article_id: str, body: str) -> None:
    """Persist (or overwrite) the article's summary on disk. Empty
    bodies still create a (zero-byte) file so the caller can
    distinguish 'fetched but empty' from 'never fetched'."""
    path = _path_for(article_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(body or "", encoding="utf-8")
    except OSError:
        log.exception("could not write summary for %s", article_id)


def read_summary(article_id: str) -> str | None:
    """Read the article's summary, or None if no file exists."""
    path = _path_for(article_id)
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        log.exception("could not read summary for %s", article_id)
        return None


def delete_summary(article_id: str) -> None:
    path = _path_for(article_id)
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            log.exception("could not delete summary for %s", article_id)
