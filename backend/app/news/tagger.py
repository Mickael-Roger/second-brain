"""Per-article hashtag extraction.

For each article without `tags_extracted_at`, ask the LLM for a small
set of topic hashtags that capture what the article is about. Long
articles can carry several distinct topics — the LLM is told to
return all of them, not just one. Once tagged, an article is never
re-prompted (the timestamp is set even on an empty array, so a
"genuinely-no-topic" article isn't re-tried forever).

The system prompt is loaded from `<vault>/NEWS_SYNTHESIS.md` if
present, otherwise from the built-in default below. Edit the file in
the vault → next pass picks it up, no restart.

Trends elsewhere in the app come from aggregating these tags across
articles in a date range (see `store.aggregate_tags`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from app.config import get_settings
from app.db.connection import open_connection
from app.llm import Message, TextBlock, complete

from .store import (
    StoredArticle,
    create_fetch_run,
    finish_fetch_run,
    list_pending_tag_articles,
    set_article_tags,
)

log = logging.getLogger(__name__)

# How many pending articles a single tagger pass tries to process. The
# pass is bounded so a giant backlog doesn't tie up the LLM forever;
# subsequent passes drain the rest. Concurrency below tunes how many
# of these are in flight at once.
MAX_ARTICLES_PER_PASS = 50
MAX_CONCURRENT_LLM = 6


_DEFAULT_SYSTEM_PROMPT = """\
You extract topic HASHTAGS from a news article so it can be grouped
with other articles covering the same things.

Return ONE JSON object and nothing else (no preamble, no code fences):

{
  "tags": ["<tag>", "<tag>", ...]
}

Rules:
- Return between 1 and 6 tags. Long articles often cover several
  distinct topics — return ALL of them, not just the headline one.
- Each tag is a short noun phrase, ideally 1–3 words. No leading '#',
  no spaces in multi-word tags (use hyphens or PascalCase). Examples:
  "GPT-5", "OpenAI", "France", "PensionReform", "AppleEarnings".
- Use canonical names. Prefer "OpenAI" over "openai", "GPT-5" over
  "GPT5", "France" over "FR". Be consistent with yourself across runs.
- Tag the SUBJECT of the article (companies, people, places, events,
  products, policies). Do NOT tag the publication source (skip
  "TechCrunch", "Le Monde", etc.).
- Avoid generic tags ("news", "tech", "world") — they don't help group.
- If an article is genuinely about nothing taggable (a stub, a
  paywalled redirect, a corrupted feed entry), return an empty list.
"""


def _load_synthesis_prompt() -> str:
    """Load the tagger's system prompt from the vault.

    Reads `<vault>/<obsidian.news_synthesis_file>` (default
    `NEWS_SYNTHESIS.md`), strips an optional YAML frontmatter block.
    Falls back to the built-in default when the file is missing,
    empty, or the vault is unconfigured."""
    from app.vault import vault_root

    s = get_settings()
    if s.obsidian.vault_path is None:
        return _DEFAULT_SYSTEM_PROMPT
    try:
        path = vault_root() / s.obsidian.news_synthesis_file
    except RuntimeError:
        return _DEFAULT_SYSTEM_PROMPT
    if not path.is_file():
        return _DEFAULT_SYSTEM_PROMPT
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("could not read news synthesis prompt %s: %s", path, exc)
        return _DEFAULT_SYSTEM_PROMPT
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end >= 0:
            text = text[end + 4 :].lstrip("\n")
    return text.strip() or _DEFAULT_SYSTEM_PROMPT


@dataclass(slots=True)
class TaggerResult:
    processed: int
    failed: int
    total_tags: int


def _build_user_prompt(article: StoredArticle) -> str:
    folder = article.feed_group or "(no folder)"
    feed = article.feed_title or article.source
    desc = (article.description or "").strip()
    # Cap the body to keep a single prompt small; tags should be
    # extractable from the first ~6k chars even on long-form pieces.
    if len(desc) > 6000:
        desc = desc[:5999] + "…"
    return (
        f"## Folder\n{folder}\n\n"
        f"## Feed\n{feed}\n\n"
        f"## Title\n{article.title}\n\n"
        f"## Body\n{desc or '(no description)'}"
    )


def _parse_tags(raw: str) -> list[str]:
    """Pull the tags array out of the LLM's JSON response.

    Tolerates code fences and leading prose. Anything that can't be
    parsed yields an empty list — the caller still records that the
    article was processed (we don't want to retry forever on a single
    bad LLM response)."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s[:-3]
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        obj = json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return []
    raw_tags = obj.get("tags") if isinstance(obj, dict) else None
    if not isinstance(raw_tags, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for t in raw_tags:
        tag = str(t).strip().lstrip("#")
        if not tag:
            continue
        # Cheap dedupe within a single article — case-insensitive.
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
    return out


async def _tag_one(
    article: StoredArticle,
    system_prompt: str,
    *,
    provider_name: str | None,
) -> tuple[str, list[str], str | None]:
    """One LLM call. Returns (article_id, tags, error). On failure the
    tags list is empty and `error` is set; the caller decides whether
    to persist (we do — extracting-once means we don't loop on bad
    inputs)."""
    try:
        raw = await complete(
            system_prompt,
            [Message(role="user", content=[TextBlock(text=_build_user_prompt(article))])],
            provider_name=provider_name,
        )
    except Exception as exc:
        return article.id, [], str(exc)
    return article.id, _parse_tags(raw), None


async def run_tagger_pass() -> TaggerResult:
    """Process up to `MAX_ARTICLES_PER_PASS` pending articles.

    Run from the scheduler (cron) and triggered after each fetch. A
    fetch run row records the pass for observability — same kind=
    bucket the cluster pass used."""
    settings = get_settings()
    conn = open_connection()
    try:
        run_id = create_fetch_run(conn, kind="cluster")  # reuse "cluster" bucket
        pending = list_pending_tag_articles(conn, limit=MAX_ARTICLES_PER_PASS)
    finally:
        conn.close()

    log.info("news tagger: starting (pending=%d)", len(pending))

    if not pending:
        log.info("news tagger: nothing to do")
        conn = open_connection()
        try:
            finish_fetch_run(conn, run_id, status="ok", fetched=0)
        finally:
            conn.close()
        return TaggerResult(processed=0, failed=0, total_tags=0)

    system_prompt = _load_synthesis_prompt()
    sem = asyncio.Semaphore(MAX_CONCURRENT_LLM)
    provider_name = settings.news.cluster_llm_provider

    async def _worker(a: StoredArticle) -> tuple[str, list[str], str | None]:
        async with sem:
            return await _tag_one(a, system_prompt, provider_name=provider_name)

    results = await asyncio.gather(*(_worker(a) for a in pending))

    processed = 0
    failed = 0
    total_tags = 0
    conn = open_connection()
    try:
        for article_id, tags, error in results:
            if error:
                # Record an empty tag set + extracted_at = now so we
                # don't re-prompt the same article on every pass. If
                # the user wants to retry a specific article they can
                # NULL its tags_extracted_at by hand.
                log.warning("news tagger: %s failed: %s", article_id, error)
                failed += 1
                set_article_tags(conn, article_id, tags=[])
            else:
                set_article_tags(conn, article_id, tags=tags)
                processed += 1
                total_tags += len(tags)
        finish_fetch_run(
            conn,
            run_id,
            status="ok",
            fetched=len(pending),
            clustered=processed,
        )
    finally:
        conn.close()

    log.info(
        "news tagger: done processed=%d failed=%d total_tags=%d",
        processed, failed, total_tags,
    )
    return TaggerResult(processed=processed, failed=failed, total_tags=total_tags)
