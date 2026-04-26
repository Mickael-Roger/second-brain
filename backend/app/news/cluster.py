"""LLM-driven topic clustering.

Given a window of unclustered articles, ask the LLM to group them by
topic and return a SHORT topic label per cluster (e.g. "GPT-5.5
release", "Apple Q1 earnings"). The bubbles are sized by how many
articles talk about each topic — this is a "hot topics" view, not a
news-headline list.

Singletons (topics with only one article) are still recorded in the DB
so the article doesn't get re-prompted on every pass, but they are
hidden from the UI by `list_events`.

We keep this LLM-only for v1 to match the codebase's existing pattern
(see `app.jobs.organize`). A future phase can add embedding-based
similarity if cost becomes a concern.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.db.connection import open_connection
from app.llm import Message, TextBlock, complete

from .store import (
    StoredArticle,
    create_fetch_run,
    finish_fetch_run,
    list_unclustered_articles,
    set_fetch_run_status,
    upsert_event,
)

log = logging.getLogger(__name__)

# Cap how much we send to the LLM per pass — both for cost and to stay
# under context windows on smaller models.
MAX_ARTICLES_PER_PASS = 200


_DEFAULT_SYSTEM_PROMPT = """\
You group news articles into HOT TOPICS.

You will receive a list of articles, each with: an id, a title, an
optional short description, the source feed, and the publication date.
Multiple articles often cover the same underlying topic (e.g. ten
outlets all reporting on the same product launch on the same day).

Your job is to surface the topics being talked about, NOT to write
news headlines. Each topic label should be a very short noun phrase
naming the thing being discussed — the kind of label you'd see on
a tag cloud or hot-topics dashboard. Examples of GOOD labels:

  "GPT-5.5 release"
  "Apple Q1 earnings"
  "France pension reform"
  "Ukraine peace talks"

Examples of BAD labels (too verbose, too headline-y):

  "OpenAI announces GPT-5.5 with improved reasoning capabilities"
  "Apple reports stronger-than-expected Q1 revenue driven by services"

Return ONE JSON object and nothing else (no preamble, no code fences):

{
  "events": [
    {
      "title": "<very short topic label, ideally 2–6 words>",
      "summary": "<one short sentence describing the topic>",
      "occurred_on": "YYYY-MM-DD",
      "article_ids": ["<id>", "<id>", ...]
    }
  ]
}

Rules:
- Only group articles that are clearly about the SAME underlying topic.
  When in doubt, leave an article as its own one-article entry — those
  will be filtered out of the dashboard automatically.
- Pick `occurred_on` from the earliest publication date in the cluster.
- Every article id you receive MUST appear in exactly one event.
- The `title` is the user-facing label of the bubble. Aim for 2–6
  words, no trailing punctuation, no source name, no date.
- Topics with only one article are recorded but hidden from the UI —
  prefer grouping over splitting whenever the connection is real.
"""


def _load_synthesis_prompt() -> str:
    """Load the cluster system prompt from the vault.

    Reads `<vault>/<obsidian.news_synthesis_file>` (default
    `NEWS_SYNTHESIS.md`), strips an optional YAML frontmatter block.
    Falls back to the built-in default when the file is missing, empty,
    or the vault is unconfigured. Loaded fresh on every pass so the
    user can edit the file and the next cron run picks it up — no
    restart needed."""
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
class ClusterResult:
    fetched: int
    events_created: int
    articles_clustered: int


def _build_user_prompt(articles: list[StoredArticle]) -> str:
    lines: list[str] = ["## Articles to cluster", ""]
    for a in articles:
        # Trim descriptions hard — we just need topic signal, not the whole text.
        desc = (a.description or "").strip().replace("\n", " ")
        if len(desc) > 280:
            desc = desc[:279] + "…"
        feed = a.feed_title or a.source
        lines.append(
            f"- id={a.id} | date={a.published_at[:10]} | feed={feed}\n"
            f"  title: {a.title}\n"
            f"  desc:  {desc or '(no description)'}"
        )
    return "\n".join(lines)


def _parse_clusters(raw: str) -> list[dict]:
    """Pull the events list out of the LLM's JSON response.

    Tolerates the same kinds of glop the Organize parser does (code
    fences, leading prose). Any malformed entries are dropped silently
    — the corresponding articles just stay unclustered and will be
    retried on the next pass."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s[:-3]
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in LLM response")
    obj = json.loads(s[start : end + 1])
    events = obj.get("events", [])
    if not isinstance(events, list):
        raise ValueError("`events` is not a list")
    return events


async def run_cluster_pass() -> ClusterResult:
    """Cluster unclustered articles within the configured time window.

    Called by the scheduler (cron) and by the manual API trigger. The
    fetch run row provides observability — same shape as fetch runs so
    the UI can list them together."""
    settings = get_settings()
    # NB: we intentionally do NOT gate on `news.enabled` here. That flag
    # controls whether the scheduler registers the cron jobs; manual
    # triggers from the UI must always be able to run, otherwise the
    # operator can't test their setup before flipping the schedule on.

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=settings.news.cluster_window_days)
    ).isoformat()

    conn = open_connection()
    try:
        run_id = create_fetch_run(conn, kind="cluster")
        articles = list_unclustered_articles(conn, since_iso=cutoff)
    finally:
        conn.close()

    log.info(
        "news cluster: starting (window=%dd, unclustered=%d)",
        settings.news.cluster_window_days, len(articles),
    )

    if not articles:
        log.info("news cluster: nothing to do (no unclustered articles in window)")
        conn = open_connection()
        try:
            finish_fetch_run(conn, run_id, status="ok", fetched=0)
        finally:
            conn.close()
        return ClusterResult(fetched=0, events_created=0, articles_clustered=0)

    # Newest first; cap so the prompt doesn't blow up. The next pass
    # (tomorrow) picks up whatever we couldn't process today.
    batch = articles[:MAX_ARTICLES_PER_PASS]
    user_prompt = _build_user_prompt(batch)

    try:
        raw = await complete(
            _load_synthesis_prompt(),
            [Message(role="user", content=[TextBlock(text=user_prompt)])],
            provider_name=settings.news.cluster_llm_provider,
        )
    except Exception as exc:
        log.exception("news cluster: LLM call failed")
        conn = open_connection()
        try:
            finish_fetch_run(
                conn, run_id, status="error", fetched=len(batch), error=str(exc)
            )
        finally:
            conn.close()
        raise

    try:
        events = _parse_clusters(raw)
    except Exception as exc:
        log.exception("news cluster: parse failed; raw=%s", raw[:500])
        conn = open_connection()
        try:
            finish_fetch_run(
                conn,
                run_id,
                status="error",
                fetched=len(batch),
                error=f"parse: {exc}",
            )
        finally:
            conn.close()
        raise

    valid_ids = {a.id for a in batch}
    events_created = 0
    articles_clustered = 0
    conn = open_connection()
    try:
        for ev in events:
            title = str(ev.get("title", "")).strip()
            if not title:
                continue
            occurred = str(ev.get("occurred_on", "")).strip()[:10]
            if not occurred:
                # Fallback: earliest article's date.
                ids_raw = ev.get("article_ids", [])
                article_ids = [str(i) for i in ids_raw if str(i) in valid_ids]
                if not article_ids:
                    continue
                earliest = min(
                    a.published_at for a in batch if a.id in article_ids
                )
                occurred = earliest[:10]
            else:
                ids_raw = ev.get("article_ids", [])
                article_ids = [str(i) for i in ids_raw if str(i) in valid_ids]
                if not article_ids:
                    continue
            summary = ev.get("summary")
            summary_str = str(summary).strip() if summary else None
            upsert_event(
                conn,
                title=title,
                summary=summary_str,
                occurred_on=occurred,
                article_ids=article_ids,
            )
            events_created += 1
            articles_clustered += len(article_ids)
        finish_fetch_run(
            conn,
            run_id,
            status="ok",
            fetched=len(batch),
            clustered=articles_clustered,
        )
    except Exception as exc:
        log.exception("news cluster: persistence failed")
        try:
            set_fetch_run_status(conn, run_id, status="error", error=str(exc))
        except Exception:
            pass
        raise
    finally:
        conn.close()

    log.info(
        "news cluster: %d events from %d articles (%d total in window)",
        events_created, articles_clustered, len(articles),
    )
    return ClusterResult(
        fetched=len(batch),
        events_created=events_created,
        articles_clustered=articles_clustered,
    )
