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
import re
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
You extract trending HASHTAGS from a news article so articles covering
the same things can be grouped together on a hot-topics dashboard.

OUTPUT FORMAT — read carefully:
Return ONE JSON object and ABSOLUTELY NOTHING ELSE. No prose, no
markdown, no headers, no '#' prefixes, no code fences, no
explanation, no refusal text. The first character of your response
must be '{' and the last must be '}'.

{"tags": ["<tag>", "<tag>", ...]}

Tag style — these are the exact shapes we want:
  "gpt-5.5"
  "ubuntu-26.04"
  "sam-altman"
  "openai"
  "france-pension-reform"
  "apple-q1-earnings"
  "ukraine-peace-talks"

Tag rules:
- ALL lowercase. Use hyphens to separate words: "sam-altman", not
  "SamAltman" and not "Sam Altman".
- Keep version numbers, dates, and dotted notation as-is inside the
  slug: "gpt-5.5", "ubuntu-26.04", "ios-19".
- 1 to 20 tags per article. Long articles routinely cover many
  distinct topics — return ALL of them. 6+ tags is normal for
  in-depth pieces; 15–20 is fine for very topic-dense articles.
  Don't pad: only return tags the article actually substantively
  discusses.
- Tags should name specific ENTITIES the article is about: people
  (sam-altman), companies (openai), products (gpt-5.5), versions
  (ubuntu-26.04), events (apple-q1-earnings), places when central
  (france), specific policies (pension-reform). NOT generic concepts
  ("news", "tech", "ai", "world", "today").
- Do NOT tag the publication source (skip "techcrunch", "le-monde",
  etc.).
- Be consistent across runs: the same entity should always get the
  same slug.
- If the article is a stub, paywalled redirect, or has nothing
  taggable, return {"tags": []} — still as a JSON object.
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
    """User-side prompt. We REPEAT the format requirement here because
    the chatgpt-codex provider has been observed to ignore the system
    prompt and default to a 'summarize this article' behaviour. Putting
    the strict-JSON instruction last (right before the model generates)
    is the most reliable way to force the desired output shape."""
    folder = article.feed_group or "(no folder)"
    feed = article.feed_title or article.source
    desc = (article.description or "").strip()
    # Cap the body to keep a single prompt small; tags should be
    # extractable from the first ~6k chars even on long-form pieces.
    if len(desc) > 6000:
        desc = desc[:5999] + "…"
    return (
        "TASK: Extract trending hashtags from the article below and "
        "return them as a JSON object.\n\n"
        "DO NOT summarise the article. DO NOT explain. DO NOT use "
        "markdown. DO NOT prefix tags with '#'.\n\n"
        'Output exactly this shape (the first character of your '
        'response must be "{" and the last must be "}"):\n'
        '{"tags": ["<slug>", "<slug>", ...]}\n\n'
        "Slugs are lowercase, hyphen-separated, and name specific "
        "entities. Good examples:\n"
        '  "gpt-5.5", "ubuntu-26.04", "sam-altman", "openai", '
        '"france-pension-reform", "apple-q1-earnings"\n\n'
        "Bad (do NOT do this): summary text, '#'-prefixed words, "
        'CamelCase, the publication source, generic words like '
        '"news"/"tech"/"world".\n\n'
        "If the article is a stub or unintelligible, return "
        '{"tags": []}.\n\n'
        f"--- Article folder ---\n{folder}\n\n"
        f"--- Article feed ---\n{feed}\n\n"
        f"--- Article title ---\n{article.title}\n\n"
        f"--- Article body ---\n{desc or '(no description)'}\n\n"
        "Now return the JSON object. Remember: first char '{', last "
        "char '}', nothing else."
    )


_HASHTAG_RE = re.compile(r"#([A-Za-z0-9][\w.\-]*)")


def _slugify_tag(tag: str) -> str:
    """Normalise a raw tag string to the canonical lowercase-kebab
    style (e.g. 'GPT-5.5' → 'gpt-5.5', 'Sam Altman' → 'sam-altman',
    'PensionReform' → 'pension-reform'). Keeps dots, digits, and
    existing hyphens; converts internal whitespace and CamelCase
    boundaries to hyphens."""
    s = tag.strip().lstrip("#").strip()
    if not s:
        return ""
    # Insert a hyphen at lower→upper transitions so "PensionReform"
    # becomes "Pension-Reform" before the lowercase pass.
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", s)
    s = s.lower()
    # Collapse any run of disallowed characters (whitespace, _,
    # punctuation that isn't '.' or '-') into a single hyphen.
    s = re.sub(r"[^a-z0-9.\-]+", "-", s)
    # Collapse multiple consecutive hyphens, trim edges.
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


def _parse_tags(raw: str) -> list[str]:
    """Pull the tags array out of the LLM's response.

    Multiple LLM output shapes are tolerated, in order of preference:
      1. JSON object with a tags-shaped key ("tags" / "topics" /
         "hashtags" / "labels").
      2. Bare JSON array.
      3. Markdown hashtag list ("#Science #Astronomy #MilkyWay") —
         we extract anything matching `#word`.
    Anything that still can't be parsed yields an empty list — the
    caller logs a sample of the raw response so format mismatches are
    visible."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s[:-3]
    s = s.strip()

    parsed: list | None = None

    # Form 1: full object with a tags-shaped key.
    obj_start = s.find("{")
    obj_end = s.rfind("}")
    if obj_start >= 0 and obj_end > obj_start:
        try:
            obj = json.loads(s[obj_start : obj_end + 1])
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            for key in ("tags", "topics", "hashtags", "labels"):
                if key in obj and isinstance(obj[key], list):
                    parsed = obj[key]
                    break

    # Form 2: bare JSON array.
    if parsed is None:
        arr_start = s.find("[")
        arr_end = s.rfind("]")
        if arr_start >= 0 and arr_end > arr_start:
            try:
                arr = json.loads(s[arr_start : arr_end + 1])
            except json.JSONDecodeError:
                arr = None
            if isinstance(arr, list):
                parsed = arr

    # Form 3: markdown hashtag list. Only fall through to this if the
    # JSON parses above turned up nothing AND the raw text contains at
    # least one '#' — gives us "#Science #Astronomy #MilkyWay" and
    # similar prose-with-tags responses.
    if parsed is None and "#" in s:
        matches = _HASHTAG_RE.findall(s)
        if matches:
            parsed = matches

    if not isinstance(parsed, list):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for t in parsed:
        tag = _slugify_tag(str(t))
        if not tag:
            continue
        if tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


async def _tag_one(
    article: StoredArticle,
    system_prompt: str,
    *,
    provider_name: str | None,
) -> tuple[str, list[str], str, str | None]:
    """One LLM call. Returns (article_id, tags, raw_response, error).

    `raw_response` is included so the caller can log a sample when the
    parser yielded zero tags — that's the only way to diagnose
    format mismatches (the LLM might be wrapping in code fences,
    using an unexpected key, or returning prose). On exception the
    tags list is empty, raw is empty, and `error` is set."""
    try:
        raw = await complete(
            system_prompt,
            [Message(role="user", content=[TextBlock(text=_build_user_prompt(article))])],
            provider_name=provider_name,
        )
    except Exception as exc:
        return article.id, [], "", str(exc)
    return article.id, _parse_tags(raw), raw, None


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

    async def _worker(a: StoredArticle) -> tuple[str, list[str], str, str | None]:
        async with sem:
            return await _tag_one(a, system_prompt, provider_name=provider_name)

    results = await asyncio.gather(*(_worker(a) for a in pending))

    processed = 0
    failed = 0
    total_tags = 0
    empty_logged = 0
    conn = open_connection()
    try:
        for article_id, tags, raw, error in results:
            if error:
                log.warning("news tagger: %s failed: %s", article_id, error)
                failed += 1
                set_article_tags(conn, article_id, tags=[])
                continue
            if not tags and empty_logged < 3:
                # First few empty-result responses get sampled into
                # the log so we can see what the LLM is actually
                # returning. Capped to avoid log spam when something
                # is systematically wrong (which is the case when
                # we get this branch in the first place).
                log.warning(
                    "news tagger: %s parsed to 0 tags; raw[:600]=%r",
                    article_id, raw[:600],
                )
                empty_logged += 1
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
