"""Turn a news article into an Obsidian vault note.

Four flows used by the news tab's capture buttons:

  - keep    → Raw/Feeds/Notes/    short LLM digest (2-3 sentences)
  - article → Raw/Feeds/Articles/ full LLM article-style summary
  - watched → Raw/Feeds/Youtube/  bare stub (link only, no LLM)
  - custom  → no fixed file       runs an LLM agent NOW with vault.*
                                  + news.read_news tools, applies the
                                  user's free-form instruction

The first three return a vault-relative path; `apply_custom_action`
returns a structured result describing what the agent did (summary
text + list of files touched).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from app.llm import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    complete,
    get_llm_router,
)
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


@dataclass(slots=True)
class CustomActionResult:
    summary: str
    files_touched: list[str]


_CUSTOM_ACTION_SYSTEM_BASE = (
    "You're a vault agent acting on a single news article on behalf of "
    "the user, who issued an ad-hoc free-form instruction. Apply that "
    "instruction faithfully and minimally — don't volunteer extra work "
    "the user didn't ask for, don't reorganise unrelated files, don't "
    "rewrite paragraphs that work fine.\n\n"
    "Tools available:\n"
    "- `news.read_news` (with `article_id=...`) — fetch the article body, "
    "summary, URL, etc. Call it whenever the instruction needs the "
    "article's content; don't guess.\n"
    "- `vault.*` (read / list / find / grep / edit_note / append / "
    "create_note / replace_in_note / update_frontmatter / move / delete "
    "/ create_folder) — read and write anywhere in the vault.\n\n"
    "The instruction can be anything: 'save the link in file X', "
    "'append this to my TODO list under Pro', 'add a paragraph to "
    "Wiki/<topic>/<page>.md', 'create a new note about this in Notes/"
    "<area>/'. Pick the right tools, do the work, stop.\n\n"
    "**Critical routing rule.** When the instruction mentions a TODO "
    "list / 'to-do' / 'à faire' / 'mets dans ma todo' / 'remind me to' "
    "or any synonymous action-item language, the destination is "
    "**`/TODO.md`** at the vault root — NEVER `Notes/To view - read - "
    "listen.md` (that's a media-consumption queue) or any other "
    "`Notes/*` reading list. `TODO.md` is the single canonical action-"
    "items file, grouped by theme (`## Perso`, `## Pro`, …). It is "
    "writable — append items via `vault.append`, remove via "
    "`vault.replace_in_note`. Read INDEX.md for the full distinction "
    "between TODO.md and the various reading-queue files.\n\n"
    "When you're done, your final text turn (no tool calls) is one or "
    "two short sentences telling the user what you did and where. "
    "That text is what they'll see — keep it factual and concrete."
)


def _build_custom_action_system_prompt() -> str:
    """ORGANIZE-style system prompt for the custom-action agent: brief
    + INDEX/USER/PREFERENCES context so the agent knows the vault.
    Falls back to just the brief if context loading fails."""
    # Stamp the current UTC time at the top so the agent has a reliable
    # "now" reference (writing today's date in TODO entries, Memory
    # event bullets, frontmatter, etc.).
    now_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC (%A)")
    pieces: list[str] = [
        f"Current date/time: {now_stamp}.",
        _CUSTOM_ACTION_SYSTEM_BASE,
    ]
    try:
        from app.vault import read_context_files

        for cf in read_context_files():
            content = cf.content.strip() or "(empty)"
            pieces.append(f"## {cf.label}\n\n{content}")
    except Exception:
        log.debug("custom-action: context files not loaded", exc_info=True)
    return "\n\n---\n\n".join(pieces)


def _track_touched(
    name: str, args: dict, touched: set[str]
) -> None:
    """Best-effort recording of which vault paths the agent wrote to,
    surfaced back to the user as `files_touched`."""
    if name in (
        "vault.edit_note", "vault.append", "vault.replace_in_note",
        "vault.update_frontmatter", "vault.delete",
    ):
        p = str(args.get("path") or "")
        if p:
            touched.add(p)
    elif name == "vault.create_note":
        folder = str(args.get("folder") or "")
        title = str(args.get("title") or "")
        if title:
            touched.add(f"{folder}/{title}.md" if folder else f"{title}.md")
    elif name == "vault.move":
        src = str(args.get("src") or "")
        dst = str(args.get("dst") or "")
        if src:
            touched.add(src)
        if dst:
            touched.add(dst)
    elif name == "vault.create_folder":
        p = str(args.get("path") or "")
        if p:
            touched.add(f"{p}/")


async def apply_custom_action(
    article: ArticleRecord, instruction: str, *, max_rounds: int = 12,
) -> CustomActionResult:
    """Run an LLM agent that applies a user-issued free-form instruction
    on a news article. The agent has access to `news.read_news` (so it
    can fetch the article body on demand) and the full `vault.*` toolset
    (so it can write the result anywhere).

    Returns the agent's final text turn + the set of files it touched.
    Raises on stream / tool-loop errors.
    """
    cleaned = (instruction or "").strip()
    if not cleaned:
        raise ValueError("instruction must not be empty")

    from app.tools.registry import get_registry

    registry = get_registry()
    tools = [
        t for t in registry.defs()
        if t.name.startswith("vault.") or t.name == "news.read_news"
    ]
    system_prompt = _build_custom_action_system_prompt()

    user_text = (
        "The user wants you to apply this instruction on a news "
        "article in their vault.\n\n"
        f"### Instruction\n\n{cleaned}\n\n"
        "### Article handle\n\n"
        f"- Title: {article.title}\n"
        f"- ID: `{article.id}`\n"
        f"- Source: {article.feed_title or article.source}"
        + (f" / {article.feed_group}" if article.feed_group else "") + "\n"
        f"- Published: {article.published_at}\n"
        f"- URL: {article.url or '(none)'}\n\n"
        f"Call `news.read_news` with `article_id=\"{article.id}\"` to "
        "read the article body whenever you need it. Then apply the "
        "instruction with the appropriate `vault.*` tools. Stop calling "
        "tools when done; your final text turn is what the user sees."
    )

    history: list[Message] = [
        Message(role="user", content=[TextBlock(text=user_text)])
    ]

    provider = get_llm_router().get(None)
    files_touched: set[str] = set()
    rounds_left = max_rounds

    while True:
        rounds_left -= 1
        if rounds_left < 0:
            raise RuntimeError(
                f"custom action hit max_rounds={max_rounds} without stopping"
            )

        assistant_message: Message | None = None
        async for ev in provider.stream(
            messages=history, tools=tools, system=system_prompt,
        ):
            if ev.type == "error":
                raise RuntimeError(ev.error or "stream error")
            if ev.type == "message_done" and ev.message:
                assistant_message = ev.message

        if assistant_message is None:
            raise RuntimeError("custom action: agent produced no message")
        history.append(assistant_message)

        pending = [
            b for b in assistant_message.content if isinstance(b, ToolUseBlock)
        ]
        if not pending:
            final_text = "".join(
                b.text for b in assistant_message.content
                if isinstance(b, TextBlock)
            ).strip()
            return CustomActionResult(
                summary=final_text or "(no summary)",
                files_touched=sorted(files_touched),
            )

        results: list[ToolResultBlock] = []
        for call in pending:
            _track_touched(call.name, call.input, files_touched)
            try:
                res = await registry.call(call.name, call.input)
                results.append(ToolResultBlock(
                    tool_use_id=call.id,
                    content=res.content,
                    is_error=res.is_error,
                ))
            except Exception as exc:
                log.exception("custom action: tool dispatch failed: %s", call.name)
                results.append(ToolResultBlock(
                    tool_use_id=call.id,
                    content=[TextBlock(text=f"Tool error: {exc!s}")],
                    is_error=True,
                ))
        history.append(Message(role="user", content=list(results)))
