"""Encode/decode chat transcripts as markdown.

The on-disk format is documented in PROJECT.md §7.1. It's chosen so the file
remains readable in Obsidian (humans don't see JSON noise, just speech bubbles)
while still being a lossless round-trip for the orchestrator.

Tool calls and results live inside `<details>` blocks tagged with HTML
comments containing the canonical JSON we read back on resume.

Layout:

    ---
    chat_id: …
    module_id: …
    title: …
    created_at: …
    updated_at: …
    model: …
    ---

    ## User
    <plain text>

    <!-- BEGIN_TOOL_RESULT
    {…json…}
    END_TOOL_RESULT -->

    ## Assistant
    <plain text>

    <!-- BEGIN_TOOL_USE
    {…json…}
    END_TOOL_USE -->
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import frontmatter

from app.llm.types import (
    ImageBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


_BEGIN_USE = "<!-- BEGIN_TOOL_USE"
_END_USE = "END_TOOL_USE -->"
_BEGIN_RES = "<!-- BEGIN_TOOL_RESULT"
_END_RES = "END_TOOL_RESULT -->"
_HEADING_RE = re.compile(r"^##\s+(User|Assistant)\s*$", re.MULTILINE)


def _block_to_md(b: TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock) -> str:
    if isinstance(b, TextBlock):
        return b.text
    if isinstance(b, ImageBlock):
        # We never store base64 image bytes inline in the markdown. The caller
        # is responsible for moving the image to vault attachments and
        # rewriting the block to a TextBlock referring to the embed.
        return f"![image]({b.mime})"
    if isinstance(b, ToolUseBlock):
        payload = json.dumps(
            {"id": b.id, "name": b.name, "input": b.input}, ensure_ascii=False, indent=2
        )
        return f"{_BEGIN_USE}\n{payload}\n{_END_USE}"
    if isinstance(b, ToolResultBlock):
        text_only = "\n".join(c.text for c in b.content if isinstance(c, TextBlock))
        payload = json.dumps(
            {
                "tool_use_id": b.tool_use_id,
                "is_error": b.is_error,
                "text": text_only,
            },
            ensure_ascii=False,
            indent=2,
        )
        return f"{_BEGIN_RES}\n{payload}\n{_END_RES}"
    raise TypeError(f"unsupported block type: {type(b)!r}")


def _message_to_md(m: Message) -> str:
    heading = "## User" if m.role == "user" else "## Assistant"
    rendered = "\n\n".join(_block_to_md(b) for b in m.content)
    return f"{heading}\n\n{rendered}"


def render_markdown(
    *,
    chat_id: str,
    title: str,
    module_id: str | None,
    model: str | None,
    created_at: datetime,
    updated_at: datetime,
    messages: list[Message],
) -> str:
    fm = frontmatter.Post(
        content="",
        chat_id=chat_id,
        title=title,
        module_id=module_id,
        model=model,
        created_at=created_at.isoformat(),
        updated_at=updated_at.isoformat(),
    )
    body_parts = [_message_to_md(m) for m in messages if m.role != "system"]
    fm.content = "\n\n".join(body_parts)
    return frontmatter.dumps(fm)


def parse_markdown(text: str) -> tuple[dict[str, Any], list[Message]]:
    """Parse a chat markdown file. Returns (frontmatter dict, messages)."""
    post = frontmatter.loads(text)
    fm = dict(post.metadata)
    body = post.content

    messages: list[Message] = []
    # Split body on `## User` / `## Assistant` headings, preserving order.
    matches = list(_HEADING_RE.finditer(body))
    for i, m in enumerate(matches):
        role = "user" if m.group(1) == "User" else "assistant"
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        chunk = body[start:end].strip()
        messages.append(Message(role=role, content=_chunk_to_blocks(chunk, role)))
    return fm, messages


def _chunk_to_blocks(chunk: str, role: str) -> list:
    """Pull tool_use / tool_result blocks out, return ordered content blocks."""
    blocks: list = []
    cursor = 0
    pattern = re.compile(
        rf"(?P<kind>{re.escape(_BEGIN_USE)}|{re.escape(_BEGIN_RES)})\s*\n(?P<json>.*?)\n"
        rf"(?:{re.escape(_END_USE)}|{re.escape(_END_RES)})",
        re.DOTALL,
    )
    for match in pattern.finditer(chunk):
        prefix = chunk[cursor : match.start()].strip()
        if prefix:
            blocks.append(TextBlock(text=prefix))
        try:
            data = json.loads(match.group("json"))
        except json.JSONDecodeError:
            cursor = match.end()
            continue
        if match.group("kind") == _BEGIN_USE:
            blocks.append(
                ToolUseBlock(id=data["id"], name=data["name"], input=data.get("input", {}))
            )
        else:
            blocks.append(
                ToolResultBlock(
                    tool_use_id=data["tool_use_id"],
                    is_error=bool(data.get("is_error", False)),
                    content=[TextBlock(text=data.get("text", ""))],
                )
            )
        cursor = match.end()

    tail = chunk[cursor:].strip()
    if tail:
        blocks.append(TextBlock(text=tail))
    return blocks


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
