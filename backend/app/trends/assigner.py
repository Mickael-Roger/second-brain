"""Per-article LLM call: classify one news article against the current
trend list.

Input
-----
The full article body (capped at ``trends.body_max_chars`` characters
of plain text) plus a compact representation of all active trends:
name, description, category, current weight, and 2-3 example titles
previously attributed.

The full body matters for two reasons:

  - Multi-topic items: many feed entries are YouTube videos / podcast
    episodes whose body lists the chapters or subjects covered. Each
    distinct subject should reinforce / create its own trend.

  - Blog-vs-news disambiguation: the body is often the only way to
    tell a real news event ("X just announced Y") from an opinion
    column or evergreen explainer.

Tools the LLM may call (zero, one, or several in the same turn — the
canonical tool loop handles that):

  - `reinforce_trend(trend_id, intensity)`
        "this article fits an existing trend"

  - `create_trend(name, description, category, intensity)`
        "this article opens a new topic that doesn't match anything"

  - `rename_trend(trend_id, new_name?, new_description?, new_category?)`
        "the trend's wording should be refined / recategorised now
         that this article broadens or clarifies its scope"

  - (no tool call)
        "this article isn't a news event, or doesn't belong to a
         trendable subject"

Multi-topic articles fan out: the LLM is told to call the
appropriate tool ONCE PER SUBJECT — a podcast covering three news
events triggers three reinforce/create calls in the same turn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.config import get_settings
from app.db.connection import open_connection
from app.llm import (
    Message,
    TextBlock,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
    get_llm_router,
)
from app.news.articles import read_article

from . import engine, store
from .engine import (
    EventOutcome,
    IntentCreate,
    IntentReinforce,
    IntentRename,
)

log = logging.getLogger(__name__)


class AssignerError(RuntimeError):
    pass


_MAX_ROUNDS = 4   # 1 turn for tool calls + 1 follow-up is plenty


SYSTEM_PROMPT_TEMPLATE = """You classify NEWS articles into trends of current events.

A "trend" is a real-world EVENT or developing situation that a wave
of news articles is reporting on right now (a product launch, a
political development, a conflict, an election, a corporate move, a
scientific announcement, etc.). The trend's NAME and DESCRIPTION
encode what fits and what doesn't; its CATEGORY groups it among
similar topics.

Categories you must pick from
=============================

{categories_block}

Use the exact spelling shown. If nothing fits, use "Other".

Critical filter — only ACTUAL NEWS counts.
=========================================

Many feed items look like news but aren't. Skip them (call no tool):

  - Blog posts / opinion columns / personal essays.
  - Tutorials, how-tos, "5 tips to…", listicles, explainers.
  - Product reviews and buyer's guides not tied to a current release.
  - Evergreen reference content, retrospectives, historical pieces.
  - Sponsored / promotional posts.
  - Lifestyle / entertainment fluff not tied to a current event.

A real news item reports something that just HAPPENED or is HAPPENING:
a concrete event with a date, an entity, a development. If the article
could have been published last year or next year with the same words,
it's almost certainly not news — skip it.

Multi-topic items (videos, podcasts, digests)
=============================================

Many feed items are YouTube videos or podcast episodes whose
DESCRIPTION lists the subjects covered (chapters, talking points,
"in this episode we discuss…"). When you see such an item:

  - DO NOT collapse it into a single "this podcast" trend.
  - Identify each distinct news subject mentioned.
  - For each one, decide between reinforce / create / skip
    INDEPENDENTLY, exactly as if it were its own article.
  - Emit ONE tool call per identified subject in the SAME turn.

Spread `intensity` across them — a video covering 3 subjects equally
might use ~0.5 per subject, not 1.0 each.

Subject-level, not keyword-level
================================

If OpenAI releases GPT-6, "OpenAI" / "ChatGPT" / "GPT-6" all belong
to ONE trend about that release — not three.

Input
-----

You will receive:
  - the article (title + full plain-text body)
  - the current trends, each with: id, name, category, description,
    weight (higher = more active right now), and example article
    titles previously attributed.

For EACH trendable subject in the article, decide one of:

  1. **Fits an existing trend.** Call `reinforce_trend` with that
     trend's id and an intensity (0.0 - 1.0). ~0.8 = clear central
     article, ~0.4 = passing mention.

  2. **The trend's wording is too narrow or its category is wrong.**
     First call `rename_trend` to broaden/refine, THEN
     `reinforce_trend` in the SAME turn.

  3. **No existing trend fits.** Call `create_trend` with a SUBJECT-
     level name (not a keyword), a 1-2 sentence description, a
     category from the closed list, and an intensity.

If no subject in the article is real, current, trendable news, call
NO tool at all and stop.

Rules
-----

  - Never create a trend that overlaps with an existing one. If in
    doubt, reinforce + rename instead.
  - Trend names are subject-level (e.g. "New OpenAI model releases",
    "Israel-Iran conflict", "European elections 2026"), NOT a single
    keyword.
  - Descriptions are 1-2 short sentences, factual, defining what
    belongs.
  - Always pick a category from the closed list above.
  - After your tool calls (or absence thereof), your final text turn
    is ONE short sentence saying what you decided and WHY — including
    "skipped: not a news event" or "skipped: nothing trendable" when
    you call no tool. That sentence is for log/audit only.
"""


_TOOL_REINFORCE = ToolDef(
    name="reinforce_trend",
    description=(
        "Attribute the current article (or one subject of it, for a "
        "multi-topic item) to an existing trend, bumping its weight. "
        "Use when the article fits the trend's described scope."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "trend_id": {
                "type": "string",
                "description": "The id of the existing trend from the list shown to you.",
            },
            "intensity": {
                "type": "number",
                "description": (
                    "How central the article is to this trend. 1.0 = "
                    "fully on-topic, 0.4 = passing mention. For multi-"
                    "topic items, spread intensity across subjects. "
                    "Clamped to [0, 1]."
                ),
                "minimum": 0.0,
                "maximum": 1.0,
            },
        },
        "required": ["trend_id", "intensity"],
        "additionalProperties": False,
    },
)


def _build_create_tool(categories: list[str]) -> ToolDef:
    return ToolDef(
        name="create_trend",
        description=(
            "Open a brand new trend for this article (or one subject "
            "of it). Only call when no existing trend captures the "
            "subject — never to create a near-duplicate."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Subject-level name (a phrase, not a single keyword). "
                        "Examples: 'New OpenAI model releases', "
                        "'Israel-Iran conflict'."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": (
                        "One or two sentences describing what fits this "
                        "trend and what doesn't."
                    ),
                },
                "category": {
                    "type": "string",
                    "description": "Category for this trend. Pick from the closed list.",
                    "enum": categories,
                },
                "intensity": {
                    "type": "number",
                    "description": "Initial weight for this article (0.0 - 1.0).",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
            },
            "required": ["name", "description", "category", "intensity"],
            "additionalProperties": False,
        },
    )


def _build_rename_tool(categories: list[str]) -> ToolDef:
    return ToolDef(
        name="rename_trend",
        description=(
            "Refine the name, description, and/or category of an "
            "existing trend. Use when the article shows the trend's "
            "wording is too narrow / too specific, or that its "
            "category is wrong. Usually called together with "
            "reinforce_trend in the same turn."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "trend_id": {
                    "type": "string",
                    "description": "The id of the existing trend to refine.",
                },
                "new_name": {
                    "type": "string",
                    "description": "Optional new name. Leave empty to keep the existing name.",
                },
                "new_description": {
                    "type": "string",
                    "description": (
                        "Optional new description. Leave empty to keep "
                        "the existing one."
                    ),
                },
                "new_category": {
                    "type": "string",
                    "description": (
                        "Optional new category from the closed list. "
                        "Leave empty to keep the existing one."
                    ),
                    "enum": categories,
                },
            },
            "required": ["trend_id"],
            "additionalProperties": False,
        },
    )


# ── Helpers ─────────────────────────────────────────────────────────


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _build_trends_context(trends: list[store.TrendRecord]) -> str:
    """Compact textual rendering of the current trends, grouped by
    category for readability."""
    if not trends:
        return "(no active trends yet — anything trendable in this article is a candidate for create_trend)"
    by_cat: dict[str, list[store.TrendRecord]] = {}
    for t in trends:
        by_cat.setdefault(t.category or "Other", []).append(t)
    lines: list[str] = []
    for cat in sorted(by_cat.keys()):
        lines.append(f"### {cat}")
        for t in by_cat[cat]:
            lines.append(f"- id: {t.id}")
            lines.append(f"  name: {t.name}")
            lines.append(f"  description: {t.description}")
            lines.append(f"  weight: {t.weight:.3f}")
            if t.examples:
                ex = ", ".join(f"“{e}”" for e in t.examples[:3])
                lines.append(f"  examples: {ex}")
    return "\n".join(lines)


def _build_categories_block(categories: list[str]) -> str:
    return ", ".join(categories) if categories else "Other"


def _build_user_message(
    *, title: str, body: str, trends: list[store.TrendRecord],
) -> str:
    return (
        "## Article\n\n"
        f"Title: {title}\n\n"
        "Body:\n"
        f"{body or '(empty)'}\n\n"
        "## Current trends\n\n"
        + _build_trends_context(trends)
        + "\n\nDecide and call the appropriate tool(s) — one per "
        "trendable subject. If nothing in the article is real, "
        "current, trendable news, call no tool at all."
    )


@dataclass(slots=True)
class _ToolIntent:
    """Captured tool call from the LLM, normalised."""
    name: str
    args: dict[str, Any]


def _collect_tool_calls(msg: Message) -> list[_ToolIntent]:
    out: list[_ToolIntent] = []
    for block in msg.content:
        if isinstance(block, ToolUseBlock):
            out.append(_ToolIntent(name=block.name, args=dict(block.input or {})))
    return out


async def _run_llm(
    *, title: str, body: str, trends: list[store.TrendRecord],
    categories: list[str],
) -> list[_ToolIntent]:
    """Send the article + trends to the LLM and return the tool calls
    it made (possibly empty = noop)."""
    settings = get_settings()
    provider_name, model, _ = settings.llm.resolve_task("trends")
    provider = get_llm_router().get(provider_name)

    user_text = _build_user_message(title=title, body=body, trends=trends)
    history: list[Message] = [
        Message(role="user", content=[TextBlock(text=user_text)])
    ]
    tools = [
        _TOOL_REINFORCE,
        _build_create_tool(categories),
        _build_rename_tool(categories),
    ]
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        categories_block=_build_categories_block(categories),
    )

    collected: list[_ToolIntent] = []
    rounds_left = _MAX_ROUNDS
    while True:
        rounds_left -= 1
        if rounds_left < 0:
            log.warning("trends assigner: max rounds reached, stopping early")
            break

        assistant_message: Message | None = None
        try:
            async for ev in provider.stream(
                messages=history, tools=tools, system=system_prompt, model=model,
            ):
                if ev.type == "error":
                    raise AssignerError(ev.error or "LLM stream error")
                if ev.type == "message_done" and ev.message:
                    assistant_message = ev.message
        except AssignerError:
            raise
        except Exception as exc:
            raise AssignerError(f"LLM call failed: {exc}") from exc

        if assistant_message is None:
            raise AssignerError("LLM produced no assistant message")
        history.append(assistant_message)

        pending = [
            b for b in assistant_message.content if isinstance(b, ToolUseBlock)
        ]
        if not pending:
            break  # final turn, no more tools — we're done

        collected.extend(_collect_tool_calls(assistant_message))

        # Echo a generic OK result for every tool so the LLM can
        # finish its turn cleanly. We don't actually mutate the DB
        # in the loop — that happens once outside, after we've
        # collected the LLM's full intent.
        results: list[ToolResultBlock] = []
        for call in pending:
            results.append(
                ToolResultBlock(
                    tool_use_id=call.id,
                    content=[TextBlock(text="ok")],
                    is_error=False,
                )
            )
        history.append(Message(role="user", content=list(results)))

    return collected


def _intents_from_tool_calls(
    raw: list[_ToolIntent],
) -> list[engine.Intent]:
    out: list[engine.Intent] = []
    for it in raw:
        try:
            if it.name == "reinforce_trend":
                tid = str(it.args.get("trend_id") or "").strip()
                if not tid:
                    continue
                out.append(IntentReinforce(
                    trend_id=tid,
                    intensity=float(it.args.get("intensity") or 0.0),
                ))
            elif it.name == "create_trend":
                name = str(it.args.get("name") or "").strip()
                desc = str(it.args.get("description") or "").strip()
                if not name or not desc:
                    continue
                out.append(IntentCreate(
                    name=name,
                    description=desc,
                    category=str(it.args.get("category") or "").strip(),
                    intensity=float(it.args.get("intensity") or 0.0),
                ))
            elif it.name == "rename_trend":
                tid = str(it.args.get("trend_id") or "").strip()
                if not tid:
                    continue
                out.append(IntentRename(
                    trend_id=tid,
                    new_name=(str(it.args.get("new_name") or "").strip() or None),
                    new_description=(
                        str(it.args.get("new_description") or "").strip() or None
                    ),
                    new_category=(
                        str(it.args.get("new_category") or "").strip() or None
                    ),
                ))
            else:
                log.warning("trends assigner: ignoring unknown tool %s", it.name)
        except Exception:
            log.exception(
                "trends assigner: malformed tool args for %s, dropping", it.name,
            )
    return out


# ── Public entry point ──────────────────────────────────────────────


async def process_article(
    article_id: str, *, article_title: str,
) -> EventOutcome:
    """Run the full pipeline for one news article: load full body,
    ask the LLM (which may emit several intents for a multi-topic
    item), apply them as ONE atomic batch event."""
    settings = get_settings()
    body_cap = settings.trends.body_max_chars
    categories = list(settings.trends.categories)

    # 1. Load full body from the on-disk article JSON.
    record = read_article(article_id)
    body = _truncate(record.summary if record else "", body_cap)
    title = article_title or (record.title if record else "")

    # 2. Snapshot the current trend landscape (used as the LLM's context).
    conn = open_connection()
    try:
        trends = store.list_active_trends(conn)
    finally:
        conn.close()

    # 3. LLM call (collect intents, don't mutate the DB yet).
    raw = await _run_llm(
        title=title, body=body, trends=trends, categories=categories,
    )
    intents = _intents_from_tool_calls(raw)

    # 4. Apply atomically: one decay, one prune, one snapshot.
    conn = open_connection()
    try:
        return engine.apply_batch(
            conn,
            article_id=article_id,
            article_title=title,
            intents=intents,
        )
    finally:
        conn.close()
