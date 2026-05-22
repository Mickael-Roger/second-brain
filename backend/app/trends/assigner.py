"""Per-article LLM call: classify one news article against the current
trend list.

Input
-----
The article's title + a capped summary, plus a compact representation
of all active trends: name, description, current softmax-normalised
weight, and 2-3 example titles previously attributed to that trend.

Tools the LLM may call (zero, one, or several in the same turn — the
canonical tool loop handles that):

  - `reinforce_trend(trend_id, intensity)`
        "this article fits an existing trend"

  - `create_trend(name, description, intensity)`
        "this article opens a new topic that doesn't match anything"

  - `rename_trend(trend_id, new_name, new_description?)`
        "the trend's wording should be refined now that this article
         broadens / clarifies its scope"

  - (no tool call)
        "this article doesn't really belong to a trend"

Output
------
We DON'T let the engine mutate state mid-loop — we collect the
LLM's intent and apply it under a single connection at the end.
This keeps the trends DB consistent even if the LLM disconnects
mid-stream.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

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
from .engine import EventOutcome

log = logging.getLogger(__name__)


class AssignerError(RuntimeError):
    pass


_SUMMARY_MAX_CHARS = 600
_MAX_ROUNDS = 4   # 1 turn for tool calls + 1 follow-up is plenty


SYSTEM_PROMPT = """You classify news articles into trends.

A "trend" is a SUBJECT/topic that a wave of news articles is about,
not a keyword. If OpenAI releases GPT-6, "OpenAI" / "ChatGPT" / "GPT-6"
all belong to ONE trend about that release — not three. The trend's
NAME and DESCRIPTION encode what fits and what doesn't.

You will receive:
  - the article (title + a short summary)
  - the current trends, each with: id, name, description, weight
    (higher = more active right now), and example article titles
    previously attributed.

Decide ONE of:

  1. **The article fits an existing trend.** Call `reinforce_trend`
     with that trend's id and an intensity (0.0 - 1.0) reflecting how
     central the article is to that trend. Use ~0.8 for a clear central
     article, ~0.4 for a passing mention.

  2. **The trend's wording is too narrow or off.** First call
     `rename_trend` to broaden/refine the name and (optionally)
     description, THEN call `reinforce_trend` in the SAME turn. Use
     this when the article expands the trend's scope (e.g. "GPT-6
     OpenAI" → "New OpenAI model releases").

  3. **No existing trend fits.** Call `create_trend` with a SUBJECT-
     level name (not a keyword), a precise 1-2 sentence description
     that captures the trend's scope, and an intensity for the first
     article.

  4. **The article is unrelated to anything trendable** (random local
     news, lifestyle pieces, etc.). Call NO tool — just stop.

Rules:
  - Never create a trend that overlaps with an existing one. If in
    doubt, reinforce + rename instead.
  - Trend names are subject-level (e.g. "New OpenAI model releases",
    "Israel-Iran conflict", "European elections 2026"), NOT a single
    keyword.
  - Descriptions are 1-2 short sentences, factual, defining what
    belongs.
  - After your tool calls, your final text turn is ONE short sentence
    saying what you decided. That sentence is for log/audit only.
"""


_TOOL_REINFORCE = ToolDef(
    name="reinforce_trend",
    description=(
        "Attribute the current article to an existing trend, bumping "
        "its weight. Use this when the article fits the trend's "
        "described scope."
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
                    "fully on-topic, 0.4 = passing mention. Clamped to [0, 1]."
                ),
                "minimum": 0.0,
                "maximum": 1.0,
            },
        },
        "required": ["trend_id", "intensity"],
        "additionalProperties": False,
    },
)


_TOOL_CREATE = ToolDef(
    name="create_trend",
    description=(
        "Open a brand new trend for this article. Only call when no "
        "existing trend captures the article's subject — never to "
        "create a near-duplicate."
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
            "intensity": {
                "type": "number",
                "description": "Initial weight for this article (0.0 - 1.0).",
                "minimum": 0.0,
                "maximum": 1.0,
            },
        },
        "required": ["name", "description", "intensity"],
        "additionalProperties": False,
    },
)


_TOOL_RENAME = ToolDef(
    name="rename_trend",
    description=(
        "Refine the name and/or description of an existing trend. Use "
        "when the article shows the trend's wording is too narrow / "
        "too specific. Usually called together with reinforce_trend "
        "in the same turn."
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
                    "Optional new description. Leave empty to keep the "
                    "existing one."
                ),
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
    """Compact textual rendering of the current trends.

    We send raw weights so the LLM can compare magnitudes; intensity
    on its side stays in 0-1, the engine adds it. Examples help the
    LLM judge whether an article fits the trend's actual scope
    instead of the description alone."""
    if not trends:
        return "(no active trends yet — anything you see is a candidate for create_trend)"
    lines: list[str] = []
    for t in trends:
        lines.append(f"- id: {t.id}")
        lines.append(f"  name: {t.name}")
        lines.append(f"  description: {t.description}")
        lines.append(f"  weight: {t.weight:.3f}")
        if t.examples:
            ex = ", ".join(f"“{e}”" for e in t.examples[:3])
            lines.append(f"  examples: {ex}")
    return "\n".join(lines)


def _build_user_message(
    *, title: str, summary: str, trends: list[store.TrendRecord],
) -> str:
    return (
        "## Article\n\n"
        f"Title: {title}\n\n"
        f"Summary: {summary or '(no summary available)'}\n\n"
        "## Current trends\n\n"
        + _build_trends_context(trends)
        + "\n\nDecide and call the appropriate tool(s). If nothing "
        "matches and the article isn't trendable, call no tool."
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
    *, title: str, summary: str, trends: list[store.TrendRecord],
) -> list[_ToolIntent]:
    """Send the article + trends to the LLM and return the tool calls
    it made (possibly empty = noop). We keep the loop tight — at most
    a couple of rounds, since the LLM should call tools and stop."""
    settings = get_settings()
    provider_name, model, _ = settings.llm.resolve_task("trends")
    provider = get_llm_router().get(provider_name)

    user_text = _build_user_message(title=title, summary=summary, trends=trends)
    history: list[Message] = [
        Message(role="user", content=[TextBlock(text=user_text)])
    ]
    tools = [_TOOL_REINFORCE, _TOOL_CREATE, _TOOL_RENAME]

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
                messages=history, tools=tools, system=SYSTEM_PROMPT, model=model,
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


def _apply_intents(
    intents: list[_ToolIntent],
    *,
    article_id: str,
    article_title: str,
) -> list[EventOutcome]:
    """Apply the LLM's tool calls to the DB in a single connection.

    Multiple tools in one turn = multiple events. Order matters:
    `rename_trend` should be applied before `reinforce_trend` so the
    snapshot shows the trend already with its new name. We sort
    accordingly.
    """
    if not intents:
        conn = open_connection()
        try:
            return [engine.apply_noop(conn, article_id=article_id)]
        finally:
            conn.close()

    order = {"rename_trend": 0, "create_trend": 1, "reinforce_trend": 2}
    sorted_intents = sorted(intents, key=lambda i: order.get(i.name, 9))

    outcomes: list[EventOutcome] = []
    conn = open_connection()
    try:
        for it in sorted_intents:
            args = it.args
            try:
                if it.name == "reinforce_trend":
                    outcomes.append(engine.apply_reinforce(
                        conn,
                        article_id=article_id,
                        article_title=article_title,
                        trend_id=str(args.get("trend_id") or ""),
                        intensity=float(args.get("intensity") or 0.0),
                    ))
                elif it.name == "create_trend":
                    outcomes.append(engine.apply_create(
                        conn,
                        article_id=article_id,
                        article_title=article_title,
                        name=str(args.get("name") or "").strip(),
                        description=str(args.get("description") or "").strip(),
                        intensity=float(args.get("intensity") or 0.0),
                    ))
                elif it.name == "rename_trend":
                    outcomes.append(engine.apply_rename(
                        conn,
                        article_id=article_id,
                        trend_id=str(args.get("trend_id") or ""),
                        new_name=str(args.get("new_name") or "") or None,
                        new_description=str(args.get("new_description") or "") or None,
                    ))
                else:
                    log.warning("trends assigner: ignoring unknown tool %s", it.name)
            except Exception:
                log.exception(
                    "trends assigner: failed to apply %s for article %s",
                    it.name, article_id,
                )
        if not outcomes:
            outcomes.append(engine.apply_noop(conn, article_id=article_id))
    finally:
        conn.close()
    return outcomes


# ── Public entry point ──────────────────────────────────────────────


async def process_article(article_id: str, *, article_title: str) -> list[EventOutcome]:
    """Run the full pipeline for one news article: load summary, ask
    the LLM, apply the resulting tool calls. Returns the list of
    events the engine produced (usually 1, sometimes 2 if the LLM
    renamed + reinforced)."""
    # 1. Build the input: title + capped summary (from the on-disk JSON).
    record = read_article(article_id)
    if record is None:
        summary = ""
    else:
        summary = _truncate(record.summary, _SUMMARY_MAX_CHARS)

    # 2. Snapshot the current trend landscape (used as the LLM's context).
    conn = open_connection()
    try:
        trends = store.list_active_trends(conn)
    finally:
        conn.close()

    # 3. LLM call.
    intents = await _run_llm(
        title=article_title or (record.title if record else ""),
        summary=summary,
        trends=trends,
    )

    # 4. Apply intents → mutate trends + insert snapshots.
    return _apply_intents(
        intents, article_id=article_id, article_title=article_title,
    )
