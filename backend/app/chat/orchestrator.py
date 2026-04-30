"""Chat orchestration loop.

Runs the LLM with the registered tool families (vault.*, daily.*, chat.search
in Phase 1), streams events to the caller, and persists the full transcript
on completion.

The system prompt is augmented at session start with the contents of
`INDEX.md` from the vault — this is how the LLM knows the user's folder
conventions and where things live.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from collections.abc import AsyncIterator
from typing import Any, Protocol

from app.config import get_settings
from app.db.models import Chat
from app.llm import (
    Message,
    StreamEvent,
    TextBlock,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
    get_llm_router,
)

from . import persistence

log = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = (
    "You are the user's second brain. You are concise, helpful, and proactive. "
    "You speak the user's language. The user prefers direct answers without "
    "unnecessary preambles. "
    "When the user wants to remember something, decide WHERE in their Obsidian "
    "vault it belongs (using INDEX.md as the map) and write it there with the "
    "appropriate tool — do not just acknowledge in chat. "
    "USER.md tells you who the user is; PREFERENCES.md tells you how they want "
    "you to operate. Update either one via vault.write / vault.append when the "
    "user shares new facts about themselves or expresses a new operating "
    "preference (e.g. 'I prefer cloze deletions for Anki', 'remember I'm "
    "vegetarian')."
)


class ToolDispatcher(Protocol):
    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResultBlock: ...


class _NullDispatcher:
    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResultBlock:
        return ToolResultBlock(
            tool_use_id="(unset)",
            content=[TextBlock(text=f"No tool named {name!r} is registered.")],
            is_error=True,
        )


_NULL_DISPATCHER: ToolDispatcher = _NullDispatcher()


def _build_system_prompt(custom: str | None, language: str) -> str:
    # Stamp the current UTC time at the top of every system prompt so
    # the model has a reliable "now" reference (relative dates in
    # journal entries, "yesterday", "next Monday", reasoning about
    # whether a TODO is overdue, etc.).
    now_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC (%A)")
    parts = [f"Current date/time: {now_stamp}.", custom or _DEFAULT_SYSTEM_PROMPT]
    parts.append(f"The user's preferred language is {language.upper()}.")
    base = "\n\n".join(parts)

    # Read INDEX.md / USER.md / PREFERENCES.md (any may be missing — silently
    # skipped). Late import: the vault package may be unconfigured at boot.
    try:
        from app.vault import read_context_files
    except Exception:
        return base

    try:
        files = read_context_files()
    except Exception as exc:
        log.debug("context files not loaded: %s", exc)
        return base

    for f in files:
        base += f"\n\n## {f.label}\n\n{f.content.strip()}"
    return base


async def run_chat(
    conn: sqlite3.Connection,
    *,
    chat: Chat,
    user_message: Message,
    provider_name: str | None = None,
    model: str | None = None,
    tools: list[ToolDef] | None = None,
    dispatcher: ToolDispatcher | None = None,
    system_prompt: str | None = None,
    language: str | None = None,
) -> AsyncIterator[StreamEvent]:
    """Append `user_message`, run the LLM-tool loop, persist, yield events.

    Yields `text_delta` / `tool_use` / `tool_result` events as they occur, then a
    final `done` event when the loop terminates.
    """
    settings = get_settings()
    router = get_llm_router()
    provider = router.get(provider_name)
    dispatcher = dispatcher or _NULL_DISPATCHER

    history = persistence.read_messages(chat)
    history.append(user_message)
    persistence.write_messages(chat, history)

    sys_prompt = _build_system_prompt(system_prompt, language or settings.app.language)

    rounds_left = settings.llm.max_tool_rounds
    while True:
        rounds_left -= 1
        try:
            stream = provider.stream(
                messages=history,
                tools=tools or [],
                system=sys_prompt,
                model=model,
            )
            assistant_message: Message | None = None
            async for ev in stream:
                if ev.type == "error":
                    yield ev
                    return
                if ev.type == "message_done" and ev.message:
                    assistant_message = ev.message
                yield ev
        except Exception as exc:
            log.exception("LLM stream failed")
            yield StreamEvent(type="error", error=f"LLM stream failed: {exc!s}")
            return

        if assistant_message is None:
            yield StreamEvent(type="error", error="LLM returned no message")
            return

        history.append(assistant_message)
        persistence.write_messages(chat, history)

        # Detect tool_use blocks; if none, we're done.
        pending = [b for b in assistant_message.content if isinstance(b, ToolUseBlock)]
        if not pending:
            persistence.touch(conn, chat.id)
            yield StreamEvent(type="done")
            return

        if rounds_left <= 0:
            yield StreamEvent(
                type="error",
                error=f"Exceeded max_tool_rounds={settings.llm.max_tool_rounds}",
            )
            return

        # Dispatch each tool call. We could parallelize; for now keep it serial
        # so a future per-tool `serial: true` flag is the only knob needed.
        results: list[ToolResultBlock] = []
        for call in pending:
            try:
                res = await dispatcher.call(call.name, call.input)
                res = ToolResultBlock(
                    tool_use_id=call.id,
                    content=res.content,
                    is_error=res.is_error,
                )
            except Exception as exc:
                log.exception("Tool dispatch failed: %s", call.name)
                res = ToolResultBlock(
                    tool_use_id=call.id,
                    content=[TextBlock(text=f"Tool error: {exc!s}")],
                    is_error=True,
                )
            results.append(res)
            yield StreamEvent(type="tool_result", tool_result=res)

        history.append(Message(role="user", content=list(results)))
        persistence.write_messages(chat, history)
        # loop again
