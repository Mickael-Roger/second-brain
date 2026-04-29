"""Turn a news article into an Obsidian vault note.

Three flows used by the news tab's capture buttons:

  - keep    → Raw/Feeds/Notes/    short LLM digest (2-3 sentences)
  - article → Raw/Feeds/Articles/ full LLM article-style summary
  - watched → Raw/Feeds/Youtube/  bare stub (link only, no LLM)

Each call returns the vault-relative path of the created note.
Filenames are derived from the article title; on collision we
append a numeric suffix so the user can re-capture an article
without manual cleanup.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from app.llm import Message, TextBlock, complete
from app.news.articles import ArticleRecord
from app.vault import create_note, find_notes

log = logging.getLogger(__name__)


FOLDER_KEEP = "Raw/Feeds/Notes"
FOLDER_ARTICLE = "Raw/Feeds/Articles"
FOLDER_WATCHED = "Raw/Feeds/Youtube"

# Cap how much article body we feed the LLM. raw_html for some sites
# can be huge — past ~30 KB we hit no extra useful detail and pay in
# tokens.
MAX_BODY_CHARS = 30_000


# ── Filename helpers ─────────────────────────────────────────────────


_SLUG_DROP = re.compile(r"[^\w\s\-]+", re.UNICODE)
_SLUG_WS = re.compile(r"\s+")


def _slugify(title: str, *, max_len: int = 80) -> str:
    """Filesystem-safe filename, keeping unicode letters (Obsidian
    handles them fine). Strips punctuation, collapses whitespace."""
    s = title.strip()
    s = _SLUG_DROP.sub(" ", s)
    s = _SLUG_WS.sub(" ", s).strip()
    if not s:
        s = "untitled"
    if len(s) > max_len:
        s = s[:max_len].rstrip()
    return s


def _unique_title(folder: str, base: str) -> str:
    """Return a title that doesn't collide with an existing note in
    `folder`. Appends ' (2)', ' (3)', … until free.

    The folder may not exist yet (first capture for this source). In
    that case there's trivially no collision; `create_note` later calls
    `_write_text` which `mkdir(parents=True)`s before writing, so the
    folder will be created on the spot.
    """
    try:
        existing = set(find_notes("*.md", in_folder=folder, limit=500))
    except FileNotFoundError:
        existing = set()
    candidate = base
    i = 2
    while f"{folder}/{candidate}.md" in existing:
        candidate = f"{base} ({i})"
        i += 1
        if i > 99:
            # Pathological — fall back to a date-stamped name.
            return f"{base} ({date.today().isoformat()})"
    return candidate


# ── LLM prompts ──────────────────────────────────────────────────────


_KEEP_SYSTEM = """You will be given the title and body of a news article.

Return 2 to 3 short sentences in Markdown that capture the key
information of the article. Aim for what someone would want to remember
about this article without re-reading it.

Output rules:
- Plain Markdown, no headers, no preamble, no concluding meta-commentary.
- Same language as the article.
- No quotes around your output.
- Do not invent facts that are not in the source."""


_ARTICLE_SYSTEM = """You will be given the title, source, and body of a news article.

Write a complete Markdown note that summarizes the article in depth.
The reader should get the full picture without going to the source.

Output rules:
- Start with a brief 1-2 sentence intro paragraph.
- Then organize the content with H2 (`## Heading`) sections if it helps clarity.
- Use bullet lists where appropriate.
- Same language as the article.
- No `# Title` H1 (the note already has a title in its frontmatter / filename).
- No preamble like "Here is a summary…", no concluding meta-commentary.
- Do not invent facts that are not in the source.
- If the article is very short, your summary may be short too — don't pad."""


def _build_user_prompt(article: ArticleRecord, *, include_body: bool) -> str:
    """Compose the LLM's user message from the article record."""
    parts: list[str] = [
        f"Title: {article.title}",
    ]
    if article.author:
        parts.append(f"Author: {article.author}")
    feed = article.feed_title or article.source
    parts.append(f"Source: {feed}")
    if article.url:
        parts.append(f"URL: {article.url}")
    parts.append("")

    if include_body:
        body = article.raw_html or article.summary or ""
        body = body.strip()
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS] + "\n\n[truncated]"
        parts.append("---")
        parts.append(body)

    return "\n".join(parts)


async def _llm_keep_digest(article: ArticleRecord) -> str:
    user = _build_user_prompt(article, include_body=True)
    text = await complete(
        _KEEP_SYSTEM,
        [Message(role="user", content=[TextBlock(text=user)])],
    )
    return text.strip()


async def _llm_full_article(article: ArticleRecord) -> str:
    user = _build_user_prompt(article, include_body=True)
    text = await complete(
        _ARTICLE_SYSTEM,
        [Message(role="user", content=[TextBlock(text=user)])],
    )
    return text.strip()


# ── Note builders ────────────────────────────────────────────────────


def _frontmatter_for(article: ArticleRecord, *, kind: str, extra_tags: list[str] | None = None) -> dict:
    """Common frontmatter for captured-from-news vault notes."""
    fm: dict = {
        "source": article.feed_title or article.source,
        "feed_group": article.feed_group,
        "url": article.url,
        "published": article.published_at,
        "captured_at": date.today().isoformat(),
        "tags": ["feed", f"feed-{kind}"] + (extra_tags or []),
    }
    # Drop None values so the YAML stays clean.
    return {k: v for k, v in fm.items() if v not in (None, "", [])}


def _link_line(article: ArticleRecord) -> str:
    if not article.url:
        return ""
    label = article.feed_title or article.source or "source"
    return f"[{label}]({article.url})"


# ── Public API ───────────────────────────────────────────────────────


async def capture_keep(article: ArticleRecord) -> str:
    """Small LLM-generated digest in Raw/Feeds/Notes/. Returns the
    created note's vault-relative path."""
    digest = await _llm_keep_digest(article)
    title_base = _slugify(article.title)
    title = _unique_title(FOLDER_KEEP, title_base)

    body_parts: list[str] = [f"# {article.title}", "", digest]
    if article.url:
        body_parts.extend(["", _link_line(article)])
    body = "\n".join(body_parts)

    fm = _frontmatter_for(article, kind="note")
    note = await create_note(
        FOLDER_KEEP, title, body,
        frontmatter=fm,
        message=f"news: keep {article.id}",
    )
    return note.path


async def capture_article(article: ArticleRecord) -> str:
    """Full LLM-generated article in Raw/Feeds/Articles/. Returns the
    created note's vault-relative path."""
    full = await _llm_full_article(article)
    title_base = _slugify(article.title)
    title = _unique_title(FOLDER_ARTICLE, title_base)

    body_parts: list[str] = [f"# {article.title}", "", full]
    if article.url:
        body_parts.extend(["", "---", "", f"Source: {_link_line(article)}"])
    body = "\n".join(body_parts)

    fm = _frontmatter_for(article, kind="article")
    note = await create_note(
        FOLDER_ARTICLE, title, body,
        frontmatter=fm,
        message=f"news: article {article.id}",
    )
    return note.path


async def capture_watched(article: ArticleRecord) -> str:
    """Stub note in Raw/Feeds/Youtube/ — link only, no LLM. Records
    that the user has consumed (read/watched/listened) this content."""
    title_base = _slugify(article.title)
    title = _unique_title(FOLDER_WATCHED, title_base)

    body_parts: list[str] = [f"# {article.title}", ""]
    if article.url:
        body_parts.append(_link_line(article))
    body = "\n".join(body_parts)

    fm = _frontmatter_for(article, kind="watched")
    note = await create_note(
        FOLDER_WATCHED, title, body,
        frontmatter=fm,
        message=f"news: watched {article.id}",
    )
    return note.path


async def capture_custom(article: ArticleRecord, instruction: str) -> str:
    """Custom-action capture in Raw/Feeds/Articles/.

    Lightweight: NO LLM call at capture time. The note carries the
    article's title, source URL, and the original feed-supplied summary
    verbatim, plus an `action: "<instruction>"` frontmatter field. The
    next nightly organize pass reads the action and does the real work
    against the original content — re-summarising at capture time would
    waste tokens AND replace the source body with an LLM rewrite the
    organize agent then has to second-guess.
    """
    cleaned = (instruction or "").strip()
    if not cleaned:
        raise ValueError("instruction must not be empty")

    title_base = _slugify(article.title)
    title = _unique_title(FOLDER_ARTICLE, title_base)

    body_parts: list[str] = [f"# {article.title}", ""]
    summary = (article.summary or "").strip()
    if summary:
        body_parts.extend([summary, ""])
    if article.url:
        body_parts.extend(["---", "", f"Source: {_link_line(article)}"])
    body = "\n".join(body_parts).rstrip() + "\n"

    fm = _frontmatter_for(article, kind="article", extra_tags=["feed-custom"])
    fm["action"] = cleaned
    note = await create_note(
        FOLDER_ARTICLE, title, body,
        frontmatter=fm,
        message=f"news: custom {article.id}",
    )
    return note.path
