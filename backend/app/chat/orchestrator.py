"""Chat orchestration loop.

Runs the LLM with the configured tools (none in phase 1), streams events to the
caller, and persists the full transcript on completion. The loop is structured
so phase 5 (MCP tools) can plug in without churning this module.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import AsyncIterator
from typing import Any

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
    "unnecessary preambles."
)


class ToolDispatcher:
    """Bridges LLM tool calls to actual handlers.

    Phase 1 ships with no tools. Phase 5 will replace this with the
    ModuleRegistry-backed dispatcher.
    """

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResultBlock:
        return ToolResultBlock(
            tool_use_id="(unset)",
            content=[TextBlock(text=f"No tool named {name!r} is registered.")],
            is_error=True,
        )


_NULL_DISPATCHER = ToolDispatcher()


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

    sys_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT
    sys_prompt += f"\n\nThe user's preferred language is {(language or settings.app.language).upper()}."

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
